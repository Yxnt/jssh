from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_DISABLED = frozenset({"0", "false", "off", "no"})
_BUNDLED_OTTY = Path("/Applications/Otty.app/Contents/MacOS/otty-cli")
_PASSWORD_PROMPT = re.compile(r"(?im)^\s*(password|passwd|口令|密码)\s*:")
_SHELL_PROMPT = re.compile(r"(?m)[^\n]*[\$#]\s*$")

OttyReason = Literal["not-otty", "no-cli", "disabled"]
Sidecar = Literal["pane", "copied"]
Status = Literal["working", "blocked", "idle", "clear"]


@dataclass(frozen=True)
class OttyOff:
    reason: OttyReason


@dataclass(frozen=True)
class OttyOn:
    cli: str
    pane_id: str | None


OttyPlan = OttyOff | OttyOn


def in_otty(env: Mapping[str, str] | None = None) -> bool:
    environ = os.environ if env is None else env
    if environ.get("JSSH_OTTY", "").strip().lower() in _DISABLED:
        return False
    return environ.get("TERM_PROGRAM") == "otty"


def find_otty_cli(*, which: Callable[[str], str | None] | None = None, bundled: Path = _BUNDLED_OTTY) -> str | None:
    lookup = shutil.which if which is None else which
    found = lookup("otty")
    if found:
        return found
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)
    return None


def otty_split_plan(
    env: Mapping[str, str] | None = None,
    *,
    cli: str | None | Literal[False] = False,
) -> OttyPlan:
    environ = os.environ if env is None else env
    if environ.get("JSSH_OTTY", "").strip().lower() in _DISABLED:
        return OttyOff("disabled")
    if environ.get("TERM_PROGRAM") != "otty":
        return OttyOff("not-otty")
    resolved = find_otty_cli() if cli is False else cli
    if not resolved:
        return OttyOff("no-cli")
    pane = environ.get("OTTY_PANE_ID") or None
    return OttyOn(cli=resolved, pane_id=pane)


def split_sftp_argv(plan: OttyOn, command: str, title: str) -> list[str]:
    argv = [
        plan.cli,
        "pane",
        "split",
        "--direction",
        "right",
        "--command",
        command,
        "--title",
        title,
    ]
    if plan.pane_id:
        argv.extend(["--pane", plan.pane_id])
    return argv


def osc_title(text: str) -> bytes:
    cleaned = "".join(ch for ch in text if ch.isprintable())[:120]
    return b"\033]2;" + cleaned.encode("utf-8") + b"\007"


def osc_status(state: Status, *, kind: str | None = None, msg: str | None = None) -> bytes:
    parts = [f"state={state}", "app=jssh"]
    if kind:
        parts.append(f"kind={kind}")
    if msg:
        encoded = base64.b64encode(msg.encode("utf-8")).decode("ascii")
        parts.append(f"msg={encoded}")
    return b"\033]7501;" + ":".join(parts).encode("ascii") + b"\033\\"


def open_sftp_sidecar(
    command: str,
    *,
    title: str,
    env: Mapping[str, str] | None = None,
    copy: Callable[[str], None] | None = None,
    run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> Sidecar:
    plan = otty_split_plan(env)
    if isinstance(plan, OttyOn):
        argv = split_sftp_argv(plan, command, title)
        runner = subprocess.run if run is None else run
        try:
            result = runner(argv, check=False, capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0:
            return "pane"
    _copy_command(command, copy)
    return "copied"


def _copy_command(command: str, copy: Callable[[str], None] | None) -> None:
    if copy is not None:
        copy(command)
        return
    subprocess.run(["pbcopy"], input=command.encode("utf-8"), check=False)


def _write_tty(data: bytes) -> None:
    try:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    except OSError:
        pass


class OttySession:
    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        *,
        emit: Callable[[bytes], None] | None = None,
    ) -> None:
        self._on = in_otty(env)
        self._emit = emit or _write_tty
        self._state: Status | None = None

    def start(self, title: str | None = None) -> None:
        if not self._on:
            return
        if title:
            self._emit(osc_title(title))
        self._emit(self._set("working", msg=title or "Connecting"))

    def observe(self, text: str, *, at_gateway: bool) -> bytes:
        if not self._on:
            return b""
        wanted = self._wanted(text, at_gateway=at_gateway)
        if wanted == self._state:
            return b""
        kind = "auth" if wanted == "blocked" else None
        msg = "Password" if wanted == "blocked" else None
        return self._set(wanted, kind=kind, msg=msg)

    def close(self) -> None:
        if not self._on:
            return
        self._emit(osc_status("clear"))
        self._state = None

    def _wanted(self, text: str, *, at_gateway: bool) -> Status:
        if _PASSWORD_PROMPT.search(text):
            return "blocked"
        if _SHELL_PROMPT.search(text.replace("\r", "")):
            return "idle"
        if at_gateway:
            return "working"
        if self._state == "blocked" and text.strip():
            return "idle"
        return self._state or "working"

    def _set(self, state: Status, *, kind: str | None = None, msg: str | None = None) -> bytes:
        self._state = state
        return osc_status(state, kind=kind, msg=msg)
