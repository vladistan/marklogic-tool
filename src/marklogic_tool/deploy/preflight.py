"""Pre-flight. It checks what the tool can verify before a single write.

The guarantee: on any failure path the tool has issued no write. Every call here is a GET,
through the `probe` seam where 404 is a value.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from marklogic_tool.core import secrets
from marklogic_tool.core.exceptions import (
    AuthenticationError,
    ConfigurationError,
    MarkLogicToolError,
)
from marklogic_tool.core.http import Absent, Present
from marklogic_tool.deploy.errors import DeclarationError, SecretReferenceError
from marklogic_tool.deploy.mapping import mapping_for
from marklogic_tool.deploy.order import Node, plan_order
from marklogic_tool.deploy.schema import Declaration, load_declaration

SECURITY_SURFACE_PATH = mapping_for("role").root
"""The cheapest read that proves the credential can see the security surface.

A credential that can list databases but not roles would sail through every other
check and then fail mid-apply on the first role, which is the half-configured state
pre-flight exists to prevent.

Derived from the mapping rather than written out, so `mapping.py` stays the only
module that knows a Manage path.
"""


class ProbeClient(Protocol):
    """The read seam. Deliberately narrow: there is no write method to call."""

    def probe(
        self, path: str, params: dict[str, str] | None = None
    ) -> Absent | Present: ...


@dataclass
class PreflightResult:
    """What pre-flight learned, handed on so nothing is probed twice."""

    declaration: Declaration
    order: list[Node]
    observed: dict[Node, Any] = field(default_factory=dict)
    absent: set[Node] = field(default_factory=set)
    resolved_secrets: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def is_absent(self, kind: str, name: str) -> bool:
        return Node(kind, name) in self.absent


def _check_host_allowlist(declaration: Declaration, resolved_host: str) -> None:
    if resolved_host not in declaration.target.hosts:
        allowed = ", ".join(declaration.target.hosts) or "(none declared)"
        msg = (
            f"Failed pre-flight against host {resolved_host!r}: it is not in the "
            f"declaration's `target.hosts` allowlist. "
            f"Refusing rather than trusting the active profile, because a profile "
            f"pointing at production would otherwise deploy a staging declaration "
            f"to it. "
            f"Add {resolved_host!r} to target.hosts, or switch profile. "
            f"Declared hosts: {allowed}."
        )
        raise DeclarationError(msg)


def _probe_everything(
    declaration: Declaration, order: list[Node], client: ProbeClient
) -> tuple[dict[Node, Any], set[Node]]:
    """Probe every declared object, then read its configuration.

    Two calls per existing object, deliberately. The object path answers only whether the
    object exists. Configuration lives at `{object}/properties`.
    """
    observed: dict[Node, Any] = {}
    absent: set[Node] = set()
    group = {"group-id": "Default"}

    for node in order:
        mapping = mapping_for(node.kind)
        params = group if mapping.group_scoped else None
        result = client.probe(mapping.probe_path(node.name), params)
        if isinstance(result, Absent):
            absent.add(node)
            continue

        if not mapping.supports_properties:
            observed[node] = result.payload
            continue

        properties = client.probe(mapping.properties_path(node.name), params)
        # A 404 on /properties for an object that demonstrably exists is not absence;
        # it means the configuration could not be read, and treating it as an empty
        # observation would silently rewrite everything. Keep the summary instead.
        observed[node] = (
            result.payload if isinstance(properties, Absent) else properties.payload
        )
    _ = declaration
    return observed, absent


def _check_security_surface(client: ProbeClient) -> None:
    try:
        client.probe(SECURITY_SURFACE_PATH)
    except AuthenticationError as exc:
        msg = (
            f"Failed pre-flight reading the Manage security surface at "
            f"{SECURITY_SURFACE_PATH}: {exc.message} "
            f"Refusing to start rather than discovering it mid-apply, where roles "
            f"and users would be left half-created. "
            f"Grant the identity a Manage role that can read roles and users."
        )
        raise ConfigurationError(msg) from exc


def _check_port_conflicts(declaration: Declaration, observed: dict[Node, Any]) -> None:
    """Refuse a declared port already bound by a *different* server."""
    bound: dict[int, str] = {}
    for node, payload in observed.items():
        if node.kind != "app_server" or not isinstance(payload, Mapping):
            continue
        port = payload.get("port")
        if isinstance(port, int):
            bound[port] = node.name

    declared_ports: dict[int, str] = {}
    for server in declaration.app_servers:
        if server.port is None:
            continue
        declared_ports[server.port] = server.name
    for api in declaration.rest_apis:
        if api.port is not None:
            declared_ports.setdefault(api.port, api.name)

    for port, declared_by in declared_ports.items():
        holder = bound.get(port)
        if holder is not None and holder != declared_by:
            msg = (
                f"Failed pre-flight: declared object {declared_by!r} wants port "
                f"{port}, which is already bound by app server {holder!r}. "
                f"Refusing rather than letting the apply fail half-way, because the "
                f"objects created before it would remain. "
                f"Choose a free port, or remove {holder!r} first."
            )
            raise DeclarationError(msg)


def _resolve_secrets(
    declaration: Declaration,
    absent: set[Node],
    mode: Literal["plan", "apply"],
    identities: Mapping[str, str] | None,
    rotate_passwords: bool = False,
) -> dict[str, Any]:
    """Resolve only the secrets a planned create needs.

    The probe answers whether a user exists, not the diff. A dry run never demands a
    production secret.
    """
    # Normally only a create needs a secret (passwords are create-only). A
    # rotation deliberately targets users that ALREADY exist, so it must resolve for
    # them too — otherwise the rotate path has nothing to write and silently destroys
    # working credentials.
    needed = [
        user
        for user in declaration.users
        if user.password is not None
        and (rotate_passwords or Node("user", user.name) in absent)
    ]

    if mode == "plan":
        # A dry run issues no writes, so it never needs a secret VALUE. Grammar was
        # already enforced at schema load; nothing is resolved here.
        return {}

    resolved: dict[str, Any] = {}
    failures: list[str] = []
    for user in needed:
        assert user.password is not None
        try:
            resolved[user.name] = secrets.resolve(user.password, identities=identities)
        except MarkLogicToolError as exc:
            failures.append(f"  user {user.name!r}: {exc.message}")

    if failures:
        joined = "\n".join(failures)
        msg = (
            f"Failed pre-flight resolving {len(failures)} secret reference(s) for "
            f"{'users being created or rotated' if rotate_passwords else 'users that do not yet exist'}:\n{joined}\n"
            f"Refusing to start an apply that would write some passwords and then stop "
            f"at the first unresolvable one. "
            f"All failing references are listed above so they can be fixed in one "
            f"pass."
        )
        raise SecretReferenceError(msg)
    return resolved


def preflight(
    raw: dict[str, Any],
    *,
    resolved_host: str,
    client: ProbeClient,
    mode: Literal["plan", "apply"] = "plan",
    identities: Mapping[str, str] | None = None,
    source: str = "declaration",
    rotate_passwords: bool = False,
) -> PreflightResult:
    """Run every honestly verifiable check, cheapest-first, issuing no writes.

    Raises on the first failure. The offline checks (1-3) complete before the client
    is touched at all, so a bad declaration never reaches the network.
    """
    # 1. Schema validity — no request issued.
    declaration = load_declaration(raw, source=source)

    # 2. Host allowlist — no request issued.
    _check_host_allowlist(declaration, resolved_host)

    # 3. Graph closure and acyclicity — no request issued. Also gives the order in
    #    which everything below is probed.
    order = plan_order(declaration)

    # 4. Reachability and 5. security-surface read, in one call: the roles endpoint
    #    is unreachable-or-forbidden before it is anything else.
    _check_security_surface(client)

    # 6. Probe every declared object.
    observed, absent = _probe_everything(declaration, order, client)

    # 7. Port conflicts, from what was just observed.
    _check_port_conflicts(declaration, observed)

    # 8. Secrets, narrowed to users a create would actually need.
    resolved = _resolve_secrets(declaration, absent, mode, identities, rotate_passwords)

    return PreflightResult(
        declaration=declaration,
        order=order,
        observed=observed,
        absent=absent,
        resolved_secrets=resolved,
    )
