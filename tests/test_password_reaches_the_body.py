"""The resolved secret must reach the request body.

`resolved_secrets` was populated by pre-flight and read by nothing, so the body carried the
reference string as the password.
"""

import pytest
from pydantic import SecretStr

from marklogic_tool.core.http import Absent, Present
from marklogic_tool.deploy.errors import SecretReferenceError
from marklogic_tool.deploy.order import Node
from marklogic_tool.deploy.plan import DeployPlan
from marklogic_tool.deploy.preflight import PreflightResult
from marklogic_tool.deploy.reconcile import reconcile
from marklogic_tool.deploy.schema import load_declaration

SECRET = "the-real-secret-value"  # pragma: allowlist secret
REFERENCE = "ssm:/example/v1/marklogic/writer-password"  # pragma: allowlist secret


class Recorder:
    def __init__(self, present=()):
        self._present = dict(present)
        self.bodies: list[tuple[str, str, dict]] = []
        self.probes: list[str] = []

    def probe(self, path, params=None):
        self.probes.append(path)
        if path.endswith("/properties"):
            owner = path[: -len("/properties")]
            return (
                Present(payload=self._present[owner])
                if owner in self._present
                else Absent()
            )
        if path in self._present:
            return Present(payload={"name": path.rsplit("/", 1)[-1]})
        return Absent()

    def post(self, path, payload, params=None, *, accept=None):
        self.bodies.append(("POST", path, payload))
        return _Ok(201)

    def put(self, path, payload, params=None, *, accept=None):
        self.bodies.append(("PUT", path, payload))
        return _Ok(204)


class _Ok:
    def __init__(self, status_code):
        self.status_code = status_code


def declaration():
    return load_declaration(
        {
            "version": 1,
            "target": {"hosts": ["h"]},
            "users": [{"name": "svc", "password": REFERENCE}],
        }
    )


def run(client, *, absent, resolved, rotate=False):
    node = Node("user", "svc")
    result = PreflightResult(
        declaration=declaration(),
        order=[node],
        observed={} if absent else {node: {"user-name": "svc"}},
        absent={node} if absent else set(),
        resolved_secrets=resolved,
    )
    plan = DeployPlan.new(mode="apply", target=["h"])
    reconcile(result, plan, client, apply=True, rotate_passwords=rotate)
    return plan


def password_in(bodies):
    return [body.get("password") for _, _, body in bodies if "password" in body]


# --- create -------------------------------------------------------------------------


def test_the_create_body_carries_the_resolved_secret():
    client = Recorder()
    run(client, absent=True, resolved={"svc": SecretStr(SECRET)})
    assert password_in(client.bodies) == [SECRET]


def test_the_create_body_never_carries_the_reference_string():
    """Setting the account's password to `ssm:/...` makes it guessable."""
    client = Recorder()
    run(client, absent=True, resolved={"svc": SecretStr(SECRET)})
    for value in password_in(client.bodies):
        assert not value.startswith("ssm:")
        assert value != REFERENCE


def test_the_emitted_password_is_never_empty():
    client = Recorder()
    run(client, absent=True, resolved={"svc": SecretStr(SECRET)})
    for value in password_in(client.bodies):
        assert value


# --- rotate -------------------------------------------------------------------------


def test_rotate_sends_the_resolved_secret_to_an_existing_user():
    """The path that DESTROYED working credentials while reporting success."""
    client = Recorder(present={"/manage/v2/users/svc": {"user-name": "svc"}})
    run(client, absent=False, resolved={"svc": SecretStr(SECRET)}, rotate=True)
    assert password_in(client.bodies) == [SECRET]


def test_rotate_never_writes_the_reference_over_a_working_password():
    client = Recorder(present={"/manage/v2/users/svc": {"user-name": "svc"}})
    run(client, absent=False, resolved={"svc": SecretStr(SECRET)}, rotate=True)
    for value in password_in(client.bodies):
        assert value == SECRET


# --- the fail-loud guard --------------------------------------------------------------


def test_an_unresolved_password_refuses_rather_than_writing_the_reference():
    """Never write a password the operator did not choose, and never call it success."""
    client = Recorder()
    with pytest.raises(SecretReferenceError) as excinfo:
        run(client, absent=True, resolved={})
    assert "never resolved" in str(excinfo.value)
    assert client.bodies == []


def test_the_refusal_does_not_report_the_object_as_applied():
    client = Recorder()
    node = Node("user", "svc")
    result = PreflightResult(
        declaration=declaration(),
        order=[node],
        observed={},
        absent={node},
        resolved_secrets={},
    )
    plan = DeployPlan.new(mode="apply", target=["h"])
    with pytest.raises(SecretReferenceError):
        reconcile(result, plan, client, apply=True)
    assert all(not o.applied for o in plan.objects)


# --- the split: body has it, plan does not ---------------------------------------------


def test_the_plan_document_still_redacts_what_the_body_carries():
    client = Recorder()
    plan = run(client, absent=True, resolved={"svc": SecretStr(SECRET)})
    change = next(
        c for o in plan.objects for c in o.changes if c.property == "password"
    )
    assert change.redacted is True
    assert change.observed is None
    assert change.desired is None


def test_the_secret_never_appears_anywhere_in_the_plan_document():
    client = Recorder()
    plan = run(client, absent=True, resolved={"svc": SecretStr(SECRET)})
    assert SECRET not in str(plan.to_wire())
    # ...while the body it just sent did carry it.
    assert SECRET in password_in(client.bodies)


def test_a_user_without_a_declared_password_needs_no_secret():
    client = Recorder()
    node = Node("user", "plain")
    result = PreflightResult(
        declaration=load_declaration(
            {
                "version": 1,
                "target": {"hosts": ["h"]},
                "users": [{"name": "plain"}],
            }
        ),
        order=[node],
        observed={},
        absent={node},
        resolved_secrets={},
    )
    plan = DeployPlan.new(mode="apply", target=["h"])
    reconcile(result, plan, client, apply=True)
    assert password_in(client.bodies) == []
    assert plan.objects[0].applied is True


# --- what `applied` is allowed to mean --------------------------


def test_applied_is_confirmed_by_reading_the_object_back():
    """`applied` must mean more than a 2xx response.

    A 2xx on an incomplete payload reports success as loudly as a correct one. So the tool
    reads the object back after the write, and this test asserts that read.
    """
    client = Recorder()
    node = Node("role", "writer")
    result = PreflightResult(
        declaration=load_declaration(
            {
                "version": 1,
                "target": {"hosts": ["h"]},
                "roles": [{"name": "writer", "description": "d"}],
            }
        ),
        order=[node],
        observed={},
        absent={node},
    )
    plan = DeployPlan.new(mode="apply", target=["h"])
    probes_before_write = len(client.probes)
    reconcile(result, plan, client, apply=True)

    assert client.bodies, "nothing was written"
    assert (
        "/manage/v2/roles/writer/properties" in client.probes[probes_before_write:]
    ), "the object was never read back, so `applied` means only 'the request was sent'"


def test_a_write_that_does_not_take_effect_is_reported_not_hidden():
    """The server accepted it; the value did not land. Say so."""

    class AcceptsButIgnores(Recorder):
        def probe(self, path, params=None):
            if path.endswith("/properties"):
                # Post-state does NOT reflect what was written.
                return Present(payload={"description": "something else"})
            return Present(payload={"name": "writer"})

    client = AcceptsButIgnores(present={"/manage/v2/roles/writer": {}})
    node = Node("role", "writer")
    result = PreflightResult(
        declaration=load_declaration(
            {
                "version": 1,
                "target": {"hosts": ["h"]},
                "roles": [{"name": "writer", "description": "declared"}],
            }
        ),
        order=[node],
        observed={node: {"description": "old"}},
        absent=set(),
    )
    plan = DeployPlan.new(mode="apply", target=["h"])
    reconcile(result, plan, client, apply=True)
    obj = plan.objects[0]
    assert any("post-state does NOT match" in note for note in obj.notes)
    assert any("did not take effect" in w for w in plan.warnings)


def test_a_password_is_reported_as_written_but_not_verifiable():
    """unobservable by design, so `applied` cannot mean confirmed for it."""
    client = Recorder()
    plan = run(client, absent=True, resolved={"svc": SecretStr(SECRET)})
    obj = plan.objects[0]
    assert any("NOT verifiable" in note for note in obj.notes)
    assert any("password" in note for note in obj.notes)
