"""Count documents over the application read path.

A count always carries its provenance. Two identities can report different counts over one
corpus, so only the identity, the source and the endpoint together make a count evidence.
"""

from dataclasses import dataclass
from typing import Any, cast

import typer

from marklogic_tool.core.client import MarkLogicClient
from marklogic_tool.core.config import ProfileSettings, resolve_profile
from marklogic_tool.core.endpoints import Endpoint, client_for, require_port
from marklogic_tool.core.exceptions import MarkLogicToolError
from marklogic_tool.core.http import require_total
from marklogic_tool.core.identity import resolve_identity
from marklogic_tool.output.formatters import format_json, format_table

COUNT_SCHEMA = "marklogic-tool/count/1"

count_app = typer.Typer(help="Count documents, with the identity that counted them.")


@dataclass(frozen=True, slots=True)
class CountResult:
    """A count and who asked, where."""

    database: str
    collection: str | None
    identity: str
    identity_source: str
    endpoint: str
    total: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": COUNT_SCHEMA,
            "database": self.database,
            "collection": self.collection,
            "identity": self.identity,
            "identity_source": self.identity_source,
            "endpoint": self.endpoint,
            "total": self.total,
        }

    def human_summary(self) -> str:
        """Say what the tool observed, and nothing more.

        In MarkLogic an unknown collection and an empty one are the same observation. The
        wording covers both cases.
        """
        if self.collection is not None and self.total == 0:
            return "0 documents - collection unknown or empty"
        return f"{self.total} documents"


@count_app.callback(invoke_without_command=True)
def count_command(
    ctx: typer.Context,
    database: str = typer.Option(..., "--database", "-d", help="Target database name."),
    collection: str | None = typer.Option(
        None, "--collection", "-c", help="Restrict the count to this collection."
    ),
    as_user: str | None = typer.Option(
        None,
        "--as-user",
        help=(
            "Count as this identity, authenticated as that user over the REST "
            "instance. Never amps and never falls back to the profile credential."
        ),
    ),
    as_user_secret: str | None = typer.Option(
        None,
        "--as-user-secret",
        help=(
            "Secret reference for --as-user: ssm:<path>, env:<VAR> or "
            "profile:<name>. Overrides the profile [identities] entry. Never a "
            "literal password."
        ),
    ),
    profile: str | None = typer.Option(None, "--profile", "-P", hidden=True),
    timeout: int | None = typer.Option(
        None,
        "--timeout",
        help="Client deadline in seconds. Defaults to the profile 'timeout'.",
    ),
) -> None:
    """Count documents in a database. Report who counted them, and where."""
    parent_obj = ctx.ensure_object(dict)
    if profile is not None:
        parent_obj["profile"] = profile
    profile_name: str | None = parent_obj.get("profile")
    output_fmt: str = parent_obj.get("output", "table")
    timeout = timeout if timeout is not None else parent_obj.get("timeout")

    if as_user_secret is not None and as_user is None:
        typer.echo(
            "Error: --as-user-secret was given without --as-user, so there is no "
            "identity for it to authenticate. Pass --as-user <name>, or drop "
            "--as-user-secret.",
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        resolved = resolve_profile(profile_name=profile_name)
        endpoint = endpoint_for(resolved, as_user=as_user)
        require_port(resolved, endpoint)
        credential = resolve_identity(
            resolved, as_user=as_user, as_user_secret=as_user_secret
        )
        client = cast(
            "MarkLogicClient", client_for(resolved, endpoint, credential, timeout)
        )
        with client:
            total = read_total(client, database=database, collection=collection)
            result = CountResult(
                database=database,
                collection=collection,
                identity=credential.username,
                identity_source=credential.source,
                endpoint=client.base_url,
                total=total,
            )
    except MarkLogicToolError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    _render(result, output_fmt)


def endpoint_for(profile: ProfileSettings, *, as_user: str | None) -> Endpoint:
    """Pick the endpoint.

    Use REST whenever it is configured, so both halves of the pairing use one app server.
    `--as-user` requires REST, so an unset `rest_port` becomes a named refusal.
    """
    if as_user is not None or profile.rest_port is not None:
        return Endpoint.REST
    return Endpoint.QUERY


def read_total(
    client: MarkLogicClient, *, database: str, collection: str | None
) -> int:
    """Ask /v1/search for the total, refusing a response that carries none."""
    params: dict[str, str | int] = {
        "format": "json",
        "pageLength": 1,
        "database": database,
    }
    if collection is not None:
        params["collection"] = collection

    response = client.get(
        "/v1/search", params=params, headers={"Accept": "application/json"}
    )
    return require_total(response.json(), f"count of database '{database}'")


def _render(result: CountResult, output_fmt: str) -> None:
    if output_fmt == "json":
        print(format_json([result.as_dict()]))
        return

    rows = [
        {"field": "database", "value": result.database},
        {"field": "collection", "value": result.collection or "(all)"},
        {"field": "identity", "value": result.identity},
        {"field": "identity source", "value": result.identity_source},
        {"field": "endpoint", "value": result.endpoint},
        {"field": "count", "value": result.human_summary()},
    ]
    output = format_table(rows, title="Document Count")
    if output:
        print(output)
