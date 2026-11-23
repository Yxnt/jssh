from __future__ import annotations

import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from jssh.connectors.terminal_tab import open_terminal_tab
from jssh.connectors.warp import open_warp_sftp

_DISABLED = frozenset({"0", "false", "off", "no"})
_BUNDLED_OTTY = Path("/Applications/Otty.app/Contents/MacOS/otty-cli")
_PASSWORD_PROMPT = re.compile(r"(?im)^\s*(password|passwd|口令|密码)\s*:")
_SHELL_PROMPT = re.compile(r"(?m)[^\n]*[\$#]\s*$")
_VALUE_CHARS = re.compile(r"^[A-Za-z0-9_.,+/=-]*$")
_APP_CHARS = re.compile(r"[^A-Za-z0-9_.,+/=-]+")
_ID_SEGMENT = re.compile(r"^[A-Za-z0-9_.+-]{1,32}$")
_STATES = frozenset({"idle", "working", "done", "blocked", "error", "clear"})
_KINDS = frozenset({"permission", "question", "auth"})
_DIRECTIONS = frozenset({"right", "left", "up", "down"})
_BADGE_KINDS = frozenset({"running", "completed", "finished", "unread", "error", "awaiting-input"})
_TITLE_MAX = 192
_MSG_MAX = 2048
_APP_MAX = 32
_SEQ_MAX = 4096
_ID_KEYS = ("id", "pane_id", "paneId", "pane", "tab_id", "tabId", "tab")

OttyReason = Literal["not-otty", "no-cli", "disabled"]
Sidecar = Literal["pane", "copied"]
Status = Literal["working", "blocked", "idle", "clear", "done", "error"]
SftpLayout = Literal["split", "tab"]


@dataclass(frozen=True)
class OttyOff:
    reason: OttyReason


@dataclass(frozen=True)
class OttyOn:
    cli: str
    pane_id: str | None


OttyPlan = OttyOff | OttyOn


@dataclass(frozen=True)
class OttyLayout:
    mode: SftpLayout = "split"
    direction: str = "right"
    size: int = 40
    no_focus: bool = True
    local_cwd: str | None = None


@dataclass(frozen=True)
class SidecarResult:
    kind: Sidecar
    pane_id: str | None = None
    tab_id: str | None = None


def in_otty(env: Mapping[str, str] | None = None) -> bool:
    environ = os.environ if env is None else env
    if environ.get("JSSH_OTTY", "").strip().lower() in _DISABLED:
        return False
    return environ.get("TERM_PROGRAM") == "otty"


def is_password_prompt(text: str) -> bool:
    return bool(_PASSWORD_PROMPT.search(text))


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


def split_sftp_argv(
    plan: OttyOn,
    command: str,
    title: str,
    *,
    layout: OttyLayout | None = None,
) -> list[str]:
    opts = layout or OttyLayout()
    argv = [
        plan.cli,
        "pane",
        "split",
        "--direction",
        opts.direction if opts.direction in _DIRECTIONS else "right",
        "--command",
        command,
        "--title",
        title,
        "--json",
        "--size",
        str(_clamp_size(opts.size)),
    ]
    if opts.no_focus:
        argv.append("--no-focus")
    if opts.local_cwd:
        argv.extend(["--cwd", opts.local_cwd])
    if plan.pane_id:
        argv.extend(["--pane", plan.pane_id])
    return argv


def tab_sftp_argv(
    plan: OttyOn,
    command: str,
    title: str,
    *,
    layout: OttyLayout | None = None,
) -> list[str]:
    opts = layout or OttyLayout()
    argv = [
        plan.cli,
        "tab",
        "new",
        "--command",
        command,
        "--title",
        title,
        "--json",
    ]
    if opts.no_focus:
        argv.append("--no-focus")
    if opts.local_cwd:
        argv.extend(["--cwd", opts.local_cwd])
    return argv


def tab_badge_argv(cli: str, tab_id: str, *, kind: str | None = None, clear: bool = False) -> list[str]:
    argv = [cli, "tab", "badge", "--tab", tab_id]
    if clear:
        argv.append("--clear")
    elif kind:
        if kind not in _BADGE_KINDS:
            raise ValueError(f"unsupported Otty tab badge kind: {kind}")
        argv.extend(["--kind", kind])
    return argv


def pane_send_text_argv(cli: str, pane_id: str, text: str) -> list[str]:
    return [cli, "pane", "send-text", "--pane", pane_id, text]


def pane_send_keys_argv(cli: str, pane_id: str, *parts: str) -> list[str]:
    return [cli, "pane", "send-keys", "--pane", pane_id, *parts]


def pane_capture_argv(cli: str, pane_id: str) -> list[str]:
    return [cli, "pane", "capture", "--pane", pane_id]


def pane_close_argv(cli: str, pane_id: str) -> list[str]:
    return [cli, "pane", "close", "--pane", pane_id]


def parse_otty_id(output: bytes | str | None, keys: tuple[str, ...] = _ID_KEYS) -> str | None:
    if output is None:
        return None
    text = output.decode("utf-8", "replace") if isinstance(output, (bytes, bytearray)) else output
    text = text.strip()
    if not text:
        return None
    for candidate in _json_candidates(text):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        found = _id_from_json(candidate, keys)
        if found:
            return found
    return None


def pane_send_text(
    cli: str,
    pane_id: str,
    text: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> bool:
    return _cli_ok(pane_send_text_argv(cli, pane_id, text), run=run)


def pane_send_keys(
    cli: str,
    pane_id: str,
    *parts: str,
    run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> bool:
    return _cli_ok(pane_send_keys_argv(cli, pane_id, *parts), run=run)


def pane_capture(
    cli: str,
    pane_id: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> str | None:
    result = _cli_run(pane_capture_argv(cli, pane_id), run=run)
    if result is None or result.returncode != 0:
        return None
    stdout = result.stdout or b""
    if isinstance(stdout, str):
        return stdout
    return stdout.decode("utf-8", "replace")


def pane_close(
    cli: str,
    pane_id: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> bool:
    return _cli_ok(pane_close_argv(cli, pane_id), run=run)


def tab_badge(
    cli: str,
    tab_id: str,
    *,
    kind: str | None = None,
    clear: bool = False,
    run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> bool:
    try:
        argv = tab_badge_argv(cli, tab_id, kind=kind, clear=clear)
    except ValueError:
        return False
    return _cli_ok(argv, run=run)


def _clean_title(text: str) -> str:
    return "".join(ch for ch in text if ch.isprintable())[:120]


def osc_title(text: str) -> bytes:
    return b"\033]2;" + _clean_title(text).encode("utf-8") + b"\007"


def osc_icon(text: str) -> bytes:
    return b"\033]1;" + _clean_title(text).encode("utf-8") + b"\007"


def osc_both(text: str) -> bytes:
    return b"\033]0;" + _clean_title(text).encode("utf-8") + b"\007"


def osc_cwd(hostname: str, cwd: str) -> bytes:
    host = quote(hostname or "localhost", safe=".-_")
    path = cwd if cwd.startswith("/") else f"/{cwd}"
    path = quote(path, safe="/")
    return f"\033]7;file://{host}{path}\007".encode("ascii")


def osc_clear_title() -> bytes:
    # Empty OSC 0 drops the program title so Otty falls back to its default
    # (${title|folder}), which follows the working directory again.
    return osc_both("")


def local_restore_sequences(
    *,
    cwd: Path | None = None,
    hostname: str | None = None,
    title: str | None = None,
) -> list[bytes]:
    path = (cwd if cwd is not None else Path.cwd()).resolve()
    host = (hostname or socket.gethostname()).split(".")[0] or "localhost"
    label = osc_clear_title() if not title else osc_both(title)
    return [osc_cwd(host, path.as_posix()), label]


@dataclass(frozen=True)
class TabSnapshot:
    tab_id: str | None = None
    title: str = ""
    cwd: str | None = None


def parse_tab_snapshot(text: str | bytes | None) -> TabSnapshot | None:
    if text is None:
        return None
    raw = text.decode("utf-8", "replace") if isinstance(text, (bytes, bytearray)) else text
    for candidate in _json_candidates(raw):
        data = candidate.get("data", candidate) if isinstance(candidate, dict) else None
        if not isinstance(data, dict):
            continue
        ident = data.get("tab_id") or data.get("tabId") or data.get("id")
        if "title" not in data and "cwd" not in data and not ident:
            continue
        title = data.get("title")
        cwd = data.get("cwd")
        return TabSnapshot(
            tab_id=str(ident) if ident else None,
            title="" if title is None else str(title),
            cwd=str(cwd) if cwd else None,
        )
    return None


def read_tab_snapshot(
    env: Mapping[str, str] | None = None,
    *,
    run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
    cli: str | None | Literal[False] = False,
) -> TabSnapshot | None:
    plan = otty_split_plan(env, cli=cli)
    if not isinstance(plan, OttyOn) or not plan.pane_id:
        return None
    tab_id = None
    if plan.pane_id:
        pane = _cli_run(
            [plan.cli, "pane", "show", "--json", "--pane", plan.pane_id],
            run=run,
        )
        if pane is not None and pane.returncode == 0:
            snapped = parse_tab_snapshot(pane.stdout)
            if snapped is not None and snapped.tab_id:
                tab_id = snapped.tab_id
    argv = [plan.cli, "tab", "show", "--json"]
    if tab_id:
        argv.extend(["--tab", tab_id])
    shown = _cli_run(argv, run=run)
    if shown is None or shown.returncode != 0:
        return None
    return parse_tab_snapshot(shown.stdout)


def osc_status(
    state: Status,
    *,
    kind: str | None = None,
    msg: str | None = None,
    title: str | None = None,
    progress: int | None = None,
    app: str = "jssh",
    ident: str | None = None,
) -> bytes:
    if state not in _STATES:
        raise ValueError(f"unsupported OSC 7501 state: {state}")
    parts = [f"state={state}"]
    app_value = _sanitize_app(app)
    if app_value:
        parts.append(f"app={app_value}")
    if ident:
        if not _valid_id(ident):
            raise ValueError(f"invalid OSC 7501 id: {ident}")
        parts.append(f"id={ident}")
    if state == "blocked" and kind:
        if kind not in _KINDS:
            raise ValueError(f"unsupported OSC 7501 kind: {kind}")
        parts.append(f"kind={kind}")
    if state in {"working", "blocked"} and progress is not None:
        if not isinstance(progress, int) or isinstance(progress, bool) or progress < 0 or progress > 100:
            raise ValueError("OSC 7501 progress must be an integer 0-100")
        parts.append(f"progress={progress}")
    if title:
        parts.append(f"title={_b64_field(title, _TITLE_MAX)}")
    if msg:
        parts.append(f"msg={_b64_field(msg, _MSG_MAX)}")
    body = ":".join(parts)
    if not _VALUE_CHARS.match(body.replace(":", "").replace(";", "")):
        raise ValueError("OSC 7501 values must use the protocol charset")
    seq = b"\033]7501;" + body.encode("ascii") + b"\033\\"
    if len(seq) > _SEQ_MAX:
        raise ValueError("OSC 7501 sequence exceeds 4096 bytes")
    return seq


def report_status(
    state: Status,
    *,
    kind: str | None = None,
    msg: str | None = None,
    title: str | None = None,
    progress: int | None = None,
    app: str = "jssh",
    ident: str | None = None,
    env: Mapping[str, str] | None = None,
    emit: Callable[[bytes], None] | None = None,
) -> bytes:
    if not in_otty(env):
        return b""
    data = osc_status(
        state,
        kind=kind,
        msg=msg,
        title=title,
        progress=progress,
        app=app,
        ident=ident,
    )
    (emit or write_tty)(data)
    return data


def write_tty(data: bytes) -> None:
    try:
        with open("/dev/tty", "wb", buffering=0) as tty:
            tty.write(data)
        return
    except OSError:
        pass
    try:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    except OSError:
        pass


def open_sftp_sidecar(
    command: str,
    *,
    title: str,
    env: Mapping[str, str] | None = None,
    copy: Callable[[str], None] | None = None,
    run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
    warp_tab: Path | None = None,
    warp_open: Callable[[str], subprocess.CompletedProcess[bytes]] | None = None,
    layout: OttyLayout | None = None,
) -> SidecarResult:
    plan = otty_split_plan(env)
    opts = layout or OttyLayout()
    if isinstance(plan, OttyOn):
        opened = _open_otty_sidecar(plan, command, title, opts, run=run)
        if opened is not None:
            return opened
    if open_warp_sftp(command, title, env=env, tab_path=warp_tab, opener=warp_open):
        return SidecarResult("pane")
    if open_terminal_tab(command, env=env, run=run):
        return SidecarResult("pane")
    _copy_command(command, copy)
    return SidecarResult("copied")


def _open_otty_sidecar(
    plan: OttyOn,
    command: str,
    title: str,
    opts: OttyLayout,
    *,
    run: Callable[..., subprocess.CompletedProcess[bytes]] | None,
) -> SidecarResult | None:
    argv = tab_sftp_argv(plan, command, title, layout=opts) if opts.mode == "tab" else split_sftp_argv(
        plan, command, title, layout=opts
    )
    result = _cli_run(argv, run=run)
    if result is None or result.returncode != 0:
        return None
    ident = parse_otty_id(getattr(result, "stdout", None))
    if opts.mode == "tab":
        if ident:
            tab_badge(plan.cli, ident, kind="running", run=run)
        return SidecarResult("pane", tab_id=ident)
    return SidecarResult("pane", pane_id=ident)


def _copy_command(command: str, copy: Callable[[str], None] | None) -> None:
    if copy is not None:
        copy(command)
        return
    subprocess.run(["pbcopy"], input=command.encode("utf-8"), check=False)


def _cli_ok(
    argv: list[str],
    *,
    run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> bool:
    result = _cli_run(argv, run=run)
    return result is not None and result.returncode == 0


def _cli_run(
    argv: list[str],
    *,
    run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> subprocess.CompletedProcess[bytes] | None:
    runner = subprocess.run if run is None else run
    try:
        return runner(argv, check=False, capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired, TypeError):
        return None


def _json_candidates(text: str) -> list[object]:
    found: list[object] = []
    try:
        found.append(json.loads(text))
    except json.JSONDecodeError:
        pass
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            found.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            found.append(json.loads(text[start : end + 1]))
        except json.JSONDecodeError:
            pass
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        try:
            found.append(json.loads(text[start : end + 1]))
        except json.JSONDecodeError:
            pass
    return found


def _id_from_json(data: object, keys: tuple[str, ...]) -> str | None:
    if isinstance(data, list):
        for item in data:
            found = _id_from_json(item, keys)
            if found:
                return found
        return None
    if not isinstance(data, dict):
        return None
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (dict, list)):
            found = _id_from_json(value, keys)
            if found:
                return found
    for nest in ("data", "result", "pane", "tab"):
        if nest in data and not (nest in keys and isinstance(data[nest], str)):
            found = _id_from_json(data[nest], keys)
            if found:
                return found
    return None


def _clamp_size(value: int) -> int:
    return max(10, min(90, int(value)))


def _sanitize_app(app: str) -> str:
    cleaned = _APP_CHARS.sub("", app)[:_APP_MAX]
    return cleaned or "jssh"


def _valid_id(ident: str) -> bool:
    if len(ident) > 128:
        return False
    parts = ident.split("/")
    if not parts or len(parts) > 8:
        return False
    return all(_ID_SEGMENT.match(part) for part in parts)


def _b64_field(text: str, limit: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) > limit:
        raw = raw[:limit]
        while raw and (raw[-1] & 0xC0) == 0x80:
            raw = raw[:-1]
        if raw and (raw[-1] & 0xC0) == 0xC0:
            raw = raw[:-1]
    return base64.b64encode(raw).decode("ascii")


class OttySession:
    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        *,
        emit: Callable[[bytes], None] | None = None,
        run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
        snapshot: TabSnapshot | None = None,
    ) -> None:
        self._on = in_otty(env)
        self._emit = emit or write_tty
        self._env = env
        self._run = run
        self._snapshot = snapshot
        self._state: Status | None = None

    def start(self, title: str | None = None, *, asset_name: str | None = None) -> None:
        if not self._on:
            return
        if self._snapshot is None:
            self._snapshot = read_tab_snapshot(self._env, run=self._run)
        if title:
            self._emit(osc_title(title))
        label = asset_name or title
        self._emit(self._set("working", title=label, msg=title or "Connecting"))

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
        saved = self._snapshot
        for item in local_restore_sequences(title=saved.title if saved else None):
            self._emit(item)
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

    def _set(
        self,
        state: Status,
        *,
        kind: str | None = None,
        msg: str | None = None,
        title: str | None = None,
        progress: int | None = None,
    ) -> bytes:
        self._state = state
        return osc_status(state, kind=kind, msg=msg, title=title, progress=progress)
