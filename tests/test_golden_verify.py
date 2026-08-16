"""Golden-fixture contract tests for `marklogic-tool/unpermissioned/1`.

The consumer is an agent, so these fixtures are the contract. A renamed field breaks a
migration gate silently unless something here goes red first.
"""

# cspell:ignore normalised

import json
from pathlib import Path

import pytest

from marklogic_tool.output.render_verify import (
    ELAPSED_PLACEHOLDER,
    build_report,
    normalise_for_golden,
)
from marklogic_tool.verify.measure import EXHAUSTIVE, SAMPLED, Measurement

GOLDEN = Path(__file__).parent / "fixtures" / "golden"

PROVENANCE = {
    "identity": "admin",
    "identity_source": "profile",
    "endpoint": "http://ml.example.com:8000",
}

SHAPES = {
    "unpermissioned_exhaustive_clean": (
        Measurement(
            database="clean-db",
            method=EXHAUSTIVE,
            unpermissioned=0,
            total_scanned=191310,
            uris=(),
            uris_requested=0,
            uris_listing="not_requested",
            elapsed_seconds=12.3456,
        ),
        True,
        0,
    ),
    "unpermissioned_exhaustive_dirty": (
        Measurement(
            database="seeded-db",
            method=EXHAUSTIVE,
            unpermissioned=9,
            total_scanned=100,
            uris=("/a.xml", "/b.xml"),
            uris_requested=2,
            uris_listing="truncated",
            elapsed_seconds=0.87,
        ),
        True,
        7,
    ),
    "unpermissioned_sampled_dirty": (
        Measurement(
            database="seeded-db",
            method=SAMPLED,
            unpermissioned=2,
            total_scanned=500,
            uris=("/a.xml",),
            uris_requested=1,
            uris_listing="truncated",
            elapsed_seconds=0.42,
        ),
        True,
        7,
    ),
}


def _report(name):
    measurement, gate, exit_code = SHAPES[name]
    return normalise_for_golden(
        build_report(measurement, gate=gate, exit_code=exit_code, **PROVENANCE)
    )


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_golden_fixture_matches_byte_for_byte(name):
    fixture = GOLDEN / f"{name}.json"
    expected = fixture.read_text()
    actual = json.dumps(_report(name), indent=2, sort_keys=True) + "\n"

    assert actual == expected, (
        f"{fixture.name} drifted. If this change is intended, the wire contract "
        "changed and the schema version must move with it."
    )


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_elapsed_is_normalised_but_still_present(name):
    """The field must survive normalisation, or the fixture stops guarding it."""
    report = _report(name)
    assert report["elapsed_seconds"] == ELAPSED_PLACEHOLDER


def test_clean_and_dirty_differ_in_the_fields_that_matter():
    clean = _report("unpermissioned_exhaustive_clean")
    dirty = _report("unpermissioned_exhaustive_dirty")

    assert clean["unpermissioned"] == 0
    assert clean["exit_code"] == 0
    assert dirty["unpermissioned"] > 0
    assert dirty["exit_code"] == 7
    assert clean["evidence"] is dirty["evidence"] is True


def test_sampled_shape_is_not_evidence_but_is_sound():
    sampled = _report("unpermissioned_sampled_dirty")

    assert sampled["evidence"] is False
    assert sampled["findings_are_sound"] is True
    assert sampled["exit_code"] == 7
