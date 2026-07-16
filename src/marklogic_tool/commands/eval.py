"""Eval command — execute XQuery/JavaScript on MarkLogic via /v1/eval."""

import json
import sys
from pathlib import Path

import typer

from marklogic_tool.core.client import MarkLogicClient
from marklogic_tool.core.config import resolve_profile
from marklogic_tool.core.exceptions import MarkLogicToolError, ParseError
from marklogic_tool.core.response import EvalResult, parse_multipart_mixed
from marklogic_tool.output.formatters import format_json

eval_app = typer.Typer(help="Execute code on MarkLogic Server.")


@eval_app.callback(invoke_without_command=True)
def eval_command(
    ctx: typer.Context,
    code: str = typer.Argument(None, help="XQuery or JavaScript code to evaluate."),
    javascript: bool = typer.Option(
        False, "--javascript", "-j", help="Evaluate as JavaScript instead of XQuery."
    ),
    file: Path | None = typer.Option(None, "--file", "-f", help="Read code from file."),
    database: str | None = typer.Option(
        None, "--database", "-d", help="Target database name."
    ),
    variables: str | None = typer.Option(
        None, "--vars", help="External variables as JSON object."
    ),
) -> None:
    """Execute XQuery or JavaScript code against MarkLogic /v1/eval.

    Examples:

        marklogic-tool eval 'xdmp:database-name(xdmp:database())'

        marklogic-tool eval --javascript 'cts.collections()'

        marklogic-tool eval --file query.xqy -d Documents

        marklogic-tool eval --vars '{"db":"mydb"}' 'declare variable $db external; $db'
    """
    parent_obj = ctx.ensure_object(dict)
    profile_name: str | None = parent_obj.get("profile")
    output_fmt: str = parent_obj.get("output", "table")

    source = _resolve_source(code, file)

    if variables:
        try:
            json.loads(variables)
        except json.JSONDecodeError as e:
            typer.echo(f"Error: Invalid JSON in --vars: {e}", err=True)
            raise typer.Exit(code=2) from None

    try:
        profile = resolve_profile(profile_name=profile_name)
    except MarkLogicToolError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    data: dict[str, str] = {}
    if javascript:
        data["javascript"] = source
    else:
        data["xquery"] = source
    if database:
        data["database"] = database
    if variables:
        data["vars"] = variables

    try:
        with MarkLogicClient(profile) as client:
            response = client.post("/v1/eval", data=data)
            results = _parse_eval_response(response)
    except MarkLogicToolError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    _render_results(results, output_fmt)


def _resolve_source(code: str | None, file: Path | None) -> str:
    if file is not None:
        if not file.exists():
            typer.echo(f"Error: File not found: {file}", err=True)
            raise typer.Exit(code=3)
        return file.read_text()

    if code is not None:
        return code

    if not sys.stdin.isatty():
        source = sys.stdin.read()
        if source.strip():
            return source

    typer.echo("Error: No code provided. Pass code, --file, or pipe stdin.", err=True)
    raise typer.Exit(code=2)


def _parse_eval_response(response: "httpx.Response") -> list[EvalResult]:  # type: ignore[name-defined]  # noqa: F821
    ct = response.headers.get("content-type", "")

    if "multipart/mixed" in ct:
        return parse_multipart_mixed(ct, response.content)

    if response.status_code == 200 and not response.content:
        return []

    if "application/json" in ct:
        try:
            error_body = response.json()
            msg = error_body.get("errorResponse", {}).get("message", response.text)
        except Exception:
            msg = response.text
        raise ParseError(f"Server error: {msg}")

    if response.content:
        return [
            EvalResult(
                primitive_type="string",
                value=response.text,
                content_type=ct or "text/plain",
            )
        ]

    return []


def _render_results(results: list[EvalResult], fmt: str) -> None:
    if not results:
        typer.echo("No results.", err=True)
        return

    if fmt == "json":
        items = [
            {"type": r.primitive_type, "value": r.value, "content_type": r.content_type}
            for r in results
        ]
        print(format_json(items))
    else:
        if len(results) == 1:
            print(results[0].value)
        else:
            for r in results:
                print(r.value)
