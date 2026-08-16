"""The `destroy` verb. It removes the recreatable objects a declaration describes.

It needs `--confirm-host`. It removes app servers, users and roles. Databases and forests
stay outside the recreatable set.
"""

import contextlib
import json
from pathlib import Path

import typer

from marklogic_tool.core.config import resolve_profile
from marklogic_tool.core.endpoints import Endpoint, client_for
from marklogic_tool.core.exceptions import MarkLogicToolError
from marklogic_tool.core.identity import resolve_identity
from marklogic_tool.deploy.loader import read_declaration_file
from marklogic_tool.deploy.preflight import preflight
from marklogic_tool.deploy.teardown import TeardownReport, teardown
from marklogic_tool.output.formatters import format_table


def destroy(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Declaration whose objects to remove."),
    confirm_host: str = typer.Option(
        ...,
        "--confirm-host",
        help="Host you intend to strip. Must match the declaration's target.hosts.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would be removed without removing it."
    ),
    profile: str | None = typer.Option(None, "--profile", "-P", hidden=True),
) -> None:
    """Remove the recreatable objects FILE declares, on a host you name explicitly."""
    parent = ctx.ensure_object(dict)
    if profile is not None:
        parent["profile"] = profile
    output_format: str = parent.get("output", "table")

    # Built BEFORE anything is removed and mutated in place, so the `finally` below
    # always has the PARTIAL result to emit. Destroy owes the operator the same
    # guarantee `deploy` gives: a run that dies must still say what it
    # removed, or the state has to be reconstructed from the server by hand.
    report: TeardownReport = {"removed": [], "would_remove": [], "remaining": []}
    try:
        settings = resolve_profile(profile_name=parent.get("profile"))
        raw = read_declaration_file(file)
        credential = resolve_identity(settings)
        client = client_for(settings, Endpoint.MANAGE, credential)
        with client:
            result = preflight(
                raw,
                resolved_host=settings.host,
                client=client,  # type: ignore[arg-type]
                mode="plan",
                source=str(file),
            )
            teardown(result, client, confirm_host, apply=not dry_run, report=report)
    except MarkLogicToolError as error:
        _emit(report, output_format)
        typer.echo(f"Error: {error.message}", err=True)
        raise typer.Exit(code=error.exit_code) from None
    except KeyboardInterrupt:
        _emit(report, output_format)
        typer.echo(
            "Error: interrupted; the report above is what was removed.", err=True
        )
        raise typer.Exit(code=1) from None
    except Exception:
        _emit(report, output_format)
        raise

    _emit(report, output_format)


def _emit(report: TeardownReport, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(report, indent=2))
        return
    rows = [
        {"object": item, "disposition": "removed"} for item in report["removed"]
    ] + [
        {"object": item, "disposition": "would remove"}
        for item in report["would_remove"]
        if item not in report["removed"]
    ]
    rows += [
        {"object": item, "disposition": "kept (not recreatable)"}
        for item in report["remaining"]
    ]
    with contextlib.suppress(Exception):
        table = format_table(rows, title="Destroy")
        if table:
            print(table)
