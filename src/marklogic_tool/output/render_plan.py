"""Plan rendering.

Two renderers over one document: machine JSON on the `marklogic-tool/deploy-plan/1` contract,
and a human table.

The status vocabulary is `create`, `update`, `unchanged` and `blocked`.
"""

import json

from marklogic_tool.deploy.plan import DeployPlan, ObjectPlan, PlanStatus
from marklogic_tool.output.formatters import format_table


def render_plan_json(plan: DeployPlan) -> str:
    """Render the plan on its wire contract."""
    return json.dumps(plan.to_wire(), indent=2, default=str)


def _human_status(plan: DeployPlan, obj: ObjectPlan) -> str:
    """Phrase the status for a person. Do not invent a status value.

    In `plan` mode nothing has happened yet. In `apply` mode the object either applied or
    did not.
    """
    if plan.mode == "plan":
        if obj.status is PlanStatus.CREATE:
            return "would create"
        if obj.status is PlanStatus.UPDATE:
            return "would update"
        return str(obj.status.value)
    if obj.status in (PlanStatus.CREATE, PlanStatus.UPDATE) and not obj.applied:
        return f"{obj.status.value} (not applied)"
    return str(obj.status.value)


def render_plan_table(plan: DeployPlan) -> str:
    """Render the plan as a human table."""
    rows: list[dict[str, str]] = []
    for obj in plan.objects:
        detail = ""
        if obj.blocked_reason:
            detail = obj.blocked_reason
        elif obj.depends_on_pending:
            detail = "awaits " + ", ".join(obj.depends_on_pending)
        elif obj.changes:
            detail = ", ".join(change.property for change in obj.changes)
        if obj.force_required:
            detail = f"{detail} [--force required]" if detail else "[--force required]"
        if obj.suppressed_changes:
            suppressed = "suppressed: " + ", ".join(
                c.property for c in obj.suppressed_changes
            )
            detail = f"{detail}; {suppressed}" if detail else suppressed
        rows.append(
            {
                "kind": obj.kind,
                "name": obj.name,
                "status": _human_status(plan, obj),
                "detail": detail,
            }
        )

    title = "Deploy plan" if plan.mode == "plan" else "Deploy result"
    table = format_table(rows, title=title)

    summary = plan.summary
    lines = [table] if table else []
    lines.append(
        f"{summary.total} object(s): {summary.create} create, {summary.update} update, "
        f"{summary.unchanged} unchanged, {summary.blocked} blocked"
    )
    lines.extend(f"warning: {w}" for w in plan.warnings)
    return "\n".join(line for line in lines if line)


def render_plan(plan: DeployPlan, fmt: str) -> str:
    """Render in the requested format."""
    if fmt == "json":
        return render_plan_json(plan)
    return render_plan_table(plan)


__all__ = ["render_plan", "render_plan_json", "render_plan_table"]
