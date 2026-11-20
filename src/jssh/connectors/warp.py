from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

_TAB_STEM = "jssh_sftp"
_TAB_PATH = Path.home() / ".warp" / "tab_configs" / f"{_TAB_STEM}.toml"
_URI = f"warp://tab_config/{_TAB_STEM}?new_window=true"


def in_warp(env: Mapping[str, str] | None = None) -> bool:
    environ = os.environ if env is None else env
    return environ.get("TERM_PROGRAM") == "WarpTerminal"


def warp_sftp_toml(command: str, title: str) -> str:
    return (
        f"name = {_toml_string('jssh sftp')}\n"
        f"title = {_toml_string(title)}\n"
        "\n"
        "[[panes]]\n"
        'id = "sftp"\n'
        'type = "terminal"\n'
        f"commands = [{_toml_string(command)}]\n"
    )


def open_warp_sftp(
    command: str,
    title: str,
    *,
    env: Mapping[str, str] | None = None,
    tab_path: Path | None = None,
    opener: Callable[[str], subprocess.CompletedProcess[bytes]] | None = None,
) -> bool:
    if not in_warp(env):
        return False
    path = _TAB_PATH if tab_path is None else tab_path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(warp_sftp_toml(command, title), encoding="utf-8")
    except OSError:
        return False
    run = opener or _open_uri
    try:
        result = run(_URI)
    except (OSError, subprocess.TimeoutExpired, TypeError):
        return False
    return getattr(result, "returncode", 1) == 0


def _open_uri(uri: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["open", uri], check=False, capture_output=True, timeout=5)


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
