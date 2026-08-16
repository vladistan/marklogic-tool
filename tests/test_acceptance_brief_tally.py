# cspell:ignore unticked UNTICKED
"""The acceptance brief tally must match its own checkboxes.

The two drift the moment somebody ticks a box without editing the sentence. A comment asking
people to keep them in step can be ignored. This cannot.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

BRIEF = Path(__file__).resolve().parents[1] / "plans" / "briefs" / "ACCEPTANCE-6.5.md"

# plans/ is a repository record and never ships -- see the same note in test_charter.py.
# Skips rather than fails outside the source tree, keyed on the required path itself, with
# tests/test_repository_anchors.py as the guard against that skip going unnoticed here.
pytestmark = pytest.mark.skipif(
    not BRIEF.is_file(),
    reason=f"{BRIEF.name} is absent: this asserts a repository invariant and does not "
    "apply outside the source tree",
)

# Only top-level criteria checkboxes count: they start at column 0. Clause-level boxes are
# indented, and counting those would inflate the denominator.
_TICKED = re.compile(r"^- \[x\] ", re.MULTILINE)
_UNTICKED = re.compile(r"^- \[ \] ", re.MULTILINE)
_STATED = re.compile(
    r"\*\*(?P<total>\d+) criteria\. (?P<ticked>\d+) ticked, (?P<partials>\d+) partials?\.\*\*"
)


def _count(text: str) -> tuple[int, int]:
    """Return (ticked, unticked) top-level criteria in `text`."""
    return len(_TICKED.findall(text)), len(_UNTICKED.findall(text))


def test_the_brief_exists_where_the_gate_looks_for_it() -> None:
    # Without this the whole module passes vacuously if the brief is ever moved or renamed.
    assert BRIEF.is_file(), f"acceptance brief not found at {BRIEF}"


def test_stated_tally_matches_the_checkboxes() -> None:
    text = BRIEF.read_text(encoding="utf-8")
    ticked, unticked = _count(text)

    # Assert the denominator: a scan that reaches nothing passes every other assertion here.
    assert ticked + unticked >= 20, (
        f"gate only found {ticked + unticked} top-level criteria in {BRIEF.name}; "
        "the checkbox format probably changed and this gate has stopped discriminating"
    )

    stated = _STATED.search(text)
    assert stated is not None, (
        "no tally sentence of the form '**N criteria. N ticked, N partials.**' in "
        f"{BRIEF.name} -- if the wording changed, update this gate deliberately"
    )

    total = int(stated["total"])
    assert total == ticked + unticked, (
        f"brief states {total} criteria but carries {ticked + unticked} checkboxes"
    )
    assert int(stated["ticked"]) == ticked, (
        f"brief states {stated['ticked']} ticked but {ticked} boxes are ticked"
    )
    assert int(stated["partials"]) == unticked, (
        f"brief states {stated['partials']} partials but {unticked} boxes are unticked"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("- [x] a\n- [x] b\n- [ ] c\n", (2, 1)),
        # Indented clause boxes are NOT criteria -- counting them would inflate the tally.
        ("- [x] a\n  - [ ] a clause\n  1. [ ] another\n", (1, 0)),
        # A checkbox inside a fenced block or quoted example is still column-0 markdown; the
        # brief has none, and this pins the parser's actual behaviour rather than a wish.
        ("Some prose mentioning - [x] inline\n", (0, 0)),
    ],
)
def test_the_counter_counts_criteria_and_not_clauses(
    text: str, expected: tuple[int, int]
) -> None:
    assert _count(text) == expected


def test_the_gate_fails_when_the_tally_drifts() -> None:
    """The must-fail twin: without it, the gate could stop discriminating unnoticed."""
    drifted = "**3 criteria. 3 ticked, 0 partials.**\n" + "- [x] a\n" * 2 + "- [ ] b\n"
    stated = _STATED.search(drifted)
    assert stated is not None
    ticked, unticked = _count(drifted)
    assert int(stated["ticked"]) != ticked or int(stated["partials"]) != unticked, (
        "the comparison this gate performs cannot detect a drifted tally"
    )
