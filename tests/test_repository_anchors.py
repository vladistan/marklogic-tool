"""The guard on the two skips. A skip that hides is worse than a failure that shows.

An anchor deleted in the source tree stops those two tests enforcing anything. So this
asserts consistency, not existence.
"""

from __future__ import annotations

from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]

# Every unshipped file some other test treats as required. Adding a path-keyed skipif
# anywhere in the suite means adding its anchor here too, or that skip becomes silent.
_ANCHORS = (
    Path("PUB_MANIFEST.md"),
    Path("plans") / "briefs" / "ACCEPTANCE-6.5.md",
)


def _present() -> list[Path]:
    return [a for a in _ANCHORS if (TOOL / a).is_file()]


def test_the_anchor_set_is_all_or_nothing() -> None:
    present = _present()
    missing = [a for a in _ANCHORS if a not in present]
    assert len(present) in (0, len(_ANCHORS)), (
        "repository anchors are inconsistent, so an invariant test is skipping silently.\n"
        f"  present: {[a.as_posix() for a in present]}\n"
        f"  missing: {[a.as_posix() for a in missing]}\n"
        "Either restore the missing file, or if it moved deliberately, update the "
        "skipif in the test that requires it AND the _ANCHORS list here."
    )


def test_the_plans_tree_and_its_brief_agree() -> None:
    """A narrower catch: the brief moved inside a tree that is still present.

    The all-or-nothing check above sees one anchor present and one absent. This names the
    likelier accident directly.
    """
    plans = TOOL / "plans"
    if not plans.is_dir():
        return  # no plans/ at all is the public-clone shape, and is not an inconsistency
    brief = TOOL / "plans" / "briefs" / "ACCEPTANCE-6.5.md"
    assert brief.is_file(), (
        f"plans/ exists but {brief.relative_to(TOOL).as_posix()} does not, so "
        "test_acceptance_brief_tally.py is skipping and its invariant is unenforced"
    )


def test_this_guard_names_at_least_two_anchors() -> None:
    """Assert the denominator: an empty _ANCHORS makes every check above vacuous."""
    assert len(_ANCHORS) >= 2, (
        f"_ANCHORS has {len(_ANCHORS)} entries; the guard is inert"
    )
