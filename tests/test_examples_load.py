"""Every file in examples/ must load through the real loader.

An example that does not parse is worse than none. These tests drive both steps deploy drives,
because the reader holds the duplicate-key refusal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marklogic_tool.deploy.loader import read_declaration_file
from marklogic_tool.deploy.schema import Declaration, load_declaration

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _example_files() -> list[Path]:
    return sorted(EXAMPLES.glob("*.yaml")) + sorted(EXAMPLES.glob("*.yml"))


def test_the_examples_directory_is_where_this_gate_looks() -> None:
    # Assert the denominator. A moved or renamed directory would otherwise make every
    # parametrized test below silently disappear, and an empty parametrization passes.
    assert EXAMPLES.is_dir(), f"examples/ not found at {EXAMPLES}"
    found = _example_files()
    assert len(found) >= 2, (
        f"expected at least 2 example declarations, found {len(found)}: "
        f"{[p.name for p in found]}"
    )


@pytest.mark.parametrize("path", _example_files(), ids=lambda p: p.name)
def test_example_loads_through_the_real_loader(path: Path) -> None:
    declaration = load_declaration(read_declaration_file(path), source=path.name)
    assert isinstance(declaration, Declaration)
    assert declaration.version == 1
    # A declaration with no target host cannot be deployed anywhere, so an example that
    # omits it would parse and still be useless.
    assert declaration.target.hosts, f"{path.name} declares no target host"


@pytest.mark.parametrize("path", _example_files(), ids=lambda p: p.name)
def test_example_declares_no_literal_password(path: Path) -> None:
    """A literal password in an example is a literal password in someone's repo.

    The loader already refuses one, so this asserts the examples never invite the pattern
    in the first place.
    """
    declaration = load_declaration(read_declaration_file(path), source=path.name)
    for user in declaration.users:
        if user.password is None:
            continue
        assert user.password.startswith(("env:", "ssm:", "profile:")), (
            f"{path.name}: user {user.name!r} carries a literal password rather than a "
            f"secret reference"
        )
