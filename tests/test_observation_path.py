"""The read path must read the configuration.

The tool probed existence at `{object}` and used that summary as the configuration. A summary
carries no declared properties, so every property read as absent.
"""

import pytest

from marklogic_tool.core.http import Absent, Present
from marklogic_tool.deploy.order import Node
from marklogic_tool.deploy.plan import DeployPlan, PlanStatus
from marklogic_tool.deploy.preflight import preflight
from marklogic_tool.deploy.reconcile import reconcile

HOST = "ml-01.example.test"


class FixtureServer:
    """A server that distinguishes the object view from the properties view.

    The summary at `{object}` carries only the name, exactly as MarkLogic's default
    view does. Everything declared lives at `{object}/properties`.
    """

    def __init__(self, objects: dict[str, dict]):
        self._properties = objects
        self.reads: list[str] = []
        self.writes: list[tuple[str, str]] = []

    def probe(self, path, params=None):
        self.reads.append(path)
        if path.endswith("/properties"):
            owner = path[: -len("/properties")]
            if owner in self._properties:
                return Present(payload=self._properties[owner])
            return Absent()
        if path in self._properties:
            # The summary view: name only, no configuration.
            return Present(payload={"name": path.rsplit("/", 1)[-1]})
        return Absent()

    def post(self, path, payload, params=None, *, accept=None):
        self.writes.append(("POST", path))
        return _Ok(201)

    def put(self, path, payload, params=None, *, accept=None):
        self.writes.append(("PUT", path))
        return _Ok(204)


class _Ok:
    def __init__(self, status_code):
        self.status_code = status_code


DECLARATION = {
    "version": 1,
    "target": {"hosts": [HOST]},
    "databases": [
        {
            "name": "content",
            "triple_index": True,
            "uri_lexicon": True,
            "directory_creation": "manual",
        }
    ],
    "roles": [{"name": "writer", "description": "Can read and write documents"}],
}

SERVER_STATE = {
    "/manage/v2/databases/content": {
        "triple-index": "true",
        "uri-lexicon": "true",
        "directory-creation": "manual",
    },
    "/manage/v2/roles/writer": {
        "role-name": "writer",
        "description": "Can read and write documents",
    },
}


@pytest.fixture
def server():
    return FixtureServer(dict(SERVER_STATE))


def run(server, *, apply=False):
    result = preflight(DECLARATION, resolved_host=HOST, client=server, mode="plan")
    plan = DeployPlan.new(mode="apply" if apply else "plan", target=[HOST])
    return reconcile(result, plan, server, apply=apply), result


# --- the observation must actually happen ---------------------------------------


def test_the_properties_endpoint_is_read(server):
    run(server)
    assert "/manage/v2/databases/content/properties" in server.reads
    assert "/manage/v2/roles/writer/properties" in server.reads


def test_observed_is_not_empty_for_a_property_the_server_holds(server):
    """The assertion the earlier tests structurally could not make."""
    _, result = run(server)
    observed = result.observed[Node("database", "content")]
    assert observed, "the observation is empty — the configuration was never read"
    assert observed["triple-index"] == "true"


def test_the_plan_records_a_non_null_observed_value(server):
    """Every `observed` came back null on the box; that is what this pins."""
    plan, _ = run(server)
    for obj in plan.objects:
        for change in obj.changes:
            assert change.observed is not None, (
                f"{obj.kind} {obj.name}: {change.property} observed as null — "
                "the read path is not seeing the server's value"
            )


# --- the consequence: idempotence ------------------------------------------------


def test_a_re_run_against_a_fully_deployed_server_is_all_unchanged(server):
    plan, _ = run(server, apply=True)
    statuses = {(o.kind, o.name): o.status for o in plan.objects}
    assert all(s is PlanStatus.UNCHANGED for s in statuses.values()), statuses


def test_a_re_run_writes_nothing(server):
    run(server, apply=True)
    assert server.writes == []


def test_a_genuine_difference_is_still_seen(server):
    """The fix must not make everything look unchanged."""
    server._properties["/manage/v2/databases/content"]["directory-creation"] = (
        "automatic"
    )
    plan, _ = run(server)
    database = next(o for o in plan.objects if o.kind == "database")
    assert database.status is PlanStatus.UPDATE
    change = next(c for c in database.changes if c.property == "directory-creation")
    assert change.observed == "automatic"
    assert change.desired == "manual"


def test_an_absent_object_is_still_absent(server):
    """Existence still comes from the object path, not from /properties."""
    empty = FixtureServer({})
    _, result = run(empty)
    assert Node("database", "content") in result.absent
    assert Node("role", "writer") in result.absent


def test_properties_unreadable_on_an_existing_object_does_not_read_as_empty():
    """A 404 on /properties for an object that exists must not wipe the observation.

    Treating it as an empty observation would classify every property as drift and
    rewrite the lot — the failure by another route.
    """

    class SummaryOnly(FixtureServer):
        def probe(self, path, params=None):
            self.reads.append(path)
            if path.endswith("/properties"):
                return Absent()
            if path in self._properties:
                return Present(payload={"name": path.rsplit("/", 1)[-1]})
            return Absent()

    server = SummaryOnly(dict(SERVER_STATE))
    _, result = run(server)
    # It exists, so it is not absent, and the summary is kept rather than nothing.
    assert Node("database", "content") not in result.absent
    assert result.observed[Node("database", "content")] == {"name": "content"}
