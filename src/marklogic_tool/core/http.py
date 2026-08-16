# cspell:ignore indistinguishability
"""The single authority for building auth and judging responses.

The same `/v1/eval` refusal returns JSON on one port and HTML on another, so the parser never
assumes JSON. `XDMP-EXTIME` arrives in both 400 and 500 responses.
"""

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from marklogic_tool.core.exceptions import (
    AuthenticationError,
    BadRequestError,
    ConflictError,
    MarkLogicToolError,
    NotFoundError,
    ParseError,
    ServerError,
    TimeoutError,
)
from marklogic_tool.core.identity import Credential
from marklogic_tool.core.secrets import redact

EXTIME_CODE = "XDMP-EXTIME"

_ERROR_CODE_PATTERN = re.compile(r"\b([A-Z][A-Z0-9]*-[A-Z0-9]+)\b")

_NEXT_ACTION = {
    400: "Check the request payload and the privileges of the effective identity.",
    401: "Check the username and the resolved secret for the effective identity.",
    403: "The identity authenticated but lacks the privilege; grant it or use --as-user.",
    404: "Check the name and that the object exists on this host.",
    409: "The object exists in a conflicting state; re-probe before retrying.",
}


@dataclass(frozen=True, slots=True)
class Absent:
    """A probe found nothing.

    404-as-value exists here and nowhere else in the tool; every other code path
    treats a 404 as an error.
    """


@dataclass(frozen=True, slots=True)
class Present:
    payload: Any


def build_auth(credential: Credential, auth_method: str) -> httpx.Auth:
    """Build an httpx auth object without binding the plaintext to a local.

    Digest is the default because a MarkLogic app server set to `digestbasic`
    still advertises Digest only in its 401 challenge.
    """
    if auth_method == "digest":
        return httpx.DigestAuth(
            credential.username, credential.secret.get_secret_value()
        )
    return httpx.BasicAuth(credential.username, credential.secret.get_secret_value())


def client_deadline_message(host: str, port: int, timeout: int) -> str:
    """The client-deadline text, in one place for both clients.

    The two timeout messages stay textually distinct, so the operator knows which limit
    stopped the run. Each message must read the same wherever the tool raises it.
    """
    return (
        f"Request timed out after {timeout}s to {host}:{port} — this is the "
        "CLIENT deadline, set by --timeout or the profile 'timeout'. Raise "
        "--timeout, or narrow the query so it completes inside it."
    )


def server_error_code(body: str) -> str | None:
    """Extract a MarkLogic error code from a JSON or HTML error body."""
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        parsed = None

    if isinstance(parsed, dict):
        response = parsed.get("errorResponse")
        if isinstance(response, dict):
            code = response.get("messageCode")
            if isinstance(code, str) and code:
                return code

    match = _ERROR_CODE_PATTERN.search(body)
    return match.group(1) if match else None


def check_response(response: httpx.Response, context: str) -> None:
    """Raise the typed error for any non-success response.

    Nothing falls through: an unknown non-success status still raises.
    """
    if response.is_success:
        return

    status = response.status_code
    body = _body_text(response)
    code = server_error_code(body)

    if code == EXTIME_CODE:
        raise TimeoutError(
            _message(
                context,
                status,
                code,
                "the server stopped the request at its own request-time limit",
                (
                    "--timeout is the CLIENT deadline and cannot raise this "
                    "limit. Raise the app server's 'request timeout' setting, "
                    "or narrow the query so it completes inside it."
                ),
            )
        )

    raise _error_type(status)(
        _message(
            context,
            status,
            code,
            _reason(status),
            _NEXT_ACTION.get(
                status,
                "Check the server status and the MarkLogic error log on the host.",
            ),
        )
    )


def probe(
    client: httpx.Client,
    path: str,
    *,
    context: str,
    params: dict[str, str] | None = None,
) -> Absent | Present:
    """Read a resource where absence is a legitimate answer.

    This is the only non-raising read in the tool. Everything except 404 is still
    judged by `check_response`.
    """
    response = client.get(path, params=params)
    if response.status_code == 404:
        return Absent()
    check_response(response, context)
    return Present(payload=_decode_json(response, context))


def require_total(payload: dict[str, Any], context: str) -> int:
    """Read `total` from a search payload. Never invent one.

    Keep this guard. No live response omits `total`, so no server falsifies it.
    `.get("total", 0)` returns a 0 that reads like an unpermissioned corpus.
    `tests/test_http.py` covers it.
    """

    if "total" not in payload:
        raise ParseError(
            f"Response for {context} carried no 'total' field, so no count can be "
            "reported. This is refused rather than defaulted to 0, because 0 is "
            "also what an unpermissioned corpus reports. Check that the request "
            "reached /v1/search and that format=json was accepted."
        )
    total = payload["total"]
    if not isinstance(total, int) or isinstance(total, bool):
        raise ParseError(
            f"Response for {context} carried a non-integer 'total' "
            f"({type(total).__name__}). Check the endpoint and the format parameter."
        )
    return total


def _error_type(status: int) -> type[MarkLogicToolError]:
    if status in (401, 403):
        return AuthenticationError
    if status == 404:
        return NotFoundError
    if status == 409:
        return ConflictError
    if status >= 500:
        return ServerError
    return BadRequestError


def _reason(status: int) -> str:
    if status == 401:
        return "authentication failed; the server rejected the credential"
    if status == 403:
        return "permission denied; the identity authenticated but lacks the privilege"
    if status == 404:
        return "the server has no such resource"
    if status == 409:
        return "the server reported a conflicting state"
    if status >= 500:
        return "the server failed to handle the request"
    return "the server rejected the request"


def _message(
    context: str, status: int, code: str | None, reason: str, next_action: str
) -> str:
    coded = f" ({code})" if code else ""
    return (
        f"{context} failed: HTTP {status}{coded} — {reason}. "
        f"No result is reported rather than a guessed one. {next_action}"
    )


def _body_text(response: httpx.Response) -> str:
    try:
        return redact(response.text)
    except (UnicodeDecodeError, ValueError):
        return ""


def _decode_json(response: httpx.Response, context: str) -> Any:
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError) as e:
        raise ParseError(
            f"Response for {context} was not valid JSON. Check that the endpoint "
            "accepts format=json."
        ) from e
