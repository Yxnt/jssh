from __future__ import annotations

import json
import re

from jssh.connectors.session import KokoDriver, KokoLogin
from jssh.connectors.warpify import (
    NestedShell,
    WarpNested,
    WarpOff,
    WarpSession,
    infer_nested_shell,
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
    for match in re.finditer(rb"\x1bP\$d([0-9a-fA-F]+)\x1b\\", raw):
        found.append(json.loads(bytes.fromhex(match.group(1).decode("ascii"))))
    return found


def test_warp_nested_when_warp_terminal() -> None:
    assert warp_plan(["ssh", "h"], env={"TERM_PROGRAM": "WarpTerminal"}, is_tty=True) == WarpNested()


def test_sftp_is_off() -> None:
    assert warp_plan(["sftp", "h"], env={"TERM_PROGRAM": "WarpTerminal"}, is_tty=True) == WarpOff(
        "sftp"
    )


def test_prompt_emits_initshell_not_sourced_rc(monkeypatch) -> None:
    monkeypatch.setenv("WARP_SESSION_ID", "111")
    warp = WarpSession(WarpNested(), env={"WARP_SESSION_ID": "111"})
    out = warp.feed_output("[root@localhost ~]# ")
    assert b"SourcedRcFileForWarp" not in _as_bytes(out)
    hooks = _hooks(out)
    assert hooks[0]["hook"] == "InitShell"
    assert hooks[0]["value"]["is_subshell"] is True
    assert hooks[0]["value"]["session_id"] != 111
    assert warp.took_shell


def test_preexec_uses_nested_id() -> None:
    warp = WarpSession(WarpNested(), session_id=7)
    warp.feed_output("[root@localhost ~]# ")
    warp.feed_input(b"ls\r")
    first = warp.feed_output("file.txt\n")
    assert _hooks(first)[0] == {"hook": "Preexec", "value": {"command": "ls", "session_id": 7}}
    names = [h["hook"] for h in _hooks(warp.feed_output("[root@localhost ~]# "))]
    assert names == ["CommandFinished", "Precmd"]


def test_input_is_never_swallowed() -> None:
    warp = WarpSession(WarpNested(), session_id=1)
    warp.feed_output("$ ")
    assert warp.feed_input(b"WARP_SESSION_ID=99\n") == b"WARP_SESSION_ID=99\n"
    assert warp.feed_input(b"ls\r") == b"ls\r"


def test_koko_does_not_quit_after_shell_prompt() -> None:
    child = Child()
    login = KokoLogin(hostname="bi-server01", account_name="linux-root")
    driver = KokoDriver(child, lambda: "111111", ("[MFA auth]:",), login)
    warp = WarpSession(WarpNested(), session_id=1)

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


def test_infer_root_prompt() -> None:
    assert infer_nested_shell("[root@localhost ~]# ") == NestedShell("bash", "Linux")
    assert infer_nested_shell("Opt>") is None
