# cspell:ignore SPA docke brul bpla brie bwit bharve
"""No packaged file may carry internal planning vocabulary.

The rule is: strip the CITATION, keep the CONSTRAINT.

The patterns come from fragments, so this file cannot match itself.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_no_private_info_ships import TOOL, _packaged_files

# Fragments, never whole literals -- including the labels, which are also text in this file.
_PLANNING: dict[str, re.Pattern[str]] = {
    "requirement-id": re.compile(r"\b(?:F" + "R|NF" + r"R)-[0-9]+"),
    # Two alternatives, both needed. The first catches a standalone id, guarded by a
    # negative lookbehind for "." so a hostname label (the permitted DSN's subdomain) is not
    # mistaken for one. The second catches an id embedded in a snake_case identifier, which
    # the first cannot: `\b` does not match between an underscore and a letter, so
    # word-boundary matching alone misses every id inside an identifier.
    "decision-id": re.compile(
        r"(?<!\.)\b[Rr][0-9]+(?:\.[0-9]+)*\b|_[Rr][0-9]+(?=_|\b)"
    ),
    "architecture-id": re.compile(r"\bA-?[0-9]+\b"),
    "defect-id": re.compile(r"\bD[0-9]+\b"),
    "phase": re.compile(r"\bPha" + r"se\s+[0-9]+", re.IGNORECASE),
    "step": re.compile(r"\bSt" + r"ep\s+[0-9]+\.[0-9]+", re.IGNORECASE),
    "methodology": re.compile(r"\bSPA" + r"RC\b", re.IGNORECASE),
    "decision-word": re.compile(r"\brul" + r"ing\b", re.IGNORECASE),
    "queue-word": re.compile(r"\bdocke" + r"t\b", re.IGNORECASE),
    # Internal document paths and message ids. A citation of an internal plan IS planning
    # language; neither of us listed these patterns first time round, and an error message
    # naming a document the reader will never have is worse than one that says nothing.
    "internal-plan-path": re.compile(r"\bpla" + r"ns/"),
    "internal-brief-path": re.compile(r"\bbrie" + r"fs/"),
    "publication-manifest": re.compile(r"PUB" + r"_MANIFEST"),
    "message-id": re.compile(r"\bm-[0-9a-f]{8,}\b"),
    # Epistemic framing: vocabulary describing how WE came to know something. It tells the
    # reader nothing they can use -- the FACT survives without it, and is more useful once
    # the framing is gone.
    #
    # Deliberately NOT included, because each has a legitimate technical meaning here and a
    # gate that fires on true statements gets disabled: `probe` (you probe an endpoint),
    # `measured` (as in "measured against a live server", a fact about the code's basis),
    # and `inferred` (the observed-vs-inferred distinction is a real property of the code).
    # Agent callsigns. A callsign identifies a member of our fleet, so it is planning
    # language by exactly the argument that makes a plan citation one. Matched with or
    # without a suffix, because the bare words are our role vocabulary rather than English.
    "agent-callsign": re.compile(
        r"\b(?:gat" + r"to|to" + r"ro|ta" + r"vuk)(?:-[A-Za-z0-9]+)?\b", re.IGNORECASE
    ),
    # Internal section references, such as a paragraph id in one of our briefs. The section
    # sign is written as an escape, so this file holds no literal one to match.
    "section-reference": re.compile("\u00a7" + r"\s*[0-9]"),
    "epistemic-framing": re.compile(
        r"\bwit" + r"ness(?:ed|es)?\b|\bharve" + r"st(?:ed)?\b", re.IGNORECASE
    ),
}

# Files whose SUBJECT is one of those paths, rather than files that merely cite one. The
# distinction is the whole reason this is an allowlist and not a weakened pattern: if a
# file's job is to detect or exclude an internal path, it has to be able to name it.
#
# Allowlisted BY EXACT PATH, never by pattern -- a pattern would re-admit the whole class.
# Every entry carries a reason, and `test_the_allowlist_stays_minimal` fails if the list
# grows, so a new entry is a deliberate decision rather than a quiet one.
_SUBJECT_ALLOWLIST: dict[str, str] = {
    "tests/test_repository_anchors.py": (
        "its entire job is detecting whether those repository anchors are present, so it "
        "must name them"
    ),
    "pyproject.toml": (
        "the sdist exclusion list has to name the paths it excludes, which is what keeps "
        "them out of the artifact in the first place"
    ),
}

_SCANNED_SUFFIXES = frozenset({".py", ".md", ".toml", ".yaml", ".yml", ".cfg", ".txt"})

# Only these labels can ever be allowlisted. An allowance for a requirement id or for
# process jargon would have no legitimate subject, so the allowlist cannot reach them.
_PATH_LABELS = frozenset(
    {"internal-plan-path", "internal-brief-path", "publication-manifest", "message-id"}
)


def _scan(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        if path.suffix not in _SCANNED_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        try:
            name = path.relative_to(TOOL).as_posix()
        except ValueError:
            name = path.name
        allowed = _SUBJECT_ALLOWLIST.get(name)
        for label, pattern in _PLANNING.items():
            found = pattern.findall(text)
            if not found:
                continue
            # The allowance covers ONLY the path/id classes, and only for the named file.
            # Requirement ids and process jargon are never legitimate anywhere.
            if allowed and label in _PATH_LABELS:
                continue
            findings.append(f"{name}: {label} {sorted(set(found))[:4]}")
    return sorted(findings)


def test_the_scan_reaches_the_package() -> None:
    """Assert the denominator: a scan of nothing passes everything."""
    packaged = _packaged_files()
    assert len(packaged) > 100, (
        f"scan reached only {len(packaged)} packaged file(s); the layout probably moved "
        "and this gate has stopped discriminating"
    )
    scanned = [p for p in packaged if p.suffix in _SCANNED_SUFFIXES]
    assert len(scanned) > 100, (
        f"only {len(scanned)} packaged file(s) have a scanned suffix"
    )


def test_the_scanner_fires_when_something_is_planted(tmp_path: Path) -> None:
    """The positive controls. Without these, a green run proves nothing at all."""
    samples = {
        "requirement-id": "# F" + "R-012 requires this\n",
        "decision-id": "# settled as R" + "8.11\n",
        "phase": "# measured in Pha" + "se 1\n",
        "decision-word": "# by rul" + "ing of the reviewer\n",
        "defect-id": "# fixes D" + "13\n",
        "agent-callsign": "# raised by ga" + "tto-REL\n",
        "section-reference": "# see " + "\u00a7" + "3.5b\n",
    }
    for label, sample in samples.items():
        planted = tmp_path / f"planted_{label}.py"
        planted.write_text(sample, encoding="utf-8")
        assert _scan([planted]), f"the scanner failed to find planted {label}"

    # Negative controls. A gate that fires on true statements gets disabled, so these must
    # stay green: `toroidal` contains a callsign as a substring but is an ordinary word, and a
    # section sign with no number is not a reference.
    for benign in (
        "# the toroidal field\n",
        "# cost in \u00a7 terms\n",
        "# probe the endpoint\n",
    ):
        harmless = tmp_path / "harmless.py"
        harmless.write_text(benign, encoding="utf-8")
        assert not _scan([harmless]), f"the scanner fired on benign prose: {benign!r}"

    clean = tmp_path / "clean.py"
    clean.write_text(
        "# the two timeout messages must stay distinct\n", encoding="utf-8"
    )
    assert not _scan([clean]), "the scanner reported a finding in a clean file"


def test_no_packaged_file_carries_planning_language() -> None:
    findings = _scan(_packaged_files())
    assert not findings, (
        "internal planning vocabulary in packaged file(s). Strip the CITATION and keep the "
        "CONSTRAINT, never the other way round:\n  " + "\n  ".join(findings)
    )


def test_the_allowlist_stays_minimal() -> None:
    """Growth must be a decision, not a habit.

    Every hole in a gate is permanent unless someone notices it. Pinning the size makes this
    test fail, so whoever raises the bound states a reason.
    """
    assert len(_SUBJECT_ALLOWLIST) <= 2, (
        f"the allowlist has grown to {len(_SUBJECT_ALLOWLIST)} entries "
        f"({sorted(_SUBJECT_ALLOWLIST)}). Before raising this bound: is the new file's "
        "SUBJECT an internal path, or is it merely CITING one? Only the first justifies an "
        "entry -- the second should be reworded, or fragmented as other files here do."
    )
    for path, reason in _SUBJECT_ALLOWLIST.items():
        assert len(reason) > 40, f"{path} is allowlisted without a real reason"


def test_every_allowlisted_file_still_needs_its_allowance() -> None:
    """A stale entry is an unnecessary hole, so each allowance must still be load-bearing.

    If a file stops naming an internal path, its entry must go rather than sit there
    covering whatever that file does next.
    """
    for relative in _SUBJECT_ALLOWLIST:
        path = TOOL / relative
        assert path.is_file(), f"{relative} is allowlisted but does not exist"
        text = path.read_text(encoding="utf-8")
        hits = [
            label for label in _PATH_LABELS if _PLANNING[label].search(text) is not None
        ]
        assert hits, (
            f"{relative} no longer names an internal path, so its allowlist entry is "
            "obsolete and should be deleted"
        )


def test_the_allowlist_cannot_excuse_a_requirement_id(tmp_path: Path) -> None:
    """The allowance is scoped to path classes only, and this proves the scoping holds."""
    assert not (_PATH_LABELS & {"requirement-id", "decision-word", "methodology"})
    # An allowlisted file carrying a requirement id must still be reported.
    allowlisted = TOOL / next(iter(_SUBJECT_ALLOWLIST))
    original = allowlisted.read_text(encoding="utf-8")
    try:
        allowlisted.write_text(
            "# F" + "R-999 requires this\n" + original, encoding="utf-8"
        )
        findings = _scan([allowlisted])
        assert any("requirement-id" in f for f in findings), (
            "the allowlist excused a requirement id, so it is scoped too widely"
        )
    finally:
        allowlisted.write_text(original, encoding="utf-8")
    assert allowlisted.read_text(encoding="utf-8") == original


def test_this_file_is_not_its_own_exception() -> None:
    """The fragments must work, or this gate exempts itself.

    If this fails, someone spelled a planning term whole in this module. Re-fragment it. Do
    not add an exemption.
    """
    findings = _scan([Path(__file__)])
    assert not findings, f"this gate matches itself: {findings}"
