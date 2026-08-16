"""Tests for count-unpermissioned — the gate and its contract (3.6)."""

# cspell:ignore ungated normalised

import json
from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from marklogic_tool.cli import app
from marklogic_tool.core.client import MarkLogicClient
from marklogic_tool.core.config import ProfileSettings
from marklogic_tool.core.endpoints import Endpoint
from marklogic_tool.core.manage_client import ManageClient
from marklogic_tool.output.render_verify import (
    ELAPSED_PLACEHOLDER,
    UNPERMISSIONED_SCHEMA,
    normalise_for_golden,
)

runner = CliRunner()
BOUNDARY = "ML-BOUNDARY"

CONTRACT_FIELDS = {
    "schema",
    "database",
    "identity",
    "identity_source",
    "endpoint",
    "method",
    "evidence",
    "findings_are_sound",
    "unpermissioned",
    "total_scanned",
    "uris",
    "uris_requested",
    "uris_listing",
    "elapsed_seconds",
    "gate",
    "exit_code",
}


@pytest.fixture
def profile():
    return ProfileSettings(
        host="ml.example.com",
        port=8000,
        manage_port=8002,
        rest_port=8030,
        username="admin",
        password=SecretStr("secret123"),
    )


def _multipart(values):
    body = b""
    for value in values:
        body += (
            f"--{BOUNDARY}\r\n"
            "Content-Type: text/plain\r\n"
            "X-Primitive: string\r\n"
            "\r\n"
            f"{value}\r\n"
        ).encode()
    body += f"--{BOUNDARY}--".encode()
    return body


def _run(args, profile, *, values=("0", "100"), lexicon=True):
    def query_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_multipart(values),
            headers={"content-type": f"multipart/mixed; boundary={BOUNDARY}"},
        )

    def manage_handler(request: httpx.Request) -> httpx.Response:
        payload: dict[str, object] = {"database-name": "db1"}
        if lexicon is not None:
            payload["uri-lexicon"] = lexicon
        return httpx.Response(200, json=payload)

    def factory(_profile, endpoint, credential, timeout=None):
        if endpoint is Endpoint.MANAGE:
            return ManageClient(
                profile,
                credential=credential,
                port=8002,
                transport=httpx.MockTransport(manage_handler),
            )
        return MarkLogicClient(
            profile,
            credential=credential,
            port=8000,
            transport=httpx.MockTransport(query_handler),
        )

    with (
        patch("marklogic_tool.commands.verify.resolve_profile", return_value=profile),
        patch("marklogic_tool.commands.verify.client_for", factory),
    ):
        return runner.invoke(app, args)


def _payload(result):
    return json.loads(result.stdout)[0]


def test_exhaustive_by_default(profile):
    result = _run(["-o", "json", "count-unpermissioned", "-d", "db1"], profile)

    assert result.exit_code == 0
    assert _payload(result)["method"] == "exhaustive"
    assert _payload(result)["evidence"] is True


def test_findings_with_the_gate_on_exit_7(profile):
    result = _run(
        ["-o", "json", "count-unpermissioned", "-d", "db1"],
        profile,
        values=("1", "100"),
    )

    assert result.exit_code == 7
    assert _payload(result)["unpermissioned"] == 1


def test_zero_findings_exit_0(profile):
    result = _run(["count-unpermissioned", "-d", "db1"], profile, values=("0", "100"))
    assert result.exit_code == 0


def test_no_gate_reports_the_same_numbers_and_exits_0(profile):
    gated = _run(
        ["-o", "json", "count-unpermissioned", "-d", "db1"],
        profile,
        values=("3", "100"),
    )
    ungated = _run(
        ["-o", "json", "count-unpermissioned", "-d", "db1", "--no-gate"],
        profile,
        values=("3", "100"),
    )

    assert gated.exit_code == 7
    assert ungated.exit_code == 0
    assert _payload(gated)["unpermissioned"] == _payload(ungated)["unpermissioned"]
    assert _payload(ungated)["gate"] is False


def test_sampled_warns_loudly_and_marks_not_evidence(profile):
    result = _run(
        ["-o", "json", "count-unpermissioned", "-d", "db1", "--sampled", "500"],
        profile,
        values=("0", "500"),
    )

    assert "WARNING" in result.stderr
    assert _payload(result)["evidence"] is False


def test_sampled_with_findings_exits_7_and_findings_are_sound(profile):
    """sampling only under-reports, so what it found is real."""
    result = _run(
        ["-o", "json", "count-unpermissioned", "-d", "db1", "--sampled", "500"],
        profile,
        values=("2", "500"),
    )

    assert result.exit_code == 7
    payload = _payload(result)
    assert payload["evidence"] is False
    assert payload["findings_are_sound"] is True


def test_sampled_with_zero_findings_exits_0_with_the_not_evidence_warning(profile):
    result = _run(
        ["count-unpermissioned", "-d", "db1", "--sampled", "500"],
        profile,
        values=("0", "500"),
    )

    assert result.exit_code == 0
    assert "NOT evidence" in result.stderr


def test_sampled_with_gate_is_legal(profile):
    result = _run(
        ["count-unpermissioned", "-d", "db1", "--sampled", "500", "--gate"],
        profile,
        values=("1", "500"),
    )
    assert result.exit_code == 7


def test_sampled_total_scanned_is_n(profile):
    """total_scanned equals N under sampling."""
    result = _run(
        ["-o", "json", "count-unpermissioned", "-d", "db1", "--sampled", "500"],
        profile,
        values=("0", "500"),
    )
    assert _payload(result)["total_scanned"] == 500


def test_sampled_requires_a_positive_n(profile):
    result = _run(["count-unpermissioned", "-d", "db1", "--sampled", "0"], profile)
    assert result.exit_code == 2


def test_list_uris_prints_at_most_n_and_states_the_true_total(profile):
    result = _run(
        ["count-unpermissioned", "-d", "db1", "--list-uris", "2"],
        profile,
        values=("9", "100", "/a.xml", "/b.xml"),
    )

    assert "/a.xml" in result.stdout
    assert "9" in result.stdout


def test_omitting_list_uris_prints_no_uris(profile):
    result = _run(
        ["count-unpermissioned", "-d", "db1"],
        profile,
        values=("9", "100"),
    )
    assert "/a.xml" not in result.stdout


def test_as_user_is_refused_with_exit_2(profile):
    """refused, never silently answered as somebody else."""
    result = _run(["count-unpermissioned", "-d", "db1", "--as-user", "writer"], profile)

    assert result.exit_code == 2
    assert "xdbc-eval" in result.stderr


def test_lexicon_refusal_is_never_a_count(profile):
    result = _run(["count-unpermissioned", "-d", "db1"], profile, lexicon=False)

    assert result.exit_code == 3
    assert "uri lexicon" in result.stderr.lower()
    assert "unpermissioned" not in result.stdout


def test_empty_database_is_a_pass_not_an_error(profile):
    result = _run(
        ["-o", "json", "count-unpermissioned", "-d", "db1"], profile, values=("0", "0")
    )

    assert result.exit_code == 0
    assert _payload(result)["total_scanned"] == 0


def test_json_carries_every_contract_field(profile):
    result = _run(["-o", "json", "count-unpermissioned", "-d", "db1"], profile)

    assert set(_payload(result)) == CONTRACT_FIELDS


def test_json_is_self_identifying(profile):
    """the agent consumer detects the version in band."""
    result = _run(["-o", "json", "count-unpermissioned", "-d", "db1"], profile)
    assert _payload(result)["schema"] == UNPERMISSIONED_SCHEMA


def test_identity_source_is_reported(profile):
    """an identity without its source cannot be audited."""
    result = _run(["-o", "json", "count-unpermissioned", "-d", "db1"], profile)
    assert _payload(result)["identity_source"] == "profile"


def test_exit_code_is_recorded_in_the_payload(profile):
    result = _run(
        ["-o", "json", "count-unpermissioned", "-d", "db1"], profile, values=("1", "10")
    )
    assert _payload(result)["exit_code"] == 7


def test_normalise_for_golden_blanks_only_elapsed(profile):
    result = _run(["-o", "json", "count-unpermissioned", "-d", "db1"], profile)
    payload = _payload(result)
    normalised = normalise_for_golden(payload)

    assert normalised["elapsed_seconds"] == ELAPSED_PLACEHOLDER
    assert set(normalised) == set(payload)
    assert {k: v for k, v in normalised.items() if k != "elapsed_seconds"} == {
        k: v for k, v in payload.items() if k != "elapsed_seconds"
    }


def test_listing_state_not_requested(profile):
    """Three states, pinned one test each. State 1: the operator did not ask."""
    result = _run(
        ["-o", "json", "count-unpermissioned", "-d", "db1"],
        profile,
        values=("9", "100"),
    )
    payload = _payload(result)

    assert payload["uris_requested"] == 0
    assert payload["uris_listing"] == "not_requested"
    assert payload["uris"] == []
    assert payload["unpermissioned"] == 9


def test_listing_state_truncated(profile):
    """State 2: asked for N, got fewer than the total."""
    result = _run(
        ["-o", "json", "count-unpermissioned", "-d", "db1", "--list-uris", "2"],
        profile,
        values=("9", "100", "/a.xml", "/b.xml"),
    )
    payload = _payload(result)

    assert payload["uris_requested"] == 2
    assert payload["uris_listing"] == "truncated"
    assert len(payload["uris"]) == 2


def test_listing_state_complete(profile):
    """State 3: asked for N, got them all."""
    result = _run(
        ["-o", "json", "count-unpermissioned", "-d", "db1", "--list-uris", "5"],
        profile,
        values=("2", "100", "/a.xml", "/b.xml"),
    )
    payload = _payload(result)

    assert payload["uris_requested"] == 5
    assert payload["uris_listing"] == "complete"
    assert len(payload["uris"]) == 2


def test_no_state_can_read_as_no_offenders(profile):
    """The requirement, stated as a test rather than trusted to the enum.

    A boolean `uris_truncated` read as "no offenders" beside `uris: []` and
    `unpermissioned: 9`. No value of `uris_listing` is falsy.
    """
    for args, values in (
        (["-o", "json", "count-unpermissioned", "-d", "db1"], ("9", "100")),
        (
            ["-o", "json", "count-unpermissioned", "-d", "db1", "--list-uris", "2"],
            ("9", "100", "/a.xml", "/b.xml"),
        ),
        (
            ["-o", "json", "count-unpermissioned", "-d", "db1", "--list-uris", "5"],
            ("2", "100", "/a.xml", "/b.xml"),
        ),
    ):
        payload = _payload(_run(args, profile, values=values))
        assert payload["uris_listing"], "every state must be truthy, never falsy"
        assert payload["uris_listing"] in {"not_requested", "truncated", "complete"}
        assert "uris_truncated" not in payload, "one fact, one field"
