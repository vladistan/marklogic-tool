"""Group commands — list and show group configuration."""

from typing import Any

import typer

from marklogic_tool.core.config import resolve_profile
from marklogic_tool.core.exceptions import MarkLogicToolError
from marklogic_tool.core.manage_client import ManageClient
from marklogic_tool.output.formatters import format_json, format_table

group_app = typer.Typer(help="Cluster group operations.", no_args_is_help=True)


@group_app.command("list")
def group_list(
    ctx: typer.Context,
    profile: str | None = typer.Option(None, "--profile", "-P", hidden=True),
) -> None:
    """List all groups in the cluster.

    Examples:

        marklogic-tool group list

        marklogic-tool -o json group list
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

    try:
        groups = _fetch_group_list(resolved)
    except MarkLogicToolError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    if not groups:
        typer.echo("No groups found.", err=True)
        return

    if output_fmt == "json":
        print(format_json(groups))
    else:
        rows = [{"name": g["name"]} for g in groups]
        output = format_table(rows, title="Groups")
        if output:
            print(output)


@group_app.command("show")
def group_show(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Group name."),
    profile: str | None = typer.Option(None, "--profile", "-P", hidden=True),
) -> None:
    """Show detailed properties for a group."""
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

    try:
        props = _fetch_group_properties(resolved, name)
    except MarkLogicToolError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    if output_fmt == "json":
        print(format_json([props]))
    else:
        typer.echo(f"  Group:                       {props['group-name']}")
        typer.echo(
            f"  List Cache Size:             {props.get('list-cache-size', 'N/A')} MB"
        )
        typer.echo(
            f"  Compressed Tree Cache Size:  {props.get('compressed-tree-cache-size', 'N/A')} MB"
        )
        typer.echo(
            f"  Metering Enabled:            {props.get('metering-enabled', 'N/A')}"
        )
        typer.echo(f"  Scheduled Tasks:             {props.get('scheduled-tasks', 0)}")


def _fetch_group_list(profile: Any) -> list[dict[str, str]]:
    with ManageClient(profile) as client:
        data = client.get_json("/manage/v2/groups")
        items = (
            data.get("group-default-list", {})
            .get("list-items", {})
            .get("list-item", [])
        )
        return [{"name": item.get("nameref", "")} for item in items]


def _fetch_group_properties(profile: Any, name: str) -> dict[str, Any]:
    with ManageClient(profile) as client:
        data = client.get_json(f"/manage/v2/groups/{name}/properties")
        scheduled_tasks = data.get("scheduled-task", [])
        return {
            "group-name": data.get("group-name", name),
            "list-cache-size": data.get("list-cache-size", 0),
            "compressed-tree-cache-size": data.get("compressed-tree-cache-size", 0),
            "metering-enabled": data.get("metering-enabled", False),
            "xdqp-ssl-enabled": data.get("xdqp-ssl-enabled", False),
            "scheduled-tasks": len(scheduled_tasks)
            if isinstance(scheduled_tasks, list)
            else 0,
        }
