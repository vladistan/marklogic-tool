"""Tests for MarkLogicClient — initialization, auth, and error translation."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from pydantic import SecretStr

from marklogic_tool.core.client import MarkLogicClient
from marklogic_tool.core.config import ProfileSettings
from marklogic_tool.core.exceptions import (
    AuthenticationError,
    NetworkError,
    NotFoundError,
    ServerError,
    TimeoutError,
)


@pytest.fixture
def digest_profile():
    return ProfileSettings(
        host="ml.example.com",
        port=8000,
        username="admin",
        password=SecretStr("secret123"),
        auth_method="digest",
        timeout=30,
    )


@pytest.fixture
def basic_profile():
    return ProfileSettings(
        host="ml.example.com",
        port=8100,
        username="reader",
        password=SecretStr("pass456"),
        auth_method="basic",
        timeout=60,
    )


def test_client_constructs_base_url(digest_profile):
    client = MarkLogicClient(digest_profile)
    assert client.base_url == "http://ml.example.com:8000"


def test_client_uses_digest_auth(digest_profile):
    client = MarkLogicClient(digest_profile)
    assert isinstance(client._auth, httpx.DigestAuth)


def test_client_uses_basic_auth(basic_profile):
    client = MarkLogicClient(basic_profile)
    assert isinstance(client._auth, httpx.BasicAuth)


def test_client_respects_timeout(digest_profile):
    with patch.object(httpx, "Client") as mock_client_cls:
        mock_client_cls.return_value = MagicMock()
        with MarkLogicClient(digest_profile):
            pass
        mock_client_cls.assert_called_once()
        call_kwargs = mock_client_cls.call_args[1]
        assert call_kwargs["timeout"] == 30


def test_client_respects_custom_timeout(basic_profile):
    with patch.object(httpx, "Client") as mock_client_cls:
        mock_client_cls.return_value = MagicMock()
        with MarkLogicClient(basic_profile):
            pass
        call_kwargs = mock_client_cls.call_args[1]
        assert call_kwargs["timeout"] == 60


def test_client_context_manager_creates_httpx_client(digest_profile):
    with MarkLogicClient(digest_profile) as client:
        assert client._client is not None


def test_client_context_manager_closes_on_exit(digest_profile):
    with MarkLogicClient(digest_profile) as client:
        assert client._client is not None
    assert client._client is None


def test_client_raises_runtime_error_without_context(digest_profile):
    client = MarkLogicClient(digest_profile)
    with pytest.raises(RuntimeError, match="Use as context manager"):
        client.get("/test")


# --- Error Translation: Connection Errors ---


def test_connect_error_raises_network_error(digest_profile):
    with MarkLogicClient(digest_profile) as client:
        client._client.request = MagicMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        with pytest.raises(NetworkError, match="Cannot connect to ml.example.com:8000"):
            client.get("/v1/documents")


def test_timeout_raises_timeout_error(digest_profile):
    with MarkLogicClient(digest_profile) as client:
        client._client.request = MagicMock(side_effect=httpx.ReadTimeout("timed out"))
        with pytest.raises(TimeoutError, match="timed out after 30s"):
            client.get("/v1/documents")


def test_dns_failure_raises_network_error(digest_profile):
    with MarkLogicClient(digest_profile) as client:
        client._client.request = MagicMock(
            side_effect=httpx.ConnectError("Name or service not known")
        )
        with pytest.raises(NetworkError, match="Cannot connect"):
            client.get("/v1/documents")


# --- Error Translation: HTTP Status Codes ---


def _mock_response(status_code, path="/v1/documents"):
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.is_success = 200 <= status_code < 300
    response.request = MagicMock()
    response.request.url = httpx.URL(f"http://ml.example.com:8000{path}")
    return response


def test_http_401_raises_authentication_error(digest_profile):
    with MarkLogicClient(digest_profile) as client:
        client._client.request = MagicMock(return_value=_mock_response(401))
        with pytest.raises(AuthenticationError, match="authentication failed"):
            client.get("/v1/documents")


def test_http_403_raises_authentication_error_permission_denied(digest_profile):
    with MarkLogicClient(digest_profile) as client:
        client._client.request = MagicMock(return_value=_mock_response(403))
        with pytest.raises(AuthenticationError, match="permission denied"):
            client.get("/v1/documents")


def test_http_404_raises_not_found_error(digest_profile):
    with MarkLogicClient(digest_profile) as client:
        client._client.request = MagicMock(
            return_value=_mock_response(404, "/v1/documents?uri=/missing.xml")
        )
        with pytest.raises(NotFoundError, match="Resource not found"):
            client.get("/v1/documents", params={"uri": "/missing.xml"})


def test_http_500_raises_server_error(digest_profile):
    with MarkLogicClient(digest_profile) as client:
        client._client.request = MagicMock(return_value=_mock_response(500))
        with pytest.raises(ServerError, match="HTTP 500"):
            client.get("/v1/documents")


def test_http_503_raises_server_error(digest_profile):
    with MarkLogicClient(digest_profile) as client:
        client._client.request = MagicMock(return_value=_mock_response(503))
        with pytest.raises(ServerError, match="HTTP 503"):
            client.get("/v1/documents")


# --- Credential Safety ---


def test_no_credentials_in_network_error_message(digest_profile):
    with MarkLogicClient(digest_profile) as client:
        client._client.request = MagicMock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(NetworkError) as exc_info:
            client.get("/v1/documents")
        msg = str(exc_info.value)
        assert "secret123" not in msg
        assert "admin" not in msg


def test_no_credentials_in_auth_error_message(digest_profile):
    with MarkLogicClient(digest_profile) as client:
        client._client.request = MagicMock(return_value=_mock_response(401))
        with pytest.raises(AuthenticationError) as exc_info:
            client.get("/v1/documents")
        msg = str(exc_info.value)
        assert "secret123" not in msg
        assert "admin" not in msg


def test_no_credentials_in_timeout_error_message(digest_profile):
    with MarkLogicClient(digest_profile) as client:
        client._client.request = MagicMock(side_effect=httpx.ReadTimeout("timed out"))
        with pytest.raises(TimeoutError) as exc_info:
            client.get("/v1/documents")
        msg = str(exc_info.value)
        assert "secret123" not in msg
        assert "admin" not in msg


# --- Success Path ---


def test_successful_get_returns_response(digest_profile):
    with MarkLogicClient(digest_profile) as client:
        mock_resp = _mock_response(200)
        client._client.request = MagicMock(return_value=mock_resp)
        result = client.get("/v1/documents")
        assert result == mock_resp


def test_post_delegates_to_request(digest_profile):
    with MarkLogicClient(digest_profile) as client:
        mock_resp = _mock_response(200)
        client._client.request = MagicMock(return_value=mock_resp)
        result = client.post("/v1/eval", content="xdmp:host-name()")
        assert result == mock_resp
        client._client.request.assert_called_once_with(
            "POST", "/v1/eval", content="xdmp:host-name()"
        )
