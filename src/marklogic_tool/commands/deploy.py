"""The `deploy` verb. It reconciles a declaration against a server.

The tool builds the plan before reconcile, then emits it in a `finally`. Every exit path
produces the plan. `--force` authorizes only the `force_required` classes.
"""

import contextlib
from pathlib import Path

import typer

from marklogic_tool.core.config import resolve_profile
from marklogic_tool.core.endpoints import Endpoint, client_for
from marklogic_tool.core.exceptions import ExitCode, MarkLogicToolError
from marklogic_tool.core.identity import resolve_identity
from marklogic_tool.deploy.errors import DataAffectingRefusal
from marklogic_tool.deploy.loader import read_declaration_file
from marklogic_tool.deploy.plan import DeployPlan, PlanStatus
from marklogic_tool.deploy.preflight import preflight
from marklogic_tool.deploy.reconcile import reconcile
from marklogic_tool.output.render_plan import render_plan


def exit_code_for(plan: DeployPlan) -> int:
    """Derive the exit code from the finished plan.

    This needs runtime facts a pure plan does not hold. Operational failures raise before
    they reach here. Blocked outranks success.
    """
    if any(o.status is PlanStatus.BLOCKED for o in plan.objects):
        return int(ExitCode.BLOCKED)
    return int(ExitCode.SUCCESS)


def deploy(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Declaration file to reconcile."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Plan only. Issues no writes and needs no secrets."
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Authorise subtractive security drift and disruptive port changes. "
            "Never reaches a data-affecting operation."
        ),
    ),
    rotate_passwords: bool = typer.Option(
        False,
        "--rotate-passwords",
        help="Also set declared passwords on users that already exist.",
    ),
    profile: str | None = typer.Option(None, "--profile", "-P", hidden=True),
) -> None:
    """Reconcile FILE against the server, or preview it with --dry-run."""
    parent = ctx.ensure_object(dict)
    if profile is not None:
        parent["profile"] = profile
    output_format: str = parent.get("output", "table")

    plan: DeployPlan | None = None
    try:
        settings = resolve_profile(profile_name=parent.get("profile"))
        raw = read_declaration_file(file)

        # Constructed BEFORE reconcile so the `finally` below always has something to
        # emit, including on a path that raises before reconcile returns.
        plan = DeployPlan.new(
            mode="plan" if dry_run else "apply", target=[settings.host]
        )

        credential = resolve_identity(settings)
        client = client_for(settings, Endpoint.MANAGE, credential)
        with client:
            result = preflight(
                raw,
                resolved_host=settings.host,
                client=client,  # type: ignore[arg-type]
                mode="plan" if dry_run else "apply",
                source=str(file),
                rotate_passwords=rotate_passwords,
            )
            plan.target = list(result.declaration.target.hosts)
            reconcile(
                result,
                plan,
                client,
                apply=not dry_run,
                force=force,
                rotate_passwords=rotate_passwords,
            )
        plan.exit_code = exit_code_for(plan)
    except DataAffectingRefusal as error:
        _record_exit_code(plan, int(error.exit_code))
        _emit(plan, output_format)
        typer.echo(f"Error: {error.message}", err=True)
        raise typer.Exit(code=error.exit_code) from None
    except MarkLogicToolError as error:
        _record_exit_code(plan, int(error.exit_code))
        _emit(plan, output_format)
        typer.echo(f"Error: {error.message}", err=True)
        raise typer.Exit(code=error.exit_code) from None
    except KeyboardInterrupt:
        # SIGINT mid-apply still owes the operator the partial plan: the writes that
        # already happened are exactly what they need to know about.
        _record_exit_code(plan, int(ExitCode.GENERAL))
        _emit(plan, output_format)
        typer.echo(
            "Error: interrupted; the partial plan above is what was applied.", err=True
        )
        raise typer.Exit(code=int(ExitCode.GENERAL)) from None
    except Exception:
        # An unexpected exception is the one case where the code is not known here;
        # cli.main's boundary owns it. GENERAL is recorded so the artifact still says
        # "this run failed" rather than leaving the field null.
        _record_exit_code(plan, int(ExitCode.GENERAL))
        _emit(plan, output_format)
        raise

    _emit(plan, output_format)
    raise typer.Exit(code=plan.exit_code or 0)


def _record_exit_code(plan: DeployPlan | None, code: int) -> None:
    """Stamp the outcome on the plan before the tool emits it.

    `exit_code: null` next to a non-zero exit states the wrong outcome. A failure can happen
    before the plan exists, so this is guarded.
    """
    if plan is not None:
        plan.exit_code = code


def _emit(plan: DeployPlan | None, output_format: str) -> None:
    """Print the plan, whatever state it reached. Never raises."""
    if plan is None:
        return
    # Emission must never mask the real error: if rendering itself fails, the
    # original exception is what the operator needs, not a traceback from the
    # reporter.
    with contextlib.suppress(Exception):
        print(render_plan(plan, "json" if output_format == "json" else "table"))
