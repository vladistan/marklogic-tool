"""`count-unpermissioned` is a gate, not a report.

Findings exit 7, so a checkpoint fails loudly.

The tool refuses `--as-user` here. The measurement runs on `/v1/eval`, and the writer role
lacks `xdbc-eval`. A sampled zero is not evidence.
"""

from typing import TYPE_CHECKING, cast

import typer

from marklogic_tool.core.config import resolve_profile
from marklogic_tool.core.endpoints import Endpoint, client_for
from marklogic_tool.core.exceptions import MarkLogicToolError
from marklogic_tool.core.identity import resolve_identity
from marklogic_tool.output.render_verify import build_report, render
from marklogic_tool.verify.measure import VerifyClients, measure_unpermissioned

if TYPE_CHECKING:
    from marklogic_tool.core.client import MarkLogicClient
    from marklogic_tool.core.manage_client import ManageClient

verify_app = typer.Typer(
    help="Count documents whose permission set is empty, and fail if any exist."
)


@verify_app.callback(invoke_without_command=True)
def count_unpermissioned(
    ctx: typer.Context,
    database: str = typer.Option(..., "--database", "-d", help="Target database name."),
    gate: bool = typer.Option(
        True,
        "--gate/--no-gate",
        help="Exit 7 when offending documents are found. On by default.",
    ),
    sampled: int | None = typer.Option(
        None,
        "--sampled",
        help=(
            "Scan only the first N URIs — quick triage, NOT evidence. Requires an "
            "explicit N; there is no default sample size."
        ),
    ),
    list_uris: int = typer.Option(
        0,
        "--list-uris",
        help="Print at most N offending URIs. Omitted means no URIs are printed.",
    ),
    as_user: str | None = typer.Option(
        None, "--as-user", help="Refused on this command — see the error text."
    ),
    profile: str | None = typer.Option(None, "--profile", "-P", hidden=True),
    timeout: int | None = typer.Option(
        None,
        "--timeout",
        help="Client deadline in seconds. Defaults to the profile 'timeout'.",
    ),
) -> None:
    """Count documents with an empty permission set. Exit 7 if any exist."""
    parent_obj = ctx.ensure_object(dict)
    if profile is not None:
        parent_obj["profile"] = profile
    profile_name: str | None = parent_obj.get("profile")
    output_fmt: str = parent_obj.get("output", "table")
    timeout = timeout if timeout is not None else parent_obj.get("timeout")

    if as_user is not None:
        typer.echo(
            "Error: count-unpermissioned cannot honour --as-user. The scan runs "
            "on /v1/eval, and the application's REST-writer role correctly lacks "
            "xdbc-eval, so the request would be made as the profile credential "
            "and reported as that user's view. It is refused rather than "
            "silently answered by someone else. Use 'count --as-user' to see "
            "what an identity can read.",
            err=True,
        )
        raise typer.Exit(code=2)

    if sampled is not None and sampled <= 0:
        typer.echo(
            f"Error: --sampled needs a positive number of URIs to scan, got "
            f"{sampled}. Omit --sampled for an exhaustive scan.",
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        resolved = resolve_profile(profile_name=profile_name)
        credential = resolve_identity(resolved)
        query = cast(
            "MarkLogicClient",
            client_for(resolved, Endpoint.QUERY, credential, timeout),
        )
        manage = cast(
            "ManageClient",
            client_for(resolved, Endpoint.MANAGE, credential, timeout),
        )
        with query, manage:
            measurement = measure_unpermissioned(
                VerifyClients(query=query, manage=manage),
                database,
                sampled_n=sampled,
                list_uris=list_uris,
            )
            endpoint = query.base_url
    except MarkLogicToolError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    if not measurement.evidence:
        typer.echo(
            f"WARNING: this was a sampled scan of {measurement.total_scanned} "
            "URIs, not an exhaustive one. Findings below are sound — sampling "
            "can only miss offenders, never invent them — but a zero here is "
            "NOT evidence that the corpus is clean. Re-run without --sampled to "
            "prove absence.",
            err=True,
        )

    exit_code = 7 if (gate and measurement.unpermissioned > 0) else 0

    render(
        build_report(
            measurement,
            identity=credential.username,
            identity_source=credential.source,
            endpoint=endpoint,
            gate=gate,
            exit_code=exit_code,
        ),
        output_fmt,
    )

    if exit_code != 0:
        raise typer.Exit(code=exit_code)
