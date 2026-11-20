from __future__ import annotations

from types import SimpleNamespace

import pytest

from jssh.cli import _connect
from jssh.errors import Cancelled, ConfigError
from jssh.models.asset import Account, Asset
from jssh.ui.fzf import pick_account, pick_action


def test_pick_account_esc_goes_back(sample_asset, monkeypatch: pytest.MonkeyPatch) -> None:
    accounts = [
        Account(id="acc-1", name="linux-root", username="root"),
        Account(id="acc-2", name="linux-user", username="user"),
    ]
    monkeypatch.setattr("jssh.ui.fzf._run", lambda *_a, **_k: (_ for _ in ()).throw(Cancelled("cancelled")))
    assert pick_account(sample_asset, accounts) is None


def test_pick_account_back_row_goes_back(sample_asset, monkeypatch: pytest.MonkeyPatch) -> None:
    accounts = [
        Account(id="acc-1", name="linux-root", username="root"),
        Account(id="acc-2", name="linux-user", username="user"),
    ]
    monkeypatch.setattr("jssh.ui.fzf._run", lambda *_a, **_k: "back\tBack")
    assert pick_account(sample_asset, accounts) is None


def test_pick_account_selects_row(sample_asset, monkeypatch: pytest.MonkeyPatch) -> None:
    accounts = [
        Account(id="acc-1", name="linux-root", username="root"),
        Account(id="acc-2", name="linux-user", username="user"),
    ]
    monkeypatch.setattr("jssh.ui.fzf._run", lambda *_a, **_k: "acc-2\tlinux-user\tuser\tssh")
    assert pick_account(sample_asset, accounts) == accounts[1]


def test_pick_account_skips_picker_for_one_account(sample_asset, sample_account, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jssh.ui.fzf._run",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("picker should not open")),
    )
    assert pick_account(sample_asset, [sample_account]) is sample_account


def test_pick_account_rejects_stale_id(sample_asset, monkeypatch: pytest.MonkeyPatch) -> None:
    accounts = [
        Account(id="acc-1", name="linux-root", username="root"),
        Account(id="acc-2", name="linux-user", username="user"),
    ]
    monkeypatch.setattr("jssh.ui.fzf._run", lambda *_a, **_k: "missing\tgone\tgone\tssh")
    with pytest.raises(ConfigError, match="no longer"):
        pick_account(sample_asset, accounts)



def test_account_and_action_share_back_row(sample_asset, monkeypatch: pytest.MonkeyPatch) -> None:
    accounts = [
        Account(id="acc-1", name="linux-root", username="root"),
        Account(id="acc-2", name="linux-user", username="user"),
    ]
    seen: list[list[str]] = []

    def run(lines: list[str], **_kwargs: object) -> str:
        seen.append(lines)
        raise Cancelled("cancelled")

    monkeypatch.setattr("jssh.ui.fzf._run", run)
    assert pick_account(sample_asset, accounts) is None
    with pytest.raises(Cancelled):
        pick_action(sample_asset, accounts[0])
    assert seen[0][-1] == seen[1][-1] == "back\tBack"

def test_connect_account_back_reopens_host_picker(
    tmp_cfg,
    sample_asset,
    sample_account,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = Asset(id="asset-2", hostname="other-host", ip="10.0.0.21")
    picks = SimpleNamespace(asset=0, account=0)

    def pick_asset(assets: list[Asset], recent=None) -> Asset:
        picks.asset += 1
        return sample_asset if picks.asset == 1 else other

    def pick_account(asset: Asset, accounts: list[Account]) -> Account | None:
        picks.account += 1
        if picks.account == 1:
            assert asset is sample_asset
            return None
        assert asset is other
        raise Cancelled("done")

    monkeypatch.setattr("jssh.cli.pick_asset", pick_asset)
    monkeypatch.setattr("jssh.cli.pick_account", pick_account)
    monkeypatch.setattr("jssh.cli.load_or_sync", lambda cfg: ([sample_asset, other], False))
    monkeypatch.setattr("jssh.cli.accounts_for", lambda cfg, asset: [sample_account])
    monkeypatch.setattr("jssh.cli.load_recent", lambda cfg: [])

    with pytest.raises(Cancelled, match="done"):
        _connect(tmp_cfg, None, None, None)
    assert picks.asset == 2
    assert picks.account == 2
