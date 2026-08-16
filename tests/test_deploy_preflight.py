"""Pre-flight.

The load-bearing assertion is the last one in this file: on every failure path the tool issues
no write. A client records every call and refuses anything that is not a probe.
"""

import pytest

from marklogic_tool.core.exceptions import (
    AuthenticationError,
    ConfigurationError,
    MarkLogicToolError,
)
from marklogic_tool.core.http import Absent, Present
from marklogic_tool.deploy.errors import (
    DanglingReferenceError,
    DeclarationError,
    DependencyCycleError,
    SecretReferenceError,
)
from marklogic_tool.deploy.preflight import SECURITY_SURFACE_PATH, preflight

REF_ML_A = "env:ML_A"  # pragma: allowlist secret
REF_ML_B = "env:ML_B"  # pragma: allowlist secret
REF_ML_C = "env:ML_C"  # pragma: allowlist secret
REF_ML_EXISTING = "env:ML_EXISTING"  # pragma: allowlist secret
REF_ML_MISSING_ROT = "env:ML_MISSING_ROT"  # pragma: allowlist secret
REF_ML_NEVER_SET = "env:ML_NEVER_SET"  # pragma: allowlist secret
REF_ML_NEW = "env:ML_NEW"  # pragma: allowlist secret
REF_ML_PW = "env:ML_PW"  # pragma: allowlist secret

HOST = "ml-01.example.test"


class RecordingClient:
    """A probe seam that records calls and can be told what to return.

    It has no put/post/delete at all: reaching for one is an AttributeError, which is
    the point.
    """

    def __init__(self, present=(), raise_on_security=None):
        self.calls: list[tuple[str, dict | None]] = []
        self._present = dict(present)
        self._raise_on_security = raise_on_security

    def probe(self, path, params=None):
        self.calls.append((path, params))
        if path == SECURITY_SURFACE_PATH and self._raise_on_security is not None:
            raise self._raise_on_security
        if path in self._present:
            return Present(payload=self._present[path])
        return Absent()

    @property
    def paths(self) -> list[str]:
        return [path for path, _ in self.calls]


def declaration(**extra):
    base = {"version": 1, "target": {"hosts": [HOST]}}
    base.update(extra)
    return base


# --- the three offline checks: they must not touch the network at all -------------


def test_schema_invalid_declaration_fails_before_any_request():
    client = RecordingClient()
    with pytest.raises(DeclarationError):
        preflight({"version": 1}, resolved_host=HOST, client=client)
    assert client.calls == []


def test_host_absent_from_allowlist_fails_before_any_request():
    client = RecordingClient()
    with pytest.raises(DeclarationError) as excinfo:
        preflight(declaration(), resolved_host="prod-99", client=client)
    assert "prod-99" in str(excinfo.value)
    assert "target.hosts" in str(excinfo.value)
    assert client.calls == []


def test_dangling_reference_fails_before_any_request():
    client = RecordingClient()
    with pytest.raises(DanglingReferenceError):
        preflight(
            declaration(users=[{"name": "svc", "roles": ["ghost"]}]),
            resolved_host=HOST,
            client=client,
        )
    assert client.calls == []


def test_cyclic_graph_fails_before_any_request():
    client = RecordingClient()
    with pytest.raises(DependencyCycleError):
        preflight(
            declaration(
                roles=[
                    {"name": "a", "inherits": ["b"]},
                    {"name": "b", "inherits": ["a"]},
                ]
            ),
            resolved_host=HOST,
            client=client,
        )
    assert client.calls == []


# --- the network checks -----------------------------------------------------------


def test_security_surface_is_read_before_anything_else():
    client = RecordingClient()
    preflight(
        declaration(databases=[{"name": "content"}]), resolved_host=HOST, client=client
    )
    assert client.paths[0] == SECURITY_SURFACE_PATH


def test_credential_without_security_read_fails_with_a_named_error():
    client = RecordingClient(
        raise_on_security=AuthenticationError("permission denied on /manage/v2/roles")
    )
    with pytest.raises(ConfigurationError) as excinfo:
        preflight(declaration(), resolved_host=HOST, client=client)
    message = str(excinfo.value)
    assert "security surface" in message
    assert "half-created" in message


def test_unreachable_endpoint_surfaces_rather_than_being_swallowed():
    from marklogic_tool.core.exceptions import NetworkError

    client = RecordingClient(raise_on_security=NetworkError("cannot connect"))
    with pytest.raises(NetworkError):
        preflight(declaration(), resolved_host=HOST, client=client)


def test_every_declared_object_is_probed():
    client = RecordingClient()
    result = preflight(
        declaration(
            databases=[{"name": "content"}],
            roles=[{"name": "writer"}],
            users=[{"name": "svc", "roles": ["writer"]}],
        ),
        resolved_host=HOST,
        client=client,
    )
    probed = set(client.paths)
    assert "/manage/v2/databases/content" in probed
    assert "/manage/v2/roles/writer" in probed
    assert "/manage/v2/users/svc" in probed
    assert len(result.order) == 3


def test_app_server_probe_is_group_scoped():
    client = RecordingClient()
    preflight(
        declaration(app_servers=[{"name": "app"}]), resolved_host=HOST, client=client
    )
    call = next(c for c in client.calls if c[0] == "/manage/v2/servers/app")
    assert call[1] == {"group-id": "Default"}


def test_absence_is_recorded_not_raised():
    client = RecordingClient()
    result = preflight(
        declaration(databases=[{"name": "content"}]), resolved_host=HOST, client=client
    )
    assert result.is_absent("database", "content")
    assert result.observed == {}


def test_observed_payload_is_kept_so_nothing_is_probed_twice():
    client = RecordingClient(
        present={"/manage/v2/databases/content": {"database-name": "content"}}
    )
    result = preflight(
        declaration(databases=[{"name": "content"}]), resolved_host=HOST, client=client
    )
    assert not result.is_absent("database", "content")
    assert list(result.observed.values()) == [{"database-name": "content"}]


# --- port conflicts ----------------------------------------------------------------


def test_port_bound_by_a_different_server_fails():
    client = RecordingClient(
        present={"/manage/v2/servers/other": {"port": 8030}},
    )
    with pytest.raises(DeclarationError) as excinfo:
        preflight(
            declaration(app_servers=[{"name": "other"}, {"name": "app", "port": 8030}]),
            resolved_host=HOST,
            client=client,
        )
    message = str(excinfo.value)
    assert "8030" in message
    assert "other" in message


def test_a_server_keeping_its_own_port_is_not_a_conflict():
    client = RecordingClient(present={"/manage/v2/servers/app": {"port": 8030}})
    result = preflight(
        declaration(app_servers=[{"name": "app", "port": 8030}]),
        resolved_host=HOST,
        client=client,
    )
    assert not result.is_absent("app_server", "app")


# --- secrets, narrowed ------------------------------------------------


def test_dry_run_on_an_unconfigured_server_never_demands_secrets(monkeypatch):
    """every user is a create here, yet plan mode resolves nothing."""
    monkeypatch.delenv("ML_NEVER_SET", raising=False)
    client = RecordingClient()
    result = preflight(
        declaration(
            users=[
                {
                    "name": "svc",
                    "password": REF_ML_NEVER_SET,
                }  # pragma: allowlist secret
            ]  # pragma: allowlist secret
        ),
        resolved_host=HOST,
        client=client,
        mode="plan",
    )
    assert result.resolved_secrets == {}
    assert result.is_absent("user", "svc")


def test_apply_resolves_only_secrets_for_users_that_do_not_exist(monkeypatch):
    monkeypatch.setenv("ML_NEW", "s3cret")
    monkeypatch.delenv("ML_EXISTING", raising=False)
    client = RecordingClient(
        present={"/manage/v2/users/existing": {"user-name": "existing"}}
    )
    result = preflight(
        declaration(
            users=[
                {
                    "name": "existing",
                    "password": REF_ML_EXISTING,
                },
                {"name": "new", "password": REF_ML_NEW},
            ]
        ),
        resolved_host=HOST,
        client=client,
        mode="apply",
    )
    # The existing user's reference is never resolved, so its unset variable is
    # irrelevant — that is the narrowing this asks for.
    assert set(result.resolved_secrets) == {"new"}


def test_all_failing_secret_references_are_reported_at_once(monkeypatch):
    for var in ("ML_A", "ML_B", "ML_C"):
        monkeypatch.delenv(var, raising=False)
    client = RecordingClient()
    with pytest.raises(SecretReferenceError) as excinfo:
        preflight(
            declaration(
                users=[
                    {"name": "a", "password": REF_ML_A},
                    {"name": "b", "password": REF_ML_B},
                    {"name": "c", "password": REF_ML_C},
                ]
            ),
            resolved_host=HOST,
            client=client,
            mode="apply",
        )
    message = str(excinfo.value)
    assert "3 secret reference(s)" in message
    for name in ("'a'", "'b'", "'c'"):
        assert name in message


def test_rotate_resolves_secrets_for_users_that_already_exist(monkeypatch):
    """Rotation targets existing users, so narrowing to absent users starves it.

    Without this test the rotate path has nothing to write. It then destroys working
    credentials and reports success.
    """
    monkeypatch.setenv("ML_PW", "rotated-secret")
    client = RecordingClient(present={"/manage/v2/users/svc": {"user-name": "svc"}})
    result = preflight(
        declaration(users=[{"name": "svc", "password": REF_ML_PW}]),
        resolved_host=HOST,
        client=client,
        mode="apply",
        rotate_passwords=True,
    )
    assert "svc" in result.resolved_secrets
    assert result.resolved_secrets["svc"].get_secret_value() == "rotated-secret"


def test_without_rotation_an_existing_user_still_resolves_nothing(monkeypatch):
    """Holds when not rotating: passwords stay create-only."""
    monkeypatch.setenv("ML_PW", "unused")
    client = RecordingClient(present={"/manage/v2/users/svc": {"user-name": "svc"}})
    result = preflight(
        declaration(users=[{"name": "svc", "password": REF_ML_PW}]),
        resolved_host=HOST,
        client=client,
        mode="apply",
    )
    assert result.resolved_secrets == {}


def test_a_user_without_a_password_needs_no_secret():
    client = RecordingClient()
    result = preflight(
        declaration(users=[{"name": "svc"}]),
        resolved_host=HOST,
        client=client,
        mode="apply",
    )
    assert result.resolved_secrets == {}


# --- the guarantee -----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        {"version": 1},
        {"version": 1, "target": {"hosts": ["somewhere-else"]}},
        {
            "version": 1,
            "target": {"hosts": [HOST]},
            "users": [{"name": "svc", "roles": ["ghost"]}],
        },
        {
            "version": 1,
            "target": {"hosts": [HOST]},
            "app_servers": [{"name": "other"}, {"name": "app", "port": 8030}],
        },
    ],
)
def test_no_write_is_issued_on_any_failure_path(raw):
    """Every failure path must leave the server untouched.

    `RecordingClient` records probes and exposes no write verb. A pre-flight that reached for
    one raises `AttributeError`, so this test requires the specific refusal instead.
    """
    client = RecordingClient(present={"/manage/v2/servers/other": {"port": 8030}})
    with pytest.raises(MarkLogicToolError):
        preflight(raw, resolved_host=HOST, client=client, mode="apply")
    assert all(isinstance(path, str) for path, _ in client.calls)


def test_the_probe_seam_exposes_no_write_verb():
    """Guard the guard: the recording client must not accidentally allow a write."""
    client = RecordingClient()
    for verb in ("put", "post", "delete", "patch"):
        assert not hasattr(client, verb)


def test_the_refusal_names_rotation_when_that_is_why_the_secret_was_needed(monkeypatch):
    """Under --rotate-passwords the users DO exist, so the old wording was wrong.

    A refusal that misstates why it needed the secret sends the operator looking
    for a user-creation problem that is not there.
    """
    monkeypatch.delenv("ML_MISSING_ROT", raising=False)
    decl = declaration(
        users=[
            {
                "name": "svc",
                "password": REF_ML_MISSING_ROT,
            }  # pragma: allowlist secret
        ]  # pragma: allowlist secret
    )

    # Rotating: the user already exists on the server.
    existing = RecordingClient(present={"/manage/v2/users/svc": {"user-name": "svc"}})
    with pytest.raises(SecretReferenceError) as rotating:
        preflight(
            decl,
            resolved_host=HOST,
            client=existing,
            mode="apply",
            rotate_passwords=True,
        )
    assert "rotated" in str(rotating.value)
    assert "do not yet exist" not in str(rotating.value)

    # Creating: the user is absent, and the original wording is correct there.
    with pytest.raises(SecretReferenceError) as creating:
        preflight(decl, resolved_host=HOST, client=RecordingClient(), mode="apply")
    assert "do not yet exist" in str(creating.value)
