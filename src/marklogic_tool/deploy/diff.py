"""Drift classification. This module is pure: no I/O, no client, no clock.

The tool reads the properties, computes the drift against the declaration, then writes only
that subset. It never sends a property the declaration does not name.
"""

# cspell:ignore unforceable unmappable unkeepable

from collections.abc import Mapping, Sequence
from fnmatch import fnmatchcase
from typing import Any

from marklogic_tool.deploy.errors import DataAffectingRefusal, UnmappedPropertyError
from marklogic_tool.deploy.mapping import (
    SECURITY_DENIED_PROPERTIES,
    to_manage_property,
)
from marklogic_tool.deploy.plan import ObjectPlan, PlanStatus, PropertyChange

HARD_REFUSAL_PROPERTIES: frozenset[str] = frozenset({"forests", "data_directory"})
"""Properties whose drift is refused unconditionally.

⚠ Deliberately NOT `mapping.DATA_AFFECTING_DENIED_PROPERTIES`. Those two sets answer
different questions and conflating them is a real bug — it was caught here by the
truth-table tests.

- The mapping deny-list guards the **escape hatches**: it is broad on purpose, because
  `ignore_properties` must not be able to hide anything security- or data-adjacent.
  `schema-database` belongs there: suppressing a change to it would be dangerous.
- This set drives **hard refusal of declared drift**, named precisely:
  forests, database delete-and-recreate, rest-api delete with content. Changing
  `schema_database` is an ordinary, appliable configuration change — a
  legitimate part of the user vocabulary — so refusing it would make the tool unable
  to do the job it was declared for.

⚠ **NEITHER MEMBER OF THIS SET IS REACHABLE FROM A DECLARATION.** Not `forests`, not
`data_directory`. Established by ENUMERATION rather than by reading the specs: every
`BaseModel` in `deploy.schema` was walked, its `model_fields` collected, and the union
intersected with this set — the result is empty. Every spec also sets `extra="forbid"`, so
an unknown key cannot smuggle one in either.

So this whole set is **defence in depth**, and before collapsing it into the mapping
deny-list — someone will try, because the overlap looks like duplication — know which one
actually fires:

- A declaration **cannot** produce `forests` or `data_directory` drift for this refusal to
  catch. It is unreachable from any YAML a user can write.
- The refusal that fires in practice is the schema-load deny-list, on
  `extra_properties` / `ignore_properties`. Verified live 2026-08-15 (`0d59e1f`) against a
  real, empty, unattached forest: four attempts through both hatches, plain and `--force`,
  all refused at schema load, forest still present afterwards.

Both are tested, and the tests pass for different reasons. **Deleting either member keeps
the suite green while removing a guard**, which is exactly why this note exists instead of
a test: no test can fail for a path no declaration can reach.

What would make them reachable: adding `forests` or `data_directory` (or any alias
`mapping` translates to them) as a field on any spec in `deploy.schema`. On the day someone
does, this is the layer that already refuses it — unconditionally, and `--force` does not
reach it. `reconcile` promotes only `force_required` objects, and a hard refusal never
becomes an `ObjectPlan` at all because `classify` raises first.
"""

DISRUPTIVE_PROPERTIES: frozenset[str] = frozenset({"port"})
"""Changes that interrupt service on an existing object: blocked unless forced."""

REDACTED_PROPERTIES: frozenset[str] = frozenset({"password"})
"""Properties whose observed value must never appear in a plan."""


def _audit_name(kind: str, user_property: str) -> str:
    """Return the Manage name for the audit record. Fall back to the user name.

    `changes[].property` is the one place a Manage name appears. An unmapped property keeps
    its user name, because the tool must not invent a Manage name.
    """
    try:
        return to_manage_property(kind, user_property)
    except UnmappedPropertyError:
        return user_property


def _as_capability_map(value: Any) -> dict[str, set[str]]:
    """Read `[{role, capabilities}]` into {role: {capability}}."""
    result: dict[str, set[str]] = {}
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return result
    for entry in value:
        if isinstance(entry, Mapping):
            role = entry.get("role")
            caps = entry.get("capabilities") or []
        else:
            role = getattr(entry, "role", None)
            caps = getattr(entry, "capabilities", None) or []
        if isinstance(role, str):
            result[role] = set(caps)
    return result


def _permission_pairs(value: Any) -> set[tuple[str, str]]:
    """Flatten to the `(role-name, capability)` pair set the Manage property really is."""
    return {
        (role, capability)
        for role, capabilities in _as_capability_map(value).items()
        for capability in capabilities
    }


def classify_default_permissions(declared: Any, observed: Any) -> str:
    """Compare default permissions. Return `subtractive`, `additive` or `unchanged`.

    A write to `permission` replaces the whole list. An omitted pair is subtractive, and the
    tool blocks it.
    """
    declared_pairs = _permission_pairs(declared)
    observed_pairs = _permission_pairs(observed)

    if observed_pairs - declared_pairs:
        return "subtractive"
    return "additive" if declared_pairs - observed_pairs else "unchanged"


def same_value(declared: Any, observed: Any) -> bool:
    """Compare a declared value against an observed value.

    Manage returns scalars as strings. `port` arrives as `"8030"`, not `8030`.

    Read the observed value in the declared type. Never coerce the declaration.
    """
    if declared == observed:
        return True
    if isinstance(declared, bool):
        if isinstance(observed, str):
            return observed.strip().lower() == ("true" if declared else "false")
        return False
    if isinstance(declared, int) and isinstance(observed, str):
        try:
            return int(observed.strip()) == declared
        except ValueError:
            return False
    if isinstance(declared, str) and isinstance(observed, bool | int):
        return declared == str(observed)
    if isinstance(declared, Sequence) and not isinstance(declared, str | bytes):
        if isinstance(observed, Sequence) and not isinstance(observed, str | bytes):
            return len(declared) == len(observed) and all(
                same_value(d, o) for d, o in zip(declared, observed, strict=False)
            )
        return False
    return False


def property_matches(prop: str, value: Any, observed: Mapping[str, Any]) -> bool:
    """Decide whether one declared property matches the observed value.

    Compare `default_permissions` as a set of pairs, never element by element. Two orders of
    the same grant are the same grant.
    """
    if prop == "default_permissions":
        return classify_default_permissions(value, observed.get(prop)) == "unchanged"
    return same_value(value, observed.get(prop))


def _is_shrinkage(declared: Any, observed: Any) -> bool:
    """True when a declared collection removes members the server currently has."""
    if (
        isinstance(declared, Sequence)
        and not isinstance(declared, str | bytes)
        and isinstance(observed, Sequence)
        and not isinstance(observed, str | bytes)
    ):
        return bool(set(map(str, observed)) - set(map(str, declared)))
    return False


def classify(
    kind: str,
    name: str,
    declared: Mapping[str, Any],
    observed: Mapping[str, Any] | None,
    *,
    observed_kind: str | None = None,
    explicit_fields: frozenset[str] = frozenset(),
) -> ObjectPlan:
    """Classify one declared object against the observed state.

    `observed=None` means the probe found nothing. `explicit_fields` names the optional
    fields the operator wrote. An explicit empty differs from an omission.
    """
    if observed is None:
        return ObjectPlan(
            kind=kind,
            name=name,
            status=PlanStatus.CREATE,
            changes=[
                PropertyChange(
                    property=_audit_name(kind, prop),
                    desired=None if prop in REDACTED_PROPERTIES else value,
                    redacted=prop in REDACTED_PROPERTIES,
                )
                for prop, value in sorted(declared.items())
            ],
        )

    # Kind collision: the name is taken by a different kind of object. `--force` has
    # nothing to do here — forcing would mean deleting somebody else's object.
    if observed_kind is not None and observed_kind != kind:
        return ObjectPlan(
            kind=kind,
            name=name,
            status=PlanStatus.BLOCKED,
            force_required=False,
            blocked_reason=(
                f"the name {name!r} is already held by a {observed_kind}, not a "
                f"{kind}; --force does not apply because resolving it would mean "
                f"deleting an object this declaration does not describe"
            ),
        )

    # Drift over DECLARED properties only. Undeclared observed properties are untouched.
    # Compared through `same_value`, because the server returns typed scalars as
    # strings and a raw comparison invents drift on every re-run.
    drift = {
        prop: value
        for prop, value in declared.items()
        if not property_matches(prop, value, observed)
    }

    # Data-affecting drift is refused before anything else can soften it.
    for prop in sorted(drift):
        if prop in HARD_REFUSAL_PROPERTIES:
            msg = (
                f"Refusing to change {kind} {name!r} property {prop!r}: it is "
                f"data-affecting, so applying it could destroy or orphan data. "
                f"This refusal is unconditional and --force does not reach it. "
                f"Make the change deliberately and outside this tool if it is really "
                f"intended."
            )
            raise DataAffectingRefusal(msg)

    blocked_reasons: list[str] = []
    # prop -> reason, so a suppression can clear exactly what that property earned.
    blocked_by: dict[str, str] = {}
    force_required = False

    for prop, value in sorted(drift.items()):
        observed_value = observed.get(prop)

        if prop == "default_permissions":
            direction = classify_default_permissions(value, observed_value)
            explicit_empty = prop in explicit_fields and not value
            if direction == "subtractive" or explicit_empty:
                force_required = True
                reason = (
                    f"{prop} on {kind} {name!r} removes capabilities the server "
                    f"currently grants"
                    if not explicit_empty
                    else (
                        f"{prop} on {kind} {name!r} is declared explicitly empty, "
                        f"which removes every default permission"
                    )
                )
                blocked_reasons.append(reason)
                blocked_by[_audit_name(kind, prop)] = reason
            continue

        if prop in SECURITY_DENIED_PROPERTIES and _is_shrinkage(value, observed_value):
            force_required = True
            reason = (
                f"{prop} on {kind} {name!r} removes entries the server currently has"
            )
            blocked_reasons.append(reason)
            blocked_by[_audit_name(kind, prop)] = reason
            continue

        # A property the observation does not carry at all is being SET, not changed.
        # There is no running service on an unset port to interrupt, and treating
        # absence as a change blocks an object for a disruption that cannot occur.
        if prop in DISRUPTIVE_PROPERTIES and prop in observed:
            force_required = True
            reason = (
                f"changing {prop} on {kind} {name!r} from {observed_value!r} to "
                f"{value!r} interrupts service on the existing object"
            )
            blocked_reasons.append(reason)
            blocked_by[_audit_name(kind, prop)] = reason

    # The gate holds here too: a declared property with no Manage spelling
    # cannot be PUT, so the object blocks rather than promising a change the apply
    # path could not make. Softening this to finish the phase is exactly the failure
    # the gate exists to prevent.
    unmappable = sorted(
        prop
        for prop in drift
        if _audit_name(kind, prop) == prop and _is_unmappable(kind, prop)
    )
    for prop in unmappable:
        reason = (
            f"{prop} on {kind} {name!r} has no Manage property name in this tool, so it "
            f"cannot be applied"
        )
        blocked_reasons.append(reason)
        blocked_by[_audit_name(kind, prop)] = reason

    if blocked_reasons:
        return ObjectPlan(
            kind=kind,
            name=name,
            status=PlanStatus.BLOCKED,
            force_required=force_required and not unmappable,
            blocked_reason="; ".join(blocked_reasons),
            blocked_by=blocked_by,
            unforceable=[_audit_name(kind, p) for p in unmappable],
            changes=_changes_for(kind, drift, observed),
        )

    if not drift:
        return ObjectPlan(kind=kind, name=name, status=PlanStatus.UNCHANGED)

    return ObjectPlan(
        kind=kind,
        name=name,
        status=PlanStatus.UPDATE,
        changes=_changes_for(kind, drift, observed),
    )


def _is_unmappable(kind: str, prop: str) -> bool:
    try:
        to_manage_property(kind, prop)
    except UnmappedPropertyError:
        return True
    return False


def _changes_for(
    kind: str, drift: Mapping[str, Any], observed: Mapping[str, Any]
) -> list[PropertyChange]:
    """Build the audit record for the drift subset — and only the drift subset."""
    return [
        PropertyChange(
            property=_audit_name(kind, prop),
            observed=None if prop in REDACTED_PROPERTIES else observed.get(prop),
            desired=None if prop in REDACTED_PROPERTIES else value,
            redacted=prop in REDACTED_PROPERTIES,
        )
        for prop, value in sorted(drift.items())
    ]


def apply_suppressions(obj: ObjectPlan, ignore_globs: Sequence[str]) -> list[str]:
    """Move matched changes into `suppressed_changes[]`. Return the warnings.

    Run this after classification, never before. The tool records every suppression and
    warns about it.
    """
    if not ignore_globs or not obj.changes:
        return []

    kept: list[PropertyChange] = []
    warnings: list[str] = []
    for change in obj.changes:
        matched = next(
            (g for g in ignore_globs if fnmatchcase(change.property, g)), None
        )
        if matched is None:
            kept.append(change)
            continue
        obj.suppressed_changes.append(change)
        warnings.append(
            f"{obj.kind} {obj.name!r}: change to {change.property!r} suppressed by "
            f"ignore_properties pattern {matched!r}"
        )

    obj.changes = kept

    # A block earned by a property that has just been suppressed must be cleared;
    # a block earned by anything else must survive. The truth table says an
    # `ignore_properties` match is "unchanged + suppressed_changes[] + warning", and
    # the previous version only ever relaxed an UPDATE — so a suppressed disruptive
    # port left the object BLOCKED at exit 8, and the hatch could not do the one thing
    # it exists for. PARTIAL suppression is the case that matters: whatever drift is
    # left decides the outcome.
    for change in obj.suppressed_changes:
        obj.blocked_by.pop(change.property, None)

    if obj.status is PlanStatus.BLOCKED:
        if obj.blocked_by:
            # Something unsuppressed still blocks. Re-derive from what remains rather
            # than leaving the original joined message, which would name a property
            # that is no longer the reason.
            obj.blocked_reason = "; ".join(obj.blocked_by.values())
            obj.force_required = not (set(obj.blocked_by) & set(obj.unforceable))
        else:
            obj.status = PlanStatus.UPDATE if kept else PlanStatus.UNCHANGED
            obj.blocked_reason = None
            obj.force_required = False
    elif not kept and obj.status is PlanStatus.UPDATE:
        obj.status = PlanStatus.UNCHANGED

    return warnings
