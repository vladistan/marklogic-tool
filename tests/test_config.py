"""Tests for configuration system."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from marklogic_tool.cli import app
from marklogic_tool.core.config import (
    AppConfig,
    ProfileSettings,
    load_app_config,
    resolve_profile,
)
from marklogic_tool.core.exceptions import ConfigurationError

runner = CliRunner()


def test_profile_settings_required_fields():
    p = ProfileSettings(host="localhost", username="admin", password="secret")
    assert p.host == "localhost"
    assert p.port == 8000
    assert p.username == "admin"
    assert p.password.get_secret_value() == "secret"
    assert p.auth_method == "digest"
    assert p.timeout == 30


def test_profile_settings_repr_no_password():
    p = ProfileSettings(host="localhost", username="admin", password="secret")
    r = repr(p)
    assert "secret" not in r
    assert "localhost" in r


def test_resolve_profile_from_toml(sample_config_path):
    profile = resolve_profile(profile_name="dev", config_path=sample_config_path)
    assert profile.host == "ml-dev.example.com"
    assert profile.port == 8000
    assert profile.username == "admin"
    assert profile.password.get_secret_value() == "dev-secret"


def test_resolve_profile_staging(sample_config_path):
    profile = resolve_profile(profile_name="staging", config_path=sample_config_path)
    assert profile.host == "ml-staging.example.com"
    assert profile.port == 8100
    assert profile.auth_method == "basic"


def test_resolve_default_profile(sample_config_path):
    profile = resolve_profile(config_path=sample_config_path)
    assert profile.host == "ml-dev.example.com"


def test_resolve_profile_env_var_precedence(sample_config_path):
    with patch.dict(os.environ, {"ML_PROFILE": "staging"}):
        profile = resolve_profile(config_path=sample_config_path)
        assert profile.host == "ml-staging.example.com"


def test_resolve_profile_cli_overrides_env(sample_config_path):
    with patch.dict(os.environ, {"ML_PROFILE": "staging"}):
        profile = resolve_profile(profile_name="dev", config_path=sample_config_path)
        assert profile.host == "ml-dev.example.com"


def test_env_var_overlay(sample_config_path):
    with patch.dict(os.environ, {"ML_HOST": "override.example.com", "ML_PORT": "9999"}):
        profile = resolve_profile(profile_name="dev", config_path=sample_config_path)
        assert profile.host == "override.example.com"
        assert profile.port == 9999


def test_unknown_profile_lists_available(sample_config_path):
    with pytest.raises(ConfigurationError, match="Unknown profile 'nonexistent'"):
        resolve_profile(profile_name="nonexistent", config_path=sample_config_path)


def test_missing_config_file():
    with pytest.raises(ConfigurationError, match="not found"):
        resolve_profile(config_path=Path("/nonexistent/config.toml"))


def test_no_config_file_at_all():
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("marklogic_tool.core.config._find_config_file", return_value=None),
        pytest.raises(ConfigurationError, match="No config file found"),
    ):
        resolve_profile()


def test_profile_manage_port_default():
    p = ProfileSettings(host="localhost", username="admin", password="secret")
    assert p.manage_port == 8002


def test_profile_manage_port_custom(sample_config_path):
    profile = resolve_profile(profile_name="staging", config_path=sample_config_path)
    assert profile.manage_port == 8003


def test_profile_default_group_default():
    p = ProfileSettings(host="localhost", username="admin", password="secret")
    assert p.default_group == "Default"


def test_profile_default_group_custom(sample_config_path):
    profile = resolve_profile(profile_name="staging", config_path=sample_config_path)
    assert profile.default_group == "Staging"


def test_profile_manage_port_from_toml(sample_config_path):
    profile = resolve_profile(profile_name="dev", config_path=sample_config_path)
    assert profile.manage_port == 8002
    assert profile.default_group == "Default"


def test_load_app_config_returns_all_profiles(sample_config_path):
    app_config = load_app_config(config_path=sample_config_path)
    assert "dev" in app_config.profiles
    assert "staging" in app_config.profiles
    assert app_config.default_profile == "dev"


def test_load_app_config_missing_file():
    with pytest.raises(ConfigurationError, match="not found"):
        load_app_config(config_path=Path("/nonexistent/config.toml"))


@patch("marklogic_tool.commands.config_cmd.load_app_config")
def test_config_list_command(mock_load):
    mock_load.return_value = AppConfig(
        default_profile="dev",
        profiles={
            "dev": ProfileSettings(
                host="ml-dev.example.com", username="admin", password="secret"
            ),
            "staging": ProfileSettings(
                host="ml-staging.example.com", username="reader", password="secret"
            ),
        },
    )
    result = runner.invoke(app, ["config", "list"])
    assert result.exit_code == 0
    assert "dev" in result.output
    assert "staging" in result.output
    assert "default" in result.output


@patch("marklogic_tool.commands.config_cmd.load_app_config")
def test_config_list_no_profiles(mock_load):
    mock_load.return_value = AppConfig(default_profile="default", profiles={})
    result = runner.invoke(app, ["config", "list"])
    assert result.exit_code == 0


@patch("marklogic_tool.commands.config_cmd.resolve_profile")
def test_config_show_success(mock_resolve):
    mock_resolve.return_value = ProfileSettings(
        host="ml-dev.example.com",
        port=8000,
        username="admin",
        password="secret",
    )
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "ml-dev.example.com" in result.output
    assert "admin" in result.output
    assert "secret" not in result.output


@patch("marklogic_tool.commands.config_cmd.load_app_config")
def test_config_list_error(mock_load):
    from marklogic_tool.core.exceptions import ConfigurationError

    mock_load.side_effect = ConfigurationError("No config file found")
    result = runner.invoke(app, ["config", "list"])
    assert result.exit_code != 0
