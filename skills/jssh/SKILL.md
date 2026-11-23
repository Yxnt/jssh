---
name: jssh
description: Use when changing jssh, the JumpServer 2.4.1 SSH/SFTP CLI, especially Otty integration, OSC 133 inject, tab title restore, OSC 7501 status, or Ctrl-F SFTP sidecars.
---

# jssh

MIT Python 3.11+ CLI. It lists JumpServer 2.4.1 assets the user can reach, lets them pick one with fzf, and opens SSH or SFTP through KoKo using pexpect plus the system `ssh` / `sftp`. It does not use JumpServer 3.x `user@root@ip`, and it never goes through Otty's `ssh()` wrapper.

Read `README.md` for install, config, and user-facing behavior. This skill is the agent contract: constraints that are easy to violate and tests that must still pass.

## Layout

- `src/jssh/cli.py` — commands and flags
- `src/jssh/config.py` — YAML / env config
- `src/jssh/connectors/session.py` — pexpect SSH session, wires Otty hooks
- `src/jssh/connectors/otty.py` — Otty detection, OSC 0/1/2/7/7501, tab snapshot, SFTP sidecar
- `src/jssh/connectors/otty_osc133.py` — silent session inject, PS1 wrap, optional `~/.bashrc` block
- `src/jssh/connectors/sftp.py` — interactive SFTP
- `tests/test_otty.py`, `tests/test_otty_osc133.py`
- `config.example.yaml` — shippable defaults, no secrets

## Hard rules

1. Do not implement `ottyd` unless the user explicitly asks.
2. Do not replace the remote `PS1`. Only append OSC 133 `B` when it is missing. Skip inject on KoKo menus, password prompts, SFTP, and non-Otty terminals.
3. Persist (`terminal.otty_shell_install`) is opt-in. The default must not write the remote `~/.bashrc`.
4. Detect Otty with `TERM_PROGRAM=otty`. `JSSH_OTTY=0` disables every Otty feature. Shell integration also honors `JSSH_OTTY_SHELL_INTEGRATION` and `terminal.otty_shell_integration`.
5. Never print, log, or commit secrets (AK, SK, TOTP seed). `jssh config` only says whether they exist. A config file that is group/world-readable must be rejected; mode `600` is required.
6. Writing `ssh.options` replaces the whole option table; it does not append to the defaults (defaults already include `ssh-rsa`).

## Silent OSC 133 inject

Full mechanism, timing, and Bash/zsh hook behavior: `docs/otty-ps1-inject.md`.

jssh sends hook scripts through pexpect `sendline`. Those commands must not appear as a dense blob in the terminal.

- Prelude: `ECHO_OFF_PRELUDE` (`stty -echo`, then erase that command line).
- Payload: `session_command()`, which ends with `ECHO_ON_SUFFIX` (`printf '\r\033[2K'; stty echo`). The trailing clear is required; without it the leftover prompt stacks on one line and the next prompt only appears after a manual Enter.
- If inject is cancelled or closed while echo is off, send only `ECHO_RESTORE` (`stty echo`), not the full suffix.
- `Osc133Injector.observe()` must ignore output while `_hiding` is true, or the 0.25s hide delay is cancelled and the payload never lands.
- Early keystrokes after the remote prompt abandon inject for that session.

Remote OSC 7 uses the JumpServer asset hostname, not the KoKo gateway host.

## Tab title and status

Otty's default tab label is `${title|folder}`: an empty program title plus cwd shows as a path such as `~/github/jssh`.

- On SSH/SFTP start, `OttySession` snapshots the current tab's `title` and `cwd` before setting a session title.
- Snapshot only when `$OTTY_PANE_ID` is set: `otty pane show --json --pane $OTTY_PANE_ID`, then `otty tab show --json --tab <tab_id>`. Without a pane id, skip the snapshot. Do not query the focused tab; that hits the wrong tab and breaks unit tests on a machine that has Otty in `PATH`.
- When parsing pane/tab JSON, prefer `data.tab_id` or `data.tabId` over `data.id`. Pane ids look like `p_*`; tab ids look like `t_*`.
- On close, emit OSC 7501 `clear`, then `local_restore_sequences`: OSC 7 for the local process cwd (`Path.cwd()`, not necessarily the snapshotted cwd), then either `osc_clear_title()` (empty OSC 0) if the captured title was empty, or `osc_both(saved_title)` if it was set.
- Do not use `otty tab rename` to restore a title. It pins the title, so later `cd` no longer updates the tab.
- Do not restore a folder basename such as `jssh` as the program title. That also pins the tab and is not Otty's default.
- Otty 1.6.5 can set or clear a tab badge (`error` is the warning triangle) but `tab show`, `tab list`, and `pane show` do not return the current badge. Do not invent badge snapshot or restore.

OSC 7501: `jssh sync` reports `working` then `done` or `error` (no `progress` unless there is a real 0–100 value). Connecting an asset is `working`; a password prompt is `blocked` with `kind=auth`; a remote shell is `idle`. Write to `/dev/tty` when stdout may be redirected.

## Ctrl-F SFTP sidecar

In Otty, Ctrl-F defaults to a right split (`otty_sftp_layout: split`) with `--size 40`, `--no-focus`, and `--json`. `tab` uses `otty tab new` and sets a `running` badge. jssh does not watch the SFTP process, so it does not later set `finished` or clear that badge. If the Otty CLI is missing, the command fails, or `JSSH_OTTY=0`, fall back to the clipboard.

When the remote cwd is known, a split pane may get `otty pane send-text` with a `cd`. This needs Settings → Advanced → IPC Allow Send Keys. Tab mode usually has no pane id, so it does not auto-`cd`. Failures are silent and must not break the sidecar. Warp, Terminal, iTerm, kitty, and WezTerm open a new tab; anything else copies the command.

## Tests

macOS system Python may be PEP 668. Use a venv, not `pip install` on the system interpreter:

```bash
python3 -m venv /tmp/jssh-venv
/tmp/jssh-venv/bin/pip install -e ".[dev]"
/tmp/jssh-venv/bin/pytest
```

A zsh inject test is skipped when `zsh` is missing. If you change inject timing tests, `ManualClock.fire` must drain chained timers. Helpers that extract `PS1` from terminal output must strip CSI (including `\x1b[2K`) and CR.

Do not commit unless the user asks. Do not add `.DS_Store`, `.pytest_cache`, `.audit/`, or secrets.
