"""Reconcile.

Two properties carry the weight: `--dry-run` and `apply` are ONE code path with a
single branch at the write site, and a mid-run failure leaves a re-runnable state with
no rollback attempted.
"""

import ast
from pathlib import Path

import pytest

from marklogic_tool.core.http import Absent, Present
from marklogic_tool.deploy.plan import DeployPlan, PlanStatus
from marklogic_tool.deploy.preflight import preflight
from marklogic_tool.deploy.reconcile import reconcile

HOST = "ml-01.example.test"
SRC = Path(__file__).resolve().parents[1] / "src" / "marklogic_tool"


class FakeClient:
    """Probe seam plus recording write verbs."""

    def __init__(self, present=(), conflict_on=(), fail_on=None):
        self._present = dict(present)
        self._conflict_on = set(conflict_on)
        self._fail_on = fail_on
        self.writes: list[tuple[str, str, dict]] = []
        self.probes: list[str] = []

    def probe(self, path, params=None):
        self.probes.append(path)
        if path in self._present:
            return Present(payload=self._present[path])
        return Absent()

    def post(self, path, payload, params=None, *, accept=None):
        self.writes.append(("POST", path, payload))
        if self._fail_on and self._fail_on in path:
            msg = f"induced failure on {path}"
            raise RuntimeError(msg)
        if path in self._conflict_on:
            return _Response(409)
        return _Response(201)

    def put(self, path, payload, params=None, *, accept=None):
        self.writes.append(("PUT", path, payload))
        if self._fail_on and self._fail_on in path:
            msg = f"induced failure on {path}"
            raise RuntimeError(msg)
        return _Response(204)


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code


# Named so the allowlist annotation sits on a definition `ruff format` cannot
# reflow away from it -- annotating it inside the nested literals below did not survive.
ENV_REFERENCE = "env:ML_PW"  # pragma: allowlist secret


def declaration(**extra):
    base = {"version": 1, "target": {"hosts": [HOST]}}
    base.update(extra)
    return base


def run(raw, client, *, apply=False, mode=None, **kwargs):
    # Pre-flight must run in the SAME mode as the reconcile: plan-mode pre-flight
    # resolves no secrets, so pairing it with apply=True is exactly the combination
    # the secret guard refuses.
    result = preflight(
        raw,
        resolved_host=HOST,
        client=client,
        mode="apply" if apply else "plan",
        rotate_passwords=kwargs.get("rotate_passwords", False),
    )
    plan = DeployPlan.new(mode=mode or ("apply" if apply else "plan"), target=[HOST])
    return reconcile(result, plan, client, apply=apply, **kwargs)


# --- one code path ------------------------------------------------------------------


def _branches_on_apply(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and "apply" in {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
    )


def test_exactly_one_branch_on_apply_exists_in_reconcile():
    """A source-level assertion. A second branch is a second implementation.

    The count is per verb, not per package. `deploy` and `destroy` are separate verbs, each
    with its own preview flag. Neither may have two.
    """
    assert _branches_on_apply(SRC / "deploy" / "reconcile.py") == 1


def test_teardown_also_has_exactly_one():
    assert _branches_on_apply(SRC / "deploy" / "teardown.py") == 1


def test_no_other_deploy_module_branches_on_apply():
    """The decision core must not know whether anything is being written."""
    for path in (SRC / "deploy").glob("*.py"):
        if path.name in ("reconcile.py", "teardown.py"):
            continue
        assert _branches_on_apply(path) == 0, f"{path.name} branches on `apply`"


def test_dry_run_and_apply_produce_the_same_statuses():
    raw = declaration(databases=[{"name": "content"}], roles=[{"name": "writer"}])
    preview = run(raw, FakeClient(), apply=False)
    applied = run(raw, FakeClient(), apply=True)
    assert [o.status for o in preview.objects] == [o.status for o in applied.objects]
    assert [o.name for o in preview.objects] == [o.name for o in applied.objects]


def test_dry_run_short_circuits_writes_only():
    client = FakeClient()
    plan = run(declaration(roles=[{"name": "writer"}]), client, apply=False)
    assert client.writes == []
    assert all(o.applied is False for o in plan.objects)
    assert plan.objects[0].status is PlanStatus.CREATE


def test_apply_issues_the_create_and_marks_applied():
    client = FakeClient()
    plan = run(declaration(roles=[{"name": "writer"}]), client, apply=True)
    assert ("POST", "/manage/v2/roles", {"role-name": "writer"}) in client.writes
    assert plan.objects[0].applied is True


# --- dependency order and drift subset ----------------------------------------------


def test_a_created_role_gets_its_permissions_by_a_follow_up_put():
    """create bare, then set `permission` — two calls, always."""
    client = FakeClient()
    run(
        declaration(
            roles=[
                {
                    "name": "writer",
                    "default_permissions": [
                        {"role": "writer", "capabilities": ["read", "update"]}
                    ],
                }
            ]
        ),
        client,
        apply=True,
    )
    verbs = [(verb, path) for verb, path, _ in client.writes]
    assert verbs == [
        ("POST", "/manage/v2/roles"),
        ("PUT", "/manage/v2/roles/writer/properties"),
    ]
    post_body = client.writes[0][2]
    put_body = client.writes[1][2]
    assert "permission" not in post_body
    assert put_body["permission"] == [
        {"role-name": "writer", "capability": "read"},
        {"role-name": "writer", "capability": "update"},
    ]


def test_a_role_without_permissions_costs_no_extra_request():
    client = FakeClient()
    run(declaration(roles=[{"name": "plain"}]), client, apply=True)
    assert [verb for verb, _, _ in client.writes] == ["POST"]


def test_objects_are_written_in_dependency_order():
    client = FakeClient()
    run(
        declaration(
            roles=[{"name": "child", "inherits": ["parent"]}, {"name": "parent"}],
            users=[{"name": "svc", "roles": ["child"]}],
        ),
        client,
        apply=True,
    )
    written = [path for _, path, _ in client.writes]
    assert written.index("/manage/v2/roles") < written.index("/manage/v2/users")


def test_only_the_drift_subset_is_put():
    client = FakeClient(
        present={
            "/manage/v2/servers/app": {
                "content-database": "old",
                "modules-database": "mods",
            }
        }
    )
    run(
        declaration(
            databases=[{"name": "content"}, {"name": "mods"}],
            app_servers=[
                {"name": "app", "database": "content", "modules_database": "mods"}
            ],
        ),
        client,
        apply=True,
    )
    put = next(p for verb, _, p in client.writes if verb == "PUT")
    # modules-database matched, so it is absent from the write.
    assert put == {"content-database": "content"}


def test_undeclared_observed_properties_are_never_written():
    client = FakeClient(
        present={
            "/manage/v2/servers/app": {
                "content-database": "old",
                "authentication": "digestbasic",
                "port": 8030,
            }
        }
    )
    run(
        declaration(
            databases=[{"name": "content"}],
            app_servers=[{"name": "app", "database": "content"}],
        ),
        client,
        apply=True,
    )
    put = next(p for verb, _, p in client.writes if verb == "PUT")
    assert set(put) == {"content-database"}


def test_a_correct_server_yields_all_unchanged_and_no_writes():
    client = FakeClient(
        present={"/manage/v2/databases/content": {"schema-database": "schemas"}}
    )
    plan = run(
        declaration(
            databases=[
                {"name": "content", "schema_database": "schemas"},
                {"name": "schemas"},
            ]
        ),
        client,
        apply=True,
    )
    content = next(o for o in plan.objects if o.name == "content")
    assert content.status is PlanStatus.UNCHANGED
    assert not any(verb == "PUT" for verb, _, _ in client.writes)


# --- depends_on_pending --------------------------------------------------


def test_rest_api_created_databases_are_marked_depends_on_pending():
    """A list of NAMES, and present in BOTH modes."""
    for apply in (False, True):
        client = FakeClient()
        plan = run(
            declaration(
                rest_apis=[{"name": "api", "database": "api-content"}],
                app_servers=[{"name": "app", "database": "api-content"}],
            ),
            client,
            apply=apply,
        )
        server = next(o for o in plan.objects if o.kind == "app_server")
        # It waits on BOTH the database the rest_api creates and the rest_api
        # itself, which is what actually creates the app server.
        assert server.depends_on_pending == ["api", "api-content"]
        assert any("rest_api POST" in note for note in server.notes)


def test_depends_on_pending_names_an_intra_run_create():
    client = FakeClient()
    plan = run(
        declaration(
            roles=[{"name": "parent"}, {"name": "child", "inherits": ["parent"]}]
        ),
        client,
        apply=False,
    )
    child = next(o for o in plan.objects if o.name == "child")
    assert child.depends_on_pending == ["parent"]


# --- 409 ------------------------------------------------------------------------


def test_409_triggers_a_reprobe_and_is_not_flattened():
    client = FakeClient(
        conflict_on={"/manage/v2/roles"},
        present={"/manage/v2/roles/writer": {"role-name": "writer"}},
    )
    # Absent at pre-flight, then present on the post-conflict re-probe.
    client._present = {}
    plan = run(declaration(roles=[{"name": "writer"}]), client, apply=True)
    client._present = {"/manage/v2/roles/writer": {"role-name": "writer"}}
    obj = plan.objects[0]
    assert obj.status in (PlanStatus.UNCHANGED, PlanStatus.BLOCKED)


def test_409_then_absent_on_reprobe_blocks_rather_than_guessing():
    client = FakeClient(conflict_on={"/manage/v2/roles"})
    plan = run(declaration(roles=[{"name": "writer"}]), client, apply=True)
    obj = plan.objects[0]
    assert obj.status is PlanStatus.BLOCKED
    assert "refusing to guess" in obj.blocked_reason


# --- failure policy -------------------------------------------------------------------


def test_a_mid_run_failure_stops_and_keeps_the_partial_plan():
    client = FakeClient(fail_on="/manage/v2/users")
    result = preflight(
        declaration(roles=[{"name": "writer"}], users=[{"name": "svc"}]),
        resolved_host=HOST,
        client=client,
        mode="plan",
    )
    plan = DeployPlan.new(mode="apply", target=[HOST])
    with pytest.raises(RuntimeError):
        reconcile(result, plan, client, apply=True)

    # The role was applied and is recorded; nothing was rolled back.
    role = next(o for o in plan.objects if o.kind == "role")
    assert role.applied is True
    assert not any(verb == "DELETE" for verb, _, _ in client.writes)


def test_no_rollback_is_attempted():
    client = FakeClient(fail_on="/manage/v2/users")
    result = preflight(
        declaration(roles=[{"name": "writer"}], users=[{"name": "svc"}]),
        resolved_host=HOST,
        client=client,
        mode="plan",
    )
    plan = DeployPlan.new(mode="apply", target=[HOST])
    with pytest.raises(RuntimeError):
        reconcile(result, plan, client, apply=True)
    assert all(verb in ("POST", "PUT") for verb, _, _ in client.writes)


def test_a_rerun_after_a_partial_failure_reconciles():
    """Idempotence is the recovery, which is why no rollback is needed."""
    first = FakeClient(fail_on="/manage/v2/users")
    raw = declaration(roles=[{"name": "writer"}], users=[{"name": "svc"}])
    result = preflight(raw, resolved_host=HOST, client=first, mode="plan")
    with pytest.raises(RuntimeError):
        reconcile(
            result, DeployPlan.new(mode="apply", target=[HOST]), first, apply=True
        )

    # Second run: the role now exists, the user does not.
    second = FakeClient(present={"/manage/v2/roles/writer": {"role-name": "writer"}})
    plan = run(raw, second, apply=True)
    role = next(o for o in plan.objects if o.kind == "role")
    user = next(o for o in plan.objects if o.kind == "user")
    assert role.status is PlanStatus.UNCHANGED
    assert user.status is PlanStatus.CREATE


# --- passwords --------------------------------------------------------------------


def test_password_is_sent_on_create(monkeypatch):
    monkeypatch.setenv("ML_PW", "resolved-secret")
    client = FakeClient()
    run(
        declaration(users=[{"name": "svc", "password": ENV_REFERENCE}]),
        client,
        apply=True,
    )
    body = next(p for verb, path, p in client.writes if "users" in path)
    # The RESOLVED secret, not the reference the declaration carries.
    assert body["password"] == "resolved-secret"  # pragma: allowlist secret


def test_password_is_not_resent_to_an_existing_user_without_rotation():
    client = FakeClient(present={"/manage/v2/users/svc": {"user-name": "svc"}})
    plan = run(
        declaration(users=[{"name": "svc", "password": ENV_REFERENCE}]),
        client,
        apply=True,
    )
    user = next(o for o in plan.objects if o.kind == "user")
    assert user.status is PlanStatus.UNCHANGED
    assert client.writes == []


def test_rotate_passwords_sends_it_to_an_existing_user_as_a_redacted_update(
    monkeypatch,
):
    monkeypatch.setenv("ML_PW", "resolved-secret")
    client = FakeClient(present={"/manage/v2/users/svc": {"user-name": "svc"}})
    plan = run(
        declaration(users=[{"name": "svc", "password": ENV_REFERENCE}]),
        client,
        apply=True,
        rotate_passwords=True,
    )
    user = next(o for o in plan.objects if o.kind == "user")
    assert user.status is PlanStatus.UPDATE
    change = next(c for c in user.changes if c.property == "password")
    assert change.redacted is True
    assert change.observed is None
