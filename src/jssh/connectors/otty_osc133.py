from __future__ import annotations

import base64
import os
import re
import threading
from collections.abc import Callable, Mapping
from typing import Protocol

from jssh.config import Config
from jssh.connectors.otty import in_otty

MARK_A = r"\[\e]133;A\a\]"
MARK_B = r"\[\e]133;B\a\]"
BLOCK_START = "# >>> JSSH OTTY SHELL INTEGRATION >>>"
BLOCK_END = "# <<< JSSH OTTY SHELL INTEGRATION <<<"
_DISABLED = frozenset({"0", "false", "off", "no"})
_ENABLED = frozenset({"1", "true", "on", "yes"})
_MARKED = re.compile(r"133;[AB]")
_FORCE = "JSSH_OTTY_SHELL_INTEGRATION"

_WRAP_FN = (
    "__jssh_otty_wrap_ps1(){ "
    "[[ $- == *i* && -n ${PS1-} ]]||return; "
    "case \"$PS1\" in *'133;A'*) return;; esac; "
    f"printf -v PS1 '%s%s%s' '{MARK_A}' \"$PS1\" '{MARK_B}'; "
    "}"
)
_WRAP_INSTALL = (
    f"{_WRAP_FN}; __jssh_otty_wrap_ps1; "
    'case "${PROMPT_COMMAND-}" in *__jssh_otty_wrap_ps1*) ;; '
    '*) PROMPT_COMMAND="${PROMPT_COMMAND:+$PROMPT_COMMAND$\'\\n\'}__jssh_otty_wrap_ps1";; '
    "esac; __JSSH_OTTY_OSC133_APPLIED=1"
)
_SESSION_GUARD = "[[ $- == *i* && -n ${PS1-} && -z ${__JSSH_OTTY_OSC133_APPLIED-} ]]"


class TimerHandle(Protocol):
    def cancel(self) -> None: ...


TimerFactory = Callable[[float, Callable[[], None]], TimerHandle]


class Osc133Injector:
    def __init__(
        self,
        send: Callable[[str], None],
        *,
        persist: bool = False,
        idle_s: float = 0.6,
        timer_factory: TimerFactory | None = None,
    ) -> None:
        self._send = send
        self._persist = persist
        self._idle_s = idle_s
        self._timer_factory = timer_factory or _thread_timer
        self._timer: TimerHandle | None = None
        self._done = False
        self._closed = False
        self._abandoned = False
        self._password = False
        self._seen = False

    def observe(self, *, gateway: bool, password: bool) -> None:
        if self._done or self._closed or self._abandoned:
            return
        if password:
            self._password = True
            self._cancel()
            return
        if gateway:
            self._password = False
            self._cancel()
            return
        self._password = False
        self._seen = True
        self._arm()

    def note_input(self, data: bytes | str) -> None:
        if self._done or self._closed or self._abandoned or not data:
            return
        if self._password or not self._seen:
            return
        self._abandoned = True
        self._cancel()

    def close(self) -> None:
        self._closed = True
        self._cancel()

    def _arm(self) -> None:
        self._cancel()
        self._timer = self._timer_factory(self._idle_s, self._fire)

    def _cancel(self) -> None:
        if self._timer is not None:
            try:
                self._timer.cancel()
            except Exception:
                pass
            self._timer = None

    def _fire(self) -> None:
        self._timer = None
        if self._done or self._closed or self._abandoned:
            return
        self._done = True
        try:
            self._send(session_command(persist=self._persist))
        except Exception:
            pass


def already_marked(ps1: str) -> bool:
    return bool(_MARKED.search(ps1))


def wrap_ps1(
    ps1: str | None,
    *,
    interactive: bool = True,
    applied: bool = False,
) -> str | None:
    if not interactive or applied or not ps1:
        return ps1
    if already_marked(ps1):
        return ps1
    return f"{MARK_A}{ps1}{MARK_B}"


def session_command(*, persist: bool = False) -> str:
    body = f"{_SESSION_GUARD}&&{{ {_WRAP_INSTALL}; }}"
    if persist:
        body = f"{body}; {persist_command()}"
    return " " + body


def wrap_snippet() -> str:
    return (
        f"{BLOCK_START}\n"
        "# Otty OSC 133 A/B prompt marks. Safe to remove this block.\n"
        "if [[ $- == *i* && -n ${PS1-} && -z ${__JSSH_OTTY_OSC133_APPLIED:-} ]]; then\n"
        "    __jssh_otty_wrap_ps1() {\n"
        "        [[ $- == *i* && -n ${PS1-} ]] || return\n"
        "        case \"$PS1\" in\n"
        "            *'133;A'*) return ;;\n"
        "        esac\n"
        f"        printf -v PS1 '%s%s%s' '{MARK_A}' \"$PS1\" '{MARK_B}'\n"
        "    }\n"
        "    __jssh_otty_wrap_ps1\n"
        "    case \"${PROMPT_COMMAND-}\" in\n"
        "        *__jssh_otty_wrap_ps1*) ;;\n"
        "        *) PROMPT_COMMAND=\"${PROMPT_COMMAND:+$PROMPT_COMMAND$'\\n'}__jssh_otty_wrap_ps1\" ;;\n"
        "    esac\n"
        "    __JSSH_OTTY_OSC133_APPLIED=1\n"
        "fi\n"
        f"{BLOCK_END}\n"
    )


def managed_block() -> str:
    return wrap_snippet()


def apply_managed_block(text: str, block: str | None = None) -> str:
    payload = (block or managed_block()).strip("\n")
    if not payload.startswith(BLOCK_START):
        payload = f"{BLOCK_START}\n{payload}\n{BLOCK_END}"
    if BLOCK_START in text and BLOCK_END in text:
        pre, rest = text.split(BLOCK_START, 1)
        _, post = rest.split(BLOCK_END, 1)
        return pre.rstrip("\n") + "\n\n" + payload + "\n" + post.lstrip("\n")
    body = text if text.endswith("\n") or not text else text + "\n"
    sep = "" if not body or body.endswith("\n\n") else "\n"
    return body + sep + payload + "\n"


def remove_managed_block(text: str) -> str:
    if BLOCK_START not in text or BLOCK_END not in text:
        return text
    pre, rest = text.split(BLOCK_START, 1)
    _, post = rest.split(BLOCK_END, 1)
    return pre.rstrip("\n") + ("\n" + post.lstrip("\n") if post.strip() else "\n" if pre else "")


def persist_command() -> str:
    payload = base64.b64encode(managed_block().encode("ascii")).decode("ascii")
    start = BLOCK_START
    end = BLOCK_END
    return (
        'f="$HOME/.bashrc"; umask 077; '
        '[ -f "$f" ] || : > "$f"; '
        f'if grep -F "{start}" "$f" >/dev/null 2>&1; then '
        'cp -p "$f" "$f.jssh-otty.bak" && '
        f'sed -i "/{start}/,/{end}/d" "$f"; '
        'else cp -p "$f" "$f.jssh-otty.bak" 2>/dev/null || true; fi; '
        f"printf '%s\\n' '{payload}' | base64 -d >> \"$f\""
    )


def shell_integration_enabled(
    cfg: Config | None,
    env: Mapping[str, str] | None = None,
) -> bool:
    environ = os.environ if env is None else env
    override = environ.get(_FORCE, "").strip().lower()
    if override in _DISABLED:
        return False
    if override in _ENABLED:
        return True
    mode = cfg.otty_shell_integration if cfg is not None else "auto"
    if mode == "off":
        return False
    if mode == "on":
        return True
    return in_otty(environ)


def _thread_timer(delay: float, fn: Callable[[], None]) -> threading.Timer:
    timer = threading.Timer(delay, fn)
    timer.daemon = True
    timer.start()
    return timer
