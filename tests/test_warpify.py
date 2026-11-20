from __future__ import annotations

import json
import os
import re

from jssh.connectors.session import KokoDriver, KokoLogin
from jssh.connectors.warpify import (
    NestedShell,
    WarpNested,
    WarpOff,
    WarpSession,
    infer_nested_shell,
    strip_erase_display,
    warp_plan,
)


class Child:
    def __init__(self) -> None:
        self.sent: list[object] = []

    def sendline(self, value: str) -> None:
        self.sent.append(value)

    def send(self, value: bytes) -> None:
        self.sent.append(value)


def _as_bytes(data: str | bytes) -> bytes:
    return data if isinstance(data, (bytes, bytearray)) else data.encode("utf-8")


def _hooks(data: str | bytes) -> list[dict]:
    raw = _as_bytes(data)
    found: list[dict] = []
    for match in re.finditer(rb"\x1bP\$f(\{.*?\})\x1b\\", raw):
        found.append(json.loads(match.group(1)))
    for match in re.finditer(rb"\x1bP\$d([0-9a-fA-F]+)\x1b\\", raw):
        found.append(json.loads(bytes.fromhex(match.group(1).decode("ascii"))))
    return found


def test_infer_root_prompt() -> None:
    assert infer_nested_shell("[root@localhost ~]# ") == NestedShell("bash", "Linux")
    assert infer_nested_shell("Opt>") is None


def test_sftp_is_off() -> None:
    assert warp_plan(["sftp", "h"], env={"TERM_PROGRAM": "WarpTerminal"}, is_tty=True) == WarpOff(
        "sftp"
    )


def test_warp_terminal_is_nested() -> None:
    assert warp_plan(["ssh", "h"], env={"TERM_PROGRAM": "WarpTerminal"}, is_tty=True) == WarpNested()


def test_first_prompt_emits_sourced_rc() -> None:
    emitted: list[bytes] = []
    warp = WarpSession(WarpNested(), emit=emitted.append)
    out = warp.feed_output("[root@localhost ~]# ")
    hooks = _hooks(out)
    assert hooks[0]["hook"] == "SourcedRcFileForWarp"
    assert emitted == []
    assert warp.took_shell


def test_bootstrap_swallowed_until_sid_then_user_keys_pass(monkeypatch) -> None:
    monkeypatch.setenv("WARP_SESSION_ID", "10403532000930851587")
    emitted: list[bytes] = []
    warp = WarpSession(WarpNested(), emit=emitted.append)
    warp.feed_output("[root@localhost ~]# ")
    first = warp.feed_input(
        b"[ -z $WARP_BOOTSTRAPPED ] && eval 'WARP_SESSION_ID=5203338155054621276; stty raw'"
    )
    more = warp.feed_input(b"WARP_BOOTSTRAPPED=1\n")
    user = warp.feed_input(b"ls\r")
    assert first == b""
    assert more == b""
    assert user == b"ls\r"
    hooks = _hooks(emitted[0])
    assert hooks[0]["hook"] == "InitShell"
    assert hooks[0]["value"]["session_id"] == 5203338155054621276
    assert hooks[0]["value"]["session_id"] != int(os.environ["WARP_SESSION_ID"])



def test_bash_preexec_after_sid_is_swallowed() -> None:
    warp = WarpSession(WarpNested(), emit=lambda _d: None)
    warp.feed_output("[root@localhost ~]# ")
    assert warp.feed_input(b"WARP_SESSION_ID=99\n") == b""
    assert (
        warp.feed_input(b"function define_bashpreexec_functions() {\n") == b""
    )
    assert warp.feed_input(b" bash_preexec_imported=\"defined\"\n") == b""
    assert warp.feed_input(b"}\n") == b""
    assert warp.feed_input(b" HISTCONTROL=ignorespace\n") == b""
    assert warp.feed_input(b"ls\r") == b"ls\r"

def test_preexec_uses_nested_id_without_waiting() -> None:
    emitted: list[bytes] = []
    warp = WarpSession(WarpNested(), emit=emitted.append)
    warp.feed_output("$ ")
    warp.feed_input(b"WARP_SESSION_ID=99\n")
    warp.feed_input(b"ls\r")
    out = warp.feed_output("a\n$ ")
    hooks = _hooks(out)
    assert hooks[0]["hook"] == "Preexec"
    assert hooks[0]["value"] == {"command": "ls", "session_id": 99}


def test_user_keys_pass_if_warp_never_bootstraps() -> None:
    warp = WarpSession(WarpNested(), emit=lambda _d: None)
    warp.feed_output("$ ")
    assert warp.feed_input(b"ls\r") == b"ls\r"
    assert warp.took_shell is True


def test_koko_does_not_quit_after_announce() -> None:
    child = Child()
    login = KokoLogin(hostname="bi-server01", account_name="linux-root")
    driver = KokoDriver(child, lambda: "111111", ("[MFA auth]:",), login)
    warp = WarpSession(WarpNested(), emit=lambda _d: None)

    def pipeline(data):
        out = driver.filter_output(data)
        out = warp.feed_output(out)
        if warp.took_shell:
            driver.suppress()
        return out

    pipeline("[MFA auth]: ")
    pipeline("Opt> ")
    pipeline("$ ")
    pipeline("logout\n[Host]> ")
    assert child.sent == ["111111", "bi-server01"]


def test_strip_erase_display_removes_clear() -> None:
    assert strip_erase_display("a\x1b[2Jb") == "ab"
    assert strip_erase_display(b"a\x1b[H\x1b[2Jb") == b"a\x1b[Hb"
    assert strip_erase_display("keep \x1b[31mred") == "keep \x1b[31mred"
