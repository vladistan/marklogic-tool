"""The pure-core boundary, enforced.

The rule is: no I/O below `reconcile` except the client seam.

This walks the AST. An import-based check passes when another test imports httpx first.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "marklogic_tool"

FORBIDDEN = {"httpx", "os", "subprocess"}

# The tuple is the target set, and modules that do
# not exist yet are skipped rather than silently dropping the assertion.
PURE_MODULES = ("diff", "mapping", "order", "plan")


def _module_path(name: str) -> Path:
    return SRC / "deploy" / f"{name}.py"


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("module", PURE_MODULES)
def test_pure_module_imports_no_io(module):
    path = _module_path(module)
    if not path.exists():
        pytest.skip(f"deploy/{module}.py not built yet")
    offending = _imported_roots(path) & FORBIDDEN
    assert not offending, f"deploy/{module}.py imports {sorted(offending)}"


def test_at_least_the_built_pure_modules_are_covered():
    """Guard the guard: if every module were missing, the sweep above would be empty."""
    built = [m for m in PURE_MODULES if _module_path(m).exists()]
    assert len(built) >= 3, f"expected the built pure modules, found {built}"


def test_the_purity_check_can_actually_fail(tmp_path):
    """Mutation guard: prove the detector fires on a module that does import I/O."""
    impure = tmp_path / "impure.py"
    impure.write_text("import os\nfrom httpx import Client\n", encoding="utf-8")
    assert _imported_roots(impure) & FORBIDDEN == {"os", "httpx"}


def test_relative_imports_do_not_confuse_the_detector(tmp_path):
    sample = tmp_path / "rel.py"
    sample.write_text("from . import sibling\nimport json\n", encoding="utf-8")
    assert _imported_roots(sample) == {"json"}


def test_schema_only_reaches_mapping_for_the_deny_list():
    """schema.py imports the deny-list constant, and nothing else from mapping."""
    path = SRC / "deploy" / "schema.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "marklogic_tool.deploy.mapping"
        ):
            imported.update(alias.name for alias in node.names)
    assert imported == {"denied_properties_matching"}


def test_no_module_outside_mapping_references_a_manage_property_name():
    """Manage names live in mapping.py. Elsewhere they appear only as data.

    The name set comes from `KIND_MAPPINGS`. This checks hyphenated spellings only, because
    Manage shares plain words like `port` with the user vocabulary.
    """
    from marklogic_tool.deploy.mapping import KIND_MAPPINGS

    manage_native = {
        manage_name
        for mapping in KIND_MAPPINGS.values()
        for manage_name in mapping.properties.values()
        if "-" in manage_name
    }
    assert manage_native, "expected some hyphenated Manage names to guard"

    for path in (SRC / "deploy").glob("*.py"):
        if path.name == "mapping.py":
            continue
        for literal in _code_string_literals(path):
            for name in manage_native:
                assert name not in literal, (
                    f"{path.name} hard-codes Manage name {name!r}"
                )


def _code_string_literals(path: Path) -> list[str]:
    """Return the string literals that are values. Exclude the documentation.

    A bare string statement is prose, not a value. Prose that explains why a Manage name
    lives in mapping.py must not trip the rule.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    documentation = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in documentation
    ]


def test_the_manage_name_guard_can_actually_fail(tmp_path):
    """Mutation guard: a hard-coded name is caught, the same name in prose is not."""
    offender = tmp_path / "leaky.py"
    offender.write_text('X = "content-database"\n', encoding="utf-8")
    assert "content-database" in _code_string_literals(offender)

    documented = tmp_path / "documented.py"
    documented.write_text(
        '"""Explains that content-database lives in mapping.py."""\n'
        "X = 1\n"
        '"""An attribute docstring mentioning modules-database."""\n',
        encoding="utf-8",
    )
    assert _code_string_literals(documented) == []
