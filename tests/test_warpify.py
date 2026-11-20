from __future__ import annotations

from jssh.connectors.warpify import WarpOff, WarpSession, strip_erase_display, warp_plan


def test_sftp_is_off() -> None:
    assert warp_plan(["sftp", "h"], env={"TERM_PROGRAM": "WarpTerminal"}, is_tty=True) == WarpOff(
        "sftp"
    )


def test_warp_terminal_ssh_does_not_start_nested_shell() -> None:
    assert warp_plan(["ssh", "h"], env={"TERM_PROGRAM": "WarpTerminal"}, is_tty=True) == WarpOff(
        "koko"
    )


def test_session_passthrough() -> None:
    warp = WarpSession(WarpOff("koko"))
    assert warp.feed_output("[root@localhost ~]# ") == "[root@localhost ~]# "
    assert warp.feed_input(b"ls\r") == b"ls\r"
    assert warp.took_shell is False


def test_strip_erase_display_removes_clear() -> None:
    assert strip_erase_display("a\x1b[2Jb") == "ab"
    assert strip_erase_display(b"a\x1b[H\x1b[2Jb") == b"a\x1b[Hb"
    assert strip_erase_display("keep \x1b[31mred") == "keep \x1b[31mred"
