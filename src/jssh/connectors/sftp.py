from __future__ import annotations

import shutil

from jssh.config import Config
from jssh.connectors.session import run_gateway
from jssh.errors import ConfigError
from jssh.services.path_resolver import SftpTarget, sftp_argv


def require_sftp() -> str:
    path = shutil.which("sftp")
    if not path:
        raise ConfigError("sftp is not on PATH")
    return path


def run_sftp(cfg: Config, target: SftpTarget) -> int:
    require_sftp()
    return run_gateway(cfg, sftp_argv(cfg, target))
