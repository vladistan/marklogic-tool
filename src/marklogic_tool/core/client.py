"""HTTP client for MarkLogic REST API communication."""

from typing import Any

import httpx
import structlog

from marklogic_tool.core.config import ProfileSettings
from marklogic_tool.core.exceptions import (
    AuthenticationError,
    NetworkError,
    NotFoundError,
    ServerError,
    TimeoutError,
)

log = structlog.get_logger()


class MarkLogicClient:
    """Synchronous HTTP client for MarkLogic Server REST API."""

    def __init__(self, profile: ProfileSettings) -> None:
        self._profile = profile
        self._base_url = f"http://{profile.host}:{profile.port}"
        self._auth = self._build_auth(profile)
        self._client: httpx.Client | None = None

    def _build_auth(self, profile: ProfileSettings) -> httpx.Auth:
        username = profile.username
        password = profile.password.get_secret_value()
        if profile.auth_method == "digest":
            return httpx.DigestAuth(username, password)
        return httpx.BasicAuth(username, password)

    def __enter__(self) -> "MarkLogicClient":
        self._client = httpx.Client(
            base_url=self._base_url,
            auth=self._auth,
            timeout=self._profile.timeout,
        )
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._client:
            self._client.close()
            self._client = None

    @property
    def base_url(self) -> str:
        return self._base_url

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Execute an HTTP request with error translation."""
        if self._client is None:
            msg = "Client not initialized. Use as context manager."
            raise RuntimeError(msg)

        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.ConnectError as e:
            raise NetworkError(
                f"Cannot connect to {self._profile.host}:{self._profile.port}. "
                f"Check that MarkLogic is running and the host/port are correct."
            ) from e
        except httpx.TimeoutException as e:
            raise TimeoutError(
                f"Request timed out after {self._profile.timeout}s "
                f"to {self._profile.host}:{self._profile.port}"
            ) from e

        self._check_response(response)
        return response

    def _check_response(self, response: httpx.Response) -> None:
        """Translate HTTP error status codes to domain exceptions."""
        if response.is_success:
            return

        status = response.status_code
        if status in (401, 403):
            action = "permission denied" if status == 403 else "authentication failed"
            raise AuthenticationError(
                f"HTTP {status}: {action} at {self._profile.host}:{self._profile.port}"
            )
        if status == 404:
            raise NotFoundError(f"Resource not found: {response.request.url.path}")
        if status >= 500:
            raise ServerError(
                f"MarkLogic server error (HTTP {status}) "
                f"at {self._profile.host}:{self._profile.port}"
            )

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        """Execute a GET request."""
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        """Execute a POST request."""
        return self.request("POST", path, **kwargs)
