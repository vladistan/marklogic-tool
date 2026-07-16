"""HTTP client for MarkLogic Management REST API (port 8002)."""

from typing import Any

import httpx

from marklogic_tool.core.config import ProfileSettings
from marklogic_tool.core.exceptions import (
    AuthenticationError,
    NetworkError,
    ServerError,
    TimeoutError,
)


class ManageClient:
    """Synchronous HTTP client for MarkLogic Management REST API."""

    def __init__(self, profile: ProfileSettings) -> None:
        self._profile = profile
        self._base_url = f"http://{profile.host}:{profile.manage_port}"
        self._auth = self._build_auth(profile)
        self._client: httpx.Client | None = None

    def _build_auth(self, profile: ProfileSettings) -> httpx.Auth:
        username = profile.username
        password = profile.password.get_secret_value()
        if profile.auth_method == "digest":
            return httpx.DigestAuth(username, password)
        return httpx.BasicAuth(username, password)

    def __enter__(self) -> "ManageClient":
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

    def get_json(
        self, path: str, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """GET a JSON response from the management API."""
        if self._client is None:
            msg = "Client not initialized. Use as context manager."
            raise RuntimeError(msg)

        merged_params = {"format": "json"}
        if params:
            merged_params.update(params)

        try:
            response = self._client.get(path, params=merged_params)
        except httpx.ConnectError as e:
            raise NetworkError(
                f"Cannot connect to manage API at {self._profile.host}:{self._profile.manage_port}"
            ) from e
        except httpx.TimeoutException as e:
            raise TimeoutError(
                f"Manage API request timed out after {self._profile.timeout}s"
            ) from e

        self._check_response(response)
        return response.json()  # type: ignore[no-any-return]

    def _check_response(self, response: httpx.Response) -> None:
        if response.is_success:
            return

        status = response.status_code
        if status in (401, 403):
            action = "permission denied" if status == 403 else "authentication failed"
            raise AuthenticationError(
                f"Manage API: {action} at {self._profile.host}:{self._profile.manage_port}"
            )
        if status >= 500:
            raise ServerError(
                f"Manage API error (HTTP {status}) at {self._profile.host}:{self._profile.manage_port}"
            )
