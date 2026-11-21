from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from jssh.connectors.otty_osc133 import (
    BLOCK_END,
    BLOCK_START,
    MARK_A,
    MARK_B,
    Osc133Injector,
    apply_managed_block,
    already_marked,
    managed_block,
    persist_command,
    remove_managed_block,
    session_command,
    shell_integration_enabled,
    wrap_ps1,
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
        fn = self._fn
        self._fn = None
        if fn is not None:
            fn()


def _extract_ps1(stdout: str) -> str:
    for line in stdout.splitlines():
        if line.startswith("PS1<") and line.endswith(">"):
            return line[4:-1]
    raise AssertionError(f"PS1 marker missing in {stdout!r}")


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
    script = f"PS1={shlex.quote(initial)}\n{session_command()}\n"
    if twice:
        script += f"{session_command()}\n"
    if source_bashrc is not None:
        script += f"source {shlex.quote(source_bashrc)}\n"
    script += 'printf "PS1<%s>\\n" "$PS1"\n'
    result = _run_bash(script, interactive=interactive)
    assert result.returncode == 0, result.stderr
    return _extract_ps1(result.stdout)


def test_wrap_default_ps1() -> None:
    raw = r"[\u@\h \W]\$ "
    assert wrap_ps1(raw) == MARK_A + raw + MARK_B


def test_wrap_color_ps1() -> None:
    raw = r"\[\e[32m\]\u@\h\[\e[0m\]:\w\$ "
    wrapped = wrap_ps1(raw)
    assert wrapped is not None
    assert wrapped.startswith(MARK_A)
    assert wrapped.endswith(MARK_B)
    assert raw in wrapped


def test_wrap_multiline_ps1() -> None:
    raw = "line1\\nline2\\$ "
    wrapped = wrap_ps1(raw)
    assert wrapped == MARK_A + raw + MARK_B


def test_wrap_empty_ps1() -> None:
    assert wrap_ps1("") == ""
    assert wrap_ps1(None) is None


def test_wrap_existing_osc133() -> None:
    raw = MARK_A + r"\u@\h\$ " + MARK_B
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
    cmd = session_command()
    assert "$PS1" in cmd
    assert r"\[\e]133;A\a\]" in cmd
    assert r"\[\e]133;B\a\]" in cmd
    assert r"\u@\h:\w\$" not in cmd
    assert "133;C" not in cmd
    assert "133;D" not in cmd
    assert cmd.startswith(" ")


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
    raw = MARK_A + r"ready\$ " + MARK_B
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
    cmd = persist_command()
    assert "133;C" not in cmd
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
    injector = Osc133Injector(child.sendline, timer_factory=clock)
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
    assert "$PS1" in cmd
    assert r"\[\e]133;A\a\]" in cmd
    assert r"\u@\h:\w\$" not in cmd
    assert "133;C" not in cmd


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
    assert "$PS1" in str(child.sent[-1])


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
    assert "$PS1" in str(child.sent[-1])


def test_osc133_mfa_and_account_select_unchanged() -> None:
    clock = ManualClock()
    child, _, driver = _driver(clock)
    _login_to_account(driver)
    assert child.sent == ["111111", "bi-server01", "1"]
    driver.filter_output("Last login: today\n")
    clock.fire()
    driver.filter_output("root@bi-server01:~# logout\n[Host]> ")
    assert child.sent[-1] == "q"
    assert any("$PS1" in str(item) for item in child.sent)


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
