"""Run the detection query. Guard its preconditions. Translate the time limits.

The lexicon pre-check runs here, never in the command layer. `cts:uris()` returns nothing
without the lexicon, and that under-report looks like a clean corpus.
"""

import json
import time
from dataclasses import dataclass

from marklogic_tool.core.client import MarkLogicClient
from marklogic_tool.core.exceptions import ConfigurationError, ParseError
from marklogic_tool.core.manage_client import ManageClient
from marklogic_tool.core.response import parse_multipart_mixed
from marklogic_tool.verify.queries import (
    UNPERMISSIONED_COUNT_AND_SAMPLE,
    UNPERMISSIONED_SAMPLED_COUNT_AND_SAMPLE,
)

EXHAUSTIVE = "exhaustive"
SAMPLED = "sampled"

# The three states a URI listing can be in. An enumerated string rather than a
# boolean, because a boolean can only ever encode two of them honestly.
#
# The rejected alternative was a tri-state `uris_truncated` with null for "not
# asked". It fails the one requirement that matters: a consumer writing the
# obvious `if not payload["uris_truncated"]` collapses null and false into the
# same branch, so "you did not ask" reads as "nothing was cut off" — and beside
# `unpermissioned: 9` that reads as "no offenders". Null-vs-false is exactly the
# distinction weakly-typed consumers lose, and this field exists to survive them.
#
# One fact, one field: `uris_truncated` is gone rather than kept alongside, so
# there is no second source of truth to drift from this one.
NOT_REQUESTED = "not_requested"
COMPLETE = "complete"
TRUNCATED = "truncated"


@dataclass(frozen=True, slots=True)
class VerifyClients:
    """The two endpoints a measurement needs, bound once by the command layer."""

    query: MarkLogicClient
    manage: ManageClient


@dataclass(frozen=True, slots=True)
class Measurement:
    """What was observed, and how much of the corpus it covered."""

    database: str
    method: str
    unpermissioned: int
    total_scanned: int
    uris: tuple[str, ...]
    uris_requested: int
    uris_listing: str
    elapsed_seconds: float

    @property
    def evidence(self) -> bool:
        """Only an exhaustive scan proves absence."""
        return self.method == EXHAUSTIVE

    @property
    def findings_are_sound(self) -> bool:
        """Sampling can only under-report, so any offender it found is real."""
        return True


def measure_unpermissioned(
    clients: VerifyClients,
    database: str,
    *,
    sampled_n: int | None,
    list_uris: int,
) -> Measurement:
    """Count documents with an empty permission set.

    `sampled_n=None` means exhaustive. A timeout raises. It never degrades to a sampled
    result, because a partial answer reported as a whole one hides the defect.
    """
    require_uri_lexicon(clients.manage, database)

    sampled = sampled_n is not None
    query = (
        UNPERMISSIONED_SAMPLED_COUNT_AND_SAMPLE
        if sampled
        else UNPERMISSIONED_COUNT_AND_SAMPLE
    )
    variables = {"sample-size": list_uris}
    if sampled_n is not None:
        variables["scan-limit"] = sampled_n

    started = time.monotonic()
    values = _eval(clients.query, query, database=database, variables=variables)
    elapsed = time.monotonic() - started

    if len(values) < 2:
        raise ParseError(
            f"The unpermissioned scan of '{database}' returned "
            f"{len(values)} values, but the query returns a count, a scanned "
            "total and then the URI sample. No count is reported rather than a "
            "guessed one."
        )

    unpermissioned = _as_int(values[0], "the unpermissioned count", database)
    total_scanned = _as_int(values[1], "the scanned total", database)
    uris = tuple(values[2:])

    return Measurement(
        database=database,
        method=SAMPLED if sampled else EXHAUSTIVE,
        unpermissioned=unpermissioned,
        total_scanned=total_scanned,
        uris=uris,
        uris_requested=list_uris,
        uris_listing=_listing_state(list_uris, unpermissioned, len(uris)),
        elapsed_seconds=elapsed,
    )


def _listing_state(requested: int, unpermissioned: int, returned: int) -> str:
    """Classify the URI listing into exactly one of three named states."""
    if requested <= 0:
        return NOT_REQUESTED
    return TRUNCATED if unpermissioned > returned else COMPLETE


def require_uri_lexicon(client: ManageClient, database: str) -> None:
    """Refuse when the URI lexicon is off.

    Without it `cts:uris()` returns nothing, and the scan reports zero offenders on a corpus
    full of them. Absence of the property is also a refusal.
    """
    payload = client.get_json(f"/manage/v2/databases/{database}/properties")
    enabled = payload.get("uri-lexicon")

    if enabled is True:
        return

    observed = "disabled" if enabled is False else "not reported"
    raise ConfigurationError(
        f"The URI lexicon is {observed} on database '{database}', so "
        "cts:uris() cannot enumerate the corpus and the scan would report zero "
        "offenders whatever the data holds. This is refused rather than "
        "under-reported. Enable the 'uri lexicon' setting on the database, then "
        "re-run."
    )


def _eval(
    client: MarkLogicClient,
    query: str,
    *,
    database: str,
    variables: dict[str, int],
) -> list[str]:
    """Run one eval and return its values in order.

    `core.http.check_response` translates XDMP-EXTIME. It reads the body's error
    code before the carrier status, because the server reports XDMP-EXTIME
    inside both 400 and 500 responses.
    """
    response = client.post(
        "/v1/eval",
        data={
            "xquery": query,
            "database": database,
            "vars": json.dumps(variables),
        },
    )
    content_type = response.headers.get("content-type", "")
    if "multipart/mixed" not in content_type:
        raise ParseError(
            f"The unpermissioned scan of '{database}' returned "
            f"{content_type or 'no content type'} rather than multipart/mixed, "
            "so its values cannot be read. Check that the request reached "
            "/v1/eval."
        )
    results = parse_multipart_mixed(content_type, response.content)
    return [result.value for result in results]


def _as_int(raw: str, what: str, database: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError) as e:
        raise ParseError(
            f"The unpermissioned scan of '{database}' returned {raw!r} for "
            f"{what}, which is not a number. No count is reported rather than a "
            "guessed one."
        ) from e
