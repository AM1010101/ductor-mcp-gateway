"""Application configuration and secret loading at the process edge."""

from __future__ import annotations

import ipaddress
import os
import secrets
import stat
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TOOL_NAMES = frozenset(
    {
        "ductor_agents_list",
        "ductor_agent_message",
        "ductor_agent_message_async",
        "ductor_tasks_create",
        "ductor_tasks_list",
        "ductor_tasks_resume",
        "ductor_tasks_cancel",
    }
)


class _SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerSettings(_SettingsModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8798, ge=1, le=65535)
    allow_non_loopback: bool = False
    allowed_hosts: list[str] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def prevent_accidental_public_bind(self) -> ServerSettings:
        try:
            is_loopback = ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            is_loopback = self.host.lower() == "localhost"
        if not is_loopback and not self.allow_non_loopback:
            raise ValueError("non-loopback server host requires allow_non_loopback=true")
        return self


class DuctorSettings(_SettingsModel):
    url: str = "http://127.0.0.1:8799"
    timeout_seconds: float = Field(default=900.0, gt=0, le=3_600)
    max_response_bytes: int = Field(default=1_048_576, ge=1_024, le=16_777_216)
    token_env: str = "DUCTOR_INTERAGENT_TOKEN"  # noqa: S105 - variable name, not a token
    token_file: Path | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("ductor.url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("ductor.url must not contain credentials, query, or fragment")
        return value.rstrip("/")

    @field_validator("token_env")
    @classmethod
    def validate_token_env_name(cls, value: str) -> str:
        if value and (not value.replace("_", "A").isalnum() or not value[0].isalpha()):
            raise ValueError("ductor.token_env must be empty or a valid environment variable name")
        return value


class AuthSettings(_SettingsModel):
    token_file: Path = Path("gateway.token")
    token_env: str = "DUCTOR_MCP_GATEWAY_TOKEN"  # noqa: S105 - variable name, not a token

    @field_validator("token_env")
    @classmethod
    def validate_env_name(cls, value: str) -> str:
        if not value or not value.replace("_", "A").isalnum() or not value[0].isalpha():
            raise ValueError("auth.token_env must be a valid environment variable name")
        return value


class PolicySettings(_SettingsModel):
    allowed_agents: list[str] = Field(default_factory=lambda: ["main"], min_length=1)
    allowed_tools: list[str] = Field(default_factory=lambda: sorted(TOOL_NAMES), min_length=1)

    @field_validator("allowed_agents")
    @classmethod
    def unique_agents(cls, value: list[str]) -> list[str]:
        if any(not item for item in value):
            raise ValueError("allowed_agents must not contain empty names")
        return list(dict.fromkeys(value))

    @field_validator("allowed_tools")
    @classmethod
    def known_tools(cls, value: list[str]) -> list[str]:
        unknown = set(value) - TOOL_NAMES
        if unknown:
            raise ValueError(f"unknown tools in policy: {', '.join(sorted(unknown))}")
        return list(dict.fromkeys(value))


class LimitSettings(_SettingsModel):
    max_body_bytes: int = Field(default=262_144, ge=1_024, le=16_777_216)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=100_000)
    max_sessions: int = Field(default=32, ge=1, le=10_000)
    session_idle_seconds: float = Field(default=1_800.0, gt=0, le=86_400)
    max_concurrent_requests: int = Field(default=100, ge=1, le=100_000)


class LoggingSettings(_SettingsModel):
    level: str = "INFO"

    @field_validator("level")
    @classmethod
    def validate_level(cls, value: str) -> str:
        normalised = value.upper()
        if normalised not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("logging.level is invalid")
        return normalised


class Settings(_SettingsModel):
    server: ServerSettings = Field(default_factory=ServerSettings)
    ductor: DuctorSettings = Field(default_factory=DuctorSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    policy: PolicySettings = Field(default_factory=PolicySettings)
    limits: LimitSettings = Field(default_factory=LimitSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)


_ENV_FIELDS: dict[str, tuple[str, str, str]] = {
    "DUCTOR_MCP_GATEWAY_HOST": ("server", "host", "str"),
    "DUCTOR_MCP_GATEWAY_PORT": ("server", "port", "int"),
    "DUCTOR_MCP_GATEWAY_ALLOW_NON_LOOPBACK": ("server", "allow_non_loopback", "bool"),
    "DUCTOR_MCP_GATEWAY_ALLOWED_HOSTS": ("server", "allowed_hosts", "list"),
    "DUCTOR_MCP_GATEWAY_ALLOWED_ORIGINS": ("server", "allowed_origins", "list"),
    "DUCTOR_MCP_GATEWAY_DUCTOR_URL": ("ductor", "url", "str"),
    "DUCTOR_MCP_GATEWAY_TIMEOUT_SECONDS": ("ductor", "timeout_seconds", "float"),
    "DUCTOR_MCP_GATEWAY_MAX_RESPONSE_BYTES": ("ductor", "max_response_bytes", "int"),
    "DUCTOR_MCP_GATEWAY_UPSTREAM_TOKEN_ENV": ("ductor", "token_env", "str"),
    "DUCTOR_MCP_GATEWAY_UPSTREAM_TOKEN_FILE": ("ductor", "token_file", "str"),
    "DUCTOR_MCP_GATEWAY_TOKEN_FILE": ("auth", "token_file", "str"),
    "DUCTOR_MCP_GATEWAY_TOKEN_ENV": ("auth", "token_env", "str"),
    "DUCTOR_MCP_GATEWAY_ALLOWED_AGENTS": ("policy", "allowed_agents", "list"),
    "DUCTOR_MCP_GATEWAY_ALLOWED_TOOLS": ("policy", "allowed_tools", "list"),
    "DUCTOR_MCP_GATEWAY_MAX_BODY_BYTES": ("limits", "max_body_bytes", "int"),
    "DUCTOR_MCP_GATEWAY_RATE_LIMIT_PER_MINUTE": (
        "limits",
        "rate_limit_per_minute",
        "int",
    ),
    "DUCTOR_MCP_GATEWAY_MAX_SESSIONS": ("limits", "max_sessions", "int"),
    "DUCTOR_MCP_GATEWAY_SESSION_IDLE_SECONDS": (
        "limits",
        "session_idle_seconds",
        "float",
    ),
    "DUCTOR_MCP_GATEWAY_MAX_CONCURRENT_REQUESTS": (
        "limits",
        "max_concurrent_requests",
        "int",
    ),
    "DUCTOR_MCP_GATEWAY_LOG_LEVEL": ("logging", "level", "str"),
}


def load_settings(
    config_path: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[Settings, Path]:
    """Load TOML settings with environment overrides and return the config directory."""
    env = os.environ if environ is None else environ
    selected_path = config_path
    if selected_path is None and env.get("DUCTOR_MCP_GATEWAY_CONFIG"):
        selected_path = Path(env["DUCTOR_MCP_GATEWAY_CONFIG"])

    raw: dict[str, Any] = {}
    config_dir = Path.cwd()
    if selected_path is not None:
        resolved = selected_path.expanduser().resolve()
        with resolved.open("rb") as handle:
            loaded = tomllib.load(handle)
        raw = dict(loaded)
        config_dir = resolved.parent

    for env_name, (section, key, value_type) in _ENV_FIELDS.items():
        if env_name not in env:
            continue
        raw.setdefault(section, {})[key] = _parse_env(env_name, env[env_name], value_type)

    return Settings.model_validate(raw), config_dir


def load_bearer_token(
    settings: AuthSettings,
    config_dir: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Load a bearer token from the configured environment variable or secure file."""
    env = os.environ if environ is None else environ
    candidate = env.get(settings.token_env, "").strip()
    if candidate:
        _validate_token(candidate)
        return candidate

    path = settings.token_file.expanduser()
    if not path.is_absolute():
        path = config_dir / path
    token = _read_secure_token_file(path)
    _validate_token(token)
    return token


def load_upstream_token(
    settings: DuctorSettings,
    config_dir: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Load Ductor's optional internal-API bearer token without exposing it."""
    env = os.environ if environ is None else environ
    if settings.token_env:
        candidate = env.get(settings.token_env, "").strip()
        if candidate:
            _validate_token(candidate)
            return candidate
    if settings.token_file is None:
        return None
    path = settings.token_file.expanduser()
    if not path.is_absolute():
        path = config_dir / path
    token = _read_secure_token_file(path)
    _validate_token(token)
    return token


def _read_secure_token_file(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError(f"unable to open MCP token file: {path}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise PermissionError(f"MCP token path is not a regular file: {path}")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise PermissionError(f"MCP token file must have mode 0600: {path}")
        raw = os.read(descriptor, 4_097)
    finally:
        os.close(descriptor)
    if len(raw) > 4_096:
        raise RuntimeError("MCP token file is unexpectedly large")
    try:
        token = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise RuntimeError("MCP token file is not valid UTF-8") from exc
    return token


def generate_token_file(path: Path, *, force: bool = False) -> Path:
    """Generate a 384-bit bearer token in a mode-0600 file without printing it."""
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    flags |= os.O_TRUNC if force else os.O_EXCL
    descriptor = os.open(resolved, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, f"{secrets.token_urlsafe(48)}\n".encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return resolved


def _validate_token(token: str) -> None:
    if not 32 <= len(token) <= 1_024 or any(character.isspace() for character in token):
        raise RuntimeError("MCP bearer token must be 32-1024 non-whitespace characters")


def _parse_env(name: str, raw: str, value_type: str) -> object:
    if value_type == "str":
        return raw
    if value_type == "int":
        return int(raw)
    if value_type == "float":
        return float(raw)
    if value_type == "list":
        return [part.strip() for part in raw.split(",") if part.strip()]
    if value_type == "bool":
        normalised = raw.strip().lower()
        if normalised in {"1", "true", "yes", "on"}:
            return True
        if normalised in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"{name} must be a boolean")
    raise AssertionError(f"unhandled environment value type: {value_type}")
