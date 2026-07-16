"""Server commands — list and show app/task server info via manage API."""

from typing import Any

import typer

from marklogic_tool.core.config import resolve_profile
from marklogic_tool.core.exceptions import MarkLogicToolError
from marklogic_tool.core.manage_client import ManageClient
from marklogic_tool.output.formatters import format_json, format_table

server_app = typer.Typer(help="App server operations.", no_args_is_help=True)


@server_app.command("list")
def server_list(
    ctx: typer.Context,
    group: str | None = typer.Option(
        None, "--group", "-G", help="Filter by group (default from profile)."
    ),
    profile: str | None = typer.Option(None, "--profile", "-P", hidden=True),
) -> None:
    """List all app servers.

    Examples:

        marklogic-tool server list

        marklogic-tool server list --group Default
    """
    parent_obj = ctx.ensure_object(dict)
    if profile is not None:
        parent_obj["profile"] = profile
    profile_name: str | None = parent_obj.get("profile")
    output_fmt: str = parent_obj.get("output", "table")

    try:
        resolved = resolve_profile(profile_name=profile_name)
    except MarkLogicToolError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    group_name = group or resolved.default_group

    try:
        servers = _fetch_server_list(resolved, group_name)
    except MarkLogicToolError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    if not servers:
        typer.echo("No servers found.", err=True)
        return

    if output_fmt == "json":
        print(format_json(servers))
    else:
        rows = [
            {
                "name": s["name"],
                "kind": s["kind"],
                "port": str(s.get("port", "")),
                "content-db": s.get("content_db", ""),
                "modules-db": s.get("modules_db", ""),
            }
            for s in servers
        ]
        output = format_table(rows, title="Servers")
        if output:
            print(output)


@server_app.command("show")
def server_show(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Server name."),
    group: str | None = typer.Option(
        None, "--group", "-G", help="Group context (default from profile)."
    ),
    profile: str | None = typer.Option(None, "--profile", "-P", hidden=True),
) -> None:
    """Show detailed properties for a server."""
    parent_obj = ctx.ensure_object(dict)
    if profile is not None:
        parent_obj["profile"] = profile
    profile_name: str | None = parent_obj.get("profile")
    output_fmt: str = parent_obj.get("output", "table")

    try:
        resolved = resolve_profile(profile_name=profile_name)
    except MarkLogicToolError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    group_name = group or resolved.default_group

    try:
        props = _fetch_server_properties(resolved, name, group_name)
    except MarkLogicToolError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    if output_fmt == "json":
        print(format_json([props]))
    else:
        for key, val in props.items():
            typer.echo(f"  {key}: {val}")


def _fetch_server_list(profile: Any, group_name: str) -> list[dict[str, Any]]:
    servers: list[dict[str, Any]] = []
    with ManageClient(profile) as client:
        data = client.get_json("/manage/v2/servers", params={"group-id": group_name})
        items = (
            data.get("server-default-list", {})
            .get("list-items", {})
            .get("list-item", [])
        )
        for item in items:
            name = item.get("nameref", "")
            props = _fetch_server_properties(profile, name, group_name, client=client)
            servers.append(
                {
                    "name": name,
                    "kind": props.get("server-type", item.get("kindref", "")),
                    "port": props.get("port", ""),
                    "content_db": props.get("content-database", ""),
                    "modules_db": props.get("modules-database", ""),
                }
            )
    return servers


def _fetch_server_properties(
    profile: Any, name: str, group_name: str, *, client: ManageClient | None = None
) -> dict[str, Any]:
    def _get(c: ManageClient) -> dict[str, Any]:
        return c.get_json(
            f"/manage/v2/servers/{name}/properties",
            params={"group-id": group_name},
        )

    if client is not None:
        return _get(client)
    with ManageClient(profile) as c:
        return _get(c)
