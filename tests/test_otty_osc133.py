from __future__ import annotations

import base64
import re
import shlex
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from jssh.connectors.otty_osc133 import (
    BLOCK_END,
    BLOCK_START,
    MARK_B,
    Osc133Injector,
    apply_managed_block,
    already_marked,
    bash_integration_script,
    managed_block,
    parse_osc7_cwd,
    persist_command,
    remove_managed_block,
    session_command,
    ECHO_OFF_PRELUDE,
    ECHO_ON_SUFFIX,
    ECHO_RESTORE,
    shell_integration_enabled,
    wrap_ps1,
    zsh_integration_script,
)
from jssh.connectors.session import KokoDriver, KokoLogin
from jssh.config import parse_config


class Child:
    def __init__(self) -> None:
        self.sent: list[object] = []

    def sendline(self, value: str) -> None:
        self.sent.append(value)

    def send(self, value: bytes) -> None:
        self.sent.append(value)


class ManualClock:
    def __init__(self) -> None:
        self._fn: Callable[[], None] | None = None

    def __call__(self, delay: float, fn: Callable[[], None]) -> ManualClock:
        self._fn = fn
        return self

    def cancel(self) -> None:
        self._fn = None

    def fire(self) -> None:
        for _ in range(8):
            fn = self._fn
            self._fn = None
            if fn is None:
                return
            fn()


def _strip_osc(text: str) -> str:
    text = re.sub(r"\x1b\].*?(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    return text.replace("\r", "")


def _extract_ps1(stdout: str) -> str:
    cleaned = _strip_osc(stdout)
    for line in cleaned.splitlines():
        if line.startswith("PS1<") and line.endswith(">"):
            return line[4:-1]
    raise AssertionError(f"PS1 marker missing in {stdout!r}")


def _decode_payloads(command: str) -> str:
    chunks: list[str] = []
    for match in re.findall(r"printf '%s' '([A-Za-z0-9+/=]+)'", command):
        try:
            chunks.append(base64.b64decode(match).decode("utf-8"))
        except Exception:
            continue
    return "\n".join(chunks)


def _is_inject(command: object) -> bool:
    text = str(command)
    return "__JSSH_OTTY_OSC133_APPLIED" in text or "133;A" in _decode_payloads(text)


def _run_prompt_hooks() -> str:
    return """
if [[ "$(declare -p PROMPT_COMMAND 2>/dev/null)" == "declare -a"* ]]; then
    for __jssh_pc in "${PROMPT_COMMAND[@]}"; do eval "$__jssh_pc"; done
else
    eval "${PROMPT_COMMAND-}"
fi
"""


def _run_bash(script: str, *, interactive: bool) -> subprocess.CompletedProcess[str]:
    argv = ["bash", "--norc", "--noprofile"]
    if interactive:
        argv.append("-i")
    return subprocess.run(argv, input=script, capture_output=True, text=True, check=False)


def _ps1_after(
    initial: str,
    *,
    interactive: bool = True,
    twice: bool = False,
    source_bashrc: str | None = None,
) -> str:
    script = f"PS1={shlex.quote(initial)}\n{session_command(hostname='bi-server01')}\n"
    if twice:
        script += f"{session_command(hostname='bi-server01')}\n"
    if source_bashrc is not None:
        script += f"source {shlex.quote(source_bashrc)}\n"
    script += _run_prompt_hooks()
    script += 'printf "PS1<%s>\\n" "$PS1"\n'
    result = _run_bash(script, interactive=interactive)
    assert result.returncode == 0, result.stderr
    return _extract_ps1(result.stdout)


def test_wrap_default_ps1() -> None:
    raw = r"[\u@\h \W]\$ "
    assert wrap_ps1(raw) == raw + MARK_B


def test_wrap_color_ps1() -> None:
    raw = r"\[\e[32m\]\u@\h\[\e[0m\]:\w\$ "
    wrapped = wrap_ps1(raw)
    assert wrapped is not None
    assert wrapped.startswith(raw)
    assert wrapped.endswith(MARK_B)
    assert raw in wrapped
    assert wrapped.count("133;B") == 1


def test_wrap_multiline_ps1() -> None:
    raw = "line1\\nline2\\$ "
    wrapped = wrap_ps1(raw)
    assert wrapped == raw + MARK_B


def test_wrap_empty_ps1() -> None:
    assert wrap_ps1("") == ""
    assert wrap_ps1(None) is None


def test_wrap_existing_osc133() -> None:
    raw = r"\u@\h\$ " + MARK_B
    assert wrap_ps1(raw) == raw
    assert already_marked(r"\[\033]133;A\007\]prompt")


def test_wrap_repeat_init() -> None:
    raw = r"\h\$ "
    once = wrap_ps1(raw)
    assert wrap_ps1(once) == once
    assert wrap_ps1(raw, applied=True) == raw


def test_wrap_noninteractive() -> None:
    raw = r"\u@\h\$ "
    assert wrap_ps1(raw, interactive=False) == raw


def test_session_command_wraps_live_ps1_not_test_prompt() -> None:
    cmd = session_command(hostname="bi-server01")
    decoded = _decode_payloads(cmd)
    assert "${PS1-}" in cmd
    assert "133;A" in decoded
    assert "133;B" in decoded
    assert "133;C" in decoded
    assert "133;D" in decoded
    assert "file://" in decoded
    assert "bi-server01" in decoded
    assert r"\u@\h:\w\$" not in decoded
    assert cmd.startswith(" ")
    assert ECHO_ON_SUFFIX in cmd
    assert r"printf '\r\033[2K'" in cmd
    assert "stty -echo" not in cmd
    bash = bash_integration_script("bi-server01")
    before_b, _sep, after_b = bash.partition('case "$PS1"')
    assert "PS1=" not in before_b
    assert MARK_B in after_b
    assert "__jssh_otty_status" in bash
    assert "COMP_LINE" in bash
    zsh = zsh_integration_script("bi-server01")
    assert "preexec_functions" in zsh
    assert "zle-line-init" in zsh
    assert "133;C" in zsh
    assert "file://" in zsh


def test_bash_default_ps1() -> None:
    raw = r"[\u@\h \W]\$ "
    assert _ps1_after(raw) == wrap_ps1(raw)


def test_bash_color_ps1() -> None:
    raw = r"\[\e[31m\]\u@\h\[\e[0m\]:\w\$ "
    assert _ps1_after(raw) == wrap_ps1(raw)


def test_bash_multiline_ps1() -> None:
    raw = "first\\nsecond\\$ "
    assert _ps1_after(raw) == wrap_ps1(raw)


def test_bash_empty_ps1() -> None:
    assert _ps1_after("") == ""


def test_bash_existing_osc133() -> None:
    raw = r"ready\$ " + MARK_B
    assert _ps1_after(raw) == raw


def test_bash_repeat_init() -> None:
    raw = r"host:\w\$ "
    assert _ps1_after(raw, twice=True) == wrap_ps1(raw)


def test_bash_noninteractive_leaves_ps1() -> None:
    raw = r"[\u@\h \W]\$ "
    assert _ps1_after(raw, interactive=False) == raw


def test_bash_resource_bashrc(tmp_path: Path) -> None:
    raw = r"[\u@\h \W]\$ "
    rc = tmp_path / "bashrc"
    rc.write_text(managed_block(), encoding="utf-8")
    first = _ps1_after(raw, source_bashrc=str(rc))
    assert first == wrap_ps1(raw)
    again = _ps1_after(raw, twice=True, source_bashrc=str(rc))
    assert again == wrap_ps1(raw)


def test_managed_block_roundtrip() -> None:
    original = "export LANG=C\nPS1='$ '\n"
    installed = apply_managed_block(original)
    assert BLOCK_START in installed
    assert BLOCK_END in installed
    updated = apply_managed_block(installed)
    assert updated.count(BLOCK_START) == 1
    assert remove_managed_block(updated).strip() == original.strip()


def test_persist_command_decodes_to_managed_block() -> None:
    cmd = persist_command(hostname="bi-server01")
    decoded = _decode_payloads(cmd)
    assert "133;C" in decoded
    assert "133;D" in decoded
    assert "file://" in decoded
    assert "bi-server01" in decoded
    assert "$HOME/.bashrc" in cmd
    assert ".jssh-otty.bak" in cmd


def test_shell_integration_auto_otty_only() -> None:
    cfg = parse_config(
        {
            "jumpserver": {
                "api_url": "https://jumpserver.example.com",
                "ssh_host": "jumpserver.example.com",
                "ssh_username": "user",
            }
        },
        Path("/tmp/jssh-test.yaml"),
    )
    assert cfg.otty_shell_integration == "auto"
    assert cfg.otty_shell_install is False
    assert shell_integration_enabled(cfg, {"TERM_PROGRAM": "otty"}) is True
    assert shell_integration_enabled(cfg, {"TERM_PROGRAM": "iTerm.app"}) is False
    assert shell_integration_enabled(cfg, {"TERM_PROGRAM": "otty", "JSSH_OTTY": "0"}) is False
    assert (
        shell_integration_enabled(
            cfg,
            {"TERM_PROGRAM": "iTerm.app", "JSSH_OTTY_SHELL_INTEGRATION": "1"},
        )
        is True
    )


def test_shell_integration_force_off(tmp_path: Path) -> None:
    cfg = parse_config(
        {
            "jumpserver": {
                "api_url": "https://jumpserver.example.com",
                "ssh_host": "jumpserver.example.com",
                "ssh_username": "user",
            },
            "terminal": {"otty_shell_integration": False},
        },
        tmp_path / "cfg.yaml",
    )
    assert cfg.otty_shell_integration == "off"
    assert shell_integration_enabled(cfg, {"TERM_PROGRAM": "otty"}) is False


def _driver(clock: ManualClock) -> tuple[Child, Osc133Injector, KokoDriver]:
    child = Child()
    injector = Osc133Injector(child.sendline, timer_factory=clock, hostname="bi-server01")
    login = KokoLogin(hostname="bi-server01", account_name="linux-root")
    driver = KokoDriver(child, lambda: "111111", ("[MFA auth]:",), login, osc133=injector)
    return child, injector, driver


def _login_to_account(driver: KokoDriver) -> None:
    driver.filter_output("[MFA auth]: ")
    driver.filter_output("Opt> ")
    driver.filter_output("ID Username\n1 linux-root root\nID> ")


def test_osc133_not_sent_on_koko_login() -> None:
    clock = ManualClock()
    child, _, driver = _driver(clock)
    _login_to_account(driver)
    clock.fire()
    assert child.sent == ["111111", "bi-server01", "1"]


def test_osc133_wraps_after_remote_shell() -> None:
    clock = ManualClock()
    child, _, driver = _driver(clock)
    _login_to_account(driver)
    driver.filter_output("Last login: today\n")
    assert all("133;A" not in str(item) for item in child.sent)
    clock.fire()
    cmd = child.sent[-1]
    assert isinstance(cmd, str)
    decoded = _decode_payloads(cmd)
    assert "${PS1-}" in cmd
    assert "133;A" in decoded
    assert "133;C" in decoded
    assert "133;D" in decoded
    assert r"\u@\h:\w\$" not in decoded



def test_osc133_hides_inject_echo() -> None:
    clock = ManualClock()
    child, _, driver = _driver(clock)
    _login_to_account(driver)
    driver.filter_output("Last login: today\n")
    clock.fire()
    prelude, payload = child.sent[-2], child.sent[-1]
    assert prelude == ECHO_OFF_PRELUDE
    assert "stty -echo" in str(prelude)
    assert _is_inject(payload)
    assert ECHO_ON_SUFFIX in str(payload)
    assert "stty -echo" not in str(payload)


def test_osc133_restore_echo_if_cancelled_after_prelude() -> None:
    clock = ManualClock()
    child, injector, driver = _driver(clock)
    _login_to_account(driver)
    driver.filter_output("Last login: today\n")
    fn = clock._fn
    clock._fn = None
    assert fn is not None
    fn()
    assert child.sent[-1] == ECHO_OFF_PRELUDE
    assert not _is_inject(child.sent[-1])
    driver.filter_input(b"l")
    assert any(str(item).strip() == ECHO_RESTORE for item in child.sent)
    clock.fire()
    assert not any(_is_inject(item) for item in child.sent)


def test_osc133_skips_other_terminals() -> None:
    child = Child()
    login = KokoLogin(hostname="bi-server01", account_name="linux-root")
    driver = KokoDriver(child, lambda: "111111", ("[MFA auth]:",), login)
    _login_to_account(driver)
    driver.filter_output("Last login: today\n")
    assert child.sent == ["111111", "bi-server01", "1"]


def test_osc133_password_then_shell() -> None:
    clock = ManualClock()
    child, _, driver = _driver(clock)
    _login_to_account(driver)
    driver.filter_output("Password: ")
    clock.fire()
    assert all("133;A" not in str(item) for item in child.sent)
    driver.filter_output("Last login: today\n")
    clock.fire()
    assert _is_inject(child.sent[-1])


def test_osc133_user_input_cancels_until_next_output() -> None:
    clock = ManualClock()
    child, _, driver = _driver(clock)
    _login_to_account(driver)
    driver.filter_output("Last login: today\n")
    driver.filter_input(b"l")
    clock.fire()
    assert all("133;A" not in str(item) for item in child.sent)


def test_osc133_unique_asset_after_shell_output() -> None:
    clock = ManualClock()
    child, _, driver = _driver(clock)
    driver.filter_output("[MFA auth]: ")
    driver.filter_output("Opt> ")
    assert child.sent == ["111111", "bi-server01"]
    driver.filter_output("Last login from gateway\n")
    clock.fire()
    assert _is_inject(child.sent[-1])


def test_osc133_mfa_and_account_select_unchanged() -> None:
    clock = ManualClock()
    child, _, driver = _driver(clock)
    _login_to_account(driver)
    assert child.sent == ["111111", "bi-server01", "1"]
    driver.filter_output("Last login: today\n")
    clock.fire()
    driver.filter_output("root@bi-server01:~# logout\n[Host]> ")
    assert child.sent[-1] == "q"
    assert any(_is_inject(item) for item in child.sent)


def test_invalid_otty_shell_mode(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="otty_shell_integration"):
        parse_config(
            {
                "jumpserver": {
                    "api_url": "https://jumpserver.example.com",
                    "ssh_host": "jumpserver.example.com",
                    "ssh_username": "user",
                },
                "terminal": {"otty_shell_integration": "maybe"},
            },
            tmp_path / "cfg.yaml",
        )


def test_bash_first_prompt_has_a_not_d() -> None:
    script = bash_integration_script("bi-server01")
    wrapper = (
        "PS1='host$ '\n"
        + script
        + "\nprintf 'OUT<'\n"
        + "__jssh_otty_status; __jssh_otty_prompt\n"
        + "printf '>END\\n'\n"
    )
    result = _run_bash(wrapper, interactive=False)
    assert result.returncode == 0, result.stderr
    body = result.stdout[result.stdout.find("OUT<") : result.stdout.find(">END")]
    assert "\033]133;A;aid=" in body
    assert "\033]133;D;" not in body
    assert "\033]7;file://bi-server01" in body


def test_bash_emits_d_after_command() -> None:
    script = bash_integration_script("bi-server01")
    wrapper = (
        "PS1='host$ '\n"
        + script
        + "\n__jssh_otty_status; __jssh_otty_prompt\n"
        + "false\n"
        + "__jssh_otty_status\n"
        + "printf 'OUT<'\n"
        + "__jssh_otty_prompt\n"
        + "printf '>END\\n'\n"
    )
    result = _run_bash(wrapper, interactive=False)
    assert result.returncode == 0, result.stderr
    body = result.stdout[result.stdout.find("OUT<") : result.stdout.find(">END")]
    assert "\033]133;D;1;aid=" in body
    assert "\033]133;A;aid=" in body
    assert "\033]7;file://bi-server01" in body


def test_bash_ps0_contains_c_on_modern_bash() -> None:
    version = subprocess.run(
        ["bash", "-c", 'printf "%s.%s" "${BASH_VERSINFO[0]}" "${BASH_VERSINFO[1]}"'],
        capture_output=True,
        text=True,
        check=False,
    )
    major, minor = (int(part) for part in version.stdout.split("."))
    if major < 4 or (major == 4 and minor < 4):
        pytest.skip("PS0 requires bash 4.4+")
    script = bash_integration_script("bi-server01")
    wrapper = script + "\n__jssh_otty_status; __jssh_otty_prompt\n" + 'printf "PS0<%s>\\n" "$PS0"\n'
    result = _run_bash(wrapper, interactive=False)
    assert result.returncode == 0, result.stderr
    assert "133;C" in result.stdout


def test_parse_osc7_cwd() -> None:
    text = "\033]7;file://bi-server01/root/work\007"
    assert parse_osc7_cwd(text, "bi-server01") == "/root/work"
    encoded = "\033]7;file://bi-server01/tmp/has%20space\033\\"
    assert parse_osc7_cwd(encoded, "bi-server01") == "/tmp/has space"
    assert parse_osc7_cwd(text, "other-host") is None
    assert parse_osc7_cwd("no marks", "bi-server01") is None


def test_managed_block_includes_full_marks() -> None:
    block = managed_block("bi-server01")
    assert BLOCK_START in block
    assert "133;C" in block
    assert "133;D" in block
    assert "file://" in block
    assert "bi-server01" in block
    assert "OTTY_MARKS_DIR" not in block


def test_otty_sftp_layout_config(tmp_path: Path) -> None:
    base = {
        "jumpserver": {
            "api_url": "https://jumpserver.example.com",
            "ssh_host": "jumpserver.example.com",
            "ssh_username": "user",
        }
    }
    default = parse_config(base, tmp_path / "cfg.yaml")
    assert default.otty_sftp_layout == "split"
    assert default.otty_sftp_direction == "right"
    assert default.otty_sftp_size == 40
    assert default.otty_sftp_no_focus is True
    cfg = parse_config(
        {**base, "terminal": {"otty_sftp_layout": "tab", "otty_sftp_size": 30, "otty_sftp_no_focus": False}},
        tmp_path / "cfg.yaml",
    )
    assert cfg.otty_sftp_layout == "tab"
    assert cfg.otty_sftp_size == 30
    assert cfg.otty_sftp_no_focus is False
    with pytest.raises(Exception, match="otty_sftp_layout"):
        parse_config({**base, "terminal": {"otty_sftp_layout": "window"}}, tmp_path / "cfg.yaml")
    with pytest.raises(Exception, match="otty_sftp_size"):
        parse_config({**base, "terminal": {"otty_sftp_size": 9}}, tmp_path / "cfg.yaml")


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not installed")
def test_zsh_script_defines_hooks() -> None:
    script = zsh_integration_script("bi-server01")
    assert "precmd_functions" in script
    assert "preexec_functions" in script
    assert "133;A" in script
    assert "133;C" in script
    assert "133;D" in script
    assert "file://%s%s" in script or "file://" in script
