from __future__ import annotations

import os
import shutil
from typing import Any

from jssh.api.assets import MY_ASSETS_TREE, MY_NODES_WITH_ASSETS, PROFILE_PATH
from jssh.api.client import Client
from jssh.config import Config, public_config
from jssh.errors import ApiError, AuthError, ConfigError, JsshError
from jssh.secrets import credential_presence, load_access_key, load_totp_secret
from jssh.ui.fzf import require_fzf
from jssh.connectors.otty import OttyOn
from jssh.connectors import otty as otty_term


def doctor(cfg: Config | None, *, probe: bool = False) -> int:
    ok = True
    if cfg is None:
        print("config: missing")
        ok = False
    else:
        path = cfg.config_path
        mode = oct(path.stat().st_mode & 0o777) if path.is_file() else "missing"
        print(f"config: {path} mode={mode}")
        if path.is_file() and (path.stat().st_mode & 0o077):
            print("warning: config file is group/world readable. chmod 600 is recommended.")
        for key, value in public_config(cfg).items():
            print(f"  {key}: {value}")
        present = credential_presence(cfg)
        print("credentials:")
        for name, found in present.items():
            print(f"  {name}: {'set' if found else 'not set'}")
        try:
            key = load_access_key(cfg)
            print(f"  access_key loaded from {key.source} (value not shown)")
        except JsshError as exc:
            print(f"  access_key: {exc}")
            ok = False
        totp = load_totp_secret(cfg)
        if totp:
            print(f"  totp loaded from {totp.source} (value not shown)")
        else:
            print("  totp: not set")
        if cfg.ssh_identity_file:
            if cfg.ssh_identity_file.is_file():
                print(f"  ssh_identity_file: {cfg.ssh_identity_file}")
            else:
                print(f"  ssh_identity_file missing: {cfg.ssh_identity_file}")
                ok = False

    for binary in ("fzf", "ssh", "sftp"):
        found = shutil.which(binary)
        print(f"{binary}: {found or 'missing'}")
        if not found:
            ok = False
    print(f"term_program: {os.environ.get('TERM_PROGRAM') or 'unset'}")
    print(f"otty_cli: {otty_term.find_otty_cli() or 'missing'}")
    print(f"otty_pane: {os.environ.get('OTTY_PANE_ID') or 'unset'}")
    plan = otty_term.otty_split_plan()
    if isinstance(plan, OttyOn):
        print("otty_sftp_split: on")
    else:
        print(f"otty_sftp_split: {plan.reason}")
    if cfg and shutil.which("fzf"):
        try:
            require_fzf()
        except ConfigError as exc:
            print(exc)
            ok = False

    if probe:
        if cfg is None:
            print("probe: skipped, no config")
            return 1
        ok = _probe(cfg) and ok
    return 0 if ok else 1


def _probe(cfg: Config) -> bool:
    try:
        key = load_access_key(cfg)
    except JsshError as exc:
        print(f"probe: {exc}")
        return False
    try:
        with Client(cfg, key) as client:
            print(f"probe GET {PROFILE_PATH}")
            profile = client.get_json(PROFILE_PATH)
            _report_shape("profile", profile)
            if isinstance(profile, dict):
                print(f"  username present: {'username' in profile}")
                print(f"  id present: {'id' in profile}")
            print(f"probe GET {MY_NODES_WITH_ASSETS}")
            tree = client.get_json(MY_NODES_WITH_ASSETS)
            _report_shape("nodes-with-assets", tree)
            print(f"probe GET {MY_ASSETS_TREE}")
            assets = client.get_json(MY_ASSETS_TREE)
            _report_shape("users/assets", assets)
    except AuthError as exc:
        print(f"probe auth failed: {exc}")
        return False
    except ApiError as exc:
        print(f"probe API error: {exc}")
        return False
    return True


def _report_shape(label: str, payload: Any) -> None:
    if payload is None:
        print(f"  {label}: empty body")
        return
    if isinstance(payload, list):
        print(f"  {label}: list len={len(payload)}")
        if payload and isinstance(payload[0], dict):
            print(f"  first keys: {sorted(payload[0])[:12]}")
            meta = payload[0].get("meta")
            if isinstance(meta, dict):
                print(f"  first meta.type: {meta.get('type')}")
        return
    if isinstance(payload, dict):
        print(f"  {label}: object keys={sorted(payload)[:12]}")
        if isinstance(payload.get("results"), list):
            print(f"  results len={len(payload['results'])}")
        return
    print(f"  {label}: {type(payload).__name__}")

