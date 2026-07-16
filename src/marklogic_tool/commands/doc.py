"""Document commands — retrieve documents and metadata from MarkLogic."""

import sys

import typer

from marklogic_tool.core.client import MarkLogicClient
from marklogic_tool.core.config import resolve_profile
from marklogic_tool.core.exceptions import MarkLogicToolError
from marklogic_tool.output.formatters import format_json, format_table

doc_app = typer.Typer(help="Document operations.", no_args_is_help=True)


@doc_app.command("get")
def doc_get(
    ctx: typer.Context,
    uri: str = typer.Argument(..., help="Document URI to retrieve."),
    format: str | None = typer.Option(
        None, "--format", "-f", help="Content format: json, xml, text, binary."
    ),
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name."
    ),
    metadata: bool = typer.Option(
        False, "--metadata", "-m", help="Retrieve document metadata instead of content."
    ),
    profile: str | None = typer.Option(None, "--profile", "-P", hidden=True),
) -> None:
    """Retrieve a document by URI from MarkLogic /v1/documents."""
    parent_obj = ctx.ensure_object(dict)
    if profile is not None:
        parent_obj["profile"] = profile
    profile_name: str | None = parent_obj.get("profile")
    output_fmt: str = parent_obj.get("output", "table")

    if metadata and format:
        typer.echo("Error: --metadata and --format are mutually exclusive.", err=True)
        raise typer.Exit(code=2)

    try:
        resolved = resolve_profile(profile_name=profile_name)
    except MarkLogicToolError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    params: dict[str, str] = {"uri": uri}
    if database:
        params["database"] = database

    headers: dict[str, str] = {}
    if metadata:
        params["category"] = "metadata"
        headers["Accept"] = "application/json"
    elif format:
        accept_map = {
            "json": "application/json",
            "xml": "application/xml",
            "text": "text/plain",
            "binary": "application/octet-stream",
        }
        headers["Accept"] = accept_map.get(format, f"application/{format}")

    try:
        with MarkLogicClient(resolved) as client:
            response = client.get("/v1/documents", params=params, headers=headers)
    except MarkLogicToolError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    content_type = response.headers.get("content-type", "")

    if metadata:
        _render_metadata(response.text, output_fmt)
    elif _is_binary(content_type):
        _handle_binary(response.content, uri)
    else:
        print(response.text)


def _render_metadata(text: str, fmt: str) -> None:
    """Render metadata response."""
    import json

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        print(text)
        return

    if fmt == "json":
        print(format_json([data]) if isinstance(data, dict) else text)
        return

    rows: list[dict[str, str]] = []
    for coll in data.get("collections", []):
        rows.append({"type": "collection", "value": coll})
    for perm in data.get("permissions", []):
        for cap in perm.get("capabilities", []):
            rows.append({"type": "permission", "value": f"{perm['role-name']}: {cap}"})
    quality = data.get("quality", 0)
    rows.append({"type": "quality", "value": str(quality)})

    if rows:
        output = format_table(rows, title="Document Metadata")
        if output:
            print(output)
    else:
        print(text)


def _is_binary(content_type: str) -> bool:
    text_types = ("text/", "application/json", "application/xml")
    return not any(content_type.startswith(t) for t in text_types)


def _handle_binary(content: bytes, uri: str) -> None:
    if sys.stdout.isatty():
        typer.echo(
            f"Binary document ({len(content)} bytes). "
            f"Pipe to file: marklogic-tool doc get {uri} > output",
            err=True,
        )
        raise typer.Exit(code=0)
    sys.stdout.buffer.write(content)
