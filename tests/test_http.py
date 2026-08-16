"""The shared HTTP authority.

The silent-4xx hole is the reason this module exists: before it, a 400 or a 409
returned from either client as if it had succeeded.
"""

import ast
import inspect
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from marklogic_tool.core import client as client_module
from marklogic_tool.core import http
from marklogic_tool.core import manage_client as manage_client_module
from marklogic_tool.core.client import MarkLogicClient
from marklogic_tool.core.config import ProfileSettings
from marklogic_tool.core.exceptions import (
    AuthenticationError,
    BadRequestError,
    ConflictError,
    NotFoundError,
    ParseError,
    ServerError,
    TimeoutError,
)
from marklogic_tool.core.identity import Credential
from marklogic_tool.core.manage_client import ManageClient

PASSWORD_SENTINEL = "http-sentinel-password"  # pragma: allowlist secret

NON_SUCCESS = [
    (400, BadRequestError),
    (401, AuthenticationError),
    (403, AuthenticationError),
    (404, NotFoundError),
    (409, ConflictError),
    (500, ServerError),
    (503, ServerError),
]

JSON_ERROR_BODY = {
    "errorResponse": {
        "statusCode": 400,
        "status": "Bad Request",
        "messageCode": "SEC-PRIV",
        "message": "Need privilege: http://marklogic.com/xdmp/privileges/xdbc-eval",
    }
}

HTML_ERROR_BODY = (
    "<html><head><title>400 Bad Request</title></head><body>"
    "<h1>400 Bad Request</h1><dl><dt>SEC-PRIV: Need privilege: "
    "http://marklogic.com/xdmp/privileges/xdbc-eval</dt></dl></body></html>"
)


@pytest.fixture
def profile():
    return ProfileSettings(
        host="ml.example.com",
        port=8000,
        manage_port=8002,
        rest_port=8030,
        username="admin",
        password=SecretStr(PASSWORD_SENTINEL),
        auth_method="digest",
        timeout=30,
    )


def _transport(status, body=b"", headers=None):
    def handler(request):
        return httpx.Response(status, content=body, headers=headers or {})

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(("status", "expected"), NON_SUCCESS)
def test_marklogic_client_raises_typed_error_for_every_non_success(
    profile, status, expected
):
    with (
        MarkLogicClient(profile, transport=_transport(status)) as client,
        pytest.raises(expected),
    ):
        client.get("/v1/search")


@pytest.mark.parametrize(("status", "expected"), NON_SUCCESS)
def test_manage_client_raises_typed_error_for_every_non_success(
    profile, status, expected
):
    with (
        ManageClient(profile, transport=_transport(status)) as client,
        pytest.raises(expected),
    ):
        client.get_json("/manage/v2/databases")


def test_malformed_create_returning_400_raises_rather_than_reading_as_success(profile):
    transport = _transport(
        400, body=b'{"errorResponse":{"messageCode":"MANAGE-INVALID"}}'
    )
    with (
        ManageClient(profile, transport=transport) as client,
        pytest.raises(BadRequestError),
    ):
        client.get_json("/manage/v2/databases")


def test_json_error_body_is_translated(profile):
    import json

    transport = _transport(400, body=json.dumps(JSON_ERROR_BODY).encode())
    with (
        MarkLogicClient(profile, transport=transport) as client,
        pytest.raises(BadRequestError, match="SEC-PRIV"),
    ):
        client.get("/v1/eval")


def test_html_error_body_is_translated_without_assuming_json(profile):
    """Measured: the same refusal is JSON on 8030 and HTML on 8000."""
    transport = _transport(
        400, body=HTML_ERROR_BODY.encode(), headers={"content-type": "text/html"}
    )
    with (
        MarkLogicClient(profile, transport=transport) as client,
        pytest.raises(BadRequestError, match="SEC-PRIV"),
    ):
        client.get("/v1/eval")


def test_unparseable_error_body_still_raises(profile):
    transport = _transport(400, body=b"\xff\xfe not text at all")
    with (
        MarkLogicClient(profile, transport=transport) as client,
        pytest.raises(BadRequestError),
    ):
        client.get("/v1/search")


@pytest.mark.parametrize("carrier_status", [400, 500])
def test_extime_maps_to_timeout_in_either_carrier_status(profile, carrier_status):
    body = f'{{"errorResponse":{{"messageCode":"XDMP-EXTIME","statusCode":{carrier_status}}}}}'
    transport = _transport(carrier_status, body=body.encode())
    with (
        MarkLogicClient(profile, transport=transport) as client,
        pytest.raises(TimeoutError),
    ):
        client.get("/v1/search")


def test_extime_in_html_body_maps_to_timeout(profile):
    body = (
        b"<h1>500 Internal Server Error</h1><dt>XDMP-EXTIME: Time limit exceeded</dt>"
    )
    transport = _transport(500, body=body, headers={"content-type": "text/html"})
    with (
        MarkLogicClient(profile, transport=transport) as client,
        pytest.raises(TimeoutError),
    ):
        client.get("/v1/search")


def test_success_passes_through(profile):
    transport = _transport(200, body=b'{"total": 191310}')
    with MarkLogicClient(profile, transport=transport) as client:
        assert client.get("/v1/search").status_code == 200


def test_probe_returns_absent_on_404(profile):
    with ManageClient(profile, transport=_transport(404)) as client:
        assert isinstance(client.probe("/manage/v2/databases/ghost"), http.Absent)


def test_probe_returns_present_on_200(profile):
    transport = _transport(200, body=b'{"database-name": "Documents"}')
    with ManageClient(profile, transport=transport) as client:
        result = client.probe("/manage/v2/databases/Documents")
    assert isinstance(result, http.Present)
    assert result.payload["database-name"] == "Documents"


def test_probe_still_raises_on_500(profile):
    with (
        ManageClient(profile, transport=_transport(500)) as client,
        pytest.raises(ServerError),
    ):
        client.probe("/manage/v2/databases/Documents")


def test_probe_still_raises_on_403(profile):
    with (
        ManageClient(profile, transport=_transport(403)) as client,
        pytest.raises(AuthenticationError),
    ):
        client.probe("/manage/v2/databases/Documents")


def test_get_json_raises_on_404_rather_than_returning_empty(profile):
    with (
        ManageClient(profile, transport=_transport(404)) as client,
        pytest.raises(NotFoundError),
    ):
        client.get_json("/manage/v2/databases/ghost")


def test_require_total_returns_the_value():
    assert http.require_total({"total": 191310}, "count on prod-host") == 191310


def test_require_total_raises_when_total_is_absent():
    with pytest.raises(ParseError, match="total"):
        http.require_total({"results": []}, "count on prod-host")


def test_require_total_does_not_silently_yield_zero():
    """The defect this tool exists to find reads as a legitimate zero."""
    with pytest.raises(ParseError):
        http.require_total({}, "count on prod-host")


def test_require_total_rejects_a_non_integer_total():
    with pytest.raises(ParseError):
        http.require_total({"total": "191310"}, "count on prod-host")


def test_build_auth_selects_digest():
    credential = Credential("admin", SecretStr(PASSWORD_SENTINEL), "profile")
    assert isinstance(http.build_auth(credential, "digest"), httpx.DigestAuth)


def test_build_auth_selects_basic():
    credential = Credential("admin", SecretStr(PASSWORD_SENTINEL), "profile")
    assert isinstance(http.build_auth(credential, "basic"), httpx.BasicAuth)


def _source_of(module):
    return Path(inspect.getfile(module)).read_text()


@pytest.mark.parametrize("module", [client_module, manage_client_module])
def test_neither_client_binds_a_plaintext_password_to_a_named_local(module):
    for number, line in enumerate(_source_of(module).splitlines(), start=1):
        stripped = line.strip()
        if "get_secret_value()" in stripped and "=" in stripped.split("(")[0]:
            pytest.fail(
                f"{module.__name__}:{number} binds a plaintext secret: {stripped}"
            )


@pytest.mark.parametrize("module", [client_module, manage_client_module])
def test_neither_client_builds_its_own_auth(module):
    assert "_build_auth" not in _source_of(module)


@pytest.mark.parametrize("module", [client_module, manage_client_module])
def test_neither_client_keeps_duplicated_response_logic(module):
    assert "_check_response" not in _source_of(module)


@pytest.mark.parametrize(("status", "expected"), NON_SUCCESS)
def test_no_exception_message_or_args_contains_the_secret(profile, status, expected):
    body = f"denied for admin with password {PASSWORD_SENTINEL}".encode()
    with (
        MarkLogicClient(profile, transport=_transport(status, body=body)) as client,
        pytest.raises(expected) as exc_info,
    ):
        client.get("/v1/search")
    assert PASSWORD_SENTINEL not in str(exc_info.value)
    assert not any(PASSWORD_SENTINEL in str(arg) for arg in exc_info.value.args)


def test_error_message_names_the_target_and_the_status(profile):
    with (
        MarkLogicClient(profile, transport=_transport(409)) as client,
        pytest.raises(ConflictError) as exc_info,
    ):
        client.get("/v1/documents")
    message = str(exc_info.value)
    assert "ml.example.com" in message
    assert "409" in message


def _get_total_default_offenders(source: str) -> list[int]:
    """Return the line numbers where the tool calls `.get("total", 0)`.

    This walks the AST, not the raw text. A text scan also matched the docstring that
    explains why the idiom is forbidden. `ast.Constant` is not `ast.Call`.
    """
    offenders: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "get"):
            continue
        if len(node.args) != 2:
            continue
        key, default = node.args
        if (
            isinstance(key, ast.Constant)
            and key.value == "total"
            and isinstance(default, ast.Constant)
            and default.value == 0
        ):
            offenders.append(node.lineno)
    return offenders


def test_no_get_total_default_idiom_anywhere_in_the_codebase():
    source_root = Path(inspect.getfile(http)).parent.parent
    scanned = 0
    offenders: list[str] = []
    for path in source_root.rglob("*.py"):
        scanned += 1
        offenders += [
            f"{path.name}:{n}" for n in _get_total_default_offenders(path.read_text())
        ]

    # Assert the denominator: a path typo or a moved package would make this gate pass
    # vacuously, and a gate that reached zero files is a decoration.
    assert scanned > 10, f"scan only reached {scanned} file(s)"
    assert offenders == []


def test_the_matcher_catches_the_call_and_ignores_prose_about_it():
    """Both halves, because a narrow gate must be shown to still bite.

    The second assertion matters most. `require_total`'s own docstring quotes the forbidden
    idiom, and the text-scanning gate failed on it.
    """
    assert _get_total_default_offenders('x = payload.get("total", 0)') == [1]
    assert _get_total_default_offenders("x = payload.get('total', 0)") == [1]

    prose = '''def f():
    """The alternative is .get("total", 0), and a 0 from that lies."""
    return require_total(payload, "ctx")
'''
    assert _get_total_default_offenders(prose) == []

    # Near-misses that must NOT be reported: a different default, a different key.
    assert _get_total_default_offenders('payload.get("total", None)') == []
    assert _get_total_default_offenders('payload.get("other", 0)') == []
