"""Teardown. This verb is not the inverse of deploy.

It needs `--confirm-host` and refuses more than it removes. Cascade behaviour is not
established, so it fails closed. `RECREATABLE_KINDS` excludes databases and forests.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from marklogic_tool.core.exceptions import NetworkError, ServerError
from marklogic_tool.core.http import Absent, Present
from marklogic_tool.core.manage_client import CONNECTION_CLOSED_MARKER
from marklogic_tool.deploy.errors import DeclarationError, DeclarationUsageError
from marklogic_tool.deploy.mapping import mapping_for
from marklogic_tool.deploy.order import Node, teardown_order
from marklogic_tool.deploy.preflight import PreflightResult

TeardownReport = dict[str, "list[str] | str"]
"""Lists of object names, plus `remaining_basis` saying how `remaining` was derived.

The provenance string is part of the contract, not decoration: `remaining` means
something different when it was observed than when it was predicted, and a reader
cannot tell from the list alone.
"""

RECREATABLE_KINDS: frozenset[str] = frozenset(
    {"app_server", "user", "role", "rest_api"}
)
"""The only kinds teardown will remove.

A database holds documents and a forest holds a database's data, so neither is
recreatable in any sense that matters to an operator: recreating the object does not
bring the content back. They are excluded permanently rather than behind a flag,
because a flag is a thing someone eventually passes.

`rest_api` is included on grounds verified against a live server. A bare
`DELETE /v1/rest-apis/{name}`, with no `include=` parameter, removes the REST instance
and its app server and leaves BOTH the content and modules databases intact. The
dangerous form is the one carrying `include=content`, and that parameter is never
emitted anywhere in this tool, so no path here reaches data.
"""


def confirm_host(preflight_result: PreflightResult, confirmed: str) -> None:
    """Refuse unless the operator names the right host.

    This runs before the pre-flight checks. Typing the host proves the operator knows which
    server teardown strips.
    """
    hosts = preflight_result.declaration.target.hosts
    if confirmed not in hosts:
        declared = ", ".join(hosts) or "(none declared)"
        msg = (
            f"Refusing to destroy anything: --confirm-host {confirmed!r} does not "
            f"match the declaration's target hosts. "
            f"The confirmation exists so a destroy cannot be a mistyped deploy. "
            f"Re-run naming one of: {declared}."
        )
        raise DeclarationUsageError(msg)


def removable(preflight_result: PreflightResult) -> list[Node]:
    """Declared objects that exist and are recreatable, in reverse creation order."""
    order = teardown_order(preflight_result.declaration)
    return [
        node
        for node in order
        if node.kind in RECREATABLE_KINDS and node not in preflight_result.absent
    ]


LIST_ITEM_KEYS = ("list-items", "list-item")


def _names_from_list(payload: object, kind: str) -> list[str]:
    """Read `{kind}-default-list.list-items.list-item[].nameref` from a collection GET."""
    if not isinstance(payload, Mapping):
        return []
    container = payload.get(f"{kind}-default-list")
    if not isinstance(container, Mapping):
        return []
    items = container.get(LIST_ITEM_KEYS[0])
    if isinstance(items, Mapping):
        items = items.get(LIST_ITEM_KEYS[1])
    if not isinstance(items, Sequence) or isinstance(items, str | bytes):
        return []
    return [
        item["nameref"]
        for item in items
        if isinstance(item, Mapping) and isinstance(item.get("nameref"), str)
    ]


def find_outside_dependents(
    preflight_result: PreflightResult, client: object
) -> list[str]:
    """Find server objects that depend on what teardown removes.

    Enumerate from the server, not from the declaration. The purpose is to find objects the
    declaration does not describe.
    """
    doomed = {node.name for node in removable(preflight_result)}
    if not doomed:
        return []

    declared_names = {
        node.name for node in teardown_order(preflight_result.declaration)
    }

    dependents: set[str] = set()
    for kind in ("role", "user"):
        mapping = mapping_for(kind)
        listing = client.probe(mapping.root)  # type: ignore[attr-defined]
        payload = getattr(listing, "payload", None)
        for name in _names_from_list(payload, kind):
            if name in declared_names:
                continue
            properties = client.probe(mapping.properties_path(name))  # type: ignore[attr-defined]
            held = getattr(properties, "payload", None)
            if not isinstance(held, Mapping):
                continue
            granted = held.get("role")
            if (
                isinstance(granted, Sequence)
                and not isinstance(granted, str | bytes)
                and any(str(role) in doomed for role in granted)
            ):
                dependents.add(f"{kind}:{name}")
    return sorted(dependents)


def refuse_on_outside_dependents(
    preflight_result: PreflightResult, observed_dependents: Sequence[str]
) -> None:
    """Refuse, and name the dependents the declaration does not describe.

    Cascade behaviour is not established, so this does not decide it. The refusal is correct
    under either answer.
    """
    if not observed_dependents:
        return
    named = ", ".join(sorted(observed_dependents))
    msg = (
        f"Refusing to destroy: {named} still depend on objects this declaration "
        f"would remove, and they are not part of it. "
        f"Refusing rather than removing them or assuming the server will cascade — "
        f"the cascade behaviour is not established, and guessing it either way risks "
        f"breaking something this declaration does not describe. "
        f"Remove the named dependents first, or exclude the objects they need."
    )
    raise DeclarationError(msg)


TRANSIENT_MARKERS: tuple[str, ...] = (
    "XDMP-DISABLED",
    "503",
    CONNECTION_CLOSED_MARKER,
)
"""The ONLY signals treated as "not right now" rather than "no".

Removing an app server puts MarkLogic into a brief reconfiguration window, during
which the Manage API answers 503 XDMP-DISABLED and then recovers by itself. Treating
that as fatal turns a two-second wait into a partial teardown.

Deliberately narrow. A blanket retry would mask precisely the failures this tool
works to make loud — a 4xx is never retried, and no other 5xx is either.
"""

SETTLE_ATTEMPTS = 5
"""Bounded, and the bound is named in the refusal when it is exhausted."""


def _is_transient(error: Exception) -> bool:
    message = getattr(error, "message", str(error))
    return isinstance(error, ServerError | NetworkError) and any(
        marker in message for marker in TRANSIENT_MARKERS
    )


def _delete_settling(
    client: object, path: str, params: dict[str, str] | None, sleep: Any = None
) -> None:
    """Delete, waiting out a reconfiguration window if the server asks us to.

    Retries ONLY on the transient markers above. Anything else propagates
    unchanged on the first attempt. Fail-fast is still the rule for real
    failures.
    """
    pause = sleep if sleep is not None else _default_sleep
    last: Exception | None = None
    for attempt in range(SETTLE_ATTEMPTS):
        try:
            client.delete(path, params)  # type: ignore[attr-defined]
        except Exception as error:  # noqa: BLE001 - re-raised unless transient
            if not _is_transient(error):
                raise
            last = error
            pause(attempt)
            continue
        return

    msg = (
        f"Failed to remove {path}: the server reported it was unavailable on "
        f"{SETTLE_ATTEMPTS} successive attempts. "
        f"Refusing to keep waiting rather than blocking indefinitely — removing an "
        f"app server briefly reconfigures MarkLogic, but this has outlasted that "
        f"window. "
        f"Check the host is healthy and re-run; the teardown is idempotent. "
        f"Last response: {getattr(last, 'message', last)}"
    )
    raise ServerError(msg)


def _default_sleep(attempt: int) -> None:  # pragma: no cover - timing only
    import time

    time.sleep(min(2**attempt, 8) * 0.25)


def teardown(
    preflight_result: PreflightResult,
    client: object,
    confirmed_host: str,
    *,
    apply: bool = False,
    outside_dependents: Sequence[str] = (),
    report: TeardownReport | None = None,
    sleep: Any = None,
) -> TeardownReport:
    """Remove recreatable declared objects in reverse order. Fail fast.

    The caller can pass `report` in. This function mutates it, so a `finally` can emit the
    partial result on any exit path.
    """
    confirm_host(preflight_result, confirmed_host)
    # Computed from the server unless the caller supplied them. Passing them in stays
    # available for tests, but the default must never be "none found" — that is how
    # the refusal came to be unreachable in the first place.
    if not outside_dependents:
        outside_dependents = find_outside_dependents(preflight_result, client)
    refuse_on_outside_dependents(preflight_result, outside_dependents)

    targets = removable(preflight_result)

    # Mutated in place so a caller's `finally` sees whatever was reached.
    if report is None:
        report = {}
    # Bound to locals so mypy sees list[str], while `report` keeps holding the SAME
    # objects — the in-place mutation a caller's `finally` depends on.
    existing_removed = report.get("removed")
    removed: list[str] = existing_removed if isinstance(existing_removed, list) else []
    report["removed"] = removed
    report["would_remove"] = [str(node) for node in targets]
    declared = teardown_order(preflight_result.declaration)

    if not apply:
        # A prediction, and labelled as one. Nothing has been removed yet, so the only
        # honest statement is which kinds this verb will not touch.
        report["remaining"] = [
            str(node) for node in declared if node.kind not in RECREATABLE_KINDS
        ]
        report["remaining_basis"] = "predicted"
        return report

    cascaded: list[str] = []
    report["removed_by_cascade"] = cascaded
    for node in targets:
        mapping = mapping_for(node.kind)
        params = {"group-id": "Default"} if mapping.group_scoped else None
        path = mapping.probe_path(node.name)

        # Removals here CASCADE: taking the app server removes the REST instance with
        # it, so a later explicit delete legitimately finds nothing. That is detected
        # by PROBING first, not by swallowing a 404 from the DELETE — swallowing it
        # would also hide a wrong probe_path, where every delete 404s and teardown
        # reports success having removed nothing at all.
        if _already_absent(client, path, params):
            cascaded.append(str(node))
            continue

        _delete_settling(client, path, params, sleep)
        removed.append(str(node))

    # OBSERVED, never inferred. The previous version derived `remaining` from
    # declared KIND, which is a claim about the declaration rather than about the
    # server — and it was wrong wherever a removal cascades. Taking the app server
    # removes the REST instance implicitly, so `rest_api` was reported as surviving
    # while GET already returned 404.
    #
    # `remaining` is a claim about what SURVIVED. Only the server can answer it.
    report["remaining"] = _observe_survivors(client, declared)
    report["remaining_basis"] = "observed"
    return report


def _already_absent(client: object, path: str, params: dict[str, str] | None) -> bool:
    """Return True only when the server reports the object gone.

    Any other probe failure returns False, so the delete still runs and the real error
    surfaces there. Observe absence. Never assume it.
    """
    try:
        return isinstance(client.probe(path, params), Absent)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - unknown is not absent
        return False


def _observe_survivors(client: object, declared: Sequence[Node]) -> list[str]:
    """Probe every declared object. Report the objects that still exist.

    An incomplete probe reports `<node> (unverified)`. The tool does not drop it and does not
    keep it silently.
    """
    survivors: list[str] = []
    for node in declared:
        mapping = mapping_for(node.kind)
        params = {"group-id": "Default"} if mapping.group_scoped else None
        try:
            outcome = client.probe(mapping.probe_path(node.name), params)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - survival is unknown, and that is reportable
            survivors.append(f"{node} (unverified)")
            continue
        if isinstance(outcome, Present):
            survivors.append(str(node))
    return survivors
