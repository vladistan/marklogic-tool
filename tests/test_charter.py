"""The charter check.

It matches the charter CLAIM, not every occurrence of "read-only". A bare grep also flags
legitimate advice in SKILL.md and the release row in the manifest. Both must survive.
"""

from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1]

# PUB_MANIFEST.md is a repository record and never ships. This file therefore cannot run
# in the public clone's tree, where the file does not exist -- and a permanently red gate
# trains people to ignore gates, so it SKIPS there instead of failing.
#
# Keyed on the required path itself, deliberately: not on an env var, not on "am I in a
# clone". Those can be set wrongly, and a skip that fires when it should not is invisible.
# The companion guard is tests/test_repository_anchors.py, which fails loudly if this file
# is present while its anchor has been deleted or moved -- because a skip that can hide is
# worse than a failure that shows.
_REQUIRED = TOOL / "PUB_MANIFEST.md"

pytestmark = pytest.mark.skipif(
    not _REQUIRED.is_file(),
    reason=f"{_REQUIRED.name} is absent: this asserts a repository invariant and does not "
    "apply outside the source tree",
)

CHARTER = (
    "CLI to query, verify, deploy and destroy MarkLogic Server configuration "
    "via the REST and Management APIs"
)

CHARTER_SITES = [
    TOOL / "src" / "marklogic_tool" / "cli.py",
    TOOL / "src" / "marklogic_tool" / "__init__.py",
    TOOL / "README.md",
    TOOL / "pyproject.toml",
    TOOL / "SKILL.md",
    TOOL / "PUB_MANIFEST.md",
]

# A claim that the tool IS read-only, as opposed to advice about how to USE it.
CLAIM_PHRASES = (
    "Read-only CLI",
    "read-only CLI",
    "Read-only MarkLogic",
)


@pytest.mark.parametrize("path", CHARTER_SITES, ids=lambda p: p.name)
def test_every_charter_site_carries_the_new_charter(path):
    assert CHARTER in path.read_text(), f"{path.name} does not carry the charter"


def test_no_live_charter_claim_survives():
    """The false 'read-only' claim is gone everywhere it was an assertion."""
    offenders: list[str] = []

    for path in CHARTER_SITES:
        text = path.read_text()
        for lineno, line in enumerate(text.splitlines(), 1):
            if path.name == "PUB_MANIFEST.md" and _is_release_history(line):
                continue
            for phrase in CLAIM_PHRASES:
                if phrase in line:
                    offenders.append(f"{path.name}:{lineno}: {line.strip()}")

    assert not offenders, "surviving charter claim(s): " + "; ".join(offenders)


def test_skill_md_keeps_its_operational_guidance():
    """advice about production profiles is not a charter claim."""
    text = (TOOL / "SKILL.md").read_text()

    assert "read-only behavior" in text
    assert "On production profiles" in text


def test_published_release_history_is_not_rewritten():
    """0.0.1 really did ship claiming read-only; that record stays true."""
    lines = (TOOL / "PUB_MANIFEST.md").read_text().splitlines()
    history = [line for line in lines if _is_release_history(line)]

    assert history, "expected a version-history row"
    assert any("Read-only MarkLogic" in line for line in history), (
        "the 0.0.1 row records what was actually published and must not be edited"
    )


def test_skill_md_no_longer_tells_agents_the_tool_never_mutates():
    """The absolute claim is gone; the qualified production-profile advice stays."""
    text = (TOOL / "SKILL.md").read_text()
    assert CHARTER in text

    for line in text.splitlines():
        if "never mutations" in line:
            assert "production profiles" in line.lower(), (
                "an unqualified 'never mutations' instruction survived"
            )


def test_acceptance_pairing_is_documented_as_the_procedure():
    readme = (TOOL / "README.md").read_text()

    assert "Acceptance procedure" in readme
    assert "--as-user writer" in readme
    assert "count-unpermissioned" in readme
    assert "identical" in readme


def test_per_command_exit_table_is_documented():
    for path in (TOOL / "README.md", TOOL / "SKILL.md"):
        text = path.read_text()
        assert "Per-command exit codes" in text, path.name
        assert "count-unpermissioned" in text, path.name


def _is_release_history(line: str) -> bool:
    """A version-history table row: | <version> | <date> | ... |"""
    parts = [p.strip() for p in line.split("|")]
    return len(parts) > 3 and parts[1][:1].isdigit() and "-" in parts[2]
