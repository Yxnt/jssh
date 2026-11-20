from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Callable, Mapping

_TAB_TERMS = frozenset({"Apple_Terminal", "iTerm.app", "kitty", "WezTerm"})


def can_open_tab(env: Mapping[str, str] | None = None) -> bool:
    return _term_program(env) in _TAB_TERMS


def open_terminal_tab(
    command: str,
    *,
    env: Mapping[str, str] | None = None,
    run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> bool:
    argv = _tab_argv(_term_program(env), command)
    if argv is None:
        return False
    runner = subprocess.run if run is None else run
    try:
        result = runner(argv, check=False, capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired, TypeError):
        return False
    return getattr(result, "returncode", 1) == 0


def _term_program(env: Mapping[str, str] | None) -> str:
    environ = os.environ if env is None else env
    return environ.get("TERM_PROGRAM", "")


def _tab_argv(program: str, command: str) -> list[str] | None:
    if program == "Apple_Terminal":
        return ["osascript", "-e", _terminal_script(command)]
    if program == "iTerm.app":
        return ["osascript", "-e", _iterm_script(command)]
    parts = shlex.split(command)
    if program == "kitty":
        return ["kitten", "@", "launch", "--type=tab", "--cwd=current", "--", *parts]
    if program == "WezTerm":
        return ["wezterm", "cli", "spawn", "--", *parts]
    return None


def _terminal_script(command: str) -> str:
    cmd = _as_string(command)
    return (
        'tell application "Terminal"\n'
        "  activate\n"
        "  if (count of windows) is 0 then\n"
        f"    do script {cmd}\n"
        "  else\n"
        f"    do script {cmd} in front window\n"
        "  end if\n"
        "end tell"
    )


def _iterm_script(command: str) -> str:
    cmd = _as_string(command)
    return (
        'tell application "iTerm"\n'
        "  activate\n"
        "  if (count of windows) is 0 then\n"
        "    create window with default profile\n"
        "  end if\n"
        "  tell current window\n"
        "    create tab with default profile\n"
        "    tell current session\n"
        f"      write text {cmd}\n"
        "    end tell\n"
        "  end tell\n"
        "end tell"
    )


def _as_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
