from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_ERASE_DISPLAY = re.compile(r"\x1b\[[0-3]?J")
_ERASE_DISPLAY_B = re.compile(rb"\x1b\[[0-3]?J")
_DISABLED = frozenset({"0", "false", "off", "no"})


@dataclass(frozen=True)
class WarpOff:
    reason: Literal["not-warp", "sftp", "disabled", "not-tty", "koko"]


@dataclass(frozen=True)
class WarpNested:
    pass


WarpPlan = WarpOff | WarpNested


def warp_plan(
    argv: list[str],
    *,
    env: Mapping[str, str] | None = None,
    is_tty: bool,
) -> WarpPlan:
    environ = os.environ if env is None else env
    if argv and Path(argv[0]).name == "sftp":
        return WarpOff("sftp")
    flag = environ.get("JSSH_WARPIFY", "").strip().lower()
    if flag in _DISABLED:
        return WarpOff("disabled")
    if environ.get("TERM_PROGRAM") != "WarpTerminal":
        return WarpOff("not-warp")
    if not is_tty:
        return WarpOff("not-tty")
    return WarpOff("koko")


def strip_erase_display(data: str | bytes) -> str | bytes:
    if isinstance(data, (bytes, bytearray)):
        return _ERASE_DISPLAY_B.sub(b"", bytes(data))
    return _ERASE_DISPLAY.sub("", data)


def in_warp_terminal(env: Mapping[str, str] | None = None) -> bool:
    environ = os.environ if env is None else env
    return environ.get("TERM_PROGRAM") == "WarpTerminal"


class WarpSession:
    def __init__(self, plan: WarpPlan) -> None:
        self._plan = plan

    @property
    def took_shell(self) -> bool:
        return False

    def feed_output(self, data: str | bytes) -> str | bytes:
        return strip_erase_display(data)

    def feed_input(self, data: str | bytes) -> str | bytes:
        return data
