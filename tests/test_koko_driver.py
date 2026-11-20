from __future__ import annotations

from types import SimpleNamespace

from jssh.connectors.session import KokoDriver, KokoLogin, pick_row_id, pty_dimensions, sftp_quick_command
from jssh.connectors.otty import OttySession

class Child:
    def __init__(self) -> None:
        self.sent: list[object] = []

    def sendline(self, value: str) -> None:
        self.sent.append(value)

    def send(self, value: bytes) -> None:
        self.sent.append(value)


def test_sftp_quick_command_is_host_only() -> None:
    login = KokoLogin(hostname="bi-server01", account_name="linux-root", account_username="root")
    cmd = sftp_quick_command(login)
    assert cmd == "jssh sftp bi-server01 --account linux-root"
    assert "totp" not in cmd.lower()
    assert "secret" not in cmd.lower()
    assert "GEZDGNBV" not in cmd


def test_pty_dimensions_are_rows_then_cols(monkeypatch) -> None:
    monkeypatch.setattr(
        "jssh.connectors.session.shutil.get_terminal_size",
        lambda fallback=(80, 24): SimpleNamespace(columns=160, lines=48),
    )
    assert pty_dimensions() == (48, 160)


def test_sftp_hint_overlay_is_not_on_prompt_row(monkeypatch) -> None:
    monkeypatch.setattr(
        "jssh.connectors.session.shutil.get_terminal_size",
        lambda fallback=(80, 24): SimpleNamespace(columns=80, lines=24),
    )
    from jssh.connectors.session import _sftp_hint_overlay

    shown = _sftp_hint_overlay(clear=False)
    assert b"[999;" not in shown
    assert b"[2K" not in shown
    assert shown.startswith(b"\0337\033[1;")
    assert shown.endswith(b"\0338")
    assert b"sftp copied" in shown




def test_pick_row_id_matches_koko_truncated_name() -> None:
    table = (
        "ID | 名称 | 用户名\n"
        "+----+----------+------------+\n"
        "  1  | li...ot  | root\n"
        "  2  | li...01  | local_hpc\n"
        "ID> "
    )
    assert pick_row_id(table, "linux-root", "root") == "1"
    assert pick_row_id(table, "linux-root") == "1"


def test_driver_sends_totp_then_hostname_then_account_id() -> None:
    child = Child()
    login = KokoLogin(hostname="bi-server01", account_name="linux-root")
    driver = KokoDriver(child, lambda: "287082", ("[MFA auth]:",), login)
    driver.filter_output("Please enter 6 digits.\n")
    assert child.sent == []
    driver.filter_output("[MFA auth]: ")
    assert child.sent == ["287082"]
    driver.filter_output("\n欢迎使用JumpServer\nOpt> ")
    assert child.sent == ["287082", "bi-server01"]
    driver.filter_output("ID Name Username\n1 linux-dn01 dn01\n2 linux-root root\nID> ")
    assert child.sent == ["287082", "bi-server01", "2"]
    driver.filter_output("root@bi-server01:~# logout\n[Host]> ")
    assert child.sent == ["287082", "bi-server01", "2", "q"]



def test_gateway_strips_cursor_home() -> None:
    child = Child()
    login = KokoLogin(hostname="bi-server01", account_name="linux-root")
    driver = KokoDriver(child, lambda: "111111", ("[MFA auth]:",), login)
    out = driver.filter_output("\x1b[H\x1b[2J[MFA auth]: ")
    assert "\x1b[H" not in out
    assert "\x1b[2J" not in out
    assert "[MFA auth]:" in out
    assert child.sent == ["111111"]


def test_remote_session_keeps_cursor_home() -> None:
    child = Child()
    login = KokoLogin(hostname="bi-server01", account_name="linux-root")
    driver = KokoDriver(child, lambda: "111111", ("[MFA auth]:",), login)
    driver.filter_output("[MFA auth]: ")
    driver.filter_output("Opt> ")
    driver.filter_output("ID Username\n1 linux-root root\nID> ")
    out = driver.filter_output("\x1b[H\x1b[2Jvim")
    assert "\x1b[H" in out
    assert "\x1b[2J" in out
    assert "vim" in out

def test_driver_emits_blocked_status_on_password() -> None:
    child = Child()
    status = OttySession(env={"TERM_PROGRAM": "otty"}, emit=lambda _data: None)
    status.start("bi-server01 / linux-root")
    driver = KokoDriver(child, lambda: "111111", ("[MFA auth]:",), KokoLogin("bi-server01", "linux-root"), status)
    driver.filter_output("[MFA auth]: ")
    out = driver.filter_output("Password: ")
    text = out if isinstance(out, str) else out.decode("ascii")
    assert "state=blocked" in text
    assert "kind=auth" in text


def test_unique_asset_skips_account_table() -> None:
    child = Child()
    login = KokoLogin(hostname="bi-server01", account_name="linux-root")
    driver = KokoDriver(child, lambda: "111111", ("[MFA auth]:",), login)
    driver.filter_output("[MFA auth]: ")
    driver.filter_output("Opt> ")
    assert child.sent == ["111111", "bi-server01"]
    driver.filter_output("$ ")
    assert child.sent == ["111111", "bi-server01"]


def test_return_to_menu_sends_quit() -> None:
    child = Child()
    login = KokoLogin(hostname="bi-server01", account_name="linux-root")
    driver = KokoDriver(child, lambda: "111111", ("[MFA auth]:",), login)
    driver.filter_output("[MFA auth]: ")
    driver.filter_output("Opt> ")
    driver.filter_output("Last login from 1 bi-server01\nroot@bi-server01:~# logout\n[Host]> ")
    assert child.sent == ["111111", "bi-server01", "q"]
    driver.filter_output("[Host]> ")
    assert child.sent == ["111111", "bi-server01", "q"]
