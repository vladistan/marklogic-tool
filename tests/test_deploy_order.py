"""Dependency ordering.

Determinism is a safety property here, not a nicety: `--dry-run` and `apply` share one
code path, so an order that depended on YAML position would make the reviewed plan
differ from the applied one.
"""

import pytest

from marklogic_tool.core.exceptions import ExitCode
from marklogic_tool.deploy.errors import DanglingReferenceError, DependencyCycleError
from marklogic_tool.deploy.order import Node, plan_order, teardown_order
from marklogic_tool.deploy.schema import load_declaration


def build(**extra):
    base = {"version": 1, "target": {"hosts": ["ml-01.example.test"]}}
    base.update(extra)
    return load_declaration(base)


def index_of(order, kind, name):
    return order.index(Node(kind, name))


def test_roles_precede_inheriting_roles_precede_users():
    order = plan_order(
        build(
            roles=[
                {"name": "child", "inherits": ["parent"]},
                {"name": "parent"},
            ],
            users=[{"name": "svc", "roles": ["child"]}],
        )
    )
    assert index_of(order, "role", "parent") < index_of(order, "role", "child")
    assert index_of(order, "role", "child") < index_of(order, "user", "svc")


def test_rest_api_precedes_databases_precedes_app_servers():
    order = plan_order(
        build(
            app_servers=[{"name": "app", "database": "content"}],
            databases=[{"name": "content"}],
            rest_apis=[{"name": "api"}],
        )
    )
    assert index_of(order, "rest_api", "api") < index_of(order, "database", "content")
    assert index_of(order, "database", "content") < index_of(order, "app_server", "app")


def test_schema_database_precedes_the_database_using_it():
    order = plan_order(
        build(
            databases=[
                {"name": "content", "schema_database": "schemas"},
                {"name": "schemas"},
            ]
        )
    )
    assert index_of(order, "database", "schemas") < index_of(
        order, "database", "content"
    )


def test_default_permissions_role_reference_creates_an_edge():
    order = plan_order(
        build(
            roles=[
                {
                    "name": "app-role",
                    "default_permissions": [
                        {"role": "reader", "capabilities": ["read"]}
                    ],
                },
                {"name": "reader"},
            ]
        )
    )
    assert index_of(order, "role", "reader") < index_of(order, "role", "app-role")


def test_role_inheritance_cycle_is_detected_and_names_its_members():
    with pytest.raises(DependencyCycleError) as excinfo:
        plan_order(
            build(
                roles=[
                    {"name": "a", "inherits": ["b"]},
                    {"name": "b", "inherits": ["a"]},
                ]
            )
        )
    message = str(excinfo.value)
    assert "role:a" in message and "role:b" in message


def test_self_inheritance_is_a_cycle():
    with pytest.raises(DependencyCycleError) as excinfo:
        plan_order(build(roles=[{"name": "loop", "inherits": ["loop"]}]))
    assert "role:loop" in str(excinfo.value)


def test_longer_cycle_is_detected():
    with pytest.raises(DependencyCycleError) as excinfo:
        plan_order(
            build(
                roles=[
                    {"name": "a", "inherits": ["b"]},
                    {"name": "b", "inherits": ["c"]},
                    {"name": "c", "inherits": ["a"]},
                ]
            )
        )
    message = str(excinfo.value)
    assert all(f"role:{name}" in message for name in "abc")


def test_builtin_roles_need_no_declaration():
    """`rest-reader` and friends ship with the server; they cannot be declared."""
    order = plan_order(
        build(
            roles=[
                {"name": "reader", "inherits": ["rest-reader"]},
                {"name": "admin", "inherits": ["rest-admin", "manage-admin"]},
            ]
        )
    )
    # They are satisfiable but never plan nodes: the tool must not touch them.
    assert Node("role", "rest-reader") not in order
    assert len(order) == 2


def test_a_non_builtin_undeclared_role_is_still_dangling():
    """The allowance is exactly four names, not 'anything that looks built-in'."""
    with pytest.raises(DanglingReferenceError) as excinfo:
        plan_order(build(roles=[{"name": "r", "inherits": ["rest-invented"]}]))
    assert "rest-invented" in str(excinfo.value)
    # The refusal names the ones that do not need declaring.
    assert "rest-reader" in str(excinfo.value)


def test_a_role_granting_default_permissions_to_itself_is_not_a_cycle():
    """The normal case. A writer can read and update its own documents.

    This is not a creation-order dependency. Treating it as one refuses a declaration that a
    writer role requires.
    """
    order = plan_order(
        build(
            roles=[
                {
                    "name": "writer",
                    "default_permissions": [
                        {"role": "writer", "capabilities": ["read", "update"]}
                    ],
                }
            ]
        )
    )
    assert order == [Node("role", "writer")]


def test_a_default_permission_on_another_role_still_orders_it_first():
    order = plan_order(
        build(
            roles=[
                {
                    "name": "writer",
                    "default_permissions": [
                        {"role": "writer", "capabilities": ["update"]},
                        {"role": "reader", "capabilities": ["read"]},
                    ],
                },
                {"name": "reader"},
            ]
        )
    )
    assert index_of(order, "role", "reader") < index_of(order, "role", "writer")


def test_dangling_role_reference_is_detected_and_named():
    with pytest.raises(DanglingReferenceError) as excinfo:
        plan_order(build(users=[{"name": "svc", "roles": ["ghost"]}]))
    message = str(excinfo.value)
    assert "ghost" in message
    assert "user:svc" in message


def test_dangling_database_reference_is_detected_and_named():
    with pytest.raises(DanglingReferenceError) as excinfo:
        plan_order(build(app_servers=[{"name": "app", "database": "ghost"}]))
    assert "ghost" in str(excinfo.value)


def test_dangling_reference_is_config_shaped():
    with pytest.raises(DanglingReferenceError) as excinfo:
        plan_order(build(users=[{"name": "svc", "roles": ["ghost"]}]))
    assert excinfo.value.exit_code == ExitCode.INPUT


def test_database_created_by_a_rest_api_is_not_dangling():
    """The seam: the rest-api POST creates its content/modules databases."""
    order = plan_order(
        build(
            rest_apis=[{"name": "api", "database": "api-content"}],
            app_servers=[{"name": "app", "database": "api-content"}],
        )
    )
    assert Node("app_server", "app") in order
    assert Node("database", "api-content") not in order


def test_teardown_order_is_the_exact_reverse():
    declaration = build(
        roles=[{"name": "child", "inherits": ["parent"]}, {"name": "parent"}],
        users=[{"name": "svc", "roles": ["child"]}],
        databases=[{"name": "content"}],
    )
    assert teardown_order(declaration) == list(reversed(plan_order(declaration)))


def test_independent_objects_order_is_independent_of_declaration_order():
    first = plan_order(build(databases=[{"name": "alpha"}, {"name": "beta"}]))
    second = plan_order(build(databases=[{"name": "beta"}, {"name": "alpha"}]))
    assert first == second


def test_order_is_stable_across_repeated_calls():
    declaration = build(
        roles=[{"name": f"r{i}"} for i in range(8)],
        databases=[{"name": f"d{i}"} for i in range(8)],
    )
    assert plan_order(declaration) == plan_order(declaration)


def test_every_declared_object_appears_exactly_once():
    order = plan_order(
        build(
            rest_apis=[{"name": "api"}],
            databases=[{"name": "content"}],
            app_servers=[{"name": "app"}],
            roles=[{"name": "writer"}],
            users=[{"name": "svc", "roles": ["writer"]}],
        )
    )
    assert len(order) == len(set(order)) == 5


def test_empty_declaration_orders_to_nothing():
    assert plan_order(build()) == []
