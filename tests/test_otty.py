from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

from jssh.connectors.otty import (
    OttyOff,
    OttyOn,
    OttySession,
    find_otty_cli,
    in_otty,
    open_sftp_sidecar,
    osc_status,
    osc_title,
    otty_split_plan,
    split_sftp_argv,
)
from jssh.connectors.session import resolve_gateway_argv


def test_otty_split_plan_reasons(monkeypatch) -> None:
    monkeypatch.setattr("jssh.connectors.otty.find_otty_cli", lambda: "/app/otty-cli")
    assert otty_split_plan({"TERM_PROGRAM": "WarpTerminal"}) == OttyOff("not-otty")
    assert otty_split_plan({"TERM_PROGRAM": "otty", "JSSH_OTTY": "0"}) == OttyOff("disabled")
    assert otty_split_plan({"TERM_PROGRAM": "otty"}, cli=None) == OttyOff("no-cli")
    assert otty_split_plan({"TERM_PROGRAM": "otty", "OTTY_PANE_ID": "p_1"}) == OttyOn(
        "/app/otty-cli", "p_1"
    )


def test_in_otty_ignores_other_terminals() -> None:
    assert in_otty({"TERM_PROGRAM": "otty"}) is True
    assert in_otty({"TERM_PROGRAM": "otty", "JSSH_OTTY": "off"}) is False
    assert in_otty({"TERM_PROGRAM": "iTerm.app"}) is False


def test_split_sftp_argv_targets_current_pane() -> None:
    plan = OttyOn(cli="/app/otty-cli", pane_id="p_9")
    argv = split_sftp_argv(plan, "jssh sftp bi-server01 --account linux-root", "sftp bi-server01")
    assert argv == [
        "/app/otty-cli",
        "pane",
        "split",
        "--direction",
        "right",
        "--command",
        "jssh sftp bi-server01 --account linux-root",
        "--title",
        "sftp bi-server01",
        "--pane",
        "p_9",
    ]


def test_ctrl_f_splits_in_otty(monkeypatch) -> None:
    monkeypatch.setattr("jssh.connectors.otty.find_otty_cli", lambda: "/app/otty-cli")
    ran: list[list[str]] = []

    def run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        ran.append(argv)
        return SimpleNamespace(returncode=0)

    copied: list[str] = []
    result = open_sftp_sidecar(
        "jssh sftp bi-server01 --account linux-root",
        title="sftp bi-server01",
        env={"TERM_PROGRAM": "otty", "OTTY_PANE_ID": "p_1"},
        copy=copied.append,
        run=run,
    )
    assert result == "pane"
    assert copied == []
    assert ran == [
        split_sftp_argv(
            OttyOn("/app/otty-cli", "p_1"),
            "jssh sftp bi-server01 --account linux-root",
            "sftp bi-server01",
        )
    ]


def test_ctrl_f_copies_when_not_otty() -> None:
    copied: list[str] = []
    result = open_sftp_sidecar(
        "jssh sftp bi-server01 --account linux-root",
        title="sftp bi-server01",
        env={"TERM_PROGRAM": "Apple_Terminal"},
        copy=copied.append,
        run=lambda *_a, **_k: SimpleNamespace(returncode=0),
    )
    assert result == "copied"
    assert copied == ["jssh sftp bi-server01 --account linux-root"]


def test_ctrl_f_opens_warp_window(tmp_path: Path) -> None:
    opened: list[str] = []
    tab = tmp_path / "jssh_sftp.toml"
    copied: list[str] = []

    def opener(uri: str) -> SimpleNamespace:
        opened.append(uri)
        return SimpleNamespace(returncode=0)

    result = open_sftp_sidecar(
        "jssh sftp bi-server01 --account linux-root",
        title="sftp bi-server01",
        env={"TERM_PROGRAM": "WarpTerminal"},
        copy=copied.append,
        warp_tab=tab,
        warp_open=opener,
    )
    assert result == "pane"
    assert copied == []
    assert opened == ["warp://tab_config/jssh_sftp?new_window=true"]
    text = tab.read_text(encoding="utf-8")
    assert "jssh sftp bi-server01 --account linux-root" in text
    assert 'title = "sftp bi-server01"' in text
    assert 'type = "terminal"' in text


def test_ctrl_f_copies_when_split_fails(monkeypatch) -> None:
    monkeypatch.setattr("jssh.connectors.otty.find_otty_cli", lambda: "/app/otty-cli")
    copied: list[str] = []
    result = open_sftp_sidecar(
        "jssh sftp bi-server01 --account linux-root",
        title="sftp bi-server01",
        env={"TERM_PROGRAM": "otty"},
        copy=copied.append,
        run=lambda *_a, **_k: SimpleNamespace(returncode=1),
    )
    assert result == "copied"
    assert copied == ["jssh sftp bi-server01 --account linux-root"]


def test_osc_status_encodes_message() -> None:
    raw = osc_status("blocked", kind="auth", msg="Password")
    assert raw.startswith(b"\033]7501;")
    assert raw.endswith(b"\033\\")
    assert b"state=blocked" in raw
    assert b"kind=auth" in raw
    assert b"app=jssh" in raw
    assert base64.b64encode(b"Password").decode("ascii").encode("ascii") in raw


def test_osc_title_strips_controls() -> None:
    raw = osc_title("bi-server01 / linux-root\n")
    assert raw == b"\033]2;bi-server01 / linux-root\007"


def test_status_working_password_then_idle() -> None:
    written: list[bytes] = []
    session = OttySession(env={"TERM_PROGRAM": "otty"}, emit=written.append)
    session.start("bi-server01 / linux-root")
    assert any(b"]2;bi-server01 / linux-root" in item for item in written)
    assert any(b"state=working" in item for item in written)
    assert session.observe("[MFA auth]: ", at_gateway=True) == b""
    blocked = session.observe("Password: ", at_gateway=False)
    assert b"state=blocked" in blocked
    assert b"kind=auth" in blocked
    idle = session.observe("root@bi-server01:~# ", at_gateway=False)
    assert b"state=idle" in idle
    session.close()
    assert written[-1] == osc_status("clear")


def test_status_idles_on_shell_without_password() -> None:
    session = OttySession(env={"TERM_PROGRAM": "otty"}, emit=lambda _data: None)
    session.start("bi-server01 / linux-root")
    assert session.observe("Opt> ", at_gateway=True) == b""
    idle = session.observe("$ ", at_gateway=True)
    assert b"state=idle" in idle


def test_status_stays_off_outside_otty() -> None:
    written: list[bytes] = []
    session = OttySession(env={"TERM_PROGRAM": "WarpTerminal"}, emit=written.append)
    session.start("bi-server01 / linux-root")
    assert written == []
    assert session.observe("Password: ", at_gateway=False) == b""
    session.close()
    assert written == []


def test_gateway_argv_uses_absolute_binary(monkeypatch) -> None:
    monkeypatch.setattr(
        "jssh.connectors.session.shutil.which", lambda name: f"/usr/bin/{name}"
    )
    assert resolve_gateway_argv(["ssh", "-p", "2222"]) == ["/usr/bin/ssh", "-p", "2222"]
    assert resolve_gateway_argv(["/usr/bin/sftp", "-P", "2222"]) == ["/usr/bin/sftp", "-P", "2222"]


def test_find_otty_cli_prefers_path_then_bundle(tmp_path: Path) -> None:
    bundled = tmp_path / "otty-cli"
    bundled.write_text("", encoding="utf-8")
    bundled.chmod(0o755)
    assert find_otty_cli(which=lambda _name: "/usr/local/bin/otty", bundled=bundled) == "/usr/local/bin/otty"
    assert find_otty_cli(which=lambda _name: None, bundled=bundled) == str(bundled)
    assert find_otty_cli(which=lambda _name: None, bundled=tmp_path / "missing") is None
