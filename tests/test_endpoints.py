"""Endpoint selection. REST must never silently borrow `port`."""

import tomllib
from pathlib import Path

import pytest
from pydantic import SecretStr

from marklogic_tool.core.config import ProfileSettings, _apply_env_overlay
from marklogic_tool.core.endpoints import Endpoint, client_for
from marklogic_tool.core.exceptions import ConfigurationError
from marklogic_tool.core.identity import Credential
from marklogic_tool.core.manage_client import ManageClient

SECRET = "endpoint-sentinel"  # pragma: allowlist secret


@pytest.fixture
def credential():
    return Credential("admin", SecretStr(SECRET), "profile")


@pytest.fixture
def profile():
    return ProfileSettings(
        host="example-host",
        port=8000,
        manage_port=8002,
        rest_port=8030,
        username="admin",
        password=SecretStr(SECRET),
    )


@pytest.fixture
def profile_without_rest_port():
    return ProfileSettings(
        host="prod-host",
        port=8000,
        username="admin",
        password=SecretStr(SECRET),
    )


def test_rest_port_is_optional_with_no_numeric_default():
    field = ProfileSettings.model_fields["rest_port"]
    assert field.default is None


def test_rest_port_accepts_none_and_int():
    assert ProfileSettings.__annotations__["rest_port"] == (int | None)


def test_query_endpoint_uses_port(profile, credential):
    client = client_for(profile, Endpoint.QUERY, credential)
    assert client.base_url == "http://example-host:8000"


def test_rest_endpoint_uses_rest_port(profile, credential):
    client = client_for(profile, Endpoint.REST, credential)
    assert client.base_url == "http://example-host:8030"


def test_manage_endpoint_returns_a_manage_client(profile, credential):
    assert isinstance(client_for(profile, Endpoint.MANAGE, credential), ManageClient)


def test_rest_without_rest_port_raises_configuration_error(
    profile_without_rest_port, credential
):
    with pytest.raises(ConfigurationError, match="rest_port"):
        client_for(profile_without_rest_port, Endpoint.REST, credential)


def test_rest_refusal_names_the_profile_key_and_the_host(
    profile_without_rest_port, credential
):
    with pytest.raises(ConfigurationError) as exc_info:
        client_for(profile_without_rest_port, Endpoint.REST, credential)
    message = str(exc_info.value)
    assert "rest_port" in message
    assert "prod-host" in message


def test_rest_never_falls_back_to_port(profile_without_rest_port, credential):
    """Falling back to 8000 would count App-Services, a different database."""
    with pytest.raises(ConfigurationError) as exc_info:
        client_for(profile_without_rest_port, Endpoint.REST, credential)
    assert "8000" not in str(exc_info.value)


def test_query_endpoint_still_works_without_rest_port(
    profile_without_rest_port, credential
):
    client = client_for(profile_without_rest_port, Endpoint.QUERY, credential)
    assert client.base_url == "http://prod-host:8000"


def test_client_carries_the_injected_credential(profile, credential):
    client = client_for(profile, Endpoint.REST, credential)
    assert client.credential.username == "admin"


def test_timeout_override_is_applied(profile, credential):
    client = client_for(profile, Endpoint.REST, credential, timeout=120)
    assert client.timeout == 120


def test_timeout_defaults_to_the_profile(profile, credential):
    client = client_for(profile, Endpoint.REST, credential)
    assert client.timeout == profile.timeout


def test_env_overlay_covers_rest_port(profile, monkeypatch):
    monkeypatch.setenv("ML_REST_PORT", "9030")
    assert _apply_env_overlay(profile).rest_port == 9030


def test_env_overlay_covers_manage_port(profile, monkeypatch):
    monkeypatch.setenv("ML_MANAGE_PORT", "9002")
    assert _apply_env_overlay(profile).manage_port == 9002


def _example_config_path():
    return Path(__file__).parent.parent / "config.example.toml"


def test_config_example_parses():
    with open(_example_config_path(), "rb") as handle:
        parsed = tomllib.load(handle)
    assert "profiles" in parsed


def test_config_example_documents_rest_port():
    assert "rest_port" in _example_config_path().read_text()


def test_config_example_rest_port_comment_is_anchored_to_a_real_key():
    """toml-sort strips floating comments, so the note must sit on a key line."""
    lines = _example_config_path().read_text().splitlines()
    anchored = [
        line for line in lines if line.strip().startswith("rest_port") and "#" in line
    ]
    assert anchored != []


def test_config_example_declares_rest_port_in_a_parsed_profile():
    with open(_example_config_path(), "rb") as handle:
        parsed = tomllib.load(handle)
    assert any("rest_port" in profile for profile in parsed["profiles"].values())
