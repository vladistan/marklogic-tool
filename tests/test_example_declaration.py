"""The shipped example declaration, validated against the real schema.

Loading a complete declaration found three gaps: `description` was mappable but undeclarable,
built-in roles read as dangling, and a self-grant read as a cycle.
"""

from pathlib import Path

import pytest

from marklogic_tool.deploy.loader import read_declaration_file
from marklogic_tool.deploy.mapping import expand_default_permissions, to_manage
from marklogic_tool.deploy.order import plan_order
from marklogic_tool.deploy.schema import load_declaration

DECLARATION = Path(__file__).resolve().parents[1] / "examples" / "application.yaml"


@pytest.fixture(scope="module")
def declaration():
    return load_declaration(read_declaration_file(DECLARATION), source=str(DECLARATION))


def test_it_loads_and_orders(declaration):
    order = plan_order(declaration)
    assert len(order) == 12


def test_it_matches_the_expected_inventory(declaration):
    """5 roles, 3 users, the schemas + content databases, one rest_api."""
    assert len(declaration.roles) == 5
    assert len(declaration.users) == 3
    assert len(declaration.rest_apis) == 1


def test_the_full_five_role_chain_is_present(declaration):
    """A partial chain grants DIFFERENT privileges under the same names.

    Declaring only -writer and -admin looks equivalent and is not: the inherited
    capabilities differ, so the example carries the whole chain.
    """
    names = {role.name for role in declaration.roles}
    assert names == {
        f"catalog-{suffix}"
        for suffix in ("nobody", "reader", "writer", "internal", "admin")
    }


def test_roles_are_ordered_before_the_roles_that_inherit_them(declaration):
    order = [str(node) for node in plan_order(declaration)]
    assert order.index("role:catalog-nobody") < order.index("role:catalog-reader")
    assert order.index("role:catalog-reader") < order.index("role:catalog-writer")


def test_users_come_after_their_roles(declaration):
    order = [str(node) for node in plan_order(declaration)]
    assert order.index("role:catalog-writer") < order.index("user:catalog-writer")


def test_schemas_database_precedes_the_content_database(declaration):
    order = [str(node) for node in plan_order(declaration)]
    assert order.index("database:catalog-schemas") < order.index(
        "database:catalog-content"
    )


# --- the two binding requirements ------------------------------------------------------


def test_binding_requirement_authentication_is_digestbasic(declaration):
    """A server left on digest refuses a client authenticating with basic, and that
    failure does not surface until cutover."""
    server = declaration.app_servers[0]
    assert server.authentication == "digest-basic"
    assert to_manage("app_server", {"authentication": server.authentication}) == {
        "authentication": "digestbasic"
    }


def test_binding_requirement_writer_role_carries_default_permissions(declaration):
    """Without this, a document can be written with an empty permission set."""
    writer = next(r for r in declaration.roles if r.name.endswith("-writer"))
    assert "default_permissions" in writer.model_fields_set
    assert writer.default_permissions


def test_the_writer_grant_covers_both_reading_and_writing(declaration):
    writer = next(r for r in declaration.roles if r.name.endswith("-writer"))
    expanded = expand_default_permissions(
        [p.model_dump() for p in writer.default_permissions]
    )
    assert {"role-name": "catalog-writer", "capability": "update"} in expanded
    assert {"role-name": "catalog-writer", "capability": "read"} in expanded
    # The reader must be able to see what the writer ingests, or a reader-facing view is
    # empty for exactly the reason this setting exists.
    assert {"role-name": "catalog-reader", "capability": "read"} in expanded


def test_the_example_explains_why_default_permissions_are_set():
    """Recorded, so nobody removes the setting as boilerplate.

    The failure it prevents is silent. An empty permission set yields zero results and no
    error. The explanation is part of the example, and this asserts it survives.
    """
    text = DECLARATION.read_text(encoding="utf-8")
    assert "EMPTY permission set" in text
    assert "zero results and NO error" in text


# --- the corpus prerequisites -----------------------------------------------------------


def test_uri_lexicon_is_declared(declaration):
    """A prerequisite of the exhaustive scan: without it verification refuses."""
    content = next(d for d in declaration.databases if d.name.endswith("-content"))
    assert content.uri_lexicon is True


def test_triple_index_is_declared(declaration):
    """Exercises a second boolean index property through the same path."""
    content = next(d for d in declaration.databases if d.name.endswith("-content"))
    assert content.triple_index is True


def test_no_password_literal_appears_anywhere(declaration):
    """Every password is a reference. None is a value.

    This checks reference-ness, not one scheme. The example shows both `env:` and `ssm:`, so a
    single-prefix pin fails on the file being more complete.
    """
    for user in declaration.users:
        assert user.password is not None
        assert user.password.startswith(("env:", "ssm:", "profile:"))
