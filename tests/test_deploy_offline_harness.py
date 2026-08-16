"""The offline integration harness.

Mocks are confined to transport-level fault injection. This file tests the seams between the
layers: a pre-flight refusal arrives before a write, plan and apply agree, and a crash leaves
a plan behind.
"""

import httpx
import pytest
from pydantic import SecretStr

from marklogic_tool.core.config import ProfileSettings
from marklogic_tool.core.exceptions import (
    AuthenticationError,
    BadRequestError,
    ConfigurationError,
    ConflictError,
    NotFoundError,
    ServerError,
)
from marklogic_tool.core.manage_client import ManageClient
from marklogic_tool.deploy.plan import DeployPlan, PlanStatus
from marklogic_tool.deploy.preflight import preflight
from marklogic_tool.deploy.reconcile import reconcile

HOST = "ml-01.example.test"

DECLARATION = {
    "version": 1,
    "target": {"hosts": [HOST]},
    "databases": [{"name": "content"}],
    "roles": [{"name": "writer"}],
    "users": [{"name": "svc", "roles": ["writer"]}],
}


@pytest.fixture
def profile():
    return ProfileSettings(
        host=HOST,
        port=8000,
        manage_port=8002,
        username="admin",
        password=SecretStr("secret123"),
        auth_method="digest",
        timeout=30,
        default_group="Default",
    )


def manage(profile, handler):
    return ManageClient(profile, transport=httpx.MockTransport(handler))


def absent_then_created(request: httpx.Request) -> httpx.Response:
    if request.method == "GET":
        return httpx.Response(404, json={})
    return httpx.Response(201, json={})


def run(client, *, apply):
    result = preflight(DECLARATION, resolved_host=HOST, client=client, mode="plan")
    plan = DeployPlan.new(mode="apply" if apply else "plan", target=[HOST])
    return reconcile(result, plan, client, apply=apply)


# --- non-success sweep, both clients ---------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, BadRequestError),
        (401, AuthenticationError),
        (403, AuthenticationError),
        (404, NotFoundError),
        (409, ConflictError),
        (500, ServerError),
        (503, ServerError),
    ],
)
def test_manage_client_get_json_sweep(profile, status, expected):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={})

    with manage(profile, handler) as client, pytest.raises(expected):
        client.get_json("/manage/v2/roles")


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 503])
def test_query_client_sweep(profile, status):
    from marklogic_tool.core.client import MarkLogicClient
    from marklogic_tool.core.exceptions import MarkLogicToolError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={})

    with (
        MarkLogicClient(profile, transport=httpx.MockTransport(handler)) as client,
        pytest.raises(MarkLogicToolError),
    ):
        client.get("/v1/search")


# --- plan vs apply equality ------------------------------------------------------------


def test_plan_and_apply_agree_modulo_mode_and_applied(profile):
    """the guarantee is equality of SHAPE, not of every field."""
    with manage(profile, absent_then_created) as client:
        preview = run(client, apply=False)
    with manage(profile, absent_then_created) as client:
        applied = run(client, apply=True)

    assert [(o.kind, o.name, o.status) for o in preview.objects] == [
        (o.kind, o.name, o.status) for o in applied.objects
    ]
    # The permitted divergences are exactly three: mode, applied, and
    # depends_on_pending.
    assert preview.mode == "plan" and applied.mode == "apply"
    assert all(o.applied is False for o in preview.objects)
    assert any(o.applied for o in applied.objects)


def test_depends_on_pending_is_a_permitted_divergence_and_says_something_true():
    """It differs between modes, because the underlying fact differs.

    In a dry run the role is still pending when the tool classifies the user. In an apply the
    role already exists. Forcing the two to agree makes one of them lie.
    """
    profile_settings = ProfileSettings(
        host=HOST,
        port=8000,
        manage_port=8002,
        username="admin",
        password=SecretStr("secret123"),
        auth_method="digest",
        timeout=30,
        default_group="Default",
    )
    with manage(profile_settings, absent_then_created) as client:
        preview = run(client, apply=False)
    with manage(profile_settings, absent_then_created) as client:
        applied = run(client, apply=True)

    preview_user = next(o for o in preview.objects if o.kind == "user")
    applied_user = next(o for o in applied.objects if o.kind == "user")
    assert preview_user.depends_on_pending == ["writer"]
    assert applied_user.depends_on_pending == []


def test_the_change_sets_are_identical_between_modes(profile):
    with manage(profile, absent_then_created) as client:
        preview = run(client, apply=False)
    with manage(profile, absent_then_created) as client:
        applied = run(client, apply=True)
    assert [[c.property for c in o.changes] for o in preview.objects] == [
        [c.property for c in o.changes] for o in applied.objects
    ]


# --- already-exists ---------------------------------------------------------------------


def test_already_exists_on_create_becomes_unchanged(profile):
    """409 then present on re-probe: reconciled, not flattened blindly."""
    state = {"created": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            if state["created"]:
                return httpx.Response(200, json={"role-name": "writer"})
            return httpx.Response(404, json={})
        state["created"] = True
        return httpx.Response(409, json={"errorResponse": {"message": "exists"}})

    with manage(profile, handler) as client:
        plan = run(client, apply=True)

    role = next(o for o in plan.objects if o.kind == "role")
    assert role.status is PlanStatus.UNCHANGED
    assert any("already existed" in note for note in role.notes)


# --- preflight catches privilege before any write ----------------------------------------


def test_a_credential_lacking_manage_privilege_is_caught_in_preflight(profile):
    """Not mid-apply: a half-configured server is the thing pre-flight prevents."""
    writes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            writes.append(request.url.path)
        return httpx.Response(403, json={})

    with manage(profile, handler) as client, pytest.raises(ConfigurationError) as exc:
        preflight(DECLARATION, resolved_host=HOST, client=client, mode="apply")

    assert "security surface" in str(exc.value)
    assert writes == []


# --- mid-apply partial plan ---------------------------------------------------------------


def test_mid_apply_failure_leaves_a_populated_plan(profile):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, json={})
        if "users" in request.url.path:
            return httpx.Response(500, json={})
        return httpx.Response(201, json={})

    with manage(profile, handler) as client:
        result = preflight(DECLARATION, resolved_host=HOST, client=client, mode="plan")
        plan = DeployPlan.new(mode="apply", target=[HOST])
        with pytest.raises(ServerError):
            reconcile(result, plan, client, apply=True)

    # The objects processed before the failure are on the record, with their outcomes.
    assert plan.objects
    assert any(o.applied for o in plan.objects)
    role = next((o for o in plan.objects if o.kind == "role"), None)
    assert role is not None and role.applied is True


def test_a_rerun_after_the_failure_is_idempotent(profile):
    """Re-running is the recovery, which is why nothing rolls back."""

    def all_present(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            # The observed state must MATCH what the declaration asks for, or the
            # re-run is an update rather than a no-op.
            owner = request.url.path.removesuffix("/properties")
            if owner.endswith("/users/svc"):
                return httpx.Response(200, json={"role": ["writer"]})
            return httpx.Response(200, json={})
        return httpx.Response(204, json={})

    with manage(profile, all_present) as client:
        plan = run(client, apply=True)

    assert all(o.status is PlanStatus.UNCHANGED for o in plan.objects)
