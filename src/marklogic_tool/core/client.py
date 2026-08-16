"""HTTP client for MarkLogic REST API communication."""

from typing import Any

import httpx

from marklogic_tool.core import http
from marklogic_tool.core.config import ProfileSettings
from marklogic_tool.core.exceptions import NetworkError, TimeoutError
from marklogic_tool.core.identity import Credential, resolve_identity


class MarkLogicClient:
    """Synchronous HTTP client for MarkLogic Server REST API."""

    def __init__(
        self,
        profile: ProfileSettings,
        *,
        credential: Credential | None = None,
        port: int | None = None,
        timeout: int | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._profile = profile
        self._port = port if port is not None else profile.port
        self._timeout = timeout if timeout is not None else profile.timeout
        self._credential = credential or resolve_identity(profile)
        self._base_url = f"http://{profile.host}:{self._port}"
        self._auth = http.build_auth(self._credential, profile.auth_method)
        self._transport = transport
        self._client: httpx.Client | None = None

    def __enter__(self) -> "MarkLogicClient":
        self._client = httpx.Client(
            base_url=self._base_url,
            auth=self._auth,
            timeout=self._timeout,
            transport=self._transport,
        )
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._client:
            self._client.close()
            self._client = None

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def credential(self) -> Credential:
        return self._credential

    @property
    def timeout(self) -> int:
        return self._timeout

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Execute an HTTP request with error translation."""
        if self._client is None:
            msg = "Client not initialized. Use as context manager."
            raise RuntimeError(msg)

        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.ConnectError as e:
            raise NetworkError(
                f"Cannot connect to {self._profile.host}:{self._port}. "
                f"Check that MarkLogic is running and the host/port are correct."
            ) from e
        except httpx.TimeoutException as e:
            raise TimeoutError(
                http.client_deadline_message(
                    self._profile.host, self._port, self._timeout
                )
            ) from e

        http.check_response(response, self._context(method, path))
        return response

    def probe(self, path: str, **kwargs: Any) -> http.Absent | http.Present:
        """Read where absence is a legitimate answer."""
        if self._client is None:
            msg = "Client not initialized. Use as context manager."
            raise RuntimeError(msg)
        return http.probe(
            self._client, path, context=self._context("GET", path), **kwargs
        )

    def _context(self, method: str, path: str) -> str:
        return f"{method} {path} on {self._profile.host}:{self._port}"

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        """Execute a GET request."""
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        """Execute a POST request."""
        return self.request("POST", path, **kwargs)
