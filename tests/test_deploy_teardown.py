"""Teardown and the destroy guards.

Every test here is about something teardown REFUSES. That ratio is the design: the
verb removes three kinds and declines everything else, and no flag reaches a database
or a forest.
"""

# cspell:ignore mltool

import pytest

from marklogic_tool.core.exceptions import ServerError
from marklogic_tool.core.http import Absent, Present
from marklogic_tool.deploy.errors import DeclarationError, DeclarationUsageError
from marklogic_tool.deploy.mapping import mapping_for
from marklogic_tool.deploy.order import teardown_order
from marklogic_tool.deploy.preflight import preflight
from marklogic_tool.deploy.teardown import (
    RECREATABLE_KINDS,
    SETTLE_ATTEMPTS,
    confirm_host,
    find_outside_dependents,
    removable,
    teardown,
)

HOST = "ml-01.example.test"


class FakeClient:
    def __init__(self, present=()):
        self._present = dict(present)
        self.deletes: list[str] = []

    def probe(self, path, params=None):
        if path in self._present:
            return Present(payload=self._present[path])
        return Absent()

    def delete(self, path, params=None):
        self.deletes.append(path)


def declaration(**extra):
    base = {"version": 1, "target": {"hosts": [HOST]}}
    base.update(extra)
    return base


def result_for(raw, client):
    return preflight(raw, resolved_host=HOST, client=client, mode="plan")


EVERYTHING = declaration(
    databases=[{"name": "content"}],
    roles=[{"name": "writer"}],
    users=[{"name": "svc", "roles": ["writer"]}],
)

ALL_PRESENT = {
    "/manage/v2/databases/content": {"database-name": "content"},
    "/manage/v2/roles/writer": {"role-name": "writer"},
    "/manage/v2/users/svc": {"user-name": "svc"},
}


# --- the confirmation guard -----------------------------------------------------------


def test_a_mismatched_confirm_host_refuses_before_anything():
    client = FakeClient(present=ALL_PRESENT)
    with pytest.raises(DeclarationUsageError) as excinfo:
        teardown(result_for(EVERYTHING, client), client, "wrong-host", apply=True)
    assert "wrong-host" in str(excinfo.value)
    assert client.deletes == []


def test_the_confirmation_refusal_names_the_valid_hosts():
    client = FakeClient(present=ALL_PRESENT)
    with pytest.raises(DeclarationUsageError) as excinfo:
        confirm_host(result_for(EVERYTHING, client), "nope")
    assert HOST in str(excinfo.value)


def test_a_matching_confirm_host_passes():
    client = FakeClient(present=ALL_PRESENT)
    confirm_host(result_for(EVERYTHING, client), HOST)


# --- what may be removed ---------------------------------------------------------------


def test_databases_and_forests_are_never_in_the_recreatable_set():
    """No flag reaches them. This is the whole safety story."""
    assert "database" not in RECREATABLE_KINDS
    assert "forest" not in RECREATABLE_KINDS
    # rest_api joined on MEASURED grounds: a bare DELETE /v1/rest-apis
    # removes the REST instance and app server and leaves BOTH databases intact.
    # The exact-set assertion is kept deliberately, so adding a kind cannot pass
    # without someone editing this line and justifying it.
    assert set(RECREATABLE_KINDS) == {"app_server", "user", "role", "rest_api"}


def test_only_recreatable_objects_are_removed():
    client = FakeClient(present=ALL_PRESENT)
    teardown(result_for(EVERYTHING, client), client, HOST, apply=True)
    assert all("database" not in path for path in client.deletes)
    assert any("roles/writer" in path for path in client.deletes)
    assert any("users/svc" in path for path in client.deletes)


def test_removal_happens_in_reverse_creation_order():
    client = FakeClient(present=ALL_PRESENT)
    teardown(result_for(EVERYTHING, client), client, HOST, apply=True)
    # A user depends on its role, so the user goes first on the way out.
    assert client.deletes.index("/manage/v2/users/svc") < client.deletes.index(
        "/manage/v2/roles/writer"
    )


def test_absent_objects_are_not_removed():
    client = FakeClient(present={"/manage/v2/roles/writer": {"role-name": "writer"}})
    teardown(result_for(EVERYTHING, client), client, HOST, apply=True)
    assert client.deletes == ["/manage/v2/roles/writer"]


def test_partial_teardown_is_reported_explicitly():
    client = FakeClient(present=ALL_PRESENT)
    report = teardown(result_for(EVERYTHING, client), client, HOST, apply=True)
    assert report["removed"]
    # The database is named as deliberately remaining, not silently skipped.
    assert any("database" in item for item in report["remaining"])


def test_dry_run_removes_nothing_but_still_reports_what_it_would():
    client = FakeClient(present=ALL_PRESENT)
    report = teardown(result_for(EVERYTHING, client), client, HOST, apply=False)
    assert client.deletes == []
    assert report["removed"] == []
    assert report["would_remove"]


# --- dependents ------------------------------------------------------------------------


def test_dependents_outside_the_declared_set_produce_a_refusal_naming_them():
    client = FakeClient(present=ALL_PRESENT)
    with pytest.raises(DeclarationError) as excinfo:
        teardown(
            result_for(EVERYTHING, client),
            client,
            HOST,
            apply=True,
            outside_dependents=["role:auditor", "user:reporting"],
        )
    message = str(excinfo.value)
    assert "role:auditor" in message
    assert "user:reporting" in message
    assert client.deletes == []


def test_the_dependent_refusal_does_not_assume_cascade_behaviour():
    """Cascade semantics are not established; refusing is correct under either answer."""
    client = FakeClient(present=ALL_PRESENT)
    with pytest.raises(DeclarationError) as excinfo:
        teardown(
            result_for(EVERYTHING, client),
            client,
            HOST,
            apply=True,
            outside_dependents=["role:auditor"],
        )
    assert "not established" in str(excinfo.value)


# --- re-deploy after destroy ------------------------------------------------------------


def test_a_redeploy_after_a_destroy_reconciles_over_the_orphans():
    """The database survives the destroy, so the next deploy sees it as unchanged."""
    client = FakeClient(present=ALL_PRESENT)
    teardown(result_for(EVERYTHING, client), client, HOST, apply=True)

    # After the destroy: roles and users gone, the database still there.
    after = FakeClient(present={"/manage/v2/databases/content": {}})
    redeploy = result_for(EVERYTHING, after)
    from marklogic_tool.deploy.order import Node

    assert Node("database", "content") not in redeploy.absent
    assert Node("role", "writer") in redeploy.absent


def test_removable_is_empty_when_nothing_exists():
    client = FakeClient()
    assert removable(result_for(EVERYTHING, client)) == []


# --- the refusal must COMPUTE its dependents, not be handed them ----------------


class LiveShapedServer:
    """Serve collection listings and `/properties` the way MarkLogic does.

    Earlier fixtures passed `outside_dependents` in, so nothing tested whether the tool
    computes it.
    """

    def __init__(self, roles: dict[str, dict], users: dict[str, dict]):
        self._roles = roles
        self._users = users
        self.deletes: list[str] = []

    def _listing(self, kind, names):
        return {
            f"{kind}-default-list": {
                "list-items": {"list-item": [{"nameref": n} for n in names]}
            }
        }

    def probe(self, path, params=None):
        if path == "/manage/v2/roles":
            return Present(payload=self._listing("role", self._roles))
        if path == "/manage/v2/users":
            return Present(payload=self._listing("user", self._users))
        if path.endswith("/properties"):
            owner = path[: -len("/properties")]
            for prefix, store in (
                ("/manage/v2/roles/", self._roles),
                ("/manage/v2/users/", self._users),
            ):
                if owner.startswith(prefix):
                    name = owner[len(prefix) :]
                    if name in store:
                        return Present(payload=store[name])
            return Absent()
        for prefix, store in (
            ("/manage/v2/roles/", self._roles),
            ("/manage/v2/users/", self._users),
        ):
            if path.startswith(prefix) and path[len(prefix) :] in store:
                return Present(payload={"name": path[len(prefix) :]})
        return Absent()

    def delete(self, path, params=None):
        self.deletes.append(path)


DECL = declaration(
    roles=[{"name": "writer"}], users=[{"name": "svc", "roles": ["writer"]}]
)


def test_a_dependent_outside_the_declaration_is_found_and_refused():
    """Live behaviour: an outside role inheriting a declared one did NOT refuse."""
    server = LiveShapedServer(
        roles={
            "writer": {"role-name": "writer"},
            # NOT declared, and it inherits the role about to be removed.
            "mltool-probe-dependent": {
                "role-name": "mltool-probe-dependent",
                "role": ["writer"],
            },
        },
        users={"svc": {"user-name": "svc", "role": ["writer"]}},
    )
    result = result_for(DECL, server)
    with pytest.raises(DeclarationError) as excinfo:
        teardown(result, server, HOST, apply=True)
    assert "role:mltool-probe-dependent" in str(excinfo.value)
    assert server.deletes == []


def test_the_dependents_are_computed_not_supplied():
    """Calling teardown WITHOUT the argument must still refuse."""
    server = LiveShapedServer(
        roles={
            "writer": {"role-name": "writer"},
            "outsider": {"role-name": "outsider", "role": ["writer"]},
        },
        users={"svc": {"user-name": "svc", "role": ["writer"]}},
    )
    found = find_outside_dependents(result_for(DECL, server), server)
    assert found == ["role:outsider"]


def test_a_declared_dependent_is_not_an_outside_dependent():
    """`svc` holds `writer`, but it is declared and is being removed too."""
    server = LiveShapedServer(
        roles={"writer": {"role-name": "writer"}},
        users={"svc": {"user-name": "svc", "role": ["writer"]}},
    )
    assert find_outside_dependents(result_for(DECL, server), server) == []


def test_an_outside_user_holding_a_doomed_role_is_a_dependent():
    server = LiveShapedServer(
        roles={"writer": {"role-name": "writer"}},
        users={
            "svc": {"user-name": "svc", "role": ["writer"]},
            "reporting": {"user-name": "reporting", "role": ["writer"]},
        },
    )
    assert find_outside_dependents(result_for(DECL, server), server) == [
        "user:reporting"
    ]


def test_an_unrelated_outside_role_is_not_a_dependent():
    """Only things depending on a DOOMED object count."""
    server = LiveShapedServer(
        roles={
            "writer": {"role-name": "writer"},
            "unrelated": {"role-name": "unrelated", "role": ["rest-reader"]},
        },
        users={"svc": {"user-name": "svc", "role": ["writer"]}},
    )
    assert find_outside_dependents(result_for(DECL, server), server) == []


def test_with_no_dependents_the_teardown_proceeds():
    server = LiveShapedServer(
        roles={"writer": {"role-name": "writer"}},
        users={"svc": {"user-name": "svc", "role": ["writer"]}},
    )
    report = teardown(result_for(DECL, server), server, HOST, apply=True)
    assert report["removed"]
    assert server.deletes


# --- a transient is "not right now", not "no" ----------------------------------


class FlakyServer(LiveShapedServer):
    """Answers 503 XDMP-DISABLED a few times, then recovers — as the real one did.

    Removing an app server puts MarkLogic into a brief reconfiguration window.
    """

    def __init__(self, roles, users, fail_times=2, error=None, absent_paths=()):
        super().__init__(roles, users)
        self._left = fail_times
        self._error = error or ServerError(
            "Manage API error (HTTP 503) XDMP-DISABLED at host:8002"
        )
        self.attempts = 0
        # Paths the server reports GONE. Teardown probes before deleting, because
        # removals cascade; without a probe() here that branch is unreachable and any
        # test of it passes for the wrong reason. It did, until a mutation exposed it.
        self.absent_paths = set(absent_paths)
        self.probes: list[str] = []

    def probe(self, path, params=None):
        from marklogic_tool.core.http import Absent, Present

        self.probes.append(path)
        if path in self.absent_paths:
            return Absent()
        return Present(payload={})

    def delete(self, path, params=None):
        self.attempts += 1
        if self._left > 0:
            self._left -= 1
            raise self._error
        self.deletes.append(path)


def _server(**kw):
    return FlakyServer(
        roles={"writer": {"role-name": "writer"}},
        users={"svc": {"user-name": "svc", "role": ["writer"]}},
        **kw,
    )


def test_a_transient_503_is_waited_out_rather_than_treated_as_fatal():
    """The live failure: 503 right after the app-server removal, recovered seconds later."""
    server = _server(fail_times=2)
    report = teardown(
        result_for(DECL, server), server, HOST, apply=True, sleep=lambda _: None
    )
    assert report["removed"]
    assert server.attempts > len(server.deletes)


def test_the_retry_is_bounded_and_says_so():
    server = _server(fail_times=99)
    with pytest.raises(ServerError) as excinfo:
        teardown(
            result_for(DECL, server), server, HOST, apply=True, sleep=lambda _: None
        )
    message = str(excinfo.value)
    assert str(SETTLE_ATTEMPTS) in message
    assert "idempotent" in message


def test_a_4xx_is_never_retried():
    """A blanket retry would mask the failures this tool works to make loud."""
    from marklogic_tool.core.exceptions import NotFoundError

    server = _server(fail_times=99, error=NotFoundError("gone"))
    with pytest.raises(NotFoundError):
        teardown(
            result_for(DECL, server), server, HOST, apply=True, sleep=lambda _: None
        )
    assert server.attempts == 1


def test_a_different_5xx_is_never_retried():
    server = _server(fail_times=99, error=ServerError("HTTP 500 internal error"))
    with pytest.raises(ServerError):
        teardown(
            result_for(DECL, server), server, HOST, apply=True, sleep=lambda _: None
        )
    assert server.attempts == 1


# --- D12b: destroy owes a report on every exit path -----------------------------------


def test_the_partial_report_survives_a_failure_mid_teardown():
    """Built as a return value it is LOST on a raise — which is what happened live."""
    server = _server(fail_times=99)
    report: dict[str, list[str]] = {"removed": [], "would_remove": [], "remaining": []}
    with pytest.raises(ServerError):
        teardown(
            result_for(DECL, server),
            server,
            HOST,
            apply=True,
            report=report,
            sleep=lambda _: None,
        )
    # The caller still holds what was reached, without reconstructing it from the server.
    assert report["would_remove"]
    assert "remaining" in report


def test_the_report_records_what_was_removed_before_the_failure():
    class HalfWay(LiveShapedServer):
        def delete(self, path, params=None):
            if "users" in path:
                raise ServerError("HTTP 500 internal error")
            self.deletes.append(path)

    server = HalfWay(
        roles={"writer": {"role-name": "writer"}},
        users={"svc": {"user-name": "svc", "role": ["writer"]}},
    )
    report: dict[str, list[str]] = {"removed": [], "would_remove": [], "remaining": []}
    with pytest.raises(ServerError):
        teardown(
            result_for(DECL, server),
            server,
            HOST,
            apply=True,
            report=report,
            sleep=lambda _: None,
        )
    # The user is removed first (reverse order), so the failure happens before any role.
    assert report["removed"] == [] or all("role" in r for r in report["removed"])


# --- rest_api removal, and `remaining` as an observation -----------------


def test_remaining_is_observed_not_inferred():
    """`remaining` is a claim about what survived. Only the server knows.

    The previous implementation derived it from the declared kind. It asserted the survival of
    a rest_api that a cascading removal had already taken.
    """
    server = _server()
    report = teardown(
        result_for(DECL, server), server, HOST, apply=True, sleep=lambda _: None
    )

    assert report["remaining_basis"] == "observed"
    # Everything declared is probed — that is what makes it an observation.
    assert server.probes, "remaining must come from probing the server"
    assert all("(unverified)" not in name for name in report["remaining"])


def test_remaining_omits_an_object_the_server_says_is_gone():
    """The discriminator between observation and inference.

    A non-recreatable object the server reports absent is the one case where the two
    implementations disagree. Inference lists it. Observation does not.
    """
    # EVERYTHING declares a database — a non-recreatable kind, which DECL lacks.
    server = _server()
    result = result_for(EVERYTHING, server)
    survivor_kinds = [
        node
        for node in teardown_order(result.declaration)
        if node.kind not in RECREATABLE_KINDS
    ]
    assert survivor_kinds, "fixture must declare a non-recreatable object"
    gone = survivor_kinds[0]
    server.absent_paths = {mapping_for(gone.kind).probe_path(gone.name)}

    report = teardown(result, server, HOST, apply=True, sleep=lambda _: None)

    assert str(gone) not in report["remaining"], (
        "an object the server reports GONE must never be listed as remaining — "
        "that is exactly what inferring from kind got wrong"
    )


def test_dry_run_labels_remaining_as_a_prediction():
    """Nothing has been removed, so nothing can be observed. Say which it is."""
    server = _server()
    report = teardown(result_for(DECL, server), server, HOST, apply=False)

    assert report["remaining_basis"] == "predicted"


def test_a_cascaded_object_is_not_reported_as_removed_by_us():
    """Removing the app server takes the REST instance with it.

    The later explicit delete finds nothing, so teardown probes first. This asserts the object
    and the absence of a DELETE for it.
    """
    server = _server()
    result = result_for(DECL, server)
    gone = removable(result)[0]
    gone_path = mapping_for(gone.kind).probe_path(gone.name)
    server.absent_paths = {gone_path}

    report = teardown(result, server, HOST, apply=True, sleep=lambda _: None)

    assert str(gone) in report["removed_by_cascade"]
    assert str(gone) not in report["removed"]
    assert gone_path not in server.deletes, "no DELETE may be issued for it"
    assert gone_path in server.probes, "it must have been probed"


def test_a_dropped_connection_during_removal_is_transient_not_fatal():
    """DELETE returns 202, then MarkLogic closes connections mid-restart.

    Treating that as fatal reports a COMPLETED removal as a crash — the shape, in
    the path the recreatable set adds.
    """
    from marklogic_tool.core.exceptions import NetworkError
    from marklogic_tool.core.manage_client import CONNECTION_CLOSED_MARKER
    from marklogic_tool.deploy.teardown import _is_transient

    dropped = NetworkError(f"Connection ... was {CONNECTION_CLOSED_MARKER}. ...")

    assert _is_transient(dropped) is True


def test_an_ordinary_network_error_is_still_fatal():
    """The widening must stay narrow: only the drop marker counts."""
    from marklogic_tool.core.exceptions import NetworkError
    from marklogic_tool.deploy.teardown import _is_transient

    assert (
        _is_transient(NetworkError("Cannot connect to manage API at h:8002")) is False
    )
