"""Database commands — list and show database info via manage API."""

# cspell:ignore COLLXCNNOTFOUND

from typing import Any

import typer

from marklogic_tool.core.client import MarkLogicClient
from marklogic_tool.core.config import resolve_profile
from marklogic_tool.core.exceptions import MarkLogicToolError
from marklogic_tool.core.manage_client import ManageClient
from marklogic_tool.output.formatters import format_json, format_table

db_app = typer.Typer(help="Database operations.", no_args_is_help=True)


@db_app.command("list")
def db_list(
    ctx: typer.Context,
    group: str | None = typer.Option(
        None, "--group", "-G", help="Filter by group (default from profile)."
    ),
    profile: str | None = typer.Option(None, "--profile", "-P", hidden=True),
) -> None:
    """List all databases with document counts and sizes.

    Examples:

        marklogic-tool db list

        marklogic-tool -o json db list
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
        databases = _fetch_database_list(resolved)
        doc_counts = _fetch_doc_counts(resolved)
    except MarkLogicToolError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    rows: list[dict[str, str]] = []
    for db in databases:
        name = db["name"]
        size_mb = db.get("data_size", 0)
        forests = db.get("forests_count", 0)
        docs = doc_counts.get(name, 0)
        size_display = _format_size(size_mb)
        rows.append(
            {
                "name": name,
                "documents": f"{docs:,}",
                "size": size_display,
                "forests": str(forests),
            }
        )

    if not rows:
        typer.echo("No databases found.", err=True)
        return

    if output_fmt == "json":
        json_rows = [
            {
                "name": r["name"],
                "documents": int(r["documents"].replace(",", "")),
                "size_mb": next(
                    (
                        d.get("data_size", 0)
                        for d in databases
                        if d["name"] == r["name"]
                    ),
                    0,
                ),
                "forests": int(r["forests"]),
            }
            for r in rows
        ]
        print(format_json(json_rows))
    else:
        output = format_table(rows, title="Databases")
        if output:
            print(output)


@db_app.command("show")
def db_show(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Database name."),
    group: str | None = typer.Option(
        None, "--group", "-G", help="Group context (default from profile)."
    ),
    profile: str | None = typer.Option(None, "--profile", "-P", hidden=True),
) -> None:
    """Show detailed status for a database."""
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
        status = _fetch_database_status(resolved, name)
        doc_count = _fetch_single_db_doc_count(resolved, name)
        # A disabled lexicon costs the collections only; the rest of the report
        # is still true and still worth printing.
        collections = (
            _fetch_collections(resolved, name)
            if collection_lexicon_enabled(resolved, name)
            else None
        )
    except MarkLogicToolError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    status["documents"] = doc_count
    # None, never []. An empty list reads as "this database has no collections";
    # the truth is that we cannot see them. Reporting a distinction we cannot
    # observe is the defect this tool exists to eliminate.
    status["collections"] = collections
    if collections is None:
        status["collections_unavailable_reason"] = COLLECTION_LEXICON_DISABLED

    if output_fmt == "json":
        print(format_json([status]))
    else:
        _print_db_detail(status)


def _fetch_database_list(profile: "ProfileSettings") -> list[dict[str, Any]]:  # type: ignore[name-defined]  # noqa: F821
    databases: list[dict[str, Any]] = []
    with ManageClient(profile) as client:
        data = client.get_json("/manage/v2/databases")
        items = (
            data.get("database-default-list", {})
            .get("list-items", {})
            .get("list-item", [])
        )
        for item in items:
            name = item.get("nameref", "")
            status = client.get_json(
                f"/manage/v2/databases/{name}", params={"view": "status"}
            )
            props = status.get("database-status", {}).get("status-properties", {})
            databases.append(
                {
                    "name": name,
                    "data_size": props.get("data-size", {}).get("value", 0),
                    "forests_count": props.get("forests-count", {}).get("value", 0),
                }
            )
    return databases


def _fetch_doc_counts(profile: "ProfileSettings") -> dict[str, int]:  # type: ignore[name-defined]  # noqa: F821
    query = (
        "for $db-id in xdmp:databases() "
        "let $name := xdmp:database-name($db-id) "
        'let $docs := xdmp:eval("xdmp:estimate(doc())", (), '
        '  <options xmlns="xdmp:eval"><database>{$db-id}</database></options>) '
        'return fn:concat($name, "|", $docs)'
    )
    from marklogic_tool.core.response import parse_multipart_mixed

    counts: dict[str, int] = {}
    with MarkLogicClient(profile) as client:
        response = client.post("/v1/eval", data={"xquery": query})
        ct = response.headers.get("content-type", "")
        if "multipart/mixed" in ct:
            results = parse_multipart_mixed(ct, response.content)
            for r in results:
                parts = r.value.split("|", 1)
                if len(parts) == 2:
                    try:
                        counts[parts[0]] = int(parts[1])
                    except ValueError:
                        counts[parts[0]] = 0
    return counts


def _fetch_database_status(profile: "ProfileSettings", name: str) -> dict[str, Any]:  # type: ignore[name-defined]  # noqa: F821
    with ManageClient(profile) as client:
        data = client.get_json(
            f"/manage/v2/databases/{name}", params={"view": "status"}
        )
        props = data.get("database-status", {}).get("status-properties", {})
        return {
            "name": name,
            "state": props.get("state", {}).get("value", "unknown"),
            "data_size_mb": props.get("data-size", {}).get("value", 0),
            "in_memory_mb": props.get("in-memory-size", {}).get("value", 0),
            "forests_count": props.get("forests-count", {}).get("value", 0),
            "min_capacity_pct": props.get("min-capacity", {}).get("value", 0),
            "merge_count": props.get("merge-count", {}).get("value", 0),
        }


def _fetch_single_db_doc_count(profile: "ProfileSettings", name: str) -> int:  # type: ignore[name-defined]  # noqa: F821
    from marklogic_tool.core.response import parse_multipart_mixed

    with MarkLogicClient(profile) as client:
        response = client.post(
            "/v1/eval",
            data={
                "xquery": "xdmp:estimate(doc())",
                "database": name,
            },
        )
        ct = response.headers.get("content-type", "")
        if "multipart/mixed" in ct:
            results = parse_multipart_mixed(ct, response.content)
            if results:
                try:
                    return int(results[0].value)
                except ValueError:
                    pass
    return 0


COLLECTION_LEXICON_DISABLED = "collection lexicon disabled on this database"


def collection_lexicon_enabled(profile: "ProfileSettings", name: str) -> bool:  # type: ignore[name-defined]  # noqa: F821
    """Say whether `cts:collections()` can answer for this database.

    The failing axis is the collection lexicon, not the MarkLogic version. Absence of the
    property means disabled.
    """
    with ManageClient(profile) as client:
        props = client.get_json(f"/manage/v2/databases/{name}/properties")
    return props.get("collection-lexicon") is True


def _fetch_collections(profile: "ProfileSettings", name: str) -> list[dict[str, Any]]:  # type: ignore[name-defined]  # noqa: F821
    query = (
        "for $c in cts:collections() "
        'return fn:concat($c, "|", xdmp:estimate(cts:search(doc(), cts:collection-query($c))))'
    )
    from marklogic_tool.core.response import parse_multipart_mixed

    collections: list[dict[str, Any]] = []
    with MarkLogicClient(profile) as client:
        response = client.post("/v1/eval", data={"xquery": query, "database": name})
        ct = response.headers.get("content-type", "")
        if "multipart/mixed" in ct:
            results = parse_multipart_mixed(ct, response.content)
            for r in results:
                parts = r.value.split("|", 1)
                if len(parts) == 2:
                    try:
                        collections.append(
                            {"name": parts[0], "documents": int(parts[1])}
                        )
                    except ValueError:
                        collections.append({"name": parts[0], "documents": 0})
    collections.sort(key=lambda x: x["documents"], reverse=True)
    return collections


def _format_size(mb: int | float) -> str:
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{int(mb)} MB"


def _print_db_detail(status: dict[str, Any]) -> None:
    typer.echo(f"  Database:     {status['name']}")
    typer.echo(f"  State:        {status['state']}")
    typer.echo(f"  Documents:    {status['documents']:,}")
    typer.echo(f"  Data Size:    {_format_size(status['data_size_mb'])}")
    typer.echo(f"  In Memory:    {status['in_memory_mb']} MB")
    typer.echo(f"  Forests:      {status['forests_count']}")
    typer.echo(f"  Capacity:     {status['min_capacity_pct']:.1f}%")
    typer.echo(f"  Merges:       {status['merge_count']}")

    collections = status.get("collections")
    if collections is None:
        reason = status.get(
            "collections_unavailable_reason", COLLECTION_LEXICON_DISABLED
        )
        # Not "0 collections" — that would assert something we did not observe.
        typer.echo(f"  Collections:  unavailable — {reason}")
    elif collections:
        typer.echo(f"  Collections:  {len(collections)}")
        for coll in collections[:10]:
            typer.echo(f"    {coll['name']} ({coll['documents']:,} docs)")
        if len(collections) > 10:
            typer.echo(f"    ... and {len(collections) - 10} more")
    else:
        typer.echo("  Collections:  0")
