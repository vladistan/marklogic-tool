"""Integration tests — real MarkLogic server connection validation.

These tests require a running MarkLogic server and valid config at
~/.config/marklogic/config.toml (or MARKLOGIC_CONFIG env var).

Run with: uv run pytest -m integration
"""

import pytest
from pydantic import SecretStr

from marklogic_tool.core.client import MarkLogicClient
from marklogic_tool.core.config import resolve_profile
from marklogic_tool.core.exceptions import (
    AuthenticationError,
    ConfigurationError,
    NetworkError,
)


def _get_profile():
    """Attempt to load the default profile, skip if unavailable."""
    try:
        return resolve_profile()
    except ConfigurationError:
        pytest.skip("No MarkLogic config available — skipping integration tests")


@pytest.mark.integration
def test_connection_with_valid_credentials():
    profile = _get_profile()
    with MarkLogicClient(profile) as client:
        response = client.get("/v1/config/properties")
        assert response.status_code == 200


@pytest.mark.integration
def test_connection_with_wrong_credentials():
    profile = _get_profile()
    bad_profile = profile.model_copy(
        update={"password": SecretStr("definitely-wrong-password")}
    )
    with MarkLogicClient(bad_profile) as client, pytest.raises(AuthenticationError):
        client.get("/v1/config/properties")


@pytest.mark.integration
def test_connection_to_wrong_port():
    profile = _get_profile()
    bad_profile = profile.model_copy(update={"port": 19999})
    with MarkLogicClient(bad_profile) as client, pytest.raises(NetworkError):
        client.get("/v1/config/properties")
