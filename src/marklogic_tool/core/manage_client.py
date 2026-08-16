"""HTTP client for MarkLogic Management REST API (port 8002)."""

from typing import Any

import httpx

from marklogic_tool.core import http
from marklogic_tool.core.config import ProfileSettings
from marklogic_tool.core.exceptions import NetworkError, TimeoutError
from marklogic_tool.core.identity import Credential, resolve_identity

CONNECTION_CLOSED_MARKER = "closed without a response"
"""Stable substring so the teardown settle loop can recognize this narrowly."""


class ManageClient:
    """Synchronous HTTP client for MarkLogic Management REST API."""

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
        self._port = port if port is not None else profile.manage_port
        self._timeout = timeout if timeout is not None else profile.timeout
        self._credential = credential or resolve_identity(profile)
        self._base_url = f"http://{profile.host}:{self._port}"
        self._auth = http.build_auth(self._credential, profile.auth_method)
        self._transport = transport
        self._client: httpx.Client | None = None

    def __enter__(self) -> "ManageClient":
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
    def credential(self) -> Credential:
        return self._credential

    @property
    def timeout(self) -> int:
        return self._timeout

    def get_json(
        self, path: str, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """GET a JSON response from the management API.

        Every non-success status raises. Callers that need absence as a value
        must say so by calling `probe()` instead.
        """
        response = self._get(path, params)
        http.check_response(response, self._context(path))
        return response.json()  # type: ignore[no-any-return]

    def probe(
        self, path: str, params: dict[str, str] | None = None
    ) -> http.Absent | http.Present:
        """Read where absence is a legitimate answer."""
        if self._client is None:
            msg = "Client not initialized. Use as context manager."
            raise RuntimeError(msg)
        return http.probe(
            self._client,
            path,
            context=self._context(path),
            params=self._merged_params(params),
        )

    def post(
        self,
        path: str,
        payload: dict[str, Any],
        params: dict[str, str] | None = None,
        *,
        accept: frozenset[int] | None = None,
    ) -> httpx.Response:
        """Create an object. Return the response, so a 409 stays distinguishable.

        The tool does not swallow a 409. An already-exists and a kind collision both present
        as 409, and only a re-probe separates them.
        """
        return self._write("POST", path, payload, params, accept)

    def put(
        self,
        path: str,
        payload: dict[str, Any],
        params: dict[str, str] | None = None,
        *,
        accept: frozenset[int] | None = None,
    ) -> httpx.Response:
        """Set properties. The caller sends the drift subset, never the whole set."""
        return self._write("PUT", path, payload, params, accept)

    def delete(self, path: str, params: dict[str, str] | None = None) -> httpx.Response:
        """Remove an object.

        This exists for teardown only. Which paths are removable is a deploy policy, and it
        lives in `deploy/teardown.py`.
        """
        return self._write("DELETE", path, None, params, None)

    def _write(
        self,
        verb: str,
        path: str,
        payload: dict[str, Any] | None,
        params: dict[str, str] | None,
        accept: frozenset[int] | None,
    ) -> httpx.Response:
        if self._client is None:
            msg = "Client not initialized. Use as context manager."
            raise RuntimeError(msg)

        context = f"{verb} {path} on manage API at {self._profile.host}:{self._port}"
        try:
            response = self._client.request(
                verb,
                path,
                json=payload,
                params=self._merged_params(params),
            )
        except httpx.ConnectError as e:
            raise NetworkError(
                f"Cannot connect to manage API at {self._profile.host}:{self._port}"
            ) from e
        except httpx.RemoteProtocolError as e:
            # DELETE /v1/rest-apis returns 202 and then MarkLogic restarts
            # its cluster configuration, closing connections mid-flight. httpx raises
            # RemoteProtocolError, which is neither a ConnectError nor a response, so
            # without this it escapes as a raw transport exception and a COMPLETED
            # removal is reported as a crash.
            raise NetworkError(
                f"Connection to the manage API at {self._profile.host}:{self._port} "
                f"was {CONNECTION_CLOSED_MARKER}. MarkLogic closes connections while "
                "it restarts cluster configuration, which a rest-api or app-server "
                "removal triggers. Retry after it settles; if it persists, the host "
                "is genuinely down."
            ) from e
        except httpx.TimeoutException as e:
            raise TimeoutError(
                http.client_deadline_message(
                    self._profile.host, self._port, self._timeout
                )
            ) from e

        if accept is not None and response.status_code in accept:
            return response
        if accept is not None and response.status_code == 409:
            # Handed back intact so reconcile can re-probe and reclassify.
            return response
        http.check_response(response, context)
        return response

    def _get(self, path: str, params: dict[str, str] | None) -> httpx.Response:
        if self._client is None:
            msg = "Client not initialized. Use as context manager."
            raise RuntimeError(msg)

        try:
            return self._client.get(path, params=self._merged_params(params))
        except httpx.ConnectError as e:
            raise NetworkError(
                f"Cannot connect to manage API at {self._profile.host}:{self._port}"
            ) from e
        except httpx.RemoteProtocolError as e:
            # DELETE /v1/rest-apis returns 202 and then MarkLogic restarts
            # its cluster configuration, closing connections mid-flight. httpx raises
            # RemoteProtocolError, which is neither a ConnectError nor a response, so
            # without this it escapes as a raw transport exception and a COMPLETED
            # removal is reported as a crash.
            raise NetworkError(
                f"Connection to the manage API at {self._profile.host}:{self._port} "
                f"was {CONNECTION_CLOSED_MARKER}. MarkLogic closes connections while "
                "it restarts cluster configuration, which a rest-api or app-server "
                "removal triggers. Retry after it settles; if it persists, the host "
                "is genuinely down."
            ) from e
        except httpx.TimeoutException as e:
            raise TimeoutError(
                http.client_deadline_message(
                    self._profile.host, self._port, self._timeout
                )
            ) from e

    def _merged_params(self, params: dict[str, str] | None) -> dict[str, str]:
        merged = {"format": "json"}
        if params:
            merged.update(params)
        return merged

    def _context(self, path: str) -> str:
        return f"GET {path} on manage API at {self._profile.host}:{self._port}"
