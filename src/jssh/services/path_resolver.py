from __future__ import annotations

from dataclasses import dataclass

from jssh.config import Config
from jssh.models.asset import Account, Asset


@dataclass(frozen=True)
class SftpTarget:
    path: str
    source: str
    argv_host: str


def sftp_folder(name: str) -> str:
    return name.replace("/", "_")


def join_sftp(*parts: str) -> str:
    cleaned = [sftp_folder(part.strip("/")) for part in parts if part and part.strip("/")]
    return "/" + "/".join(cleaned) + "/"


def resolve_sftp_path(cfg: Config, asset: Asset, account: Account) -> SftpTarget:
    override = _override(cfg, asset, account)
    if override:
        path = override if override.endswith("/") else override + "/"
        if not path.startswith("/"):
            path = "/" + path
        return SftpTarget(path=path, source="config override", argv_host=_argv(cfg, path))

    node_parts = _node_parts(cfg, asset)
    path = join_sftp(*node_parts, asset.hostname, account.sftp_folder)
    return SftpTarget(
        path=path,
        source="koko v2.4.1 node.Value + hostname + SystemUser.name",
        argv_host=_argv(cfg, path),
    )


def ssh_auth_args(cfg: Config) -> list[str]:
    args: list[str] = []
    if cfg.ssh_identity_file:
        args.extend(
            [
                "-i",
                str(cfg.ssh_identity_file),
                "-o",
                "IdentitiesOnly=yes",
            ]
        )
    for option in cfg.ssh_options:
        args.extend(["-o", option])
    return args


def sftp_argv(cfg: Config, target: SftpTarget) -> list[str]:
    return ["sftp", "-P", str(cfg.ssh_port), *ssh_auth_args(cfg), target.argv_host]


def ssh_argv(cfg: Config) -> list[str]:
    return [
        "ssh",
        "-p",
        str(cfg.ssh_port),
        *ssh_auth_args(cfg),
        "-t",
        f"{cfg.ssh_username}@{cfg.ssh_host}",
    ]


def copy_command(argv: list[str]) -> str:
    return " ".join(_shell_single(part) for part in argv)


def _override(cfg: Config, asset: Asset, account: Account) -> str | None:
    keys = [
        f"{asset.id}:{account.id}",
        f"{asset.id}:{account.name}",
        f"{asset.hostname}:{account.name}",
        asset.id,
        asset.hostname,
    ]
    for key in keys:
        value = cfg.path_overrides.get(key)
        if value:
            if "{account}" in value:
                return value.replace("{account}", account.sftp_folder)
            return value
    return None


def _node_parts(cfg: Config, asset: Asset) -> list[str]:
    if asset.node and asset.node.name:
        return [part for part in asset.node.name.split("/") if part]
    if asset.node and asset.node.value:
        return [asset.node.value]
    if cfg.sftp_default_root:
        return [cfg.sftp_default_root]
    return []


def _argv(cfg: Config, path: str) -> str:
    return f"{cfg.ssh_username}@{cfg.ssh_host}:{path}"


def _shell_single(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"
