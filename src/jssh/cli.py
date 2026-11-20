from __future__ import annotations

import argparse
import json
import sys
from getpass import getpass

from jssh import __version__
from jssh.cache import clear_cache, load_assets, load_recent
from jssh.complete import account_tokens, host_tokens
from jssh.config import Config, load_config, public_config
from jssh.connectors.sftp import run_sftp
from jssh.connectors.ssh import run_ssh
from jssh.diagnostics import doctor
from jssh.errors import Cancelled, JsshError
from jssh.models.asset import Account, Asset
from jssh.secrets import load_totp_secret, store_keychain, store_totp_file
from jssh.totp import totp_code
from jssh.services.asset_service import (
    account_from_name,
    accounts_for,
    load_or_sync,
    remember_use,
    resolve_query,
    sync_assets,
)
from jssh.services.path_resolver import copy_command, resolve_sftp_path, sftp_argv, ssh_argv
from jssh.ui.fzf import pick_account, pick_action, pick_asset


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command is None:
            return _interactive(_cfg(args), None, None)
        return args.func(args)
    except KeyboardInterrupt:
        print()
        return 130
    except Cancelled:
        return 130
    except JsshError as exc:
        print(f"jssh: {exc}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jssh",
        description="Search JumpServer 2.4.1 granted assets and open native SSH or SFTP.",
    )
    parser.add_argument("--version", action="version", version=f"jssh {__version__}")
    parser.add_argument("--config", help="Path to config.yaml")
    sub = parser.add_subparsers(dest="command")

    interactive = sub.add_parser("connect", help="Interactive asset selector (default)")
    interactive.set_defaults(func=lambda args: _interactive(_cfg(args), None, None))

    ssh = sub.add_parser("ssh", help="Search and open SSH through KoKo")
    ssh.add_argument("query", nargs="?", help="Asset id, hostname, IP, or node text")
    ssh.add_argument("--account", help="System user name")
    ssh.add_argument("--last", action="store_true", help="Use the most recent host and account")
    ssh.set_defaults(func=lambda args: _cmd_connect_action(args, "ssh"))

    sftp = sub.add_parser("sftp", help="Search and open interactive SFTP")
    sftp.add_argument("query", nargs="?", help="Asset id, hostname, IP, or node text")
    sftp.add_argument("--account", help="System user name")
    sftp.add_argument("--last", action="store_true", help="Use the most recent host and account")
    sftp.set_defaults(func=lambda args: _cmd_connect_action(args, "sftp"))

    sync = sub.add_parser("sync", help="Refresh granted assets from the API")
    sync.set_defaults(func=_cmd_sync)

    listing = sub.add_parser("list", help="Print cached granted assets")
    listing.set_defaults(func=_cmd_list)

    complete = sub.add_parser("complete", help="Print cache tokens for shell completion")
    complete.add_argument("kind", choices=("host", "account"))
    complete.add_argument("query", nargs="?", help="Hostname when kind is account")
    complete.set_defaults(func=_cmd_complete)
    recent = sub.add_parser("recent", help="Print recent connections")
    recent.set_defaults(func=_cmd_recent)

    cache = sub.add_parser("cache", help="Cache operations")
    cache_sub = cache.add_subparsers(dest="cache_command", required=True)
    cache_clear = cache_sub.add_parser("clear", help="Delete asset and recent cache")
    cache_clear.set_defaults(func=_cmd_cache_clear)

    doc = sub.add_parser("doctor", help="Check config, binaries, and optional API probe")
    doc.add_argument("--probe", action="store_true", help="Call read-only profile and asset endpoints")
    doc.set_defaults(func=_cmd_doctor)

    probe = sub.add_parser("api-probe", help="Alias for jssh doctor --probe")
    probe.set_defaults(func=_cmd_probe)

    cfg = sub.add_parser("config", help="Show non-secret config")
    cfg.set_defaults(func=_cmd_config)

    secrets = sub.add_parser("secrets", help="Store AK/SK and TOTP on this Mac")
    secrets_sub = secrets.add_subparsers(dest="secrets_command", required=True)
    keychain = secrets_sub.add_parser("keychain-set", help="Read secrets from hidden prompts and store in Keychain")
    keychain.set_defaults(func=_cmd_keychain_set)
    totp_file = secrets_sub.add_parser("totp-file", help="Store TOTP seed in ~/.config/jssh/totp mode 0600")
    totp_file.set_defaults(func=_cmd_totp_file)

    totp = sub.add_parser("totp", help="Print the current TOTP code")
    totp.set_defaults(func=_cmd_totp)
    return parser


def _cfg(args: argparse.Namespace) -> Config:
    return load_config(getattr(args, "config", None))


def _cmd_sync(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    profile, assets = sync_assets(cfg)
    print(f"synced {len(assets)} granted assets for {profile.username}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    assets, stale = load_or_sync(cfg)
    if stale:
        print("warning: using stale cache. Grants may have changed.", file=sys.stderr)
    if not assets:
        print("no assets")
        return 0
    print(f"{'HOSTNAME':<32} {'IP':<16} NODE")
    for asset in assets:
        print(f"{asset.hostname:<32} {asset.ip:<16} {asset.node_label}")
    return 0


def _cmd_recent(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    items = load_recent(cfg)
    if not items:
        print("no recent connections")
        return 0
    for item in items:
        print(f"{item.ts}  {item.action:8}  {item.hostname}  {item.account_name}")
    return 0


def _cmd_complete(args: argparse.Namespace) -> int:
    try:
        cfg = _cfg(args)
    except JsshError:
        return 0
    cached = load_assets(cfg)
    assets = cached[0] if cached else []
    recent = load_recent(cfg)
    if args.kind == "host":
        tokens = host_tokens(assets, recent)
    else:
        tokens = account_tokens(assets, recent, args.query)
    for token in tokens:
        print(token)
    return 0


def _cmd_cache_clear(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    clear_cache(cfg)
    print(f"cleared {cfg.cache_dir}")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    try:
        cfg = _cfg(args)
    except JsshError as exc:
        print(f"jssh: {exc}", file=sys.stderr)
        cfg = None
    return doctor(cfg, probe=args.probe)


def _cmd_probe(args: argparse.Namespace) -> int:
    args.probe = True
    return _cmd_doctor(args)


def _cmd_config(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    print(json.dumps(public_config(cfg), indent=2, ensure_ascii=False))
    return 0


def _cmd_keychain_set(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    if not cfg.keychain_service:
        raise JsshError("Set auth.keychain_service in config.yaml first")
    key_id = getpass("Access Key ID (input hidden): ")
    secret = getpass("Access Key Secret (input hidden): ")
    totp = getpass("TOTP secret base32 (input hidden, empty to skip): ")
    store_keychain(cfg.keychain_service, "access_key", key_id.strip())
    store_keychain(cfg.keychain_service, "secret_key", secret.strip())
    if totp.strip():
        store_keychain(cfg.keychain_service, "totp_secret", totp.strip())
        print(f"stored access_key, secret_key, and totp_secret in keychain service {cfg.keychain_service}")
    else:
        print(f"stored access_key and secret_key in keychain service {cfg.keychain_service}")
    return 0


def _cmd_totp_file(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    secret = getpass("TOTP secret base32 (input hidden): ")
    if not secret.strip():
        raise JsshError("TOTP secret was empty")
    path = store_totp_file(cfg, secret.strip())
    print(f"stored TOTP seed in {path} mode 0600")
    return 0

def _cmd_totp(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    secret = load_totp_secret(cfg)
    if secret is None:
        raise JsshError(
            f"TOTP secret is missing. Set {cfg.totp_env}, Keychain totp_secret, or auth.onepassword_totp."
        )
    print(
        totp_code(
            secret.secret,
            digits=cfg.totp_digits,
            period=cfg.totp_period,
            algorithm=cfg.totp_algorithm,
        )
    )
    return 0


def _interactive(cfg: Config, query: str | None, account_name: str | None) -> int:
    return _connect(cfg, None, query, account_name)


def _cmd_connect_action(args: argparse.Namespace, action: str) -> int:
    cfg = _cfg(args)
    if args.last:
        items = load_recent(cfg)
        if not items:
            raise JsshError("No recent host. Connect with jssh first, then jssh sftp --last")
        entry = items[0]
        return _connect(cfg, action, entry.hostname, entry.account_name or None)
    return _connect(cfg, action, args.query, args.account)


def _connect(cfg: Config, action: str | None, query: str | None, account_name: str | None) -> int:
    resume_picker = action is None or not query
    while True:
        assets, stale = load_or_sync(cfg)
        if stale:
            print("warning: using stale cache. Grants may have changed.", file=sys.stderr)
        matched = resolve_query(assets, query)
        if not matched:
            raise JsshError(f"No granted asset matches {query!r}")
        if len(matched) == 1 and query and (matched[0].id == query or matched[0].hostname == query):
            asset = matched[0]
        else:
            asset = pick_asset(matched, load_recent(cfg))
        accounts = accounts_for(cfg, asset)
        account = account_from_name(accounts, account_name) if account_name else pick_account(asset, accounts)
        while True:
            chosen = action or pick_action(asset, account)
            action = None
            if chosen == "back":
                query = None
                account_name = None
                break
            code = _run_action(cfg, chosen, asset, account)
            if not sys.stdin.isatty():
                return code
            if chosen in {"copy-sftp", "copy-ssh"}:
                continue
            if chosen in {"ssh", "sftp"}:
                if not resume_picker:
                    return code
                query = None
                account_name = None
                break
            return code


def _run_action(cfg: Config, action: str, asset: Asset, account: Account) -> int:
    if action in {"sftp", "copy-sftp"}:
        target = resolve_sftp_path(cfg, asset, account)
        argv = sftp_argv(cfg, target)
        if action == "copy-sftp":
            print(copy_command(argv))
            return 0
        print(f"SFTP path {target.path} ({target.source})", flush=True)
        remember_use(cfg, asset, account, "sftp")
        return run_sftp(cfg, target)
    if action in {"ssh", "copy-ssh"}:
        argv = ssh_argv(cfg)
        if action == "copy-ssh":
            print(copy_command(argv))
            return 0
        remember_use(cfg, asset, account, "ssh")
        return run_ssh(cfg, asset, account)
    raise JsshError(f"Unknown action {action}")
