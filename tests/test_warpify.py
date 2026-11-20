from __future__ import annotations

from jssh.connectors.warpify import WarpOff, WarpSession, warp_plan


def test_sftp_is_off() -> None:
    assert warp_plan(["sftp", "h"], env={"TERM_PROGRAM": "WarpTerminal"}, is_tty=True) == WarpOff(
        "sftp"
    )


def test_warp_ssh_does_not_nest() -> None:
    assert warp_plan(["ssh", "h"], env={"TERM_PROGRAM": "WarpTerminal"}, is_tty=True) == WarpOff(
        "koko"
    )


def test_session_passthrough() -> None:
    warp = WarpSession(WarpOff("koko"))
    assert warp.feed_output("[root@localhost ~]# ") == "[root@localhost ~]# "
    assert warp.feed_input(b"ls\r") == b"ls\r"
    assert warp.took_shell is False
