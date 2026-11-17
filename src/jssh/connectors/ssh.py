from __future__ import annotations

import shutil

from jssh.config import Config
from jssh.connectors.session import KokoLogin, run_gateway
from jssh.errors import ConfigError
from jssh.models.asset import Account, Asset
from jssh.services.path_resolver import ssh_argv


def require_ssh() -> str:
    path = shutil.which("ssh")
    if not path:
        raise ConfigError("ssh is not on PATH")
    return path


def run_ssh(cfg: Config, asset: Asset | None = None, account: Account | None = None) -> int:
    require_ssh()
    argv = ssh_argv(cfg)
    login = None
    if asset and account:
        login = KokoLogin(
            hostname=asset.hostname,
            account_name=account.name,
            account_username=account.username,
        )
        print(
            f"Connecting {asset.hostname} / {account.name} through "
            f"{cfg.ssh_username}@{cfg.ssh_host}:{cfg.ssh_port}.",
            flush=True,
        )
    elif asset:
        print(
            f"Connecting through JumpServer KoKo as {cfg.ssh_username}@{cfg.ssh_host}:{cfg.ssh_port}.",
            flush=True,
        )
    return run_gateway(cfg, argv, login)
