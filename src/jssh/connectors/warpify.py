from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_CSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\].*?(?:\x07|\x1b\\)")
_ERASE_DISPLAY = re.compile(r"\x1b\[[0-3]?J")
_ERASE_DISPLAY_B = re.compile(rb"\x1b\[[0-3]?J")
_PROMPT_TAIL = re.compile(r"[#$%] ?$")
_PASSWORD_TAIL = re.compile(r"(?i)password.*: ?$")
_SFTP_TAIL = re.compile(r"(?i)sftp> ?$")
_KOKO_TAILS = ("Opt>", "ID>", "[Host]>", "[MFA auth]:")
_DISABLED = frozenset({"0", "false", "off", "no"})
_SID = re.compile(rb"WARP_SESSION_ID=(\d+)")
_BOOTSTRAP = re.compile(rb"WARP_|stty raw|SourcedRcFileForWarp|WARP_BOOTSTRAPPED")


@dataclass(frozen=True)
class NestedShell:
    kind: Literal["bash", "zsh", "fish"]
    uname: Literal["Linux", "Darwin"]


@dataclass(frozen=True)
class WarpOff:
    reason: Literal["not-warp", "sftp", "disabled", "not-tty"]


@dataclass(frozen=True)
class WarpNested:
    pass


WarpPlan = WarpOff | WarpNested


@dataclass(frozen=True)
class Watching:
    pass


@dataclass(frozen=True)
class Announced:
    shell: NestedShell


@dataclass(frozen=True)
class Live:
    shell: NestedShell
    session_id: int


WarpState = WarpOff | Watching | Announced | Live


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
    return WarpNested()


def infer_nested_shell(chunk: str) -> NestedShell | None:
    last = chunk.rsplit("\n", 1)[-1].rstrip("\r")
    plain = _CSI.sub("", last)
    if any(token in plain for token in _KOKO_TAILS):
        return None
    if _PASSWORD_TAIL.search(plain) or _SFTP_TAIL.search(plain):
        return None
    if not _PROMPT_TAIL.search(plain):
        return None
    kind: Literal["bash", "zsh", "fish"] = "zsh" if plain.rstrip().endswith("%") else "bash"
    return NestedShell(kind, "Linux")


def encode_sourced_rc(shell: NestedShell) -> bytes:
    body = (
        '{"hook": "SourcedRcFileForWarp", "value": { "shell": "'
        + shell.kind
        + '", "uname": "'
        + shell.uname
        + '"}}'
    )
    return b"\x1bP$f" + body.encode("ascii") + b"\x1b\\"


def encode_hook(hook: str, value: dict) -> bytes:
    body = json.dumps({"hook": hook, "value": value}, separators=(",", ":"))
    return b"\x1bP$d" + body.encode("ascii").hex().encode("ascii") + b"\x1b\\"


def strip_erase_display(data: str | bytes) -> str | bytes:
    if isinstance(data, (bytes, bytearray)):
        return _ERASE_DISPLAY_B.sub(b"", bytes(data))
    return _ERASE_DISPLAY.sub("", data)


def in_warp_terminal(env: Mapping[str, str] | None = None) -> bool:
    environ = os.environ if env is None else env
    return environ.get("TERM_PROGRAM") == "WarpTerminal"


def _write_stdout(dcs: bytes) -> None:
    os.write(sys.stdout.fileno(), dcs)


def _is_bootstrap(raw: bytes) -> bool:
    return _BOOTSTRAP.search(raw) is not None


class WarpSession:
    def __init__(
        self,
        plan: WarpPlan,
        *,
        emit: Callable[[bytes], None] | None = None,
    ) -> None:
        self._state: WarpState = Watching() if isinstance(plan, WarpNested) else plan
        self._emit = emit or _write_stdout
        self._boot = bytearray()
        self._line = bytearray()
        self._pending = b""
        self._awaiting = False
        self._secret = False
        self._block = 0

    @property
    def took_shell(self) -> bool:
        return isinstance(self._state, (Announced, Live))

    def feed_output(self, data: str | bytes) -> str | bytes:
        data = strip_erase_display(data)
        binary = isinstance(data, (bytes, bytearray))
        text = data.decode("utf-8", "replace") if binary else data
        extra = b""
        if isinstance(self._state, Watching):
            shell = infer_nested_shell(text)
            if shell is not None:
                self._state = Announced(shell)
                extra += encode_sourced_rc(shell)
        last = _CSI.sub("", text.rsplit("\n", 1)[-1])
        self._secret = bool(_PASSWORD_TAIL.search(last))
        extra = self._pending + extra
        self._pending = b""
        if (
            self._awaiting
            and isinstance(self._state, Live)
            and infer_nested_shell(text) is not None
        ):
            extra += self._finish_hooks()
            self._awaiting = False
        if not extra:
            return data
        if binary:
            return bytes(data) + extra
        return data + extra.decode("ascii")

    def feed_input(self, data: str | bytes) -> str | bytes:
        raw = bytes(data) if isinstance(data, (bytes, bytearray)) else data.encode("utf-8")
        if self._swallow_bootstrap(raw):
            return b"" if isinstance(data, (bytes, bytearray)) else ""
        if not isinstance(self._state, Live):
            return data
        for byte in raw:
            if byte in (0x08, 0x7F):
                if self._line:
                    self._line.pop()
            elif byte == 0x03:
                self._line.clear()
                self._awaiting = False
            elif byte in (0x0D, 0x0A):
                self._on_enter()
            elif 32 <= byte < 127:
                self._line.append(byte)
        return data

    def _swallow_bootstrap(self, raw: bytes) -> bool:
        if isinstance(self._state, Announced):
            if not _is_bootstrap(raw) and _SID.search(raw) is None:
                return False
            self._boot.extend(raw)
            self._capture_sid()
            return True
        if isinstance(self._state, Live) and _is_bootstrap(raw):
            self._boot.extend(raw)
            return True
        return False

    def _capture_sid(self) -> None:
        if not isinstance(self._state, Announced):
            return
        match = _SID.search(bytes(self._boot))
        if match is None:
            return
        sid = int(match.group(1))
        shell = self._state.shell
        self._state = Live(shell, sid)
        self._emit(
            encode_hook(
                "InitShell",
                {
                    "session_id": sid,
                    "shell": shell.kind,
                    "user": "root",
                    "hostname": "localhost",
                    "is_subshell": True,
                },
            )
        )

    def _on_enter(self) -> None:
        if not isinstance(self._state, Live):
            return
        cmd = self._line.decode("utf-8", "replace").strip()
        self._line.clear()
        if not cmd or self._secret:
            return
        self._block += 1
        self._pending += encode_hook(
            "Preexec",
            {"command": cmd, "session_id": self._state.session_id},
        )
        self._awaiting = True

    def _finish_hooks(self) -> bytes:
        assert isinstance(self._state, Live)
        block = str(self._block)
        sid = self._state.session_id
        return encode_hook(
            "CommandFinished",
            {"exit_code": 0, "next_block_id": block, "session_id": sid},
        ) + encode_hook(
            "Precmd",
            {
                "exit_code": 0,
                "next_block_id": block,
                "pwd": "",
                "ps1": "",
                "git_head": "",
                "git_branch": "",
                "virtual_env": "",
                "conda_env": "",
                "node_version": "",
                "session_id": sid,
                "is_after_in_band_command": False,
            },
        )
