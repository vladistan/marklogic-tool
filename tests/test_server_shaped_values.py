"""The defects only a real server exposed.

Every fixture in this suite was authored, and an authored fixture carries the shapes its
author expected. These tests use the server shapes instead.
"""

import pytest

from marklogic_tool.deploy.diff import classify, same_value
from marklogic_tool.deploy.mapping import build_create_body, to_manage, to_user
from marklogic_tool.deploy.plan import PlanStatus

# --- the write path must emit the Manage shape ---------------------------------


DECLARED_PERMISSIONS = [
    {"role": "rest-reader", "capabilities": ["read", "update"]},
]

EXPANDED = [
    {"role-name": "rest-reader", "capability": "read"},
    {"role-name": "rest-reader", "capability": "update"},
]


def test_a_create_body_never_carries_permission_at_all():
    """A POST with `permission` returns 404. A POST without it returns 201.

    The grant resolves against roles that already exist. The tool excludes it
    unconditionally, not only for self-references.
    """
    body = build_create_body(
        "role", {"name": "writer", "default_permissions": DECLARED_PERMISSIONS}
    )
    assert "permission" not in body
    assert body == {"role-name": "writer"}


def test_no_kind_smuggles_permission_into_a_create_body():
    for kind, name_field in (("role", "role-name"), ("user", "user-name")):
        body = build_create_body(
            kind, {"name": "x", "default_permissions": DECLARED_PERMISSIONS}
        )
        assert "permission" not in body
        assert body[name_field] == "x"


def test_the_expanded_shape_is_still_what_a_properties_write_carries():
    """Expansion is unchanged — only its DESTINATION moved from POST to PUT."""
    written = to_manage("role", {"default_permissions": DECLARED_PERMISSIONS})
    assert written["permission"] == EXPANDED
    for entry in written["permission"]:
        assert set(entry) == {"role-name", "capability"}
        assert "capabilities" not in entry
        assert isinstance(entry["capability"], str)


def test_the_properties_put_payload_is_expanded_too():
    """The update path goes through the same translation as create."""
    assert to_manage("role", {"default_permissions": DECLARED_PERMISSIONS}) == {
        "permission": EXPANDED
    }


def test_a_user_carrying_permissions_also_defers_them_to_the_properties_write():
    """Superseded: this asserted the create body carried `permission`.

    It encoded the earlier behaviour, which the server rejects. The expansion still
    happens — it just happens on the follow-up PUT.
    """
    body = build_create_body(
        "user", {"name": "svc", "default_permissions": DECLARED_PERMISSIONS}
    )
    assert "permission" not in body
    assert to_manage("user", {"default_permissions": DECLARED_PERMISSIONS}) == {
        "permission": EXPANDED
    }


def test_observed_manage_entries_collapse_back_to_the_user_shape():
    assert to_user("role", {"permission": EXPANDED}) == {
        "default_permissions": [
            {"role": "rest-reader", "capabilities": ["read", "update"]}
        ]
    }


def test_expansion_round_trips_through_the_real_translation_functions():
    """Not the helper in isolation — the functions the product actually calls."""
    declared = {"default_permissions": DECLARED_PERMISSIONS}
    assert to_user("role", to_manage("role", declared)) == declared


# --- the server returns typed scalars as strings --------------------------------


@pytest.mark.parametrize(
    ("declared", "observed"),
    [
        (8030, "8030"),
        (0, "0"),
        (True, "true"),
        (False, "false"),
        ("manual", "manual"),
    ],
)
def test_server_string_matches_the_declared_type(declared, observed):
    assert same_value(declared, observed)


@pytest.mark.parametrize(
    ("declared", "observed"),
    [
        (8030, "8040"),
        (True, "false"),
        (False, "true"),
        (8030, "not-a-number"),
        (8030, None),
        ("manual", "automatic"),
    ],
)
def test_a_genuine_difference_is_never_normalised_away(declared, observed):
    """The risk of normalising is masking real drift. It must not."""
    assert not same_value(declared, observed)


def test_a_re_run_against_server_shaped_values_is_unchanged():
    """The regression, in the exact shape the box produced it.

    port observed as the STRING "8030" against a declaration typing it 8030.
    """
    obj = classify(
        "app_server",
        "example-app",
        {"port": 8030, "authentication": "digest-basic"},
        {"port": "8030", "authentication": "digest-basic"},
    )
    assert obj.status is PlanStatus.UNCHANGED
    assert obj.blocked_reason is None
    assert obj.force_required is False


def test_the_phantom_port_change_no_longer_blocks():
    obj = classify("app_server", "app", {"port": 8030}, {"port": "8030"})
    assert obj.status is not PlanStatus.BLOCKED


def test_a_real_port_change_still_blocks():
    """The fix must not disarm the disruptive-port guard."""
    obj = classify("app_server", "app", {"port": 8040}, {"port": "8030"})
    assert obj.status is PlanStatus.BLOCKED
    assert obj.force_required is True


@pytest.mark.parametrize("prop", ["triple_index", "collection_lexicon", "uri_lexicon"])
def test_boolean_database_properties_do_not_drift_against_server_strings(prop):
    """Same class as port — checked because the fix is for the class, not the case."""
    obj = classify("database", "content", {prop: True}, {prop: "true"})
    assert obj.status is PlanStatus.UNCHANGED


def test_a_boolean_that_genuinely_differs_still_drifts():
    obj = classify(
        "database", "content", {"uri_lexicon": True}, {"uri_lexicon": "false"}
    )
    assert obj.status is PlanStatus.UPDATE


# --- found by dogfooding the first fixes against real box state ---------


def test_observed_values_are_translated_not_just_names():
    """The server says `digestbasic`. The declaration says `digest-basic`.

    Translating only the property name leaves those two compared against each other, so the
    app server drifts on every run.
    """
    from marklogic_tool.deploy.order import Node
    from marklogic_tool.deploy.reconcile import _observed_properties

    observed = _observed_properties(
        Node("app_server", "app"), {"authentication": "digestbasic", "port": "8030"}
    )
    assert observed["authentication"] == "digest-basic"


def test_an_unset_property_is_not_a_disruptive_change():
    """absence is being SET, not changed. There is no service to interrupt."""
    obj = classify("app_server", "app", {"port": 8030}, {})
    assert obj.status is PlanStatus.UPDATE
    assert obj.blocked_reason is None
    assert obj.force_required is False


def test_default_permissions_compare_as_a_set_not_a_sequence():
    """the server returns the pair list in ITS order, not the declaration's.

    Comparing positionally makes the role drift forever on ordering alone.
    """
    declared = [
        {"role": "writer", "capabilities": ["read", "update"]},
        {"role": "reader", "capabilities": ["read"]},
    ]
    observed = [
        {"role": "reader", "capabilities": ["read"]},
        {"role": "writer", "capabilities": ["update", "read"]},
    ]
    obj = classify(
        "role",
        "writer",
        {"default_permissions": declared},
        {"default_permissions": observed},
    )
    assert obj.status is PlanStatus.UNCHANGED


def test_a_kind_without_a_properties_endpoint_is_never_updated():
    """a rest_api's declared fields are CREATE-TIME parameters.

    There is no /properties to PUT them to, so an existing one must resolve to
    unchanged rather than attempting a write that cannot land.
    """
    from marklogic_tool.core.http import Present
    from marklogic_tool.deploy.order import Node
    from marklogic_tool.deploy.plan import DeployPlan
    from marklogic_tool.deploy.preflight import PreflightResult
    from marklogic_tool.deploy.reconcile import reconcile
    from marklogic_tool.deploy.schema import load_declaration

    declaration = load_declaration(
        {
            "version": 1,
            "target": {"hosts": ["h"]},
            "rest_apis": [{"name": "api", "port": 8030, "database": "c"}],
        }
    )
    node = Node("rest_api", "api")
    result = PreflightResult(
        declaration=declaration,
        order=[node],
        observed={node: {"name": "api"}},
        absent=set(),
    )

    class NoWrites:
        def probe(self, path, params=None):
            return Present(payload={})

    plan = DeployPlan.new(mode="apply", target=["h"])
    reconcile(result, plan, NoWrites(), apply=True)
    assert plan.objects[0].status is PlanStatus.UNCHANGED
    assert any("create-time parameters" in note for note in plan.objects[0].notes)


def test_the_whole_content_database_re_runs_unchanged_against_server_shapes():
    """Every value as the server returns it: strings throughout."""
    declared = {
        "triple_index": True,
        "collection_lexicon": True,
        "uri_lexicon": True,
        "directory_creation": "manual",
        "schema_database": "example-schemas",
    }
    observed = {
        "triple_index": "true",
        "collection_lexicon": "true",
        "uri_lexicon": "true",
        "directory_creation": "manual",
        "schema_database": "example-schemas",
    }
    obj = classify("database", "example-content", declared, observed)
    assert obj.status is PlanStatus.UNCHANGED
    assert obj.changes == []
