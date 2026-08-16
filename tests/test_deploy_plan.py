"""Plan document behaviour.

The highest structural risk here is a frozen plan model. It makes "emit the plan on every exit
path" unimplementable, and the failure appears only when something raises part way.
"""

import pytest

from marklogic_tool.deploy.plan import (
    DeployPlan,
    ObjectPlan,
    PlanStatus,
    PropertyChange,
)


def test_plan_is_constructed_before_reconcile_and_mutated_in_place():
    plan = DeployPlan.new(mode="plan", target=["ml-01"])
    identity = id(plan)
    plan.add_object(
        ObjectPlan(kind="database", name="content", status=PlanStatus.CREATE)
    )
    assert id(plan) == identity
    assert plan.summary.total == 1


def test_partial_plan_survives_a_raising_reconcile():
    """the artefact must exist in `finally`, not be lost with the exception."""
    plan = DeployPlan.new(mode="apply", target=["ml-01"])

    def reconcile(p: DeployPlan) -> None:
        p.add_object(
            ObjectPlan(
                kind="database", name="content", status=PlanStatus.CREATE, applied=True
            )
        )
        msg = "connection reset mid-apply"
        raise RuntimeError(msg)

    emitted: list[DeployPlan] = []
    with pytest.raises(RuntimeError):
        try:
            reconcile(plan)
        finally:
            emitted.append(plan)

    assert len(emitted) == 1
    assert emitted[0].summary.total == 1
    assert emitted[0].objects[0].applied is True


def test_an_object_stays_mutable_after_being_added():
    """The apply path marks `applied` after the object is already in the plan."""
    plan = DeployPlan.new(mode="apply", target=["ml-01"])
    obj = plan.add_object(
        ObjectPlan(kind="database", name="content", status=PlanStatus.CREATE)
    )
    obj.applied = True
    obj.notes.append("created")
    assert plan.objects[0].applied is True
    assert plan.objects[0].notes == ["created"]


def test_empty_plan_is_valid_and_emittable():
    plan = DeployPlan.new(mode="plan", target=["ml-01"])
    wire = plan.to_wire()
    assert wire["objects"] == []
    assert wire["summary"]["total"] == 0


def test_summary_tracks_every_status():
    plan = DeployPlan.new(mode="plan", target=["ml-01"])
    for status in PlanStatus:
        plan.add_object(ObjectPlan(kind="database", name=status.value, status=status))
    s = plan.summary
    assert (s.create, s.update, s.unchanged, s.blocked, s.total) == (1, 1, 1, 1, 4)


def test_exit_code_is_not_computed_by_the_plan():
    """a pure plan cannot know --force or a runtime write failure."""
    plan = DeployPlan.new(mode="plan", target=["ml-01"])
    plan.add_object(
        ObjectPlan(
            kind="role",
            name="writer",
            status=PlanStatus.BLOCKED,
            blocked_reason="subtractive",
            force_required=True,
        )
    )
    # A blocked object is present, yet the plan still refuses to invent an exit code.
    assert plan.exit_code is None
    plan.exit_code = 8
    assert plan.to_wire()["exit_code"] == 8


def test_warnings_accumulate():
    plan = DeployPlan.new(mode="plan", target=["ml-01"])
    plan.warn("first")
    plan.warn("second")
    assert plan.to_wire()["warnings"] == ["first", "second"]


def test_suppressed_changes_are_separate_from_changes():
    """suppression must stay visible, never silently folded into `changes`."""
    obj = ObjectPlan(
        kind="app_server",
        name="app",
        status=PlanStatus.UNCHANGED,
        suppressed_changes=[PropertyChange(property="log-errors", desired=False)],
    )
    assert obj.changes == []
    assert len(obj.suppressed_changes) == 1


def test_target_is_copied_not_aliased():
    hosts = ["ml-01"]
    plan = DeployPlan.new(mode="plan", target=hosts)
    hosts.append("ml-02")
    assert plan.target == ["ml-01"]


def test_two_plans_do_not_share_mutable_defaults():
    a = DeployPlan.new(mode="plan", target=["h"])
    b = DeployPlan.new(mode="plan", target=["h"])
    a.warn("only a")
    a.add_object(ObjectPlan(kind="database", name="d", status=PlanStatus.CREATE))
    assert b.warnings == []
    assert b.objects == []
