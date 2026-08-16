# cspell:ignore MLTOOL
"""The plan document contract `marklogic-tool/deploy-plan/1`.

The tool generates four golden fixtures from the model and compares them byte for byte. A
renamed field, a reordering or a vocabulary change fails here.
"""

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from marklogic_tool.deploy.plan import (
    PLAN_SCHEMA,
    DeployPlan,
    ObjectPlan,
    PlanStatus,
    PropertyChange,
)
from marklogic_tool.output.render_plan import (
    render_plan,
    render_plan_json,
    render_plan_table,
)

FIXTURES = Path(__file__).parent / "fixtures" / "plans"


def _all_create() -> DeployPlan:
    plan = DeployPlan.new(mode="plan", target=["ml-01.example.test"])
    plan.add_object(
        ObjectPlan(
            kind="database",
            name="content",
            status=PlanStatus.CREATE,
            changes=[PropertyChange(property="database-name", desired="content")],
        )
    )
    plan.add_object(
        ObjectPlan(
            kind="app_server",
            name="app",
            status=PlanStatus.CREATE,
            changes=[PropertyChange(property="port", desired=8040)],
            depends_on_pending=["content"],
        )
    )
    return plan


def _all_unchanged() -> DeployPlan:
    plan = DeployPlan.new(mode="apply", target=["ml-01.example.test"])
    for name in ("content", "schemas"):
        plan.add_object(
            ObjectPlan(kind="database", name=name, status=PlanStatus.UNCHANGED)
        )
    return plan


def _blocked() -> DeployPlan:
    plan = DeployPlan.new(mode="plan", target=["ml-01.example.test"])
    plan.add_object(
        ObjectPlan(
            kind="role",
            name="writer",
            status=PlanStatus.BLOCKED,
            blocked_reason=(
                "capability set on declared role 'writer' shrinks from "
                "[read, update] to [read]; subtractive security drift"
            ),
            force_required=True,
            changes=[
                PropertyChange(
                    property="permission",
                    observed=["read", "update"],
                    desired=["read"],
                )
            ],
        )
    )
    plan.add_object(
        ObjectPlan(
            kind="user",
            name="svc",
            status=PlanStatus.UPDATE,
            changes=[PropertyChange.redacted_change("password", desired="ssm:/ml/svc")],
        )
    )
    return plan


def _suppressed() -> DeployPlan:
    plan = DeployPlan.new(mode="plan", target=["ml-01.example.test"])
    plan.add_object(
        ObjectPlan(
            kind="app_server",
            name="app",
            status=PlanStatus.UNCHANGED,
            suppressed_changes=[
                PropertyChange(property="log-errors", observed=True, desired=False)
            ],
            notes=["1 change suppressed by ignore_properties"],
        )
    )
    plan.warn("app server 'app': 1 change suppressed by ignore_properties")
    return plan


GOLDEN = {
    "all-create": _all_create,
    "all-unchanged": _all_unchanged,
    "blocked": _blocked,
    "suppressed-change": _suppressed,
}


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_golden_fixture_matches(name):
    plan = GOLDEN[name]()
    rendered = render_plan_json(plan) + "\n"
    path = FIXTURES / f"{name}.json"

    if os.environ.get("MLTOOL_REGENERATE_PLAN_FIXTURES"):
        path.write_text(rendered, encoding="utf-8")

    assert path.exists(), f"missing golden fixture {path}"
    assert rendered == path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_golden_fixture_round_trips_through_the_model(name):
    """A fixture that cannot be read back is not a contract."""
    raw = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    assert DeployPlan.model_validate(raw).to_wire() == raw


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_every_fixture_is_self_identifying(name):
    raw = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    assert raw["schema"] == PLAN_SCHEMA


def test_status_vocabulary_is_exactly_four_values():
    assert {s.value for s in PlanStatus} == {
        "create",
        "update",
        "unchanged",
        "blocked",
    }


def test_would_create_never_appears_in_the_wire_document():
    """'would create' is a rendering choice, not a fifth status."""
    for factory in GOLDEN.values():
        assert "would" not in render_plan_json(factory())


def test_would_create_appears_in_the_human_renderer_in_plan_mode():
    assert "would create" in render_plan_table(_all_create())


def test_apply_mode_does_not_say_would():
    plan = _all_create()
    plan.mode = "apply"
    assert "would" not in render_plan_table(plan)


def test_unapplied_create_in_apply_mode_is_reported_as_such():
    plan = DeployPlan.new(mode="apply", target=["h"])
    plan.add_object(ObjectPlan(kind="database", name="d", status=PlanStatus.CREATE))
    assert "not applied" in render_plan_table(plan)


def test_password_change_is_redacted_with_no_observed_value():
    raw = json.loads((FIXTURES / "blocked.json").read_text(encoding="utf-8"))
    user = next(o for o in raw["objects"] if o["kind"] == "user")
    change = user["changes"][0]
    assert change["redacted"] is True
    assert change["observed"] is None


def test_a_redacted_change_carrying_an_observed_value_is_refused():
    with pytest.raises(ValidationError):
        PropertyChange(property="password", observed="hunter2", redacted=True)


def test_depends_on_pending_is_a_list_of_names_in_both_modes():
    """a list of names, present in both modes — never a bare bool."""
    for mode in ("plan", "apply"):
        plan = DeployPlan.new(mode=mode, target=["h"])
        obj = plan.add_object(
            ObjectPlan(
                kind="app_server",
                name="app",
                status=PlanStatus.CREATE,
                depends_on_pending=["content", "modules"],
            )
        )
        assert obj.depends_on_pending == ["content", "modules"]
        wire = plan.to_wire()["objects"][0]["depends_on_pending"]
        assert wire == ["content", "modules"]


def test_render_plan_dispatches_on_format():
    plan = _all_unchanged()
    assert render_plan(plan, "json") == render_plan_json(plan)
    assert render_plan(plan, "table") == render_plan_table(plan)


def test_unknown_field_is_refused():
    with pytest.raises(ValidationError):
        ObjectPlan(kind="database", name="d", status=PlanStatus.CREATE, bogus=1)
