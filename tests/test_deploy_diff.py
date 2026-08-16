# cspell:ignore unkeepable
"""The drift truth table, one test per cell.

This is the load-bearing tier of the whole unit: every safety decision the tool makes
about an existing server is decided here, offline, with no client involved.
"""

import pytest

from marklogic_tool.deploy.diff import (
    apply_suppressions,
    classify,
    classify_default_permissions,
)
from marklogic_tool.deploy.errors import DataAffectingRefusal
from marklogic_tool.deploy.plan import PlanStatus

# --- the truth table, declared x observed ------------------------------------------


def test_absent_object_is_a_create():
    obj = classify("database", "content", {"schema_database": "schemas"}, None)
    assert obj.status is PlanStatus.CREATE
    assert [c.property for c in obj.changes] == ["schema-database"]


def test_declared_scalar_drift_is_an_update_with_the_drift_subset_only():
    obj = classify(
        "database",
        "content",
        {"schema_database": "new", "uri_lexicon": True},
        {"schema_database": "old", "uri_lexicon": True},
    )
    assert obj.status is PlanStatus.UPDATE
    # uri_lexicon matched, so it is NOT in the change set: PUT the drift subset only.
    assert [c.property for c in obj.changes] == ["schema-database"]
    assert obj.changes[0].observed == "old"
    assert obj.changes[0].desired == "new"


def test_undeclared_observed_properties_are_untouched():
    """Declared-subset semantics: the declaration does not own the whole object."""
    obj = classify(
        "database",
        "content",
        {"uri_lexicon": True},
        {"uri_lexicon": True, "triple_index": True, "directory_creation": "manual"},
    )
    assert obj.status is PlanStatus.UNCHANGED
    assert obj.changes == []


def test_no_drift_is_unchanged():
    obj = classify("database", "content", {"uri_lexicon": True}, {"uri_lexicon": True})
    assert obj.status is PlanStatus.UNCHANGED


def test_concurrent_already_exists_is_unchanged():
    """An object that appeared between plan and apply, matching what we declared."""
    obj = classify("role", "writer", {"name": "writer"}, {"name": "writer"})
    assert obj.status is PlanStatus.UNCHANGED


def test_additive_security_drift_is_an_update():
    obj = classify(
        "user", "svc", {"roles": ["writer", "reader"]}, {"roles": ["writer"]}
    )
    assert obj.status is PlanStatus.UPDATE
    assert [c.property for c in obj.changes] == ["role"]


def test_subtractive_security_drift_is_blocked_unless_force():
    obj = classify(
        "user", "svc", {"roles": ["reader"]}, {"roles": ["writer", "reader"]}
    )
    assert obj.status is PlanStatus.BLOCKED
    assert obj.force_required is True
    assert "removes entries" in obj.blocked_reason


def test_disruptive_port_change_is_blocked_unless_force():
    obj = classify("app_server", "app", {"port": 8040}, {"port": 8030})
    assert obj.status is PlanStatus.BLOCKED
    assert obj.force_required is True
    assert "interrupts service" in obj.blocked_reason


def test_kind_collision_is_blocked_and_force_does_not_apply():
    obj = classify(
        "database",
        "shared",
        {"uri_lexicon": True},
        {"port": 8030},
        observed_kind="app_server",
    )
    assert obj.status is PlanStatus.BLOCKED
    assert obj.force_required is False
    assert "--force does not apply" in obj.blocked_reason


def test_data_affecting_drift_is_an_unconditional_hard_refusal():
    with pytest.raises(DataAffectingRefusal) as excinfo:
        classify(
            "database",
            "content",
            {"forests": ["f2"]},
            {"forests": ["f1"]},
        )
    message = str(excinfo.value)
    assert "unconditional" in message
    assert "--force does not reach it" in message


def test_data_affecting_refusal_exits_eight():
    err = DataAffectingRefusal("x")
    assert err.exit_code == 8


def test_hard_refusal_set_is_not_the_escape_hatch_deny_list():
    """Two sets, two questions. Conflating them was a real bug.

    The mapping deny-list is broad, so `ignore_properties` cannot hide anything
    security-adjacent. The hard-refusal set is narrow.
    """
    from marklogic_tool.deploy.diff import HARD_REFUSAL_PROPERTIES
    from marklogic_tool.deploy.mapping import DATA_AFFECTING_DENIED_PROPERTIES

    assert HARD_REFUSAL_PROPERTIES != DATA_AFFECTING_DENIED_PROPERTIES
    # schema_database is deny-listed for the hatches but is an ordinary change here.
    assert "schema-database" in DATA_AFFECTING_DENIED_PROPERTIES
    assert "schema_database" not in HARD_REFUSAL_PROPERTIES


def test_changing_schema_database_is_an_ordinary_update_not_a_refusal():
    """Regression: it is in the user vocabulary, so refusing it breaks the feature."""
    obj = classify(
        "database", "content", {"schema_database": "new"}, {"schema_database": "old"}
    )
    assert obj.status is PlanStatus.UPDATE


# --- default_permissions -------------------------------------------------


def test_capability_shrinkage_is_subtractive():
    declared = [{"role": "writer", "capabilities": ["read"]}]
    observed = [{"role": "writer", "capabilities": ["read", "update"]}]
    assert classify_default_permissions(declared, observed) == "subtractive"


def test_capability_growth_is_additive():
    declared = [{"role": "writer", "capabilities": ["read", "update"]}]
    observed = [{"role": "writer", "capabilities": ["read"]}]
    assert classify_default_permissions(declared, observed) == "additive"


def test_identical_sets_are_unchanged():
    same = [{"role": "writer", "capabilities": ["read", "update"]}]
    assert classify_default_permissions(same, list(same)) == "unchanged"


def test_a_server_pair_the_declaration_omits_is_subtractive():
    """The completeness half. A write replaces the whole list.

    This test asserted "unchanged" before the measurement. A pair the declaration omits is
    unkeepable, because the write removes it. Blocking is the only honest answer.
    """
    declared = [{"role": "writer", "capabilities": ["read"]}]
    observed = [
        {"role": "writer", "capabilities": ["read"]},
        {"role": "auditor", "capabilities": ["read"]},
    ]
    assert classify_default_permissions(declared, observed) == "subtractive"


def test_comparison_is_over_pairs_not_per_role_capability_lists():
    """Dropping one capability from one role is a lost PAIR, and blocks."""
    declared = [
        {"role": "writer", "capabilities": ["read"]},
        {"role": "reader", "capabilities": ["read"]},
    ]
    observed = [
        {"role": "writer", "capabilities": ["read", "update"]},
        {"role": "reader", "capabilities": ["read"]},
    ]
    assert classify_default_permissions(declared, observed) == "subtractive"


def test_declaring_every_observed_pair_plus_more_is_additive():
    declared = [
        {"role": "writer", "capabilities": ["read", "update"]},
        {"role": "auditor", "capabilities": ["read"]},
    ]
    observed = [{"role": "writer", "capabilities": ["read", "update"]}]
    assert classify_default_permissions(declared, observed) == "additive"


def test_a_declared_role_absent_from_the_server_is_growth():
    declared = [{"role": "new", "capabilities": ["read"]}]
    assert classify_default_permissions(declared, []) == "additive"


def test_explicit_empty_default_permissions_is_blocked_with_a_reason():
    """explicit `[]` removes every default permission."""
    obj = classify(
        "role",
        "writer",
        {"default_permissions": []},
        {"default_permissions": [{"role": "writer", "capabilities": ["read"]}]},
        explicit_fields=frozenset({"default_permissions"}),
    )
    assert obj.status is PlanStatus.BLOCKED
    assert "explicitly empty" in obj.blocked_reason


def test_absent_default_permissions_does_not_classify_as_subtractive():
    """Omission means 'not declared, do not touch' — never a removal."""
    obj = classify(
        "role",
        "writer",
        {"description": "d"},
        {
            "description": "d",
            "default_permissions": [{"role": "writer", "capabilities": ["read"]}],
        },
    )
    assert obj.status is PlanStatus.UNCHANGED
    assert obj.blocked_reason is None


def test_declared_default_permissions_now_classifies_by_not_by_the_gate():
    """The spelling is known, so the unmappable block no longer fires.

    Growth is additive. The comparison semantics do not change here. Only the mapping gate
    stopped blocking.
    """
    obj = classify(
        "role",
        "writer",
        {
            "default_permissions": [
                {"role": "writer", "capabilities": ["read", "update"]}
            ]
        },
        {"default_permissions": [{"role": "writer", "capabilities": ["read"]}]},
        explicit_fields=frozenset({"default_permissions"}),
    )
    assert obj.status is PlanStatus.UPDATE
    assert obj.blocked_reason is None


def test_default_permissions_shrinkage_still_blocks():
    obj = classify(
        "role",
        "writer",
        {"default_permissions": [{"role": "writer", "capabilities": ["read"]}]},
        {
            "default_permissions": [
                {"role": "writer", "capabilities": ["read", "update"]}
            ]
        },
        explicit_fields=frozenset({"default_permissions"}),
    )
    assert obj.status is PlanStatus.BLOCKED
    assert obj.force_required is True
    assert "removes capabilities" in obj.blocked_reason


def test_default_permissions_audit_record_uses_the_manage_name():
    obj = classify(
        "role",
        "writer",
        {
            "default_permissions": [
                {"role": "writer", "capabilities": ["read", "update"]}
            ]
        },
        {"default_permissions": [{"role": "writer", "capabilities": ["read"]}]},
    )
    assert [c.property for c in obj.changes] == ["permission"]


# --- suppression is post-diff ---------------------------------------------------


def test_ignore_properties_match_becomes_unchanged_plus_a_record_and_warning():
    obj = classify(
        "app_server",
        "app",
        {"authentication": "digest-basic"},
        {"authentication": "basic"},
    )
    assert obj.status is PlanStatus.UPDATE
    warnings = apply_suppressions(obj, ["authentication"])
    assert obj.status is PlanStatus.UNCHANGED
    assert obj.changes == []
    assert [c.property for c in obj.suppressed_changes] == ["authentication"]
    assert warnings and "suppressed by ignore_properties" in warnings[0]


def test_suppression_is_recorded_not_silently_dropped():
    obj = classify(
        "database", "content", {"schema_database": "new"}, {"schema_database": "old"}
    )
    apply_suppressions(obj, ["schema-*"])
    assert len(obj.suppressed_changes) == 1
    assert obj.suppressed_changes[0].observed == "old"


def test_partial_suppression_leaves_the_object_an_update():
    obj = classify(
        "database",
        "content",
        {"schema_database": "new", "directory_creation": "automatic"},
        {"schema_database": "old", "directory_creation": "manual"},
    )
    apply_suppressions(obj, ["schema-database"])
    assert obj.status is PlanStatus.UPDATE
    assert [c.property for c in obj.changes] == ["directory-creation"]
    assert len(obj.suppressed_changes) == 1


def test_suppression_never_pre_filters_the_diff():
    """the change is computed first, then labelled — never filtered out of sight."""
    obj = classify(
        "database", "content", {"schema_database": "new"}, {"schema_database": "old"}
    )
    computed = [c.property for c in obj.changes]
    apply_suppressions(obj, ["*"])
    assert computed == ["schema-database"]
    assert [c.property for c in obj.suppressed_changes] == ["schema-database"]


def test_no_globs_suppresses_nothing():
    obj = classify(
        "database", "content", {"schema_database": "new"}, {"schema_database": "old"}
    )
    assert apply_suppressions(obj, []) == []
    assert len(obj.changes) == 1


def test_a_security_glob_cannot_reach_a_change_because_schema_load_refused_it():
    """The deny-list is the defence; diff still records anything that does slip past."""
    from marklogic_tool.deploy.errors import DeniedPropertyError
    from marklogic_tool.deploy.schema import load_declaration

    with pytest.raises(DeniedPropertyError):
        load_declaration(
            {
                "version": 1,
                "target": {"hosts": ["h"]},
                "app_servers": [{"name": "app", "ignore_properties": ["*permission*"]}],
            }
        )


# --- passwords ----------------------------------------------------------------------


def test_password_change_is_redacted_with_no_observed_value():
    obj = classify("user", "svc", {"password": "ssm:/ml/svc"}, None)
    change = next(c for c in obj.changes if c.property == "password")
    assert change.redacted is True
    assert change.observed is None
    assert change.desired is None
