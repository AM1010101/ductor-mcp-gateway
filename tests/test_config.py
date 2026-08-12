from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from ductor_mcp_gateway.cli import main
from ductor_mcp_gateway.config import (
    AuthSettings,
    DuctorSettings,
    generate_token_file,
    load_bearer_token,
    load_settings,
    load_upstream_token,
)


def test_environment_overrides_configuration(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """
[policy]
allowed_agents = ["main"]
[limits]
rate_limit_per_minute = 10
""",
        encoding="utf-8",
    )
    settings, config_dir = load_settings(
        config,
        environ={
            "DUCTOR_MCP_GATEWAY_ALLOWED_AGENTS": "main,coder",
            "DUCTOR_MCP_GATEWAY_RATE_LIMIT_PER_MINUTE": "25",
        },
    )
    assert config_dir == tmp_path
    assert settings.policy.allowed_agents == ["main", "coder"]
    assert settings.limits.rate_limit_per_minute == 25


def test_unknown_config_keys_fail_closed(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("unexpected = true\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings(config, environ={})


def test_non_loopback_listener_requires_explicit_switch() -> None:
    with pytest.raises(ValidationError, match="allow_non_loopback"):
        load_settings(environ={"DUCTOR_MCP_GATEWAY_HOST": "0.0.0.0"})  # noqa: S104


def test_token_file_permissions_and_environment_precedence(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("f" * 40, encoding="utf-8")
    token_file.chmod(0o600)
    auth = AuthSettings(token_file=token_file)
    assert load_bearer_token(auth, tmp_path, environ={}) == "f" * 40
    assert load_bearer_token(auth, tmp_path, environ={auth.token_env: "e" * 40}) == "e" * 40

    token_file.chmod(0o640)
    with pytest.raises(PermissionError, match="0600"):
        load_bearer_token(auth, tmp_path, environ={})


def test_optional_upstream_token_uses_explicit_sources(tmp_path: Path) -> None:
    settings = DuctorSettings()
    assert load_upstream_token(settings, tmp_path, environ={}) is None
    assert (
        load_upstream_token(settings, tmp_path, environ={settings.token_env: "u" * 40}) == "u" * 40
    )

    token_file = tmp_path / "upstream.token"
    token_file.write_text("f" * 40, encoding="utf-8")
    token_file.chmod(0o600)
    file_settings = DuctorSettings(token_env="", token_file=token_file)
    assert load_upstream_token(file_settings, tmp_path, environ={}) == "f" * 40


def test_token_generation_does_not_print_secret(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "secret.token"
    assert main(["generate-token", "--output", os.fspath(path)]) == 0
    output = capsys.readouterr().out
    token = path.read_text(encoding="utf-8").strip()
    assert token not in output
    assert len(token) >= 32
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        generate_token_file(path)
