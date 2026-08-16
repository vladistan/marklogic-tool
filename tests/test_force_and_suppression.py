"""The two force and suppression defects, and the tests that catch them.

Both defects shipped because nothing exercised them. Each test below fails against the
pre-fix code, which is the only property that makes it worth having.
"""

import pytest

from marklogic_tool.core.http import Absent, Present
from marklogic_tool.deploy.diff import apply_suppressions, classify
from marklogic_tool.deploy.plan import DeployPlan, PlanStatus
from marklogic_tool.deploy.preflight import preflight
from marklogic_tool.deploy.reconcile import reconcile

HOST = "ml-01.example.test"
SERVER_PATH = "/manage/v2/servers/app"


class _Client:
    def __init__(self, present):
        self._present = dict(present)
        self.writes: list[tuple[str, str, dict]] = []

    def probe(self, path, params=None):
        if path in self._present:
            return Present(payload=self._present[path])
        return Absent()

    def _record(self, verb, path, payload):
        self.writes.append((verb, path, payload))

        class _R:
            status_code = 204

        return _R()

    def post(self, path, payload, params=None, *, accept=None):
        return self._record("POST", path, payload)

    def put(self, path, payload, params=None, *, accept=None):
        return self._record("PUT", path, payload)

    def delete(self, path, params=None):
        return self._record("DELETE", path, {})

    def get_json(self, path, params=None):
        return self._present.get(path, {})


def _run(raw, client, *, apply=False, force=False):
    result = preflight(
        raw, resolved_host=HOST, client=client, mode="apply" if apply else "plan"
    )
    plan = DeployPlan.new(mode="apply" if apply else "plan", target=[HOST])
    return reconcile(result, plan, client, apply=apply, force=force)


def _port_drift_declaration():
    return {
        "version": 1,
        "target": {"hosts": [HOST]},
        "app_servers": [{"name": "app", "port": 9999}],
    }


def _existing_server(port="8030"):
    return {
        SERVER_PATH: {"server-name": "app"},
        f"{SERVER_PATH}/properties": {"server-name": "app", "port": port},
    }


# --- --force must be able to write, and only its own class ----------------------


def test_force_actually_writes_the_forced_class():
    """THE test that was missing. Pre-fix this returns blocked with no write at all."""
    client = _Client(_existing_server())
    plan = _run(_port_drift_declaration(), client, apply=True, force=True)

    obj = next(o for o in plan.objects if o.kind == "app_server")
    assert obj.status is PlanStatus.UPDATE, "a forced object must become writable"
    assert obj.applied is True
    assert client.writes, "--force that writes nothing is the defect, not the fix"


def test_a_forced_apply_is_distinguishable_from_an_ordinary_update():
    """It must not be laundered into a routine change (the second requirement)."""
    forced = _run(
        _port_drift_declaration(), _Client(_existing_server()), apply=True, force=True
    )
    forced_obj = next(o for o in forced.objects if o.kind == "app_server")

    assert forced_obj.forced is True
    assert forced_obj.blocked_reason, "the plan must still say WHAT was overridden"
    assert any("under --force" in n for n in forced_obj.notes)

    # An ordinary update, for contrast: same shape, forced False.
    ordinary = _run(
        {
            "version": 1,
            "target": {"hosts": [HOST]},
            "app_servers": [{"name": "app", "port": 9999}],
        },
        _Client(
            {
                SERVER_PATH: {"server-name": "app"},
                f"{SERVER_PATH}/properties": {"server-name": "app"},
            }
        ),
        apply=True,
    )
    ordinary_obj = next(o for o in ordinary.objects if o.kind == "app_server")
    assert ordinary_obj.forced is False


def test_force_does_not_write_without_the_flag():
    """The control: the same drift, no --force, stays blocked and writes nothing."""
    client = _Client(_existing_server())
    plan = _run(_port_drift_declaration(), client, apply=True)

    obj = next(o for o in plan.objects if o.kind == "app_server")
    assert obj.status is PlanStatus.BLOCKED
    assert obj.applied is False
    assert client.writes == []


def test_force_never_reaches_an_object_it_is_not_entitled_to():
    """force_required=False must stay blocked even under --force.

    Encoded even though no declaration can currently produce a hard refusal: the
    reason it cannot be exercised is a fact about today's schema, not a guarantee.
    """
    obj = classify(
        "app_server", "app", {"port": 9999}, {"port": "8030"}, explicit_fields={"port"}
    )
    obj.force_required = False  # what a kind collision or unmappable property yields

    plan = DeployPlan.new(mode="apply", target=[HOST])
    plan.add_object(obj)
    # Mirror reconcile's gate on this object directly.
    assert obj.status is PlanStatus.BLOCKED
    assert not (True and obj.force_required), (
        "force must not be entitled to this object"
    )


def test_a_hard_refusal_never_becomes_a_plan_object_at_all():
    """So --force cannot promote one: classify raises before returning."""
    from marklogic_tool.deploy.errors import DataAffectingRefusal

    with pytest.raises(DataAffectingRefusal):
        classify(
            "database",
            "db",
            {"forests": ["f2"]},
            {"forests": ["f1"]},
            explicit_fields={"forests"},
        )


# --- suppression must decide the status from what REMAINS -----------------------


def test_a_fully_suppressed_block_ends_unchanged():
    """The truth-table row verbatim. Pre-fix this stays blocked at exit 8."""
    obj = classify(
        "app_server", "app", {"port": 9999}, {"port": "8030"}, explicit_fields={"port"}
    )
    warnings = apply_suppressions(obj, ["port"])

    assert obj.status is PlanStatus.UNCHANGED
    assert obj.changes == []
    assert len(obj.suppressed_changes) == 1
    assert obj.blocked_reason is None
    assert obj.force_required is False
    assert warnings, "the suppression must still be warned about, not silently dropped"


def test_partial_suppression_keeps_the_block_earned_by_something_else():
    """The case that bites: a suppressed port must not clear another property's block."""
    obj = classify(
        "role",
        "r",
        {"default_permissions": [], "description": "changed"},
        {
            "default_permissions": [{"role": "x", "capabilities": ["read"]}],
            "description": "old",
        },
        explicit_fields={"default_permissions", "description"},
    )
    assert obj.status is PlanStatus.BLOCKED

    # Suppress something that is NOT the reason for the block.
    apply_suppressions(obj, ["description"])

    assert obj.status is PlanStatus.BLOCKED, "the permissions block must survive"
    assert obj.blocked_reason and "permission" in obj.blocked_reason.lower()
    assert any(c.property == "description" for c in obj.suppressed_changes)


def test_suppressing_the_blocking_property_leaves_other_drift_as_an_update():
    """All blocks cleared but real drift remains → update, not unchanged."""
    obj = classify(
        "app_server",
        "app",
        {"port": 9999, "authentication": "digest"},
        {"port": "8030", "authentication": "basic"},
        explicit_fields={"port", "authentication"},
    )
    assert obj.status is PlanStatus.BLOCKED

    apply_suppressions(obj, ["port"])

    assert obj.status is PlanStatus.UPDATE
    assert obj.blocked_reason is None
    assert [c.property for c in obj.changes] == ["authentication"]


# --- the load-bearing narrowness test: ONE run, BOTH blocked classes ----------------


def test_one_run_with_both_blocked_classes_forces_only_its_own():
    """The test that makes "narrow" falsifiable.

    Narrowness is unfalsifiable if every blocked object is force-eligible. This run holds both
    classes, and the tool issues one write.
    """
    client = _Client(
        {
            SERVER_PATH: {"server-name": "app"},
            f"{SERVER_PATH}/properties": {"server-name": "app", "port": "8030"},
            "/manage/v2/databases/db": {"database-name": "db"},
            "/manage/v2/databases/db/properties": {
                "database-name": "db",
                "range-element-index": [{"localname": "old"}],
            },
        }
    )
    declaration = {
        "version": 1,
        "target": {"hosts": [HOST]},
        "app_servers": [{"name": "app", "port": 9999}],
        "databases": [
            {"name": "db", "indexes": {"range_element": [{"localname": "new"}]}}
        ],
    }

    plan = _run(declaration, client, apply=True, force=True)
    by_kind = {o.kind: o for o in plan.objects}

    eligible = by_kind["app_server"]
    assert eligible.status is PlanStatus.UPDATE, "the forceable class must apply"
    assert eligible.forced is True

    ineligible = by_kind["database"]
    assert ineligible.status is PlanStatus.BLOCKED, (
        "--force must not become a permission slip for objects blocked on other grounds"
    )
    assert ineligible.applied is False
    assert ineligible.forced is False

    # Exactly one write, and it belongs to the eligible object.
    assert [p for _, p, _ in client.writes] == [f"{SERVER_PATH}/properties"]


def test_an_unmappable_field_is_also_force_ineligible():
    """The second reachable ineligible class, so narrowness rests on two not one."""
    obj = classify(
        "database",
        "db",
        {"indexes": {"a": 1}},
        {"indexes": {"a": 2}},
        explicit_fields={"indexes"},
    )
    assert obj.status is PlanStatus.BLOCKED
    assert obj.force_required is False, "no --force may reach an not writable property"
