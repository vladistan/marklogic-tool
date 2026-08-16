"""Manage API names: the property, endpoint and payload tables, in both directions.

This module holds the deny-list that `schema.py` imports. The tool refuses a glob that
intersects the list. The list holds both spellings.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase

from marklogic_tool.deploy.errors import UnmappedPropertyError

SECURITY_DENIED_PROPERTIES: frozenset[str] = frozenset(
    {
        "default_permissions",
        "default-permissions",
        "permissions",
        "permission",
        "capabilities",
        "capability",
        "roles",
        "role",
        "role-name",
        "privileges",
        "privilege",
        "privilege-name",
        "users",
        "user",
        "user-name",
        "password",
        "authentication",
        "security_database",
        "security-database",
        "external_security",
        "external-security",
        "internal_security",
        "internal-security",
    }
)
"""Properties whose drift is a security posture change."""

DATA_AFFECTING_DENIED_PROPERTIES: frozenset[str] = frozenset(
    {
        "forest",
        "forests",
        "forest-name",
        "data_directory",
        "data-directory",
        "database",
        "database-name",
        "content_database",
        "content-database",
        "modules_database",
        "modules-database",
        "schema_database",
        "schema-database",
        "triggers_database",
        "triggers-database",
    }
)
"""Properties whose drift can destroy or orphan data."""

DENIED_PROPERTIES: frozenset[str] = (
    SECURITY_DENIED_PROPERTIES | DATA_AFFECTING_DENIED_PROPERTIES
)
"""The deny-list the schema enforces on both escape hatches.

Membership is derived rather than quoted from a settled list: security-affecting from
"subtractive security drift is blocked unless --force", data-affecting from "forests, db
delete-recreate, rest-api delete with content".

Deliberately NOT denied, so drift classification keeps owning them: `port`, because a
disruptive port change is blocked-unless-force by the diff, which is a classification
rather than an escape-hatch concern; and the index properties, because reindexing is
disruptive but is not data-affecting, and widening this list unilaterally would silently
change what `deploy` refuses.
"""


def denied_properties_matching(pattern: str) -> tuple[str, ...]:
    """Return the denied properties a glob intersects. Sort them for stable messages.

    Test for intersection, not equality. `"*"` reaches a denied property without equalling
    one. An exact name is also a glob.
    """
    return tuple(sorted(p for p in DENIED_PROPERTIES if fnmatchcase(p, pattern)))


# --------------------------------------------------------------------------------
# Endpoints, property names and payload shapes.
#
# Every name below is verified against a live MarkLogic 12.0.3 server, never taken from
# documentation or from the shape of a
# sibling endpoint.
# --------------------------------------------------------------------------------

MANAGE_ROOT = "/manage/v2"
"""Manage API root. Served on the manage port (8002)."""

REST_APIS_ROOT = "/v1/rest-apis"
"""REST-API provisioning root.

⚠ Served on the MANAGE port (8002), NOT the REST instance port. The path looks like an
instance path and is not one; sending it to `rest_port` fails in a way that reads like a
missing feature.
"""

CREATE_ACCEPTED_STATUSES: frozenset[int] = frozenset({200, 201, 202, 204})
"""Statuses the server returns for a successful create (POST)."""

SET_PROPERTIES_ACCEPTED_STATUSES: frozenset[int] = frozenset({200, 202, 204})
"""Statuses the server returns for a successful properties write (PUT)."""

UNMAPPED_PROPERTIES: frozenset[str] = frozenset(
    {
        "forests",
    }
)
"""Properties this tool will not map to a Manage name.

`forests` is deliberately outside the recreatable set and permanently in the
hard-refusal set. It is refused by `to_manage_property` rather than guessed — a wrong
spelling here would silently drop a security instruction.

`default_permissions` was here until it was measured directly against a live server. It
is now verified and mapped below. Nobody guessed its shape correctly, which is the
argument for having refused to.
"""

DEFAULT_PERMISSIONS_MANAGE_PROPERTY = "permission"
"""The Manage spelling — SINGULAR, and not `default-permissions`.

Its value is a list of `{"role-name": ..., "capability": ...}` entries where
`capability` is a SINGULAR STRING and there is one entry PER capability. So the user
vocabulary's `{role: writer, capabilities: [read, update]}` expands to TWO Manage
entries. This is the one property whose mapping is not one-to-one, which is why
`expand_default_permissions` / `collapse_default_permissions` exist and why the
round-trip test has to cover expand-then-collapse rather than plain equality.

⚠ ABSENT, not empty, when a role has none: a writer role on a live server
carries only `role-name`, `description`, `role` and `privilege` — no `permission`
key at all. That absence IS the defect at source, and it
is exactly why the absent-vs-explicit-empty distinction is load-bearing rather than
pedantic.

📌 Not to be confused with `commands/doc.py`'s `{"role-name", "capabilities": [...]}`:
that is the `/v1/documents` DOCUMENT permission shape, with capabilities PLURAL and an
ARRAY. Adjacent surface, different shape.
"""


@dataclass(frozen=True)
class KindMapping:
    """Everything the tool knows about one declared kind's Manage surface."""

    kind: str
    root: str
    create_body_key: str | None
    create_wrapper: str | None
    properties: dict[str, str]
    supports_create: bool = True
    supports_properties: bool = True
    group_scoped: bool = False

    def probe_path(self, name: str) -> str:
        return f"{self.root}/{name}"

    def create_path(self) -> str:
        return self.root

    def properties_path(self, name: str) -> str:
        return f"{self.root}/{name}/properties"


KIND_MAPPINGS: dict[str, KindMapping] = {
    # A rest_api POST creates THREE objects: the app server plus its content and
    # modules databases, with the rewriter and error-handler that make /v1/... work.
    # That side-effect is why a database it names counts as satisfiable by creation.
    "rest_api": KindMapping(
        kind="rest_api",
        root=REST_APIS_ROOT,
        create_body_key=None,
        create_wrapper="rest-api",
        properties={
            "name": "name",
            "port": "port",
            "database": "database",
            "modules_database": "modules-database",
        },
        supports_properties=False,
    ),
    "database": KindMapping(
        kind="database",
        root=f"{MANAGE_ROOT}/databases",
        create_body_key="database-name",
        create_wrapper=None,
        properties={
            "schema_database": "schema-database",
            "triple_index": "triple-index",
            "collection_lexicon": "collection-lexicon",
            # The property the pre-check reads: the exhaustive unpermissioned
            # scan depends on it.
            "uri_lexicon": "uri-lexicon",
            "directory_creation": "directory-creation",
        },
    ),
    # App servers are not created directly — the rest_api POST creates them. Only the
    # read and properties-update surfaces are supported.
    "app_server": KindMapping(
        kind="app_server",
        root=f"{MANAGE_ROOT}/servers",
        create_body_key=None,
        create_wrapper=None,
        properties={
            "port": "port",
            "database": "content-database",
            "modules_database": "modules-database",
            "authentication": "authentication",
        },
        supports_create=False,
        group_scoped=True,
    ),
    "role": KindMapping(
        kind="role",
        root=f"{MANAGE_ROOT}/roles",
        create_body_key="role-name",
        create_wrapper=None,
        properties={
            "name": "role-name",
            "description": "description",
            "inherits": "role",
            "default_permissions": DEFAULT_PERMISSIONS_MANAGE_PROPERTY,
        },
    ),
    "user": KindMapping(
        kind="user",
        root=f"{MANAGE_ROOT}/users",
        create_body_key="user-name",
        create_wrapper=None,
        properties={
            "name": "user-name",
            "roles": "role",
            # Supplied only at create and resolved at call time.
            # Both sides are PROPERTY NAMES, not a value: the declaration's
            # `password` key maps to Manage's `password` property.
            "password": "password",  # pragma: allowlist secret
            "default_permissions": DEFAULT_PERMISSIONS_MANAGE_PROPERTY,
        },
    ),
}

VALUE_MAPPINGS: dict[tuple[str, str], dict[str, str]] = {
    # Measured directly: the property is `digestbasic`, and the
    # 401 challenge advertises Digest only even so.
    ("app_server", "authentication"): {"digest-basic": "digestbasic"},
}
"""User-vocabulary value -> Manage value, keyed by (kind, user property)."""


def _invert(forward: dict[str, str], label: str) -> dict[str, str]:
    """Invert a name map. Refuse a collision.

    Two user properties that share one Manage name make the reverse direction lossy. The
    check runs at import, not at the first round trip.
    """
    reverse: dict[str, str] = {}
    for user_name, manage_name in forward.items():
        if manage_name in reverse:
            msg = (
                f"{label}: Manage property {manage_name!r} is claimed by both "
                f"{reverse[manage_name]!r} and {user_name!r}; the mapping would not "
                f"be reversible"
            )
            raise ValueError(msg)
        reverse[manage_name] = user_name
    return reverse


REVERSE_PROPERTIES: dict[str, dict[str, str]] = {
    kind: _invert(mapping.properties, kind) for kind, mapping in KIND_MAPPINGS.items()
}

REVERSE_VALUE_MAPPINGS: dict[tuple[str, str], dict[str, str]] = {
    key: _invert(values, f"{key[0]}.{key[1]}") for key, values in VALUE_MAPPINGS.items()
}


def expand_default_permissions(declared: object) -> list[dict[str, str]]:
    """Convert user `[{role, capabilities: [...]}]` to Manage `[{role-name, capability}]`.

    Emit one Manage entry per capability. Sort the output so a plan is reproducible.
    """
    entries: list[dict[str, str]] = []
    if not isinstance(declared, Sequence) or isinstance(declared, str | bytes):
        return entries
    for item in declared:
        if isinstance(item, Mapping):
            role = item.get("role")
            capabilities = item.get("capabilities") or []
        else:
            role = getattr(item, "role", None)
            capabilities = getattr(item, "capabilities", None) or []
        if not isinstance(role, str):
            continue
        for capability in capabilities:
            entries.append({"role-name": role, "capability": str(capability)})
    return sorted(entries, key=lambda e: (e["role-name"], e["capability"]))


def collapse_default_permissions(observed: object) -> list[dict[str, object]]:
    """Convert Manage `[{role-name, capability}]` to user `[{role, capabilities: [...]}]`.

    This inverts the expansion. The round trip is lossless, but the entry count changes.
    """
    grouped: dict[str, list[str]] = {}
    if not isinstance(observed, Sequence) or isinstance(observed, str | bytes):
        return []
    for entry in observed:
        if not isinstance(entry, Mapping):
            continue
        role = entry.get("role-name")
        capability = entry.get("capability")
        if isinstance(role, str) and isinstance(capability, str):
            grouped.setdefault(role, [])
            if capability not in grouped[role]:
                grouped[role].append(capability)
    return [
        {"role": role, "capabilities": sorted(grouped[role])}
        for role in sorted(grouped)
    ]


def mapping_for(kind: str) -> KindMapping:
    """Return the mapping for a declared kind, or refuse by name."""
    try:
        return KIND_MAPPINGS[kind]
    except KeyError:
        known = ", ".join(sorted(KIND_MAPPINGS))
        msg = (
            f"Failed to map kind {kind!r}: this tool has no Manage surface for it. "
            f"Refusing rather than assuming an endpoint shape. "
            f"Kinds this tool can map are: {known}."
        )
        raise UnmappedPropertyError(msg) from None


def to_manage_property(kind: str, user_property: str) -> str:
    """Translate one user-vocabulary property name to its Manage spelling."""
    if user_property in UNMAPPED_PROPERTIES:
        msg = (
            f"Failed to map {kind}.{user_property}: this tool has no verified Manage "
            f"property name for it. "
            f"Refusing to guess, because a wrong spelling is accepted by the server as a "
            f"no-op and the declaration would silently stop applying. "
            f"Remove {user_property!r} from the declaration, or set the Manage-native name "
            f"yourself through the extra_properties escape hatch."
        )
        raise UnmappedPropertyError(msg)
    mapping = mapping_for(kind)
    try:
        return mapping.properties[user_property]
    except KeyError:
        known = ", ".join(sorted(mapping.properties))
        msg = (
            f"Failed to map {kind}.{user_property}: no Manage property in this tool "
            f"corresponds to it. "
            f"Refusing to pass the name through unchanged, which would send an "
            f"unrecognised property to the server. "
            f"Properties this tool can map for {kind} are: {known}."
        )
        raise UnmappedPropertyError(msg) from None


def to_user_property(kind: str, manage_property: str) -> str:
    """Translate one Manage property name back to the user vocabulary."""
    mapping_for(kind)
    try:
        return REVERSE_PROPERTIES[kind][manage_property]
    except KeyError:
        known = ", ".join(sorted(REVERSE_PROPERTIES[kind]))
        msg = (
            f"Failed to map the observed Manage property {manage_property!r} on {kind} "
            f"back to the user vocabulary. "
            f"Refusing to invent a user-facing name for it. "
            f"Manage properties this tool knows for {kind} are: {known}."
        )
        raise UnmappedPropertyError(msg) from None


def to_manage_value(kind: str, user_property: str, value: object) -> object:
    """Translate a user value to its Manage value.

    This is where `default_permissions` expands. Every write goes through `to_manage`, so
    both the create body and the properties write get the Manage shape.
    """
    if user_property == "default_permissions":
        return expand_default_permissions(value)
    table = VALUE_MAPPINGS.get((kind, user_property))
    if table is None or not isinstance(value, str):
        return value
    return table.get(value, value)


def to_user_value(kind: str, user_property: str, value: object) -> object:
    """Translate a Manage value back to the user vocabulary."""
    if user_property == "default_permissions":
        return collapse_default_permissions(value)
    table = REVERSE_VALUE_MAPPINGS.get((kind, user_property))
    if table is None or not isinstance(value, str):
        return value
    return table.get(value, value)


def to_manage(kind: str, declared: dict[str, object]) -> dict[str, object]:
    """Translate a declared object's properties into Manage terms."""
    return {
        to_manage_property(kind, name): to_manage_value(kind, name, value)
        for name, value in declared.items()
    }


def to_user(kind: str, observed: dict[str, object]) -> dict[str, object]:
    """Translate an observed Manage payload back into user-vocabulary terms.

    Declared-subset semantics live in the caller, not here: this translates whatever it
    is given and refuses anything it cannot name.
    """
    result: dict[str, object] = {}
    for manage_name, value in observed.items():
        user_name = to_user_property(kind, manage_name)
        result[user_name] = to_user_value(kind, user_name, value)
    return result


def build_create_body(kind: str, declared: dict[str, object]) -> dict[str, object]:
    """Build the POST body for creating one object."""
    mapping = mapping_for(kind)
    if not mapping.supports_create:
        msg = (
            f"Failed to build a create body for {kind}: this tool has no create "
            f"endpoint. "
            f"Refusing to hand-build it, because the rest_api POST creates the app "
            f"server together with its content and modules databases and the rewriter "
            f"that makes /v1/ work; building it directly omits them. "
            f"Declare it under `rest_apis:` instead."
        )
        raise UnmappedPropertyError(msg)
    payload = dict(declared)

    # `permission` NEVER goes in a create body:
    #   POST {"role-name": "X", "permission": [{"role-name": "X", ...}]} -> 404 SEC-ROLEDNE
    #   POST {"role-name": "X"} -> 201, then PUT .../properties -> 204
    # The grant is resolved against roles that must already EXIST, and an object cannot
    # reference itself — or a peer created later in the same run — at create time.
    # Excluding it unconditionally is uniform: no self-reference special case, and it
    # cannot regress when someone adds a role that grants to a peer.
    #
    # This is why the reference implementation calls ensure() and set_properties()
    # separately. Its two-step shape was correct for a reason nobody had written down;
    # do not "simplify" it back into a single POST.
    payload.pop("default_permissions", None)

    name = payload.pop("name", None) if "name" not in mapping.properties else None

    body = to_manage(kind, payload)

    # Kinds whose name is not a mapped property carry it under `create_body_key`
    # instead — a database create takes {"database-name": "..."}, and the
    # name never appears in its properties table.
    if name is not None and mapping.create_body_key is not None:
        body[mapping.create_body_key] = name

    if mapping.create_wrapper is not None:
        return {mapping.create_wrapper: body}
    return body
