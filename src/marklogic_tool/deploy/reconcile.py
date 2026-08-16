"""Reconcile a declaration against a server.

One code path serves preview and apply. Only one branch tests `apply`, at the write site.

Reconcile fails fast and does not roll back. Run it again: apply is idempotent.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from marklogic_tool.core.http import Absent, Present
from marklogic_tool.deploy.diff import (
    apply_suppressions,
    classify,
    property_matches,
)
from marklogic_tool.deploy.errors import SecretReferenceError
from marklogic_tool.deploy.mapping import (
    CREATE_ACCEPTED_STATUSES,
    SET_PROPERTIES_ACCEPTED_STATUSES,
    build_create_body,
    mapping_for,
    to_manage,
)
from marklogic_tool.deploy.order import Node
from marklogic_tool.deploy.plan import DeployPlan, ObjectPlan, PlanStatus
from marklogic_tool.deploy.preflight import PreflightResult
from marklogic_tool.deploy.schema import Declaration

GROUP_PARAMS = {"group-id": "Default"}


def _declared_objects(declaration: Declaration) -> dict[Node, Any]:
    """Index the declaration by node so reconcile can walk it in dependency order."""
    indexed: dict[Node, Any] = {}
    for api in declaration.rest_apis:
        indexed[Node("rest_api", api.name)] = api
    for database in declaration.databases:
        indexed[Node("database", database.name)] = database
    for role in declaration.roles:
        indexed[Node("role", role.name)] = role
    for user in declaration.users:
        indexed[Node("user", user.name)] = user
    for server in declaration.app_servers:
        indexed[Node("app_server", server.name)] = server
    return indexed


def _declared_properties(spec: Any) -> tuple[dict[str, Any], frozenset[str]]:
    """Split a spec into the declared properties and the fields the operator wrote.

    Include only the fields the operator wrote. A full dump sends `permission: []`, which
    tells Manage to remove every default permission.
    """
    explicit = frozenset(spec.model_fields_set)
    dumped = spec.model_dump(exclude_none=True)
    data = {key: value for key, value in dumped.items() if key in explicit}
    data.pop("name", None)
    data.pop("ignore_properties", None)
    data.pop("extra_properties", None)
    return data, explicit


def _ignore_globs(spec: Any) -> list[str]:
    globs = getattr(spec, "ignore_properties", None)
    return list(globs) if globs else []


def _rest_api_created_databases(declaration: Declaration) -> set[str]:
    """Databases a declared rest_api creates as a side-effect."""
    created: set[str] = set()
    for api in declaration.rest_apis:
        for referenced in (api.database, api.modules_database):
            if referenced is not None:
                created.add(referenced)
    return created


def reconcile(
    preflight_result: PreflightResult,
    plan: DeployPlan,
    client: Any,
    *,
    apply: bool = False,
    force: bool = False,
    rotate_passwords: bool = False,
) -> DeployPlan:
    """Probe, classify and (optionally) apply, in dependency order.

    The caller constructs the plan, and this function mutates it in place. A
    caller's `finally` can therefore emit it however this returns, including by
    raising.
    """
    declaration = preflight_result.declaration
    declared = _declared_objects(declaration)
    side_effect_databases = _rest_api_created_databases(declaration)
    pending: set[str] = set()

    for node in preflight_result.order:
        spec = declared.get(node)
        if spec is None:
            continue

        properties, explicit = _declared_properties(spec)
        # passwords are create-only. On an object that already exists the
        # declared password is dropped entirely — it is unobservable, so keeping it
        # would classify as drift on every single run. `--rotate-passwords` is the
        # only way it reaches an existing object.
        if node not in preflight_result.absent and not rotate_passwords:
            properties.pop("password", None)
        observed_payload = preflight_result.observed.get(node)
        observed = _observed_properties(node, observed_payload)

        obj = classify(
            node.kind,
            node.name,
            properties,
            observed,
            explicit_fields=explicit,
        )

        # A kind with no properties endpoint cannot be updated in place: its declared
        # fields are CREATE-TIME parameters. A rest_api's port and databases are what
        # the POST consumed; there is no /properties to PUT them to afterwards. So an
        # existing one is unchanged, and apparent drift is create-time parameters the
        # GET does not echo back — not something to write.
        if (
            obj.status is PlanStatus.UPDATE
            and not mapping_for(node.kind).supports_properties
        ):
            obj.status = PlanStatus.UNCHANGED
            obj.changes = []
            obj.notes.append(
                "exists; its declared fields are create-time parameters with no "
                "properties endpoint, so nothing is written"
            )

        # a rest_api creates its content and modules databases, so an object
        # naming one of them is waiting on that create rather than on a database this
        # run will make itself. Recorded in BOTH modes.
        awaiting = sorted(
            name
            for name in _referenced_names(spec)
            if name in pending or name in side_effect_databases
        )

        # An absent object of a kind with no create endpoint is not ours to create:
        # the rest_api POST makes it, along with its databases and the rewriter.
        # It waits on that create rather than being hand-built.
        created_by_rest_api = (
            obj.status is PlanStatus.CREATE
            and not mapping_for(node.kind).supports_create
        )
        if created_by_rest_api:
            awaiting = sorted({*awaiting, *(api.name for api in declaration.rest_apis)})
            obj.notes.append(
                "created by the rest_api POST, not directly; no write is issued for it"
            )

        if awaiting:
            obj.depends_on_pending = awaiting

        warnings = apply_suppressions(obj, _ignore_globs(spec))
        for warning in warnings:
            plan.warn(warning)

        plan.add_object(obj)

        if obj.status is PlanStatus.CREATE:
            pending.add(node.name)

        if obj.status is PlanStatus.BLOCKED:
            if not (force and obj.force_required):
                continue
            # Falling through was not enough: the write gate below admits only
            # CREATE and UPDATE, and nothing anywhere flipped BLOCKED — so `--force`
            # could never write anything at all, while the tool went on telling the
            # operator to use it. The status is promoted here so the gate can be
            # reached.
            #
            # ONLY the force_required class is promoted, and that is the safety
            # property: a hard refusal never becomes an ObjectPlan (classify raises
            # DataAffectingRefusal before returning), a kind collision carries
            # force_required=False, and an unmappable property clears it too. So no
            # amount of --force reaches a data-affecting change. That distinction is
            # encoded here rather than assumed, because the reason it cannot be
            # exercised today is a fact about the schema, not a promise about tomorrow.
            #
            # `forced` and `blocked_reason` both survive into the artifact: the plan
            # must still say this applied under duress, and what was overridden.
            obj.status = PlanStatus.UPDATE
            obj.forced = True
            obj.notes.append(f"applied under --force, overriding: {obj.blocked_reason}")

        # THE single branch on `apply` in the package. Everything above ran identically
        # in both modes; only the write below is short-circuited.
        if (
            apply
            and obj.status in (PlanStatus.CREATE, PlanStatus.UPDATE)
            and not created_by_rest_api
        ):
            _write(
                client,
                node,
                obj,
                properties,
                plan,
                preflight_result.resolved_secrets,
            )
            obj.applied = True
            _confirm_after_write(client, node, obj, properties, plan)
            pending.discard(node.name)

    plan.refresh_summary()
    return plan


def _referenced_names(spec: Any) -> list[str]:
    names: list[str] = []
    for attribute in ("database", "modules_database", "schema_database"):
        value = getattr(spec, attribute, None)
        if isinstance(value, str):
            names.append(value)
    for attribute in ("inherits", "roles"):
        value = getattr(spec, attribute, None)
        if isinstance(value, Sequence) and not isinstance(value, str | bytes):
            names.extend(str(item) for item in value)
    return names


def _observed_properties(node: Node, payload: Any) -> dict[str, Any] | None:
    """Translate an observed Manage payload into user terms, or None if absent."""
    if payload is None:
        return None
    if isinstance(payload, Absent):
        return None
    if isinstance(payload, Present):
        payload = payload.payload
    if not isinstance(payload, Mapping):
        return None
    from marklogic_tool.deploy.mapping import REVERSE_PROPERTIES, to_user_value

    known = REVERSE_PROPERTIES.get(node.kind, {})
    # Names AND values. Translating only the names leaves the server's `digestbasic`
    # sitting against the declaration's `digest-basic`, so the app server drifts on
    # every single run — the same all-unchanged-defeating shape as the port strings,
    # arriving through a different door.
    return {
        known[manage_name]: to_user_value(node.kind, known[manage_name], value)
        for manage_name, value in payload.items()
        if manage_name in known
    }


UNVERIFIABLE_PROPERTIES: frozenset[str] = frozenset({"password"})
"""Properties whose post-state cannot be read back, so `applied` cannot mean confirmed.

A password is unobservable by design. The only check available is a
PRECONDITION — that the request carried a non-empty secret — which is enforced in
`_with_resolved_secret` and fails loudly. Saying "written, not verifiable" in the
output is honest; a bare `applied: true` is not.
"""


def _confirm_after_write(
    client: Any,
    node: Node,
    obj: ObjectPlan,
    properties: Mapping[str, Any],
    plan: DeployPlan,
) -> None:
    """Read the object back after a write. Report whether the state matches.

    `applied` alone means only that the server accepted the request. A 2xx on an incomplete
    payload also reports success.
    """
    mapping = mapping_for(node.kind)
    if not mapping.supports_properties:
        return

    unverifiable = sorted(set(properties) & UNVERIFIABLE_PROPERTIES)
    for prop in unverifiable:
        obj.notes.append(
            f"{prop}: written, NOT verifiable — it cannot be read back, so this run "
            f"confirms only that a non-empty value was sent"
        )

    verifiable = {
        k: v for k, v in properties.items() if k not in UNVERIFIABLE_PROPERTIES
    }
    if not verifiable:
        return

    params = dict(GROUP_PARAMS) if mapping.group_scoped else None
    result = client.probe(mapping.properties_path(node.name), params)
    if isinstance(result, Absent):
        obj.notes.append("post-state could not be read back; `applied` is unconfirmed")
        plan.warn(
            f"{node.kind} {node.name!r}: written but the post-state could not be read "
            f"back, so it is unconfirmed"
        )
        return

    after = _observed_properties(node, result)
    mismatched = sorted(
        prop
        for prop, value in verifiable.items()
        if not property_matches(prop, value, after or {})
    )
    if mismatched:
        named = ", ".join(mismatched)
        obj.notes.append(f"post-state does NOT match for: {named}")
        plan.warn(
            f"{node.kind} {node.name!r}: the write was accepted but {named} did not "
            f"take effect — reporting it rather than claiming a clean apply"
        )


def _with_resolved_secret(
    node: Node, properties: Mapping[str, Any], resolved: Mapping[str, Any]
) -> dict[str, Any]:
    """Put the resolved secret in the request body, in place of the reference.

    The reference is not a password. If the tool sends it as one, the account password
    becomes guessable. The tool refuses when the secret is missing.
    """
    body = dict(properties)
    reference = body.get("password")
    if reference is None:
        return body

    secret = resolved.get(node.name)
    if secret is None:
        msg = (
            f"Refusing to write {node.kind} {node.name!r}: its password reference was "
            f"never resolved, so the request would carry the reference itself instead "
            f"of a secret. "
            f"Refusing rather than writing it, because the account would be created "
            f"with a guessable password and would not authenticate with the real one — "
            f"and the run would report success. "
            f"Re-run so pre-flight resolves it, or use --rotate-passwords if the user "
            f"already exists."
        )
        raise SecretReferenceError(msg)

    body["password"] = (
        secret.get_secret_value() if hasattr(secret, "get_secret_value") else secret
    )
    return body


def _write(
    client: Any,
    node: Node,
    obj: ObjectPlan,
    properties: Mapping[str, Any],
    plan: DeployPlan,
    resolved_secrets: Mapping[str, Any] | None = None,
) -> None:
    """Issue the one write this object needs. Failures propagate — no rollback."""
    mapping = mapping_for(node.kind)
    properties = _with_resolved_secret(node, properties, resolved_secrets or {})
    params = dict(GROUP_PARAMS) if mapping.group_scoped else None

    if obj.status is PlanStatus.CREATE:
        body = build_create_body(node.kind, {"name": node.name, **properties})
        response = client.post(
            mapping.create_path(), body, params, accept=CREATE_ACCEPTED_STATUSES
        )
        if getattr(response, "status_code", None) == 409:
            # never flattened. Re-probe and reclassify rather than assuming
            # already-exists — a kind collision presents identically.
            _reclassify_after_conflict(client, node, obj, plan)
            return
        # `permission` cannot go in the create body, because the grant resolves
        # against roles that must already exist — including the object being created.
        # So it is a SECOND call, always, which is the shape the reference implementation
        # used and nobody had written down the reason for.
        _set_permissions_after_create(client, node, properties, mapping, params)
        return

    drift = {
        prop: properties[prop]
        for prop in (_user_name_of(node.kind, c.property) for c in obj.changes)
        if prop in properties
    }
    client.put(
        mapping.properties_path(node.name),
        to_manage(node.kind, drift),
        params,
        accept=SET_PROPERTIES_ACCEPTED_STATUSES,
    )


def _set_permissions_after_create(
    client: Any,
    node: Node,
    properties: Mapping[str, Any],
    mapping: Any,
    params: dict[str, str] | None,
) -> None:
    """Set `default_permissions` on a just-created object, as a separate PUT.

    Silent when nothing was declared, so an object without permissions costs no extra
    request. A kind with no properties endpoint cannot carry them at all.
    """
    declared = properties.get("default_permissions")
    if not declared or not mapping.supports_properties:
        return
    client.put(
        mapping.properties_path(node.name),
        to_manage(node.kind, {"default_permissions": declared}),
        params,
        accept=SET_PROPERTIES_ACCEPTED_STATUSES,
    )


def _user_name_of(kind: str, manage_property: str) -> str:
    from marklogic_tool.deploy.mapping import REVERSE_PROPERTIES

    return REVERSE_PROPERTIES.get(kind, {}).get(manage_property, manage_property)


def _reclassify_after_conflict(
    client: Any, node: Node, obj: ObjectPlan, plan: DeployPlan
) -> None:
    mapping = mapping_for(node.kind)
    params = dict(GROUP_PARAMS) if mapping.group_scoped else None
    result = client.probe(mapping.probe_path(node.name), params)
    if isinstance(result, Absent):
        obj.status = PlanStatus.BLOCKED
        obj.blocked_reason = (
            f"the server refused to create {node.kind} {node.name!r} with a conflict, "
            f"yet a re-probe finds nothing there; refusing to guess what happened"
        )
        plan.warn(f"{node.kind} {node.name!r}: 409 on create but absent on re-probe")
        return
    obj.status = PlanStatus.UNCHANGED
    obj.notes.append("already existed on create; re-probed and reconciled")
