"""Search command — full-text and structured search via /v1/search."""

import json
import sys

import typer

from marklogic_tool.core.client import MarkLogicClient
from marklogic_tool.core.config import resolve_profile
from marklogic_tool.core.exceptions import MarkLogicToolError
from marklogic_tool.output.formatters import format_json, format_table

search_app = typer.Typer(help="Search documents.")


@search_app.callback(invoke_without_command=True)
def search_command(
    ctx: typer.Context,
    query: str = typer.Argument(None, help="Search query string."),
    structured: str | None = typer.Option(
        None, "--structured", "-s", help="Structured query as JSON."
    ),
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name."
    ),
    collection: list[str] | None = typer.Option(
        None, "--collection", "-c", help="Restrict to collection (repeatable)."
    ),
    directory: str | None = typer.Option(
        None, "--directory", help="Restrict to directory URI."
    ),
    page_length: int = typer.Option(
        10, "--page-length", "-n", help="Number of results per page."
    ),
    start: int = typer.Option(1, "--start", help="Start position (1-based)."),
) -> None:
    """Search documents in MarkLogic via /v1/search.

    Examples:

        marklogic-tool search "armstrong"

        marklogic-tool search -c /emails -c /contacts "test"

        marklogic-tool search --structured '{"query":{"word-query":{"text":"test"}}}'

        marklogic-tool search -d Documents -n 25 "invoice"
    """
    parent_obj = ctx.ensure_object(dict)
    profile_name: str | None = parent_obj.get("profile")
    output_fmt: str = parent_obj.get("output", "table")

    if not query and not structured:
        if not sys.stdin.isatty():
            query = sys.stdin.read().strip()
        if not query:
            typer.echo("Error: Provide a query string or --structured JSON.", err=True)
            raise typer.Exit(code=2)

    if structured:
        try:
            json.loads(structured)
        except json.JSONDecodeError as e:
            typer.echo(f"Error: Invalid JSON in --structured: {e}", err=True)
            raise typer.Exit(code=2) from None

    try:
        profile = resolve_profile(profile_name=profile_name)
    except MarkLogicToolError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    params: dict[str, str | int | list[str]] = {
        "format": "json",
        "pageLength": page_length,
        "start": start,
    }
    if query:
        params["q"] = query
    if database:
        params["database"] = database
    if collection:
        params["collection"] = list(collection)
    if directory:
        params["directory"] = directory

    headers: dict[str, str] = {"Accept": "application/json"}

    try:
        with MarkLogicClient(profile) as client:
            if structured:
                response = client.post(
                    "/v1/search",
                    params=params,
                    content=structured,
                    headers={**headers, "Content-Type": "application/json"},
                )
            else:
                response = client.get("/v1/search", params=params, headers=headers)
    except MarkLogicToolError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    _render_search_results(response.text, output_fmt, start, page_length)


def _render_search_results(text: str, fmt: str, start: int, page_length: int) -> None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        print(text)
        return

    if fmt == "json":
        print(format_json([data]) if isinstance(data, dict) else text)
        return

    total = data.get("total", 0)
    results = data.get("results", [])

    if not results:
        typer.echo(f"No results found. (total: {total})", err=True)
        return

    end = min(start + len(results) - 1, total)
    typer.echo(f"Results {start}-{end} of {total}", err=True)

    rows: list[dict[str, str]] = []
    for r in results:
        uri = r.get("uri", "")
        score = str(r.get("score", "0"))
        snippet = _extract_snippet(r)
        rows.append({"uri": uri, "score": score, "snippet": snippet})

    output = format_table(rows, title="Search Results")
    if output:
        print(output)


def _extract_snippet(result: dict) -> str:  # type: ignore[type-arg]
    matches = result.get("matches", [])
    if not matches:
        return ""
    first_match = matches[0]
    match_text = first_match.get("match-text", [])
    parts: list[str] = []
    for item in match_text:
        if isinstance(item, dict):
            parts.append(item.get("highlight", ""))
        elif isinstance(item, str):
            parts.append(item)
    snippet = "".join(parts)
    if len(snippet) > 80:
        snippet = snippet[:77] + "..."
    return snippet
