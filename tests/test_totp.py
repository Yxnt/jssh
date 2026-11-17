from __future__ import annotations

from pathlib import Path

import pytest

from jssh.cli import main
from jssh.connectors.session import _MfaInjector
from jssh.errors import AuthError
from jssh.totp import totp_code

RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def test_rfc6238_sha1_eight_digits() -> None:
    assert totp_code(RFC_SECRET, timestamp=59, digits=8, period=30, algorithm="sha1") == "94287082"


def test_rfc6238_sha1_six_digits() -> None:
    assert totp_code(RFC_SECRET, timestamp=59, digits=6, period=30, algorithm="sha1") == "287082"


def test_invalid_secret_message_hides_value() -> None:
    secret = "not-a-real-secret-value"
    with pytest.raises(AuthError) as exc:
        totp_code(secret)
    assert secret not in str(exc.value)
    assert "base32" in str(exc.value)


def test_mfa_injector_sends_code_once() -> None:
    class Child:
        def __init__(self) -> None:
            self.sent: list[object] = []

        def sendline(self, value: str) -> None:
            self.sent.append(value)

        def send(self, value: bytes) -> None:
            self.sent.append(value)

    child = Child()
    injector = _MfaInjector(child, lambda: "287082", ("[MFA auth]:",))
    injector.filter_output("Please enter 6 digits.\n")
    assert child.sent == []
    injector.filter_output("[MFA auth]: ")
    assert child.sent == ["287082"]
    injector.filter_output("[MFA auth]: ")
    assert child.sent == ["287082"]


def test_mfa_injector_bytes_sends_cr() -> None:
    class Child:
        def __init__(self) -> None:
            self.sent: list[object] = []

        def send(self, value: bytes) -> None:
            self.sent.append(value)

    child = Child()
    injector = _MfaInjector(child, lambda: "287082", ("[MFA auth]:",))
    injector.filter_output(b"[MFA auth]: ")
    assert child.sent == [b"287082\r"]


def test_totp_cli_prints_code_not_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "jumpserver:",
                "  api_url: https://jumpserver.example.com",
                "  ssh_host: jumpserver.example.com",
                "  ssh_port: 2222",
                "  ssh_username: jumpserver_user",
                "auth:",
                "  access_key_env: JMS_AK",
                "  secret_key_env: JMS_SK",
                "  totp_env: JMS_TOTP_SECRET",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("JMS_TOTP_SECRET", RFC_SECRET)
    monkeypatch.setenv("JSSH_CACHE_DIR", str(tmp_path / "cache"))
    assert main(["--config", str(cfg_path), "totp"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.isdigit()
    assert len(out) == 6
    assert RFC_SECRET not in out


def test_totp_from_file_not_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "jumpserver:",
                "  api_url: https://jumpserver.example.com",
                "  ssh_host: jumpserver.example.com",
                "  ssh_port: 2222",
                "  ssh_username: jumpserver_user",
                "auth:",
                "  totp_env: JMS_TOTP_SECRET",
            ]
        ),
        encoding="utf-8",
    )
    totp_path = tmp_path / "totp"
    totp_path.write_text(RFC_SECRET + "\n", encoding="utf-8")
    totp_path.chmod(0o600)
    monkeypatch.delenv("JMS_TOTP_SECRET", raising=False)
    monkeypatch.setenv("JSSH_CACHE_DIR", str(tmp_path / "cache"))
    assert main(["--config", str(cfg_path), "totp"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.isdigit()
    assert RFC_SECRET not in out
