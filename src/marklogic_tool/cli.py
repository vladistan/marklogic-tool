"""Typer CLI application — entry point, global options, error boundary."""

import sys
from enum import StrEnum

import typer

from marklogic_tool.__about__ import __version__
from marklogic_tool.commands.config_cmd import config_app
from marklogic_tool.commands.count import count_app
from marklogic_tool.commands.db import db_app
from marklogic_tool.commands.deploy import deploy as deploy_command
from marklogic_tool.commands.destroy import destroy as destroy_command
from marklogic_tool.commands.doc import doc_app
from marklogic_tool.commands.eval import eval_app
from marklogic_tool.commands.group import group_app
from marklogic_tool.commands.host import host_app
from marklogic_tool.commands.search import search_app
from marklogic_tool.commands.server import server_app
from marklogic_tool.commands.status import status_app
from marklogic_tool.commands.test_cmd import test_app
from marklogic_tool.commands.verify import verify_app
from marklogic_tool.core.exceptions import MarkLogicToolError
from marklogic_tool.core.logging import setup_logging
from marklogic_tool.core.monitoring import setup_sentry


class OutputFormat(StrEnum):
    json = "json"
    table = "table"
    raw = "raw"


app = typer.Typer(
    help="marklogic-tool: CLI to query, verify, deploy and destroy MarkLogic Server configuration via the REST and Management APIs.",
    no_args_is_help=True,
)

app.add_typer(config_app, name="config")
app.add_typer(count_app, name="count")
app.add_typer(db_app, name="db")
app.add_typer(doc_app, name="doc")
app.add_typer(eval_app, name="eval")
app.add_typer(group_app, name="group")
app.add_typer(host_app, name="host")
app.add_typer(search_app, name="search")
app.add_typer(server_app, name="server")
app.add_typer(status_app, name="status")
app.add_typer(verify_app, name="count-unpermissioned")
app.add_typer(test_app, name="test")

# A leaf verb, not a group: it takes FILE positionally and has no subcommands.
app.command("deploy")(deploy_command)
app.command("destroy")(destroy_command)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"marklogic-tool {__version__}")
        raise typer.Exit()


@app.callback()
def cli_callback(
    ctx: typer.Context,
    _version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        "-P",
        help="Configuration profile name.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable debug logging.",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress warning messages.",
    ),
    output: OutputFormat | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Output format (auto-detects: table for TTY, json for pipes).",
    ),
    timeout: int | None = typer.Option(
        None,
        "--timeout",
        help=(
            "Client deadline in seconds. Defaults to the active profile's "
            "'timeout'. Cannot raise the server's own request-time limit."
        ),
    ),
) -> None:
    """Global options shared across all commands."""
    ctx.ensure_object(dict)
    ctx.obj["profile"] = profile
    ctx.obj["verbose"] = verbose
    ctx.obj["quiet"] = quiet
    ctx.obj["timeout"] = timeout
    if output is not None:
        ctx.obj["output"] = output
    else:
        ctx.obj["output"] = "table" if sys.stdout.isatty() else "json"


def main() -> None:
    """Entry point — sets up observability then runs the CLI."""
    setup_logging(
        verbose="--verbose" in sys.argv or "-v" in sys.argv,
        quiet="--quiet" in sys.argv or "-q" in sys.argv,
    )
    setup_sentry()
    try:
        app()
    except MarkLogicToolError as e:
        typer.echo(f"Error: {e.message}", err=True)
        sys.exit(e.exit_code)
