from __future__ import annotations

import base64
import os
import re
import threading
from collections.abc import Callable, Mapping
from typing import Protocol
from urllib.parse import unquote, urlparse

from jssh.config import Config
from jssh.connectors.otty import in_otty

# PS1 B mark matches Otty ssh-marks.sh: append only, do not replace PS1.
MARK_A = r"\033]133;A;aid=$$\007"
MARK_B = r"\[\033]133;B\007\]"
BLOCK_START = "# >>> JSSH OTTY SHELL INTEGRATION >>>"
BLOCK_END = "# <<< JSSH OTTY SHELL INTEGRATION <<<"
_DISABLED = frozenset({"0", "false", "off", "no"})
_ENABLED = frozenset({"1", "true", "on", "yes"})
_MARKED = re.compile(r"133;[AB]")
_FORCE = "JSSH_OTTY_SHELL_INTEGRATION"
_OSC7 = re.compile(r"\x1b\]7;(file://.*?)(?:\x07|\x1b\\)")
_SESSION_GUARD = "[[ $- == *i* && -n ${PS1-} && -z ${__JSSH_OTTY_OSC133_APPLIED-} ]]"
# Short first command: disable echo, then erase that command line from the display.
ECHO_OFF_PRELUDE = r" stty -echo 2>/dev/null; printf '\033[A\r\033[2K'"
ECHO_ON_SUFFIX = r"printf '\r\033[2K'; stty echo 2>/dev/null"
ECHO_RESTORE = "stty echo 2>/dev/null"


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
        hide_delay_s: float = 0.25,
        timer_factory: TimerFactory | None = None,
        hostname: str = "",
    ) -> None:
        self._send = send
        self._persist = persist
        self._idle_s = idle_s
        self._hide_delay_s = hide_delay_s
        self._timer_factory = timer_factory or _thread_timer
        self._hostname = hostname
        self._timer: TimerHandle | None = None
        self._done = False
        self._closed = False
        self._abandoned = False
        self._password = False
        self._seen = False
        self._hiding = False

    def observe(self, *, gateway: bool, password: bool) -> None:
        if self._done or self._closed or self._abandoned or self._hiding:
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
        self._restore_echo()

    def close(self) -> None:
        self._closed = True
        self._cancel()
        self._restore_echo()

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

    def _restore_echo(self) -> None:
        if not self._hiding or self._done:
            return
        self._hiding = False
        try:
            self._send(" " + ECHO_RESTORE)
        except Exception:
            pass

    def _fire(self) -> None:
        self._timer = None
        if self._done or self._closed or self._abandoned:
            return
        if not self._hiding:
            try:
                self._send(ECHO_OFF_PRELUDE)
            except Exception:
                return
            self._hiding = True
            self._timer = self._timer_factory(self._hide_delay_s, self._fire)
            return
        self._done = True
        self._hiding = False
        try:
            self._send(session_command(persist=self._persist, hostname=self._hostname))
        except Exception:
            try:
                self._send(" " + ECHO_RESTORE)
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
    if "133;B" in ps1:
        return ps1
    return f"{ps1}{MARK_B}"


def session_command(*, persist: bool = False, hostname: str = "") -> str:
    bash_b64 = _b64(bash_integration_script(hostname))
    zsh_b64 = _b64(zsh_integration_script(hostname))
    body = (
        f"{_SESSION_GUARD}&&{{ "
        f'if [ -n "${{BASH_VERSION:-}}" ]; then '
        f'eval "$({_decode_cmd(bash_b64)})"; '
        f'elif [ -n "${{ZSH_VERSION:-}}" ]; then '
        f'eval "$({_decode_cmd(zsh_b64)})"; '
        "fi; }"
    )
    if persist:
        body = f"{body}; {persist_command(hostname)}"
    return f" {body}; {ECHO_ON_SUFFIX}"


def bash_integration_script(hostname: str = "") -> str:
    host = _sh_single_quote(hostname)
    return f"""\
__jssh_otty_host={host}
__jssh_otty_urlenc() {{
    local LC_ALL=C
    local s=$1 i c out=
    for (( i=0; i<${{#s}}; i++ )); do
        c=${{s:i:1}}
        case $c in
            [A-Za-z0-9._~/-]) out+=$c ;;
            *) printf -v c '%%%02X' "'$c"
               out+=$c ;;
        esac
    done
    printf '%s' "$out"
}}
__jssh_otty_osc7() {{
    [ -n "$__jssh_otty_host" ] || return 0
    local h p
    h=$(__jssh_otty_urlenc "$__jssh_otty_host")
    p=$(__jssh_otty_urlenc "$PWD")
    printf '\\033]7;file://%s%s\\007' "$h" "$p"
}}
__jssh_otty_seen=
__jssh_otty_armed=
__jssh_otty_status() {{
    __jssh_otty_exit=$?
    __jssh_otty_armed=
}}
__jssh_otty_prompt() {{
    [ -z "$__jssh_otty_seen" ] || printf '\\033]133;D;%d;aid=%s\\007' "$__jssh_otty_exit" "$$"
    __jssh_otty_seen=1
    printf '\\033]133;A;aid=%s\\007' "$$"
    __jssh_otty_osc7
    case "$PS1" in *'133;B'*) ;; *) PS1="$PS1"'\\[\\033]133;B\\007\\]' ;; esac
    if [ -n "$__jssh_otty_ps0" ]; then
        case "$PS0" in *'133;C'*) ;; *) PS0="$PS0"'\\033]133;C\\007' ;; esac
    else
        __jssh_otty_armed=1
    fi
}}
__jssh_otty_ps0=
if (( BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 4) )); then
    __jssh_otty_ps0=1
else
    __jssh_otty_preexec() {{
        [ -n "$__jssh_otty_armed" ] && [ -z "$COMP_LINE" ] || return 0
        case "$BASH_COMMAND" in __jssh_otty_*) return 0 ;; esac
        __jssh_otty_armed=
        printf '\\033]133;C\\007'
    }}
    trap __jssh_otty_preexec DEBUG
fi
if [[ "$(declare -p PROMPT_COMMAND 2>/dev/null)" == "declare -a"* ]]; then
    PROMPT_COMMAND=(__jssh_otty_status "${{PROMPT_COMMAND[@]}}" __jssh_otty_prompt)
else
    PROMPT_COMMAND="__jssh_otty_status"$'\\n'"${{PROMPT_COMMAND:-:}}"$'\\n'"__jssh_otty_prompt"
fi
__JSSH_OTTY_OSC133_APPLIED=1
"""


def zsh_integration_script(hostname: str = "") -> str:
    host = _sh_single_quote(hostname)
    return f"""\
__jssh_otty_host={host}
__jssh_otty_urlenc() {{
    local LC_ALL=C s=$1 out= c
    while [ -n "$s" ]; do
        c=${{s%"${{s#?}}"}}
        s=${{s#?}}
        case $c in
            [A-Za-z0-9._~/-]) out=$out$c ;;
            *) printf -v c '%%%02X' "'$c"
               out=$out$c ;;
        esac
    done
    printf '%s' "$out"
}}
__jssh_otty_osc7() {{
    [ -n "$__jssh_otty_host" ] || return 0
    local h p
    h=$(__jssh_otty_urlenc "$__jssh_otty_host")
    p=$(__jssh_otty_urlenc "$PWD")
    builtin printf '\\033]7;file://%s%s\\007' "$h" "$p"
}}
if [[ -o interactive ]] && [ -z "${{__JSSH_OTTY_OSC133_APPLIED:-}}" ]; then
    __jssh_otty_ran=
    __jssh_otty_line_init_orig=
    __jssh_otty_line_init() {{
        builtin printf '\\033]133;B\\007'
        [[ -z "$__jssh_otty_line_init_orig" ]] || "$__jssh_otty_line_init_orig"
    }}
    __jssh_otty_precmd() {{
        local s=$?
        [[ -z "$__jssh_otty_ran" ]] || builtin printf '\\033]133;D;%d;aid=%s\\007' "$s" "$$"
        builtin printf '\\033]133;A;aid=%s\\007' "$$"
        __jssh_otty_osc7
        __jssh_otty_ran=
        precmd_functions=("${{(@)precmd_functions:#__jssh_otty_precmd}}" __jssh_otty_precmd)
        if [[ "${{widgets[zle-line-init]-}}" != user:__jssh_otty_line_init ]]; then
            __jssh_otty_line_init_orig=
            case "${{widgets[zle-line-init]-}}" in
                user:*) __jssh_otty_line_init_orig="${{widgets[zle-line-init]#user:}}" ;;
            esac
            builtin zle -N zle-line-init __jssh_otty_line_init
        fi
    }}
    __jssh_otty_preexec() {{
        __jssh_otty_ran=1
        builtin printf '\\033]133;C\\007'
    }}
    precmd_functions+=(__jssh_otty_precmd)
    preexec_functions+=(__jssh_otty_preexec)
    __JSSH_OTTY_OSC133_APPLIED=1
fi
"""


def wrap_snippet(hostname: str = "") -> str:
    return (
        f"{BLOCK_START}\n"
        "# Otty OSC 133 A/B/C/D and OSC 7 prompt marks. Safe to remove this block.\n"
        "# Prompt and command marks follow Otty ssh-marks.sh. This block does not\n"
        "# use Otty's ssh bootstrap or a temporary directory on the asset.\n"
        "if [[ -n ${BASH_VERSION-} && $- == *i* && -z ${__JSSH_OTTY_OSC133_APPLIED:-} ]]; then\n"
        + _indent(bash_integration_script(hostname).rstrip(), 4)
        + "\nfi\n"
        f"{BLOCK_END}\n"
    )


def managed_block(hostname: str = "") -> str:
    return wrap_snippet(hostname)


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


def persist_command(hostname: str = "") -> str:
    payload = _b64(managed_block(hostname))
    start = BLOCK_START
    end = BLOCK_END
    return (
        'f="$HOME/.bashrc"; umask 077; '
        '[ -f "$f" ] || : > "$f"; '
        f'if grep -F "{start}" "$f" >/dev/null 2>&1; then '
        'cp -p "$f" "$f.jssh-otty.bak" && '
        f'sed -i "/{start}/,/{end}/d" "$f"; '
        'else cp -p "$f" "$f.jssh-otty.bak" 2>/dev/null || true; fi; '
        f"{_decode_cmd(payload)} >> \"$f\""
    )


def parse_osc7_cwd(text: str, hostname: str | None = None) -> str | None:
    last: str | None = None
    for match in _OSC7.finditer(text):
        path = _osc7_path(match.group(1), hostname)
        if path:
            last = path
    return last


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


def _osc7_path(uri: str, hostname: str | None) -> str | None:
    raw = uri if "://" in uri else f"file://{uri}"
    parsed = urlparse(raw)
    host = unquote(parsed.netloc or parsed.hostname or "")
    path = unquote(parsed.path or "")
    if hostname and host and host.casefold() != hostname.casefold():
        return None
    if not path:
        return None
    if not path.startswith("/"):
        path = "/" + path
    if "\n" in path or "\r" in path or "\x00" in path:
        return None
    return path


def _decode_cmd(payload: str) -> str:
    return f"{{ printf '%s' '{payload}' | base64 -d 2>/dev/null || printf '%s' '{payload}' | base64 -D; }}"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _sh_single_quote(value: str) -> str:
    cleaned = value.replace("\n", "").replace("\r", "")
    return "'" + cleaned.replace("'", "'\\''") + "'"


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line else line for line in text.splitlines())


def _thread_timer(delay: float, fn: Callable[[], None]) -> threading.Timer:
    timer = threading.Timer(delay, fn)
    timer.daemon = True
    timer.start()
    return timer
