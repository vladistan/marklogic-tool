# cspell:ignore txnext arsen
"""No packaged file may carry internal infrastructure detail.

`tests/` ships, so every test file is a published file.

The file set comes from the pyproject sdist exclusions. Reach is asserted with a control.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]

# Fragments, never whole literals -- see point 3 in the module docstring.
_FORBIDDEN: dict[str, re.Pattern[str]] = {
    "internal-project-a": re.compile("tax" + "time", re.IGNORECASE),
    "internal-project-b": re.compile("txnext" + "gen", re.IGNORECASE),
    "internal-org": re.compile("arsen" + "ale", re.IGNORECASE),
    "private-network": re.compile("192" + r"\.168\."),
    "internal-subdomain": re.compile(r"\." + "r" + r"4\."),
    "absolute-home-path": re.compile(r"/(?:Use" + "rs|ho" + "me)/[A-Za-z0-9_.-]+/"),
}

# The ONE permitted occurrence, allowed at exactly one path and no other.
#
# The Sentry DSN host carries an internal subdomain and ships deliberately until the
# release-time rewrite. It is allowed HERE ONLY, and `internal-subdomain` still fails
# everywhere else. Allowing it by broad pattern instead is what made the earlier manual
# check cry wolf and get ignored.
# Fragmented like the patterns above: spelling this host whole would make THIS file
# match `internal-subdomain`, which is how the self-check below caught it.
_DSN_HOST = "sentry." + "r" + "4." + "v-lad.org"
_DSN_PATH = "src/marklogic_tool/core/monitoring.py"

# Generated or environment-local trees that are never part of the distribution. Anything
# that SHOULD be excluded from the artifact belongs in the pyproject sdist stanza instead,
# so that the artifact and this gate cannot disagree.
_NON_SOURCE_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".claude",
        "dist",
        "build",
        "htmlcov",
        "node_modules",
    }
)


def _sdist_exclusions() -> list[Path]:
    config = tomllib.loads((TOOL / "pyproject.toml").read_text(encoding="utf-8"))
    excludes = config["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"]
    return [TOOL / entry.lstrip("/") for entry in excludes]


def _packaged_files() -> list[Path]:
    excluded = _sdist_exclusions()
    found: list[Path] = []
    for path in TOOL.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _NON_SOURCE_DIRS for part in path.relative_to(TOOL).parts):
            continue
        if any(path == ex or ex in path.parents for ex in excluded):
            continue
        found.append(path)
    return found


def _scan(paths: list[Path]) -> list[str]:
    """Return one finding per (file, pattern), with the permitted DSN removed."""
    findings: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # a binary asset cannot carry a readable internal name
        try:
            relative = path.relative_to(TOOL).as_posix()
        except ValueError:
            # A path outside the tool tree: the control files in the tests below. They have
            # no packaged location, so no path-scoped allowance can apply to them.
            relative = path.name
        for label, pattern in _FORBIDDEN.items():
            hits = pattern.findall(text)
            if not hits:
                continue
            # Permit ONLY the known DSN host, at its one path, and only as many times as
            # that host actually appears -- an extra match at that path still fails.
            if (
                label == "internal-subdomain"
                and relative == _DSN_PATH
                and len(hits) == text.count(_DSN_HOST)
            ):
                continue
            findings.append(f"{relative}: {label} ({len(hits)}x)")
    return sorted(findings)


def test_the_scan_reaches_the_package() -> None:
    """Assert the denominator: a scan of nothing passes everything."""
    packaged = _packaged_files()
    assert len(packaged) > 100, (
        f"scan reached only {len(packaged)} packaged file(s); the layout probably moved "
        "and this gate has stopped discriminating"
    )
    names = {p.relative_to(TOOL).as_posix() for p in packaged}
    # Spot-check both halves: source ships, and so do the tests.
    assert _DSN_PATH in names, f"{_DSN_PATH} not in the packaged set"
    assert "tests/test_cli.py" in names, "tests/ are supposed to ship"


def test_the_scanner_fires_when_something_is_planted(tmp_path: Path) -> None:
    """The positive control. Without this, a green run proves nothing at all."""
    planted = tmp_path / "planted.py"
    planted.write_text("HOST = '" + "tax" + "time" + "-2023'\n", encoding="utf-8")
    assert _scan([planted]), "the scanner failed to find a planted internal name"

    clean = tmp_path / "clean.py"
    clean.write_text("HOST = 'ml.example.com'\n", encoding="utf-8")
    assert not _scan([clean]), "the scanner reported a finding in a clean file"


def test_the_dsn_allowance_is_narrow(tmp_path: Path) -> None:
    """The allowance must be path-scoped, or it is an allowance for the whole pattern."""
    elsewhere = tmp_path / "elsewhere.py"
    elsewhere.write_text(f"DSN = 'https://key@{_DSN_HOST}/1'\n", encoding="utf-8")
    findings = _scan([elsewhere])
    assert findings, (
        "the permitted DSN host was accepted OUTSIDE its one allowed path, so the "
        "allowance is a pattern-wide hole rather than a single exception"
    )


def test_no_packaged_file_carries_private_info() -> None:
    findings = _scan(_packaged_files())
    assert not findings, "private information in packaged file(s):\n  " + "\n  ".join(
        findings
    )


def test_this_file_is_not_its_own_exception() -> None:
    """The fragments must work, or the gate exempts this file.

    If this fails, someone wrote a forbidden literal into this module. Re-fragment it. Do not
    add an exemption.
    """
    findings = _scan([Path(__file__)])
    assert not findings, f"this gate matches itself: {findings}"
