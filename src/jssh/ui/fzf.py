from __future__ import annotations

import shutil
import subprocess

from jssh.errors import Cancelled, ConfigError
from jssh.models.asset import Account, Asset, RecentEntry

_BACK = "back\tBack"

def require_fzf() -> str:
    path = shutil.which("fzf")
    if not path:
        raise ConfigError("fzf is not on PATH. Install it with: brew install fzf")
    return path


def pick_asset(assets: list[Asset], recent: list[RecentEntry] | None = None) -> Asset:
    if not assets:
        raise ConfigError("No granted assets in cache. Run jssh sync.")
    ranked = _rank_recent(assets, recent or [])
    lines = [_asset_line(asset, starred=asset.select_key in _recent_keys(recent or [])) for asset in ranked]
    selected = _run(
        lines,
        header="JumpServer Asset Manager  |  NAME / IP / NODE  |  Enter select  Esc cancel",
        nth="2..",
    )
    key = selected.split("\t", 2)
    ident, node_key = key[0], key[1] if len(key) > 1 else ""
    for asset in ranked:
        current_node = asset.node.key if asset.node else ""
        if asset.id == ident and current_node == node_key:
            return asset
    raise ConfigError("Selected asset is no longer in the list")


def pick_account(asset: Asset, accounts: list[Account]) -> Account | None:
    if not accounts:
        raise ConfigError(
            f"No system accounts for {asset.hostname}. "
            "The API returned none. Add sftp.account_overrides after you confirm the names."
        )
    if len(accounts) == 1:
        return accounts[0]
    lines = [f"{item.id}\t{item.name}\t{item.username}\t{item.protocol}" for item in accounts]
    lines.append(_BACK)
    try:
        selected = _run(
            lines,
            header=f"{asset.hostname}  |  Enter select  Esc back",
            nth="2..",
        )
    except Cancelled:
        return None
    ident = selected.split("\t", 1)[0]
    if ident == "back":
        return None
    for item in accounts:
        if item.id == ident:
            return item
    raise ConfigError("Selected account is no longer in the list")


def pick_action(asset: Asset | None = None, account: Account | None = None) -> str:
    lines = [
        "sftp\tSFTP File Manager",
        "ssh\tSSH Terminal",
        "copy-sftp\tCopy SFTP command",
        "copy-ssh\tCopy SSH command",
        _BACK,
    ]
    header = "Choose action"
    if asset and account:
        header = f"{asset.hostname} / {account.name}  |  Enter select  Esc back"
    try:
        selected = _run(lines, header=header, nth="2..")
    except Cancelled:
        return "back"
    return selected.split("\t", 1)[0]


def filter_assets(assets: list[Asset], query: str) -> list[Asset]:
    needle = query.strip().lower()
    if not needle:
        return list(assets)
    matched: list[Asset] = []
    for asset in assets:
        hay = " ".join(
            [
                asset.hostname,
                asset.ip,
                asset.comment,
                asset.node_label,
                asset.id,
            ]
        ).lower()
        if needle in hay:
            matched.append(asset)
    return matched


def _run(lines: list[str], *, header: str, nth: str) -> str:
    fzf = require_fzf()
    result = subprocess.run(
        [
            fzf,
            "--ansi",
            "--height=80%",
            "--layout=reverse",
            "--border",
            "--delimiter=\t",
            f"--with-nth={nth}",
            "--header",
            header,
            "--prompt=Search: ",
            "--info=inline",
        ],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise Cancelled("cancelled")
    selected = result.stdout.strip()
    if not selected:
        raise Cancelled("cancelled")
    return selected.splitlines()[0]


def _asset_line(asset: Asset, *, starred: bool) -> str:
    mark = "*" if starred else " "
    node = asset.node_label
    return f"{asset.id}\t{asset.node.key if asset.node else ''}\t{mark} {asset.hostname}\t{asset.ip}\t{node}"


def _recent_keys(recent: list[RecentEntry]) -> set[str]:
    return {f"{item.asset_id}\t{item.node_key}" for item in recent}


def _rank_recent(assets: list[Asset], recent: list[RecentEntry]) -> list[Asset]:
    order = {f"{item.asset_id}\t{item.node_key}": index for index, item in enumerate(recent)}
    return sorted(assets, key=lambda asset: (order.get(asset.select_key, len(order)), asset.hostname))
