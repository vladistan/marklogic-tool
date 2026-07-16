"""Host commands — list and show cluster host info."""

from typing import Any

import typer

from marklogic_tool.core.client import MarkLogicClient
from marklogic_tool.core.config import resolve_profile
from marklogic_tool.core.exceptions import MarkLogicToolError
from marklogic_tool.core.manage_client import ManageClient
from marklogic_tool.core.response import parse_multipart_mixed
from marklogic_tool.output.formatters import format_json, format_table

host_app = typer.Typer(help="Cluster host operations.", no_args_is_help=True)


@host_app.command("list")
def host_list(
    ctx: typer.Context,
    profile: str | None = typer.Option(None, "--profile", "-P", hidden=True),
) -> None:
    """List all hosts in the cluster.

    Examples:

        marklogic-tool host list

        marklogic-tool -o json host list
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
        hosts = _fetch_host_list(resolved)
    except MarkLogicToolError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    if not hosts:
        typer.echo("No hosts found.", err=True)
        return

    if output_fmt == "json":
        print(format_json(hosts))
    else:
        rows = [{"name": h["name"], "role": h.get("role", "")} for h in hosts]
        output = format_table(rows, title="Hosts")
        if output:
            print(output)


@host_app.command("show")
def host_show(
    ctx: typer.Context,
    profile: str | None = typer.Option(None, "--profile", "-P", hidden=True),
) -> None:
    """Show host details (version, platform, edition)."""
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
        info = _fetch_host_info(resolved)
    except MarkLogicToolError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    if output_fmt == "json":
        print(format_json([info]))
    else:
        for key, val in info.items():
            typer.echo(f"  {key}: {val}")


def _fetch_host_list(profile: Any) -> list[dict[str, str]]:
    with ManageClient(profile) as client:
        data = client.get_json("/manage/v2/hosts")
        items = (
            data.get("host-default-list", {}).get("list-items", {}).get("list-item", [])
        )
        return [
            {"name": item.get("nameref", ""), "role": item.get("roleref", "")}
            for item in items
        ]


def _fetch_host_info(profile: Any) -> dict[str, str]:
    query = (
        'fn:concat("name=", xdmp:host-name(xdmp:hosts()[1])), '
        'fn:concat("version=", xdmp:version()), '
        'fn:concat("platform=", xdmp:platform()), '
        'fn:concat("architecture=", xdmp:architecture()), '
        'fn:concat("edition=", xdmp:product-edition())'
    )
    info: dict[str, str] = {}
    with MarkLogicClient(profile) as client:
        response = client.post("/v1/eval", data={"xquery": query})
        ct = response.headers.get("content-type", "")
        if "multipart/mixed" in ct:
            results = parse_multipart_mixed(ct, response.content)
            for r in results:
                if "=" in r.value:
                    key, _, val = r.value.partition("=")
                    info[key] = val
    return info
