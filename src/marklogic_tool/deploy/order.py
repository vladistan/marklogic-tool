# cspell:ignore unreviewable
"""Dependency ordering. It returns a deterministic creation order, or a refusal.

Teardown is the exact reverse of creation. `--dry-run` and apply share one code path, so the
tool orders independent objects by kind and name.
"""

import heapq
from dataclasses import dataclass

from marklogic_tool.deploy.errors import DanglingReferenceError, DependencyCycleError
from marklogic_tool.deploy.schema import Declaration

BUILTIN_ROLES: frozenset[str] = frozenset(
    {
        "rest-reader",
        "rest-writer",
        "rest-admin",
        "manage-admin",
    }
)
"""MarkLogic roles that ship with the server and therefore cannot be declared.

Inheriting from these is how the reference implementation builds its role chain, and
that script is proven idempotent against live ML 12.0.3 — so their existence is
demonstrated by it running at all.

They are satisfiable-without-declaration but never plan NODES: the tool must not
create, modify or remove a built-in role, so no edge is drawn to them either. The
membership is deliberately the exact four the reference uses. A role outside this set
and outside the declaration is still a dangling reference, because granting against a
role the declaration does not control is how privilege drifts in unnoticed.
"""

KIND_RANK: dict[str, int] = {
    "rest_api": 0,
    "database": 1,
    "role": 2,
    "user": 3,
    "app_server": 4,
}
"""Tiebreak rank for objects with no edge between them."""


@dataclass(frozen=True, order=True)
class Node:
    """One declared object, identified by kind and name."""

    kind: str
    name: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.name}"


def _sort_key(node: Node) -> tuple[int, str]:
    return (KIND_RANK[node.kind], node.name)


def _satisfiable_database_names(declaration: Declaration) -> set[str]:
    """Database names that exist: declared, or created by a declared rest_api.

    A rest-api POST creates its content and modules databases. So a reference to one is not
    a dangling reference, and the tool draws no edge to it.
    """
    names = {db.name for db in declaration.databases}
    for api in declaration.rest_apis:
        for referenced in (api.database, api.modules_database):
            if referenced is not None:
                names.add(referenced)
    return names


def build_dependency_graph(declaration: Declaration) -> dict[Node, set[Node]]:
    """Map each declared object to the objects that must exist before it.

    Raises `DanglingReferenceError` naming the reference that cannot be satisfied.
    """
    nodes: set[Node] = set()
    for api in declaration.rest_apis:
        nodes.add(Node("rest_api", api.name))
    for db in declaration.databases:
        nodes.add(Node("database", db.name))
    for role in declaration.roles:
        nodes.add(Node("role", role.name))
    for user in declaration.users:
        nodes.add(Node("user", user.name))
    for server in declaration.app_servers:
        nodes.add(Node("app_server", server.name))

    role_names = {role.name for role in declaration.roles}
    database_names = _satisfiable_database_names(declaration)
    declared_databases = {db.name for db in declaration.databases}

    dependencies: dict[Node, set[Node]] = {node: set() for node in nodes}

    def require_role(owner: Node, referenced: str, field: str) -> None:
        # A built-in role ships with the server and cannot be declared, so inheriting
        # from one is not a dangling reference. No edge either: it is not a plan node
        # and the tool never touches it.
        if referenced in BUILTIN_ROLES:
            return
        if referenced not in role_names:
            builtins = ", ".join(sorted(BUILTIN_ROLES))
            raise DanglingReferenceError(
                f"Failed to order the declaration: {owner} references the role "
                f"{referenced!r} via `{field}`, which is not declared. "
                f"Refusing rather than assuming the role already exists on the "
                f"server, because granting against a role the declaration does not "
                f"control is exactly how privilege drifts in unnoticed. "
                f"Declare {referenced!r} under `roles:`, or remove the reference. "
                f"The built-in roles that need no declaration are: {builtins}."
            )
        dependencies[owner].add(Node("role", referenced))

    def require_database(owner: Node, referenced: str, field: str) -> None:
        if referenced not in database_names:
            raise DanglingReferenceError(
                f"Failed to order the declaration: {owner} references the database "
                f"{referenced!r} via `{field}`, which is not declared and is not "
                f"created by any declared rest_api. "
                f"Refusing rather than planning against a database that may not "
                f"exist. "
                f"Declare {referenced!r} under `databases:` or correct the name."
            )
        # No edge to a database the rest_api creates: that one is not a plan node.
        if referenced in declared_databases:
            dependencies[owner].add(Node("database", referenced))

    for role in declaration.roles:
        owner = Node("role", role.name)
        for inherited in role.inherits:
            require_role(owner, inherited, "inherits")
        for permission in role.default_permissions:
            # A role granting default permissions to ITSELF is the normal case — it
            # means "documents I write are readable and updatable by me". It is not a
            # creation-order dependency, because the role plainly exists by the time
            # its own properties are set. Treating it as one reads as a self-cycle and
            # refuses a declaration that is not only valid but required.
            if permission.role == role.name:
                continue
            require_role(owner, permission.role, "default_permissions")

    for user in declaration.users:
        owner = Node("user", user.name)
        for granted in user.roles:
            require_role(owner, granted, "roles")
        for permission in user.default_permissions:
            require_role(owner, permission.role, "default_permissions")

    for db in declaration.databases:
        owner = Node("database", db.name)
        if db.schema_database is not None:
            require_database(owner, db.schema_database, "schema_database")

    for server in declaration.app_servers:
        owner = Node("app_server", server.name)
        if server.database is not None:
            require_database(owner, server.database, "database")
        if server.modules_database is not None:
            require_database(owner, server.modules_database, "modules_database")

    return dependencies


def _find_cycle(
    dependencies: dict[Node, set[Node]], remaining: set[Node]
) -> list[Node]:
    """Recover one concrete cycle from the nodes Kahn's algorithm could not place."""
    path: list[Node] = []
    on_path: set[Node] = set()
    visited: set[Node] = set()

    def walk(node: Node) -> list[Node]:
        if node in on_path:
            return path[path.index(node) :] + [node]
        if node in visited:
            return []
        visited.add(node)
        on_path.add(node)
        path.append(node)
        for dependency in sorted(dependencies[node] & remaining, key=_sort_key):
            found = walk(dependency)
            if found:
                return found
        path.pop()
        on_path.discard(node)
        return []

    for start in sorted(remaining, key=_sort_key):
        cycle = walk(start)
        if cycle:
            return cycle
    return sorted(remaining, key=_sort_key)


def plan_order(declaration: Declaration) -> list[Node]:
    """Return the deterministic creation order, or refuse.

    Raises `DanglingReferenceError` or `DependencyCycleError`; both are pre-flight
    failures that leave the server untouched.
    """
    dependencies = build_dependency_graph(declaration)

    outstanding = {node: set(deps) for node, deps in dependencies.items()}
    dependents: dict[Node, set[Node]] = {node: set() for node in dependencies}
    for node, deps in dependencies.items():
        for dependency in deps:
            dependents[dependency].add(node)

    ready = [(_sort_key(n), n) for n, deps in outstanding.items() if not deps]
    heapq.heapify(ready)

    ordered: list[Node] = []
    while ready:
        _, node = heapq.heappop(ready)
        ordered.append(node)
        for dependent in sorted(dependents[node], key=_sort_key):
            outstanding[dependent].discard(node)
            if not outstanding[dependent]:
                heapq.heappush(ready, (_sort_key(dependent), dependent))

    if len(ordered) != len(dependencies):
        remaining = set(dependencies) - set(ordered)
        cycle = _find_cycle(dependencies, remaining)
        members = " -> ".join(str(node) for node in cycle)
        raise DependencyCycleError(
            f"Failed to order the declaration: the declared objects form a "
            f"dependency cycle: {members}. "
            f"Refusing rather than picking an arbitrary starting point, because no "
            f"creation order can satisfy a cycle and a partial apply would leave "
            f"mixed state. "
            f"Break the cycle by removing one of the references named above."
        )

    return ordered


def teardown_order(declaration: Declaration) -> list[Node]:
    """Return the exact reverse of the creation order."""
    return list(reversed(plan_order(declaration)))
