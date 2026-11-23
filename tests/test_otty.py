from __future__ import annotations

import base64
import subprocess
from pathlib import Path
from types import SimpleNamespace

from jssh.connectors.otty import (
    OttyLayout,
    OttyOff,
    OttyOn,
    OttySession,
    find_otty_cli,
    in_otty,
    open_sftp_sidecar,
    osc_status,
    osc_title,
    osc_both,
    osc_cwd,
    local_restore_sequences,
    osc_clear_title,
    parse_tab_snapshot,
    read_tab_snapshot,
    TabSnapshot,
    otty_split_plan,
    pane_capture,
    pane_close,
    pane_send_keys,
    pane_send_text,
    parse_otty_id,
    report_status,
    split_sftp_argv,
    tab_badge_argv,
    tab_sftp_argv,
)
from jssh.connectors.session import resolve_gateway_argv


def test_otty_split_plan_reasons(monkeypatch) -> None:
    monkeypatch.setattr("jssh.connectors.otty.find_otty_cli", lambda: "/app/otty-cli")
    assert otty_split_plan({"TERM_PROGRAM": "WarpTerminal"}) == OttyOff("not-otty")
    assert otty_split_plan({"TERM_PROGRAM": "otty", "JSSH_OTTY": "0"}) == OttyOff("disabled")
    assert otty_split_plan({"TERM_PROGRAM": "otty"}, cli=None) == OttyOff("no-cli")
    assert otty_split_plan({"TERM_PROGRAM": "otty", "OTTY_PANE_ID": "p_1"}) == OttyOn(
        "/app/otty-cli", "p_1"
    )


def test_in_otty_ignores_other_terminals() -> None:
    assert in_otty({"TERM_PROGRAM": "otty"}) is True
    assert in_otty({"TERM_PROGRAM": "otty", "JSSH_OTTY": "off"}) is False
    assert in_otty({"TERM_PROGRAM": "iTerm.app"}) is False


def test_split_sftp_argv_targets_current_pane() -> None:
    plan = OttyOn(cli="/app/otty-cli", pane_id="p_9")
    argv = split_sftp_argv(plan, "jssh sftp bi-server01 --account linux-root", "sftp bi-server01")
    assert argv == [
        "/app/otty-cli",
        "pane",
        "split",
        "--direction",
        "right",
        "--command",
        "jssh sftp bi-server01 --account linux-root",
        "--title",
        "sftp bi-server01",
        "--json",
        "--size",
        "40",
        "--no-focus",
        "--pane",
        "p_9",
    ]


def test_ctrl_f_splits_in_otty(monkeypatch) -> None:
    monkeypatch.setattr("jssh.connectors.otty.find_otty_cli", lambda: "/app/otty-cli")
    ran: list[list[str]] = []

    def run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        ran.append(argv)
        return SimpleNamespace(returncode=0)

    copied: list[str] = []
    result = open_sftp_sidecar(
        "jssh sftp bi-server01 --account linux-root",
        title="sftp bi-server01",
        env={"TERM_PROGRAM": "otty", "OTTY_PANE_ID": "p_1"},
        copy=copied.append,
        run=run,
    )
    assert result.kind == "pane"
    assert copied == []
    assert ran == [
        split_sftp_argv(
            OttyOn("/app/otty-cli", "p_1"),
            "jssh sftp bi-server01 --account linux-root",
            "sftp bi-server01",
        )
    ]


def test_ctrl_f_copies_when_no_tab_api() -> None:
    copied: list[str] = []
    result = open_sftp_sidecar(
        "jssh sftp bi-server01 --account linux-root",
        title="sftp bi-server01",
        env={"TERM_PROGRAM": "xterm"},
        copy=copied.append,
        run=lambda *_a, **_k: SimpleNamespace(returncode=0),
    )
    assert result.kind == "copied"
    assert copied == ["jssh sftp bi-server01 --account linux-root"]


def _sidecar_run(env: dict[str, str]) -> tuple[str, list[list[str]], list[str]]:
    ran: list[list[str]] = []
    copied: list[str] = []

    def run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        ran.append(argv)
        return SimpleNamespace(returncode=0)

    result = open_sftp_sidecar(
        "jssh sftp bi-server01 --account linux-root",
        title="sftp bi-server01",
        env=env,
        copy=copied.append,
        run=run,
    )
    return result, ran, copied


def test_ctrl_f_opens_terminal_tab() -> None:
    result, ran, copied = _sidecar_run({"TERM_PROGRAM": "Apple_Terminal"})
    assert result.kind == "pane"
    assert copied == []
    assert ran[0][:2] == ["osascript", "-e"]
    script = ran[0][2]
    assert "Terminal" in script
    assert "do script" in script
    assert "in front window" in script
    assert "jssh sftp bi-server01 --account linux-root" in script


def test_ctrl_f_opens_iterm_tab() -> None:
    result, ran, copied = _sidecar_run({"TERM_PROGRAM": "iTerm.app"})
    assert result.kind == "pane"
    assert copied == []
    script = ran[0][2]
    assert "iTerm" in script
    assert "create tab with default profile" in script
    assert "write text" in script
    assert "jssh sftp bi-server01 --account linux-root" in script


def test_ctrl_f_opens_kitty_tab() -> None:
    result, ran, copied = _sidecar_run({"TERM_PROGRAM": "kitty"})
    assert result.kind == "pane"
    assert copied == []
    assert ran == [
        [
            "kitten",
            "@",
            "launch",
            "--type=tab",
            "--cwd=current",
            "--",
            "jssh",
            "sftp",
            "bi-server01",
            "--account",
            "linux-root",
        ]
    ]


def test_ctrl_f_opens_wezterm_tab() -> None:
    result, ran, copied = _sidecar_run({"TERM_PROGRAM": "WezTerm"})
    assert result.kind == "pane"
    assert copied == []
    assert ran == [
        [
            "wezterm",
            "cli",
            "spawn",
            "--",
            "jssh",
            "sftp",
            "bi-server01",
            "--account",
            "linux-root",
        ]
    ]


def test_ctrl_f_opens_warp_window(tmp_path: Path) -> None:
    opened: list[str] = []
    tab = tmp_path / "jssh_sftp.toml"
    copied: list[str] = []

    def opener(uri: str) -> SimpleNamespace:
        opened.append(uri)
        return SimpleNamespace(returncode=0)

    result = open_sftp_sidecar(
        "jssh sftp bi-server01 --account linux-root",
        title="sftp bi-server01",
        env={"TERM_PROGRAM": "WarpTerminal"},
        copy=copied.append,
        warp_tab=tab,
        warp_open=opener,
    )
    assert result.kind == "pane"
    assert copied == []
    assert opened == ["warp://tab_config/jssh_sftp"]
    text = tab.read_text(encoding="utf-8")
    assert "jssh sftp bi-server01 --account linux-root" in text
    assert 'title = "sftp bi-server01"' in text
    assert 'type = "terminal"' in text


def test_ctrl_f_copies_when_split_fails(monkeypatch) -> None:
    monkeypatch.setattr("jssh.connectors.otty.find_otty_cli", lambda: "/app/otty-cli")
    copied: list[str] = []
    result = open_sftp_sidecar(
        "jssh sftp bi-server01 --account linux-root",
        title="sftp bi-server01",
        env={"TERM_PROGRAM": "otty"},
        copy=copied.append,
        run=lambda *_a, **_k: SimpleNamespace(returncode=1),
    )
    assert result.kind == "copied"
    assert copied == ["jssh sftp bi-server01 --account linux-root"]


def test_osc_status_encodes_message() -> None:
    raw = osc_status("blocked", kind="auth", msg="Password")
    assert raw.startswith(b"\033]7501;")
    assert raw.endswith(b"\033\\")
    assert b"state=blocked" in raw
    assert b"kind=auth" in raw
    assert b"app=jssh" in raw
    assert base64.b64encode(b"Password").decode("ascii").encode("ascii") in raw


def test_osc_title_strips_controls() -> None:
    raw = osc_title("bi-server01 / linux-root\n")
    assert raw == b"\033]2;bi-server01 / linux-root\007"


def test_osc_cwd_encodes_host_and_path() -> None:
    raw = osc_cwd("my host", "/tmp/a b")
    assert raw == b"\033]7;file://my%20host/tmp/a%20b\007"


def test_close_clears_program_title_for_otty_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("jssh.connectors.otty.socket.gethostname", lambda: "macbook.local")
    written: list[bytes] = []
    session = OttySession(env={"TERM_PROGRAM": "otty"}, emit=written.append)
    session.start("bi-server01 / linux-root")
    session.close()
    assert osc_status("clear") in written
    assert written[-2:] == [
        osc_cwd("macbook", str(tmp_path.resolve())),
        osc_clear_title(),
    ]
    assert written[-1] == b"\033]0;\007"
    assert tmp_path.name.encode() not in written[-1]
    assert b"root@localhost" not in b"".join(written[-2:])
    assert osc_title("bi-server01 / linux-root") not in written[-2:]



def test_parse_tab_snapshot_empty_title_is_otty_default() -> None:
    snapped = parse_tab_snapshot(
        """{"ok": true, "data": {"id": "t_1", "title": "", "cwd": "/Users/yan_dou/github/jssh"}}"""
    )
    assert snapped == TabSnapshot(tab_id="t_1", title="", cwd="/Users/yan_dou/github/jssh")


def test_read_tab_snapshot_uses_pane_then_tab(monkeypatch) -> None:
    calls: list[list[str]] = []

    def run(argv, **_kwargs):
        calls.append(argv)
        if "pane" in argv:
            stdout = b'{"ok":true,"data":{"id":"p_1","tab_id":"t_9","cwd":"/tmp"}}'
        else:
            stdout = b'{"ok":true,"data":{"id":"t_9","title":"","cwd":"/Users/yan_dou/github/jssh"}}'
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr=b"")

    env = {"TERM_PROGRAM": "otty", "OTTY_PANE_ID": "p_1"}
    snapped = read_tab_snapshot(env, run=run, cli="/app/otty-cli")
    assert snapped == TabSnapshot(tab_id="t_9", title="", cwd="/Users/yan_dou/github/jssh")
    assert calls[0][:5] == ["/app/otty-cli", "pane", "show", "--json", "--pane"]
    assert calls[1][:3] == ["/app/otty-cli", "tab", "show"]
    assert "--tab" in calls[1] and "t_9" in calls[1]


def test_close_restores_captured_custom_title(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("jssh.connectors.otty.socket.gethostname", lambda: "macbook.local")
    written: list[bytes] = []
    session = OttySession(
        env={"TERM_PROGRAM": "otty"},
        emit=written.append,
        snapshot=TabSnapshot(tab_id="t_1", title="notes", cwd=str(tmp_path)),
    )
    session.start("bi-server01 / linux-root")
    session.close()
    assert written[-2:] == local_restore_sequences(title="notes")
    assert written[-1] == osc_both("notes")


def test_status_working_password_then_idle() -> None:
    written: list[bytes] = []
    session = OttySession(env={"TERM_PROGRAM": "otty"}, emit=written.append)
    session.start("bi-server01 / linux-root")
    assert any(b"]2;bi-server01 / linux-root" in item for item in written)
    assert any(b"state=working" in item for item in written)
    assert session.observe("[MFA auth]: ", at_gateway=True) == b""
    blocked = session.observe("Password: ", at_gateway=False)
    assert b"state=blocked" in blocked
    assert b"kind=auth" in blocked
    idle = session.observe("root@bi-server01:~# ", at_gateway=False)
    assert b"state=idle" in idle
    session.close()
    assert osc_status("clear") in written
    assert written[-2:] == local_restore_sequences()


def test_status_idles_on_shell_without_password() -> None:
    session = OttySession(env={"TERM_PROGRAM": "otty"}, emit=lambda _data: None)
    session.start("bi-server01 / linux-root")
    assert session.observe("Opt> ", at_gateway=True) == b""
    idle = session.observe("$ ", at_gateway=True)
    assert b"state=idle" in idle


def test_status_stays_off_outside_otty() -> None:
    written: list[bytes] = []
    session = OttySession(env={"TERM_PROGRAM": "WarpTerminal"}, emit=written.append)
    session.start("bi-server01 / linux-root")
    assert written == []
    assert session.observe("Password: ", at_gateway=False) == b""
    session.close()
    assert written == []


def test_gateway_argv_uses_absolute_binary(monkeypatch) -> None:
    monkeypatch.setattr(
        "jssh.connectors.session.shutil.which", lambda name: f"/usr/bin/{name}"
    )
    assert resolve_gateway_argv(["ssh", "-p", "2222"]) == ["/usr/bin/ssh", "-p", "2222"]
    assert resolve_gateway_argv(["/usr/bin/sftp", "-P", "2222"]) == ["/usr/bin/sftp", "-P", "2222"]


def test_find_otty_cli_prefers_path_then_bundle(tmp_path: Path) -> None:
    bundled = tmp_path / "otty-cli"
    bundled.write_text("", encoding="utf-8")
    bundled.chmod(0o755)
    assert find_otty_cli(which=lambda _name: "/usr/local/bin/otty", bundled=bundled) == "/usr/local/bin/otty"
    assert find_otty_cli(which=lambda _name: None, bundled=bundled) == str(bundled)
    assert find_otty_cli(which=lambda _name: None, bundled=tmp_path / "missing") is None

def test_split_sftp_argv_honors_layout() -> None:
    plan = OttyOn(cli="/app/otty-cli", pane_id="p_1")
    argv = split_sftp_argv(
        plan,
        "jssh sftp bi-server01 --account linux-root",
        "sftp bi-server01",
        layout=OttyLayout(direction="left", size=25, no_focus=False, local_cwd="/tmp"),
    )
    assert "--direction" in argv and argv[argv.index("--direction") + 1] == "left"
    assert "--size" in argv and argv[argv.index("--size") + 1] == "25"
    assert "--no-focus" not in argv
    assert "--cwd" in argv and argv[argv.index("--cwd") + 1] == "/tmp"
    assert "--json" in argv


def test_tab_sftp_argv_and_badge() -> None:
    plan = OttyOn(cli="/app/otty-cli", pane_id="p_1")
    argv = tab_sftp_argv(plan, "jssh sftp bi-server01 --account linux-root", "sftp bi-server01")
    assert argv[:4] == ["/app/otty-cli", "tab", "new", "--command"]
    assert "--title" in argv
    assert "--json" in argv
    assert "--no-focus" in argv
    assert "--space" not in argv
    assert tab_badge_argv("/app/otty-cli", "t_9", kind="running") == [
        "/app/otty-cli",
        "tab",
        "badge",
        "--tab",
        "t_9",
        "--kind",
        "running",
    ]
    assert "--clear" in tab_badge_argv("/app/otty-cli", "t_9", clear=True)


def test_ctrl_f_opens_otty_tab(monkeypatch) -> None:
    monkeypatch.setattr("jssh.connectors.otty.find_otty_cli", lambda: "/app/otty-cli")
    ran: list[list[str]] = []

    def run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        ran.append(argv)
        if argv[1:3] == ["tab", "new"]:
            return SimpleNamespace(returncode=0, stdout=b'{"id":"t_1"}')
        return SimpleNamespace(returncode=0, stdout=b"")

    result = open_sftp_sidecar(
        "jssh sftp bi-server01 --account linux-root",
        title="sftp bi-server01",
        env={"TERM_PROGRAM": "otty", "OTTY_PANE_ID": "p_1"},
        copy=lambda _cmd: None,
        run=run,
        layout=OttyLayout(mode="tab"),
    )
    assert result.kind == "pane"
    assert result.tab_id == "t_1"
    assert result.pane_id is None
    assert ran[0][:3] == ["/app/otty-cli", "tab", "new"]
    assert ran[1] == tab_badge_argv("/app/otty-cli", "t_1", kind="running")


def test_ctrl_f_split_captures_pane_id(monkeypatch) -> None:
    monkeypatch.setattr("jssh.connectors.otty.find_otty_cli", lambda: "/app/otty-cli")

    def run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=b'{"id":"p_42"}')

    result = open_sftp_sidecar(
        "jssh sftp bi-server01 --account linux-root",
        title="sftp bi-server01",
        env={"TERM_PROGRAM": "otty", "OTTY_PANE_ID": "p_1"},
        copy=lambda _cmd: None,
        run=run,
    )
    assert result.kind == "pane"
    assert result.pane_id == "p_42"


def test_parse_otty_id_defensive() -> None:
    assert parse_otty_id(b'{"id":"p_1"}') == "p_1"
    assert parse_otty_id('noise\\n{"data":{"pane_id":"p_2"}}\\n') == "p_2"
    assert parse_otty_id('["nope"]') is None or parse_otty_id('[{"id":"t_3"}]') == "t_3"
    assert parse_otty_id("") is None
    assert parse_otty_id("not json") is None
    assert parse_otty_id(None) is None


def test_pane_helpers_use_pane_flag() -> None:
    ran: list[list[str]] = []

    def run(argv: list[str], **_kwargs: object) -> SimpleNamespace:
        ran.append(argv)
        return SimpleNamespace(returncode=0, stdout=b"ok")

    assert pane_send_text("/app/otty-cli", "p_1", "cd /tmp", run=run) is True
    assert pane_send_keys("/app/otty-cli", "p_1", "cd /tmp", "key:Enter", run=run) is True
    assert pane_capture("/app/otty-cli", "p_1", run=run) == "ok"
    assert pane_close("/app/otty-cli", "p_1", run=run) is True
    assert ran[0] == ["/app/otty-cli", "pane", "send-text", "--pane", "p_1", "cd /tmp"]
    assert ran[1] == ["/app/otty-cli", "pane", "send-keys", "--pane", "p_1", "cd /tmp", "key:Enter"]
    assert ran[2] == ["/app/otty-cli", "pane", "capture", "--pane", "p_1"]
    assert ran[3] == ["/app/otty-cli", "pane", "close", "--pane", "p_1"]


def test_pane_helpers_fail_quietly() -> None:
    def run(_argv: list[str], **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"ipc denied")

    assert pane_send_text("/app/otty-cli", "p_1", "cd /tmp", run=run) is False
    assert pane_send_keys("/app/otty-cli", "p_1", "key:Enter", run=run) is False
    assert pane_capture("/app/otty-cli", "p_1", run=run) is None
    assert pane_close("/app/otty-cli", "p_1", run=run) is False


def test_osc_status_title_is_base64() -> None:
    raw = osc_status("working", title="bi-server01", msg="Connecting")
    encoded_title = base64.b64encode(b"bi-server01").decode("ascii").encode("ascii")
    encoded_msg = base64.b64encode(b"Connecting").decode("ascii").encode("ascii")
    assert b"state=working" in raw
    assert b"app=jssh" in raw
    assert b"title=" + encoded_title in raw
    assert b"msg=" + encoded_msg in raw
    assert b"kind=" not in raw
    assert b"progress=" not in raw


def test_osc_status_progress_and_limits() -> None:
    raw = osc_status("working", progress=40, msg="x" * 3000)
    assert b"progress=40" in raw
    decoded_msg = base64.b64decode(raw.split(b"msg=")[1].split(b"\033")[0])
    assert len(decoded_msg) == 2048
    try:
        osc_status("working", progress=101)
        raise AssertionError("expected progress error")
    except ValueError:
        pass
    idle = osc_status("idle", kind="auth", progress=10)
    assert b"kind=" not in idle
    assert b"progress=" not in idle
    done = osc_status("done", msg="Synced 3 assets")
    assert b"state=done" in done
    err = osc_status("error", msg="Authentication failed")
    assert b"state=error" in err


def test_report_status_stays_off_outside_otty() -> None:
    written: list[bytes] = []
    assert report_status("error", msg="nope", env={"TERM_PROGRAM": "iTerm.app"}, emit=written.append) == b""
    assert written == []
    assert report_status(
        "working",
        msg="Syncing assets",
        env={"TERM_PROGRAM": "otty"},
        emit=written.append,
    ).startswith(b"\033]7501;")
    assert written and b"state=working" in written[0]
    assert report_status(
        "error",
        msg="Authentication failed",
        env={"TERM_PROGRAM": "otty", "JSSH_OTTY": "0"},
        emit=written.append,
    ) == b""
    assert len(written) == 1


def test_status_start_sets_asset_title() -> None:
    written: list[bytes] = []
    session = OttySession(env={"TERM_PROGRAM": "otty"}, emit=written.append)
    session.start("bi-server01 / linux-root", asset_name="bi-server01")
    encoded = base64.b64encode(b"bi-server01").decode("ascii").encode("ascii")
    assert any(b"title=" + encoded in item for item in written)
    assert any(b"state=working" in item for item in written)


def test_sync_sftp_cwd_sends_cd(monkeypatch) -> None:
    from jssh.connectors.otty import SidecarResult
    from jssh.connectors.session import SidecarTrack, sftp_cd_text, sync_sftp_cwd

    assert sftp_cd_text("/root") == "cd /root"
    assert sftp_cd_text("/tmp/has space") == 'cd "/tmp/has space"'
    ran: list[list[str]] = []

    def fake_send(cli: str, pane_id: str, text: str, **_kwargs: object) -> bool:
        ran.append([cli, pane_id, text])
        return True

    monkeypatch.setattr("jssh.connectors.session.pane_send_text", fake_send)
    track = SidecarTrack(cwd="/root", result=SidecarResult("pane", pane_id="p_9"), cli="/app/otty-cli")
    assert sync_sftp_cwd(track) is True
    assert ran == [["/app/otty-cli", "p_9", "cd /root"]]
    tab_only = SidecarTrack(cwd="/root", result=SidecarResult("pane", tab_id="t_1"), cli="/app/otty-cli")
    assert sync_sftp_cwd(tab_only) is False
