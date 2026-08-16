"""Profile-based configuration — TOML + env var overlay."""

import os
import stat
import tomllib
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, SecretStr

from marklogic_tool.core import secrets
from marklogic_tool.core.exceptions import ConfigurationError


class ProfileSettings(BaseModel):
    """Settings for one MarkLogic server profile.

    `rest_port` has no default. The REST instance addresses a different content database. A
    default lets a command count the wrong corpus.
    """

    host: str
    port: int = 8000
    manage_port: int = 8002
    rest_port: int | None = None
    username: str
    password: SecretStr
    auth_method: str = "digest"
    timeout: int = 30
    default_group: str = "Default"
    identities: dict[str, str] = {}

    def __repr__(self) -> str:
        return f"ProfileSettings(host={self.host!r}, port={self.port}, username={self.username!r})"


class AppConfig(BaseModel):
    """Top-level application config parsed from TOML."""

    default_profile: str = "default"
    profiles: dict[str, ProfileSettings] = {}


def _default_config_path() -> Path:
    return Path.home() / ".config" / "marklogic-tool" / "config.toml"


def config_path() -> Path:
    """The path `config add` writes to: the override if set, else the default."""
    if "MARKLOGIC_CONFIG" in os.environ:
        return Path(os.environ["MARKLOGIC_CONFIG"])
    return _default_config_path()


def _find_config_file() -> Path | None:
    if "MARKLOGIC_CONFIG" in os.environ:
        return Path(os.environ["MARKLOGIC_CONFIG"])
    default = _default_config_path()
    if default.exists():
        return default
    return None


def _check_permissions(path: Path) -> None:
    """Warn if config file is world-readable."""
    log = structlog.get_logger()
    try:
        mode = path.stat().st_mode
        if mode & stat.S_IROTH:
            log.warning(
                "Config file is world-readable, consider chmod 600",
                path=str(path),
            )
    except OSError:
        pass


def _load_toml(path: Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _parse_config(data: dict[str, Any]) -> AppConfig:
    """Parse raw TOML dict into AppConfig."""
    default_profile = data.get("default_profile", "default")
    profiles: dict[str, ProfileSettings] = {}
    for name, section in data.get("profiles", {}).items():
        profiles[name] = ProfileSettings(**section)
    return AppConfig(default_profile=default_profile, profiles=profiles)


def _apply_env_overlay(profile: ProfileSettings) -> ProfileSettings:
    """Override profile fields with ML_* env vars if set."""
    overrides: dict[str, Any] = {}
    if "ML_HOST" in os.environ:
        overrides["host"] = os.environ["ML_HOST"]
    if "ML_PORT" in os.environ:
        overrides["port"] = int(os.environ["ML_PORT"])
    if "ML_REST_PORT" in os.environ:
        overrides["rest_port"] = int(os.environ["ML_REST_PORT"])
    if "ML_MANAGE_PORT" in os.environ:
        overrides["manage_port"] = int(os.environ["ML_MANAGE_PORT"])
    if "ML_USERNAME" in os.environ:
        overrides["username"] = os.environ["ML_USERNAME"]
    if "ML_PASSWORD" in os.environ:
        overrides["password"] = SecretStr(os.environ["ML_PASSWORD"])
    if overrides:
        return profile.model_copy(update=overrides)
    return profile


def load_app_config(config_path: Path | None = None) -> AppConfig:
    """Load and return the full application config without resolving a profile."""
    path = config_path or _find_config_file()
    if path is None:
        raise ConfigurationError(
            f"No config file found. Create {_default_config_path()} "
            "or set MARKLOGIC_CONFIG env var."
        )
    if not path.exists():
        raise ConfigurationError(f"Config file not found: {path}")
    _check_permissions(path)
    data = _load_toml(path)
    return _parse_config(data)


def resolve_profile(
    *, profile_name: str | None = None, config_path: Path | None = None
) -> ProfileSettings:
    """Resolve and return the active profile settings.

    Precedence for profile name: profile_name arg > ML_PROFILE env > default_profile key.
    """
    path = config_path or _find_config_file()
    if path is None:
        raise ConfigurationError(
            f"No config file found. Create {_default_config_path()} "
            "or set MARKLOGIC_CONFIG env var."
        )
    if not path.exists():
        raise ConfigurationError(f"Config file not found: {path}")

    _check_permissions(path)

    data = _load_toml(path)
    app_config = _parse_config(data)

    name = profile_name or os.environ.get("ML_PROFILE") or app_config.default_profile
    if name not in app_config.profiles:
        available = ", ".join(sorted(app_config.profiles.keys()))
        raise ConfigurationError(
            f"Unknown profile '{name}'. Available profiles: {available}"
        )

    profile = app_config.profiles[name]
    return _apply_env_overlay(_resolve_password_reference(profile))


def _resolve_password_reference(profile: ProfileSettings) -> ProfileSettings:
    """Resolve a `password` that carries a secret reference.

    The tool resolves it here, once. A literal password stays as it is, so existing config
    files keep working.
    """
    raw = profile.password.get_secret_value()
    if not raw.startswith(secrets.SCHEMES):
        return profile
    return profile.model_copy(
        update={"password": secrets.resolve(raw, identities=profile.identities)}
    )
