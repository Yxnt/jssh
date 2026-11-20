from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_CSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\].*?(?:\x07|\x1b\\)")
_PROMPT_TAIL = re.compile(r"[#$%] ?$")
_PASSWORD_TAIL = re.compile(r"(?i)password.*: ?$")
_SFTP_TAIL = re.compile(r"(?i)sftp> ?$")
_KOKO_TAILS = ("Opt>", "ID>", "[Host]>", "[MFA auth]:")
_DISABLED = frozenset({"0", "false", "off", "no"})
_DCS_PRE = b"\x1bP$d"
_DCS_END = b"\x1b\\"


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
class Live:
    shell: NestedShell


WarpState = WarpOff | Watching | Live


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


def _parent_sid(env: Mapping[str, str]) -> int | None:
    raw = env.get("WARP_SESSION_ID", "").strip()
    if raw.isdigit():
        return int(raw)
    return None


def _nested_sid(parent: int | None) -> int:
    sid = int.from_bytes(os.urandom(8), "big")
    if parent is not None and sid == parent:
        sid ^= 1
    return sid


def encode_hook(hook: str, value: dict) -> bytes:
    body = json.dumps({"hook": hook, "value": value}, separators=(",", ":"))
    return _DCS_PRE + body.encode("ascii").hex().encode("ascii") + _DCS_END


def _join(left: str | bytes, right: str | bytes) -> str | bytes:
    if isinstance(right, (bytes, bytearray)):
        lbytes = left if isinstance(left, (bytes, bytearray)) else left.encode("ascii")
        return bytes(lbytes) + bytes(right)
    rtext = right if isinstance(right, str) else right.decode("ascii")
    ltext = left if isinstance(left, str) else left.decode("ascii")
    return ltext + rtext


class WarpSession:
    def __init__(
        self,
        plan: WarpPlan,
        *,
        env: Mapping[str, str] | None = None,
        session_id: int | None = None,
    ) -> None:
        environ = os.environ if env is None else env
        self._state: WarpState = Watching() if isinstance(plan, WarpNested) else plan
        self._parent_id = _parent_sid(environ)
        self._session_id = session_id
        self._line = bytearray()
        self._pending = b""
        self._awaiting = False
        self._secret = False
        self._block = 0

    @property
    def took_shell(self) -> bool:
        return isinstance(self._state, Live)

    def feed_output(self, data: str | bytes) -> str | bytes:
        binary = isinstance(data, (bytes, bytearray))
        text = data.decode("utf-8", "replace") if binary else data
        extra_before = self._pending
        self._pending = b""
        extra_after = b""
        if isinstance(self._state, Watching):
            shell = infer_nested_shell(text)
            if shell is not None:
                self._state = Live(shell)
                if self._session_id is None:
                    self._session_id = _nested_sid(self._parent_id)
                extra_after += encode_hook(
                    "InitShell",
                    {
                        "session_id": self._session_id,
                        "shell": shell.kind,
                        "user": "root",
                        "hostname": "localhost",
                        "is_subshell": True,
                    },
                )
        self._secret = bool(_PASSWORD_TAIL.search(_CSI.sub("", text.rsplit("\n", 1)[-1])))
        if (
            self._awaiting
            and isinstance(self._state, Live)
            and infer_nested_shell(text) is not None
        ):
            extra_after += self._finish_hooks()
            self._awaiting = False
        out = data
        if extra_before:
            out = _join(extra_before, out)
        if extra_after:
            out = _join(out, extra_after)
        return out

    def feed_input(self, data: str | bytes) -> str | bytes:
        if not isinstance(self._state, Live) or self._session_id is None:
            return data
        raw = data if isinstance(data, (bytes, bytearray)) else data.encode("utf-8")
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

    def _on_enter(self) -> None:
        cmd = self._line.decode("utf-8", "replace").strip()
        self._line.clear()
        if not cmd or self._secret or self._session_id is None:
            return
        self._block += 1
        self._pending += encode_hook(
            "Preexec",
            {"command": cmd, "session_id": self._session_id},
        )
        self._awaiting = True

    def _finish_hooks(self) -> bytes:
        assert self._session_id is not None
        block = str(self._block)
        sid = self._session_id
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
