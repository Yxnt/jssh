from __future__ import annotations

import enum
import re
import shutil
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pexpect

from jssh.config import Config
from jssh.connectors.otty import OttyOn, OttySession, open_sftp_sidecar, otty_split_plan
from jssh.connectors.terminal_tab import can_open_tab
from jssh.connectors.warp import in_warp
from jssh.errors import JsshError
from jssh.secrets import load_totp_secret
from jssh.totp import totp_code

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\].*?(?:\x07|\x1b\\)")
_REWIND = re.compile(r"\x1b\[[0-9;]*[HfAF]|\x1b\[[0-3]?J")
_REWIND_B = re.compile(rb"\x1b\[[0-9;]*[HfAF]|\x1b\[[0-3]?J")
_ASSET_HEADERS = ("Hostname", "主机名")
_ACCOUNT_HEADERS = ("Username", "用户名")
_SFTP_HINT_TEXT = "[jssh] sftp copied"
_SFTP_PANE_HINT = "[jssh] sftp pane"
_sftp_hint_timer: threading.Timer | None = None


@dataclass(frozen=True)
class KokoLogin:
    hostname: str
    account_name: str
    account_username: str = ""


class Phase(enum.Enum):
    MFA = "mfa"
    MENU = "menu"
    AFTER_SEARCH = "after_search"
    SESSION = "session"
    DONE = "done"


def run_gateway(cfg: Config, argv: list[str], login: KokoLogin | None = None) -> int:
    argv = resolve_gateway_argv(argv)
    print("starting: " + " ".join(argv), flush=True)
    if login:
        print(f"SFTP:  {sftp_quick_command(login)}", flush=True)
        if isinstance(otty_split_plan(), OttyOn):
            print(
                "In this SSH session, Ctrl-F opens SFTP in a split pane. TOTP is never copied.",
                flush=True,
            )
        elif in_warp() or can_open_tab():
            print(
                "In this SSH session, Ctrl-F opens SFTP in a new tab. TOTP is never copied.",
                flush=True,
            )
        else:
            print(
                "In this SSH session, Ctrl-F copies that command. TOTP is never copied.",
                flush=True,
            )
    otty = OttySession()
    title = f"{login.hostname} / {login.account_name}" if login else None
    otty.start(title)
    try:
        if not cfg.totp_auto:
            return _run_plain(argv)
        secret = load_totp_secret(cfg)
        if secret is None:
            print(
                "warning: TOTP secret missing in this pane. "
                "New terminal windows do not inherit exports. Run: jssh secrets totp-file",
                flush=True,
            )
            return _run_plain(argv)
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print("warning: no TTY. MFA auto-fill disabled.", flush=True)
            return _run_plain(argv)

        def code() -> str:
            return totp_code(
                secret.secret,
                digits=cfg.totp_digits,
                period=cfg.totp_period,
                algorithm=cfg.totp_algorithm,
            )

        return run_with_mfa(argv, code, cfg.totp_prompts, login, otty)
    finally:
        otty.close()


def _run_plain(argv: list[str]) -> int:
    try:
        return subprocess.run(argv, check=False).returncode
    except KeyboardInterrupt:
        return 130


def pty_dimensions() -> tuple[int, int]:
    size = shutil.get_terminal_size(fallback=(80, 24))
    return size.lines, size.columns


def resolve_gateway_argv(argv: list[str]) -> list[str]:
    if not argv:
        return argv
    binary = argv[0]
    if Path(binary).is_absolute():
        return argv
    found = shutil.which(binary)
    if found is None:
        return argv
    return [found, *argv[1:]]


def run_with_mfa(
    argv: list[str],
    code: Callable[[], str],
    prompts: tuple[str, ...],
    login: KokoLogin | None = None,
    otty: OttySession | None = None,
) -> int:
    rows, cols = pty_dimensions()
    child = pexpect.spawn(
        argv[0],
        argv[1:],
        timeout=None,
        dimensions=(rows, cols),
    )
    driver = KokoDriver(child, code, prompts, login, otty)
    previous = signal.getsignal(signal.SIGWINCH)

    def on_winch(signum, frame) -> None:
        if not child.isalive():
            return
        new_rows, new_cols = pty_dimensions()
        try:
            child.setwinsize(new_rows, new_cols)
        except (OSError, ValueError):
            pass

    signal.signal(signal.SIGWINCH, on_winch)
    on_winch(signal.SIGWINCH, None)
    try:
        child.interact(
            output_filter=driver.filter_output,
            input_filter=_sftp_hotkey(login) if login else None,
        )
    except KeyboardInterrupt:
        if child.isalive():
            child.close(force=True)
        return 130
    except OSError as exc:
        raise JsshError("SSH session I/O failed") from exc
    finally:
        signal.signal(signal.SIGWINCH, previous)
    if child.isalive():
        child.wait()
    status = child.exitstatus
    if status is None:
        status = child.signalstatus
    return int(status) if status is not None else 0


def sftp_quick_command(login: KokoLogin) -> str:
    return f"jssh sftp {login.hostname} --account {login.account_name}"


def _sftp_hint_overlay(clear: bool, kind: str = "copied") -> bytes:
    text = _SFTP_PANE_HINT if kind == "pane" else _SFTP_HINT_TEXT
    _, cols = pty_dimensions()
    col = max(1, cols - len(text) + 1)
    payload = b" " * len(text) if clear else b"\033[2m" + text.encode("ascii") + b"\033[0m"
    return b"\0337\033[1;" + str(col).encode("ascii") + b"H" + payload + b"\0338"


def _flash_sftp_hint(kind: str) -> None:
    global _sftp_hint_timer
    sys.stdout.buffer.write(_sftp_hint_overlay(clear=False, kind=kind))
    sys.stdout.buffer.flush()
    if _sftp_hint_timer is not None:
        _sftp_hint_timer.cancel()

    def clear() -> None:
        try:
            sys.stdout.buffer.write(_sftp_hint_overlay(clear=True, kind=kind))
            sys.stdout.buffer.flush()
        except OSError:
            pass

    _sftp_hint_timer = threading.Timer(2.0, clear)
    _sftp_hint_timer.daemon = True
    _sftp_hint_timer.start()


def _sftp_hotkey(login: KokoLogin) -> Callable[[bytes], bytes]:
    def input_filter(data: bytes) -> bytes:
        if b"\x06" not in data:
            return data
        kind = open_sftp_sidecar(
            sftp_quick_command(login),
            title=f"sftp {login.hostname}",
        )
        _flash_sftp_hint(kind)
        return data.replace(b"\x06", b"")

    return input_filter


def pick_row_id(buf: str, name: str, username: str = "") -> str | None:
    plain = _ANSI.sub("", buf)
    rows = _table_rows(plain)
    exact = [rid for rid, cols in rows if cols and cols[0] == name]
    if len(exact) == 1:
        return exact[0]
    if username:
        user_hits = [rid for rid, cols in rows if username in cols]
        if len(user_hits) == 1:
            return user_hits[0]
    trunc = [rid for rid, cols in rows if cols and _matches_shown(name, cols[0])]
    if len(trunc) == 1:
        return trunc[0]
    any_name = [rid for rid, cols in rows if any(_matches_shown(name, col) for col in cols)]
    if len(any_name) == 1:
        return any_name[0]
    return None


def _table_rows(plain: str) -> list[tuple[str, list[str]]]:
    rows: list[tuple[str, list[str]]] = []
    for line in plain.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("+"):
            continue
        if "|" in line:
            cells = [cell.strip() for cell in line.split("|") if cell.strip()]
            if cells and cells[0].isdigit() and len(cells) >= 2:
                rows.append((cells[0], cells[1:]))
            continue
        parts = stripped.split()
        if len(parts) >= 2 and parts[0].isdigit():
            rows.append((parts[0], parts[1:]))
    return rows


def _matches_shown(full: str, shown: str) -> bool:
    if not full or not shown:
        return False
    if shown == full:
        return True
    if "..." not in shown:
        return False
    prefix, suffix = shown.split("...", 1)
    if prefix and not full.startswith(prefix):
        return False
    if suffix and not full.endswith(suffix):
        return False
    return len(full) >= len(prefix) + len(suffix)


def _plain(buf: str) -> str:
    return _ANSI.sub("", buf)


def _strip_rewind(data: str | bytes) -> str | bytes:
    if isinstance(data, (bytes, bytearray)):
        return _REWIND_B.sub(b"", bytes(data))
    return _REWIND.sub("", data)


def _has_header(plain: str, names: tuple[str, ...]) -> bool:
    return any(name in plain for name in names)


class KokoDriver:
    def __init__(
        self,
        child: pexpect.spawn,
        code: Callable[[], str],
        prompts: tuple[str, ...],
        login: KokoLogin | None,
        status: OttySession | None = None,
    ) -> None:
        self._child = child
        self._code = code
        self._prompts = prompts
        self._login = login
        self._status = status
        self._buf = ""
        self._phase = Phase.MFA
        self._binary = False
        self._entered_session = False

    def filter_output(self, data: str | bytes) -> str | bytes:
        gateway = self._phase in {Phase.MFA, Phase.MENU, Phase.AFTER_SEARCH}
        if gateway:
            data = _strip_rewind(data)
        self._binary = isinstance(data, (bytes, bytearray))
        text = data.decode("utf-8", "replace") if self._binary else data
        self._buf += text
        if len(self._buf) > 8192:
            self._buf = self._buf[-2048:]
        if self._phase is Phase.MFA:
            self._on_mfa()
        elif self._phase is Phase.MENU:
            self._on_menu()
        elif self._phase is Phase.AFTER_SEARCH:
            self._on_after_search()
        elif self._phase is Phase.SESSION:
            self._on_session()
        if self._status is None:
            return data
        at_gateway = self._phase in {Phase.MFA, Phase.MENU, Phase.AFTER_SEARCH}
        osc = self._status.observe(text, at_gateway=at_gateway)
        if not osc:
            return data
        if self._binary:
            return bytes(data) + osc
        return data + osc.decode("ascii")

    def _on_mfa(self) -> None:
        plain = _plain(self._buf)
        if not any(prompt in plain for prompt in self._prompts):
            return
        self._send(self._code())
        self._buf = ""
        self._phase = Phase.MENU if self._login else Phase.DONE

    def _on_menu(self) -> None:
        if self._login is None or "Opt>" not in _plain(self._buf):
            return
        self._send(self._login.hostname)
        self._buf = ""
        self._entered_session = True
        self._phase = Phase.AFTER_SEARCH

    def _on_after_search(self) -> None:
        if self._login is None:
            self._phase = Phase.DONE
            return
        plain = _plain(self._buf)
        if "ID>" in plain and _has_header(plain, _ACCOUNT_HEADERS):
            row = pick_row_id(plain, self._login.account_name, self._login.account_username)
            if row is None:
                print(
                    f"warning: could not match system user {self._login.account_name!r} in the KoKo table. Pick it yourself.",
                    flush=True,
                )
                self._phase = Phase.SESSION
                return
            self._send(row)
            self._buf = ""
            self._phase = Phase.SESSION
            return
        if "Opt>" in plain or "[Host]>" in plain:
            if _has_header(plain, _ASSET_HEADERS):
                row = pick_row_id(plain, self._login.hostname)
                if row is not None:
                    self._send(row)
                    self._buf = ""
                    return
            if self._entered_session:
                self._quit_koko()
                return
            print(
                f"warning: hostname {self._login.hostname!r} was not unique. Pick the ID yourself.",
                flush=True,
            )
            self._phase = Phase.DONE

    def _on_session(self) -> None:
        plain = _plain(self._buf)
        if "Opt>" in plain or "[Host]>" in plain:
            self._quit_koko()

    def _quit_koko(self) -> None:
        self._send("q")
        self._buf = ""
        self._phase = Phase.DONE


    def _send(self, line: str) -> None:
        try:
            if self._binary:
                self._child.send(line.encode("utf-8") + b"\r")
            else:
                self._child.sendline(line)
        except Exception as exc:
            raise JsshError("Failed to send input to KoKo") from exc


class _MfaInjector(KokoDriver):
    def __init__(self, child: pexpect.spawn, code: Callable[[], str], prompts: tuple[str, ...]) -> None:
        super().__init__(child, code, prompts, None)
