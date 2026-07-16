"""Tests for ManageClient — management API HTTP client."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from pydantic import SecretStr

from marklogic_tool.core.config import ProfileSettings
from marklogic_tool.core.exceptions import (
    AuthenticationError,
    NetworkError,
    TimeoutError,
)
from marklogic_tool.core.manage_client import ManageClient


@pytest.fixture
def profile():
    return ProfileSettings(
        host="ml.example.com",
        port=8000,
        manage_port=8002,
        username="admin",
        password=SecretStr("secret123"),
        auth_method="digest",
        timeout=30,
        default_group="Default",
    )


def test_manage_client_base_url(profile):
    client = ManageClient(profile)
    assert client._base_url == "http://ml.example.com:8002"


def test_manage_client_uses_digest_auth(profile):
    client = ManageClient(profile)
    assert isinstance(client._auth, httpx.DigestAuth)


def test_manage_client_uses_basic_auth():
    profile = ProfileSettings(
        host="ml.example.com",
        manage_port=8002,
        username="admin",
        password=SecretStr("pass"),
        auth_method="basic",
    )
    client = ManageClient(profile)
    assert isinstance(client._auth, httpx.BasicAuth)


def test_manage_client_context_manager(profile):
    with ManageClient(profile) as client:
        assert client._client is not None
    assert client._client is None


def test_manage_client_not_initialized_raises(profile):
    client = ManageClient(profile)
    with pytest.raises(RuntimeError, match="not initialized"):
        client.get_json("/manage/v2")


@patch("marklogic_tool.core.manage_client.httpx.Client")
def test_manage_client_get_json(mock_client_cls, profile):
    mock_http = MagicMock()
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"database-default-list": {"list-items": []}}
    mock_http.get.return_value = mock_response
    mock_client_cls.return_value = mock_http

    with ManageClient(profile) as client:
        result = client.get_json("/manage/v2/databases")

    assert "database-default-list" in result
    mock_http.get.assert_called_once()
    call_kwargs = mock_http.get.call_args
    assert call_kwargs[1]["params"]["format"] == "json"


@patch("marklogic_tool.core.manage_client.httpx.Client")
def test_manage_client_connection_refused(mock_client_cls, profile):
    mock_http = MagicMock()
    mock_http.get.side_effect = httpx.ConnectError("Connection refused")
    mock_client_cls.return_value = mock_http

    with (
        ManageClient(profile) as client,
        pytest.raises(NetworkError, match="Cannot connect to manage API"),
    ):
        client.get_json("/manage/v2")


@patch("marklogic_tool.core.manage_client.httpx.Client")
def test_manage_client_timeout(mock_client_cls, profile):
    mock_http = MagicMock()
    mock_http.get.side_effect = httpx.TimeoutException("timed out")
    mock_client_cls.return_value = mock_http

    with (
        ManageClient(profile) as client,
        pytest.raises(TimeoutError, match="timed out"),
    ):
        client.get_json("/manage/v2")


@patch("marklogic_tool.core.manage_client.httpx.Client")
def test_manage_client_401_raises_auth_error(mock_client_cls, profile):
    mock_http = MagicMock()
    mock_response = MagicMock()
    mock_response.is_success = False
    mock_response.status_code = 401
    mock_http.get.return_value = mock_response
    mock_client_cls.return_value = mock_http

    with (
        ManageClient(profile) as client,
        pytest.raises(AuthenticationError, match="authentication failed"),
    ):
        client.get_json("/manage/v2")


@patch("marklogic_tool.core.manage_client.httpx.Client")
def test_manage_client_passes_extra_params(mock_client_cls, profile):
    mock_http = MagicMock()
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {}
    mock_http.get.return_value = mock_response
    mock_client_cls.return_value = mock_http

    with ManageClient(profile) as client:
        client.get_json("/manage/v2/databases", params={"view": "status"})

    call_kwargs = mock_http.get.call_args
    assert call_kwargs[1]["params"]["view"] == "status"
    assert call_kwargs[1]["params"]["format"] == "json"
