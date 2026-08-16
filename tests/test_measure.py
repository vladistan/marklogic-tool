"""Tests for the unpermissioned measurement core."""

# cspell:ignore Asubsequence

import httpx
import pytest
from pydantic import SecretStr

from marklogic_tool.core.client import MarkLogicClient
from marklogic_tool.core.config import ProfileSettings
from marklogic_tool.core.exceptions import ConfigurationError, ParseError, TimeoutError
from marklogic_tool.core.manage_client import ManageClient
from marklogic_tool.verify.measure import (
    EXHAUSTIVE,
    SAMPLED,
    VerifyClients,
    measure_unpermissioned,
)
from marklogic_tool.verify.queries import (
    DETECTION_PREDICATE,
    PERMISSION_FILTER,
    UNPERMISSIONED_COUNT_AND_SAMPLE,
    UNPERMISSIONED_SAMPLED_COUNT_AND_SAMPLE,
)

BOUNDARY = "ML-BOUNDARY"


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


def _eval_response(values):
    return httpx.Response(
        200,
        content=_multipart(values),
        headers={"content-type": f"multipart/mixed; boundary={BOUNDARY}"},
    )


def _clients(profile, *, eval_response, lexicon=True, recorded=None):
    recorded = recorded if recorded is not None else []

    def query_handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        if callable(eval_response):
            return eval_response(request)
        return eval_response

    def manage_handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        payload: dict[str, object] = {"database-name": "db1"}
        if lexicon is not None:
            payload["uri-lexicon"] = lexicon
        return httpx.Response(200, json=payload)

    query = MarkLogicClient(
        profile, port=8000, transport=httpx.MockTransport(query_handler)
    )
    manage = ManageClient(
        profile, port=8002, transport=httpx.MockTransport(manage_handler)
    )
    return query, manage, recorded


def _measure(profile, **kwargs):
    sampled_n = kwargs.pop("sampled_n", None)
    list_uris = kwargs.pop("list_uris", 0)
    query, manage, recorded = _clients(profile, **kwargs)
    with query, manage:
        result = measure_unpermissioned(
            VerifyClients(query=query, manage=manage),
            "db1",
            sampled_n=sampled_n,
            list_uris=list_uris,
        )
    return result, recorded


def test_exhaustive_counts_documents_with_empty_permissions(profile):
    result, _ = _measure(profile, eval_response=_eval_response(["1", "191310"]))

    assert result.unpermissioned == 1
    assert result.total_scanned == 191310
    assert result.method == EXHAUSTIVE
    assert result.evidence is True


def test_both_queries_are_composed_from_the_one_filter():
    """One definition, structurally — not two copies that happen to agree today."""
    assert PERMISSION_FILTER in UNPERMISSIONED_COUNT_AND_SAMPLE
    assert PERMISSION_FILTER in UNPERMISSIONED_SAMPLED_COUNT_AND_SAMPLE
    assert f"cts:uris(){PERMISSION_FILTER}" == DETECTION_PREDICATE

    # The filter appears once per query; a second inlined copy would drift.
    assert UNPERMISSIONED_COUNT_AND_SAMPLE.count(PERMISSION_FILTER) == 1
    assert UNPERMISSIONED_SAMPLED_COUNT_AND_SAMPLE.count(PERMISSION_FILTER) == 1


def test_subsequence_is_pushed_into_the_server_side_query(profile):
    """the client never materialises 200k URIs to slice them locally."""
    recorded: list[httpx.Request] = []
    _measure(
        profile,
        eval_response=_eval_response(["2", "10", "/a.xml", "/b.xml"]),
        list_uris=2,
        recorded=recorded,
    )

    evals = [r for r in recorded if r.url.path == "/v1/eval"]
    posted = evals[0].content.decode()
    assert "fn%3Asubsequence" in posted or "fn:subsequence" in posted
    assert "sample-size" in posted


def test_count_and_sample_come_from_one_eval(profile):
    recorded: list[httpx.Request] = []
    _measure(
        profile,
        eval_response=_eval_response(["2", "10", "/a.xml", "/b.xml"]),
        list_uris=2,
        recorded=recorded,
    )

    evals = [r for r in recorded if r.url.path == "/v1/eval"]
    assert len(evals) == 1


def test_uri_lexicon_disabled_is_a_named_refusal_not_a_count(profile):
    """cts:uris() returns nothing without it, which reads as a clean corpus."""
    with pytest.raises(ConfigurationError) as excinfo:
        _measure(profile, eval_response=_eval_response(["0", "0"]), lexicon=False)

    assert "uri lexicon" in str(excinfo.value).lower()
    assert "db1" in str(excinfo.value)


def test_absent_uri_lexicon_property_fails_closed(profile):
    """Absence is refused too — the one thing this may never do is under-report."""
    with pytest.raises(ConfigurationError):
        _measure(profile, eval_response=_eval_response(["0", "0"]), lexicon=None)


def test_lexicon_precheck_runs_before_any_eval(profile):
    """the guard is welded to the measurement, not left to the caller."""
    recorded: list[httpx.Request] = []
    with pytest.raises(ConfigurationError):
        _measure(
            profile,
            eval_response=_eval_response(["0", "0"]),
            lexicon=False,
            recorded=recorded,
        )

    assert not [r for r in recorded if r.url.path == "/v1/eval"]


def test_extime_inside_a_400_becomes_a_timeout(profile):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"errorResponse": {"messageCode": "XDMP-EXTIME"}}
        )

    with pytest.raises(TimeoutError):
        _measure(profile, eval_response=handler)


def test_extime_inside_a_500_behaves_identically(profile):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, json={"errorResponse": {"messageCode": "XDMP-EXTIME"}}
        )

    with pytest.raises(TimeoutError):
        _measure(profile, eval_response=handler)


def test_timeout_never_degrades_to_a_sampled_result(profile):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, json={"errorResponse": {"messageCode": "XDMP-EXTIME"}}
        )

    with pytest.raises(TimeoutError):
        _measure(profile, eval_response=handler, sampled_n=None)


def test_sampled_uses_the_sampled_query_and_scans_n(profile):
    """fn:subsequence over cts:uris(), total_scanned == N."""
    recorded: list[httpx.Request] = []
    result, _ = _measure(
        profile,
        eval_response=_eval_response(["3", "500"]),
        sampled_n=500,
        recorded=recorded,
    )

    assert result.method == SAMPLED
    assert result.total_scanned == 500
    assert result.evidence is False
    assert result.findings_are_sound is True

    posted = [r for r in recorded if r.url.path == "/v1/eval"][0].content.decode()
    assert "scan-limit" in posted


def test_sampled_query_applies_the_same_predicate():
    """The soundness claim holds only if the filter is byte-identical."""
    assert PERMISSION_FILTER in UNPERMISSIONED_SAMPLED_COUNT_AND_SAMPLE
    assert PERMISSION_FILTER in UNPERMISSIONED_COUNT_AND_SAMPLE


def test_truncation_is_flagged_against_the_true_total(profile):
    result, _ = _measure(
        profile,
        eval_response=_eval_response(["9", "100", "/a.xml", "/b.xml"]),
        list_uris=2,
    )

    assert result.unpermissioned == 9
    assert result.uris == ("/a.xml", "/b.xml")
    assert result.uris_listing == "truncated"


def test_no_truncation_when_every_offender_is_listed(profile):
    result, _ = _measure(
        profile,
        eval_response=_eval_response(["2", "100", "/a.xml", "/b.xml"]),
        list_uris=5,
    )

    assert result.uris_listing == "complete"


def test_empty_database_reports_zero(profile):
    result, _ = _measure(profile, eval_response=_eval_response(["0", "0"]))

    assert result.unpermissioned == 0
    assert result.evidence is True


def test_a_short_response_is_an_error_not_a_zero(profile):
    with pytest.raises(ParseError):
        _measure(profile, eval_response=_eval_response(["7"]))


def test_a_non_numeric_count_is_an_error(profile):
    with pytest.raises(ParseError):
        _measure(profile, eval_response=_eval_response(["lots", "100"]))
