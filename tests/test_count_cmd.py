"""Tests for the count command: provenance, endpoint choice, secret ownership."""

# cspell:ignore NOSUCHDB nosuch

import os
from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from marklogic_tool.cli import app
from marklogic_tool.commands.count import CountResult, endpoint_for
from marklogic_tool.core.client import MarkLogicClient
from marklogic_tool.core.config import ProfileSettings
from marklogic_tool.core.endpoints import Endpoint

runner = CliRunner()


@pytest.fixture
def profile():
    return ProfileSettings(
        host="ml.example.com",
        port=8000,
        manage_port=8002,
        rest_port=8030,
        username="admin",
        password=SecretStr("secret123"),
        identities={"writer": "env:WRITER_SECRET"},
    )


@pytest.fixture
def profile_no_rest():
    return ProfileSettings(
        host="ml.example.com",
        port=8000,
        manage_port=8002,
        username="admin",
        password=SecretStr("secret123"),
        identities={"writer": "env:WRITER_SECRET"},
    )


def _run(args, profile, recorded, *, payload=None, status=200):
    body = {"total": 42} if payload is None else payload

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        if status != 200:
            return httpx.Response(
                status, json={"errorResponse": {"messageCode": "XDMP-NOSUCHDB"}}
            )
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)

    def factory(_profile, endpoint, credential, timeout=None):
        port = 8030 if endpoint is Endpoint.REST else 8000
        return MarkLogicClient(
            profile, credential=credential, port=port, transport=transport
        )

    with (
        patch("marklogic_tool.commands.count.resolve_profile", return_value=profile),
        patch("marklogic_tool.commands.count.client_for", factory),
    ):
        return runner.invoke(app, args)


def test_count_reports_total_from_search(profile):
    recorded: list[httpx.Request] = []
    result = _run(["-o", "json", "count", "-d", "db1"], profile, recorded)

    assert result.exit_code == 0
    assert '"total": 42' in result.stdout


def test_count_carries_provenance_on_every_result(profile):
    recorded: list[httpx.Request] = []
    result = _run(["-o", "json", "count", "-d", "db1"], profile, recorded)

    assert '"identity": "admin"' in result.stdout
    assert '"identity_source": "profile"' in result.stdout
    assert '"endpoint": "http://ml.example.com:8030"' in result.stdout


def test_collection_narrows_the_count(profile):
    recorded: list[httpx.Request] = []
    _run(["count", "-d", "db1", "-c", "expenses"], profile, recorded)

    assert "collection=expenses" in str(recorded[0].url)


def test_missing_total_errors_rather_than_reporting_zero(profile):
    """0 is what an unpermissioned corpus reports; it may never be invented."""
    recorded: list[httpx.Request] = []
    result = _run(["count", "-d", "db1"], profile, recorded, payload={"results": []})

    assert result.exit_code == 4
    assert "total" in result.stderr


def test_nonexistent_database_is_not_a_silent_zero(profile):
    recorded: list[httpx.Request] = []
    result = _run(["count", "-d", "nosuch"], profile, recorded, status=400)

    assert result.exit_code != 0
    assert "0" not in result.stdout


def test_identity_that_sees_zero_is_reported_plainly(profile):
    """That IS the defect signature — the caller decides, the tool does not."""
    recorded: list[httpx.Request] = []
    result = _run(
        ["-o", "json", "count", "-d", "db1"], profile, recorded, payload={"total": 0}
    )

    assert result.exit_code == 0
    assert '"total": 0' in result.stdout


def test_as_user_goes_over_the_rest_instance_port(profile):
    recorded: list[httpx.Request] = []
    os.environ["WRITER_SECRET"] = "writer-pass"  # pragma: allowlist secret
    try:
        result = _run(
            ["-o", "json", "count", "-d", "db1", "--as-user", "writer"],
            profile,
            recorded,
        )
    finally:
        del os.environ["WRITER_SECRET"]

    assert result.exit_code == 0
    assert recorded[0].url.port == 8030
    assert '"identity": "writer"' in result.stdout
    assert '"identity_source": "as-user:writer"' in result.stdout


def test_admin_half_also_uses_rest_when_configured(profile):
    """both halves of the pairing traverse the identical endpoint."""
    recorded: list[httpx.Request] = []
    _run(["count", "-d", "db1"], profile, recorded)

    assert recorded[0].url.port == 8030


def test_endpoint_for_prefers_rest_whenever_it_is_configured():
    with_rest = ProfileSettings(
        host="h", rest_port=8030, username="u", password=SecretStr("p")
    )
    without_rest = ProfileSettings(host="h", username="u", password=SecretStr("p"))

    assert endpoint_for(with_rest, as_user=None) is Endpoint.REST
    assert endpoint_for(with_rest, as_user="writer") is Endpoint.REST
    assert endpoint_for(without_rest, as_user="writer") is Endpoint.REST
    assert endpoint_for(without_rest, as_user=None) is Endpoint.QUERY


def test_as_user_without_rest_port_names_the_missing_key(profile_no_rest):
    """a named configuration error, never a fall back to 'port'."""
    with patch(
        "marklogic_tool.commands.count.resolve_profile", return_value=profile_no_rest
    ):
        result = runner.invoke(app, ["count", "-d", "db1", "--as-user", "writer"])

    assert result.exit_code == 3
    assert "rest_port" in result.stderr


def test_as_user_secret_is_accepted_and_used(profile):
    """--as-user-secret is owned by this step."""
    recorded: list[httpx.Request] = []
    os.environ["ALT_SECRET"] = "alt-pass"  # pragma: allowlist secret
    try:
        result = _run(
            [
                "-o",
                "json",
                "count",
                "-d",
                "db1",
                "--as-user",
                "writer",
                "--as-user-secret",
                "env:ALT_SECRET",
            ],
            profile,
            recorded,
        )
    finally:
        del os.environ["ALT_SECRET"]

    assert result.exit_code == 0
    assert '"identity": "writer"' in result.stdout


def test_as_user_secret_without_as_user_is_a_usage_refusal(profile):
    """it may not be silently ignored."""
    recorded: list[httpx.Request] = []
    result = _run(
        ["count", "-d", "db1", "--as-user-secret", "env:X"], profile, recorded
    )

    assert result.exit_code == 2
    assert "--as-user" in result.stderr


def test_undeclared_identity_refuses_and_never_falls_back(profile):
    recorded: list[httpx.Request] = []
    result = _run(["count", "-d", "db1", "--as-user", "ghost"], profile, recorded)

    assert result.exit_code == 3
    assert "ghost" in result.stderr


def test_env_overlay_is_reported_as_the_effective_identity(profile):
    recorded: list[httpx.Request] = []
    os.environ["ML_USERNAME"] = "overlaid"
    try:
        result = _run(["-o", "json", "count", "-d", "db1"], profile, recorded)
    finally:
        del os.environ["ML_USERNAME"]

    assert '"identity_source": "env:ML_USERNAME"' in result.stdout


def test_unknown_collection_wording_covers_both_cases():
    """the tool never claims a distinction MarkLogic cannot make."""
    zero = CountResult(
        database="db1",
        collection="ghost",
        identity="admin",
        identity_source="profile",
        endpoint="http://h:8030",
        total=0,
    )
    assert zero.human_summary() == "0 documents - collection unknown or empty"

    nonzero = CountResult(
        database="db1",
        collection="expenses",
        identity="admin",
        identity_source="profile",
        endpoint="http://h:8030",
        total=7,
    )
    assert nonzero.human_summary() == "7 documents"


def test_no_unknown_collection_note_field_in_json(profile):
    """The NOTE was dropped; it must not reappear as a JSON field."""
    recorded: list[httpx.Request] = []
    result = _run(
        ["-o", "json", "count", "-d", "db1", "-c", "ghost"],
        profile,
        recorded,
        payload={"total": 0},
    )

    assert "unknown-collection" not in result.stdout
    assert '"note"' not in result.stdout


def test_count_json_is_self_identifying(profile):
    """the agent consumer detects the version in band."""
    recorded: list[httpx.Request] = []
    result = _run(["-o", "json", "count", "-d", "db1"], profile, recorded)

    assert '"schema": "marklogic-tool/count/1"' in result.stdout
