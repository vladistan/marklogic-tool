"""Server health, over the Management API only.

`/v1/eval` is unreachable from here. The application role lacks `xdbc-eval`, so a check
through eval reports a healthy server as broken.

The verdict comes from the state fields.
"""

from dataclasses import dataclass
from typing import Any

import typer

from marklogic_tool.core.config import resolve_profile
from marklogic_tool.core.exceptions import MarkLogicToolError, ParseError
from marklogic_tool.core.manage_client import ManageClient
from marklogic_tool.output.formatters import format_json, format_table

STATUS_SCHEMA = "marklogic-tool/status/1"

HEALTHY_HOST_MODES = frozenset({"normal"})

status_app = typer.Typer(help="Report server reachability, version and health.")


@dataclass(frozen=True, slots=True)
class ServerStatus:
    """What the Management API observed, with no inference from HTTP 200."""

    host: str
    version: str
    host_mode: str
    host_mode_description: str
    online: bool
    cluster_name: str

    @property
    def healthy(self) -> bool:
        return self.online and self.host_mode.lower() in HEALTHY_HOST_MODES

    def unhealthy_reason(self) -> str:
        parts: list[str] = []
        if not self.online:
            parts.append(f"host '{self.host}' reports online=false")
        if self.host_mode.lower() not in HEALTHY_HOST_MODES:
            described = (
                f" ({self.host_mode_description})" if self.host_mode_description else ""
            )
            parts.append(f"host-mode is {self.host_mode!r}{described}")
        return "; ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": STATUS_SCHEMA,
            "host": self.host,
            "version": self.version,
            "host_mode": self.host_mode,
            "host_mode_description": self.host_mode_description,
            "online": self.online,
            "cluster_name": self.cluster_name,
            "healthy": self.healthy,
        }


@status_app.callback(invoke_without_command=True)
def status_command(
    ctx: typer.Context,
    profile: str | None = typer.Option(None, "--profile", "-P", hidden=True),
    timeout: int | None = typer.Option(
        None,
        "--timeout",
        help="Client deadline in seconds. Defaults to the profile 'timeout'.",
    ),
) -> None:
    """Report reachability, MarkLogic version and host/cluster state.

    Uses the Management API only, never /v1/eval.

    Examples:

        marklogic-tool status

        marklogic-tool -o json status
    """
    parent_obj = ctx.ensure_object(dict)
    if profile is not None:
        parent_obj["profile"] = profile
    profile_name: str | None = parent_obj.get("profile")
    output_fmt: str = parent_obj.get("output", "table")
    timeout = timeout if timeout is not None else parent_obj.get("timeout")

    try:
        resolved = resolve_profile(profile_name=profile_name)
        with ManageClient(resolved, timeout=timeout) as client:
            status = read_status(client)
    except MarkLogicToolError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    _render(status, output_fmt)

    if not status.healthy:
        typer.echo(
            f"Server is reachable but not healthy: {status.unhealthy_reason()}. "
            "Reported as a verification failure rather than success, because the "
            "Management API answered and the state it reported is not serviceable.",
            err=True,
        )
        raise typer.Exit(code=7)


def read_status(client: ManageClient) -> ServerStatus:
    """Read health from the Management API.

    The name and version come from `GET /manage/v2`. The host name comes from
    `/manage/v2/hosts`. `host-mode` and `online` come from the host status view.
    """
    host = _first_host_name(client)
    cluster = client.get_json("/manage/v2")
    host_status = client.get_json(f"/manage/v2/hosts/{host}", {"view": "status"})

    return ServerStatus(
        host=host,
        version=_require(
            cluster,
            ("local-cluster-default.version",),
            "the MarkLogic version",
        ),
        host_mode=_require(
            host_status,
            ("host-status.host-mode",),
            "the host mode",
        ),
        host_mode_description=_require(
            host_status,
            ("host-status.host-mode-description",),
            "the host mode description",
            allow_empty=True,
        ),
        online=_require_bool(
            host_status,
            ("host-status.status-properties.online",),
            "the host online flag",
        ),
        cluster_name=_require(
            cluster,
            ("local-cluster-default.name",),
            "the cluster name",
        ),
    )


def _first_host_name(client: ManageClient) -> str:
    data = client.get_json("/manage/v2/hosts")
    items = data.get("host-default-list", {}).get("list-items", {}).get("list-item", [])
    if not items:
        raise ParseError(
            "The Management API listed no hosts, so no host state can be "
            "reported. This is refused rather than reported as healthy. Check "
            "that the credential may read /manage/v2/hosts on this host."
        )
    name = items[0].get("nameref")
    if not name:
        raise ParseError(
            "The Management API returned a host entry with no 'nameref', so the "
            "host cannot be named. Check the Management API version on this host."
        )
    return str(name)


def _require(
    payload: dict[str, Any],
    paths: tuple[str, ...],
    what: str,
    *,
    allow_empty: bool = False,
) -> str:
    """Read the first path that resolves. Name every path tried when none resolve.

    Absence is an error. `allow_empty` marks a field whose empty value is meaningful:
    `host-mode-description` is empty on a healthy host.
    """
    for path in paths:
        value = _walk(payload, path)
        if value is None or isinstance(value, dict | list):
            continue
        text = str(value)
        if text or allow_empty:
            return text

    raise ParseError(
        f"The Management API response carried no value for {what}. Tried: "
        f"{', '.join(paths)}. No status is reported rather than a guessed one. "
        "Check the MarkLogic version and that format=json was accepted."
    )


def _require_bool(payload: dict[str, Any], paths: tuple[str, ...], what: str) -> bool:
    """Read a typed status property. MarkLogic wraps it as units and value.

    `status-properties.online` is `{"units": "bool", "value": true}`, not a bare boolean.
    The dict is truthy whatever it holds, so read `value` explicitly.
    """
    for path in paths:
        wrapper = _walk(payload, path)
        if isinstance(wrapper, dict):
            if "value" not in wrapper:
                continue
            return bool(wrapper["value"])
        if isinstance(wrapper, bool):
            return wrapper

    raise ParseError(
        f"The Management API response carried no readable value for {what}. "
        f"Tried: {', '.join(paths)}, expecting either a bool or a "
        '{"units": ..., "value": ...} wrapper. No status is reported rather '
        "than a guessed one."
    )


def _walk(payload: dict[str, Any], path: str) -> Any:
    value: Any = payload
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _render(status: ServerStatus, output_fmt: str) -> None:
    if output_fmt == "json":
        print(format_json([status.as_dict()]))
        return

    rows = [
        {"field": "host", "value": status.host},
        {"field": "version", "value": status.version},
        {"field": "host-mode", "value": status.host_mode},
        {"field": "online", "value": "yes" if status.online else "no"},
        {"field": "cluster", "value": status.cluster_name},
        {"field": "healthy", "value": "yes" if status.healthy else "no"},
    ]
    output = format_table(rows, title="Server Status")
    if output:
        print(output)
