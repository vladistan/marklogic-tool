"""Sentry verification.

A diagnostic that cannot fail cannot diagnose. The earlier version printed success even when
it sent nothing. So the tool forces the sample, prints the event id, and exits non-zero when
it captures nothing.
"""

import typer

from marklogic_tool.core.exceptions import ExitCode

test_app = typer.Typer(help="Diagnostic test commands.", no_args_is_help=True)


@test_app.command("sentry")
def test_sentry() -> None:
    """Send a test error and span to Sentry, and report the captured event id.

    Exits non-zero when nothing was captured. The usual cause is an unset or
    unreachable DSN. That failure is the whole reason the command exists.
    """
    import sentry_sdk

    typer.echo("Sending test error to Sentry...", err=True)
    try:
        raise RuntimeError("marklogic-tool test-sentry verification")
    except RuntimeError as e:
        event_id = sentry_sdk.capture_exception(e)

    typer.echo("Sending test transaction to Sentry...", err=True)
    # sampled=True bypasses traces_sample_rate. Sampling a diagnostic whose only
    # job is to emit one event means the diagnostic usually emits nothing.
    with sentry_sdk.start_transaction(
        op="test", name="test-sentry", sampled=True
    ) as txn:
        with sentry_sdk.start_span(op="test.span", description="test span"):
            pass
        txn.set_status("ok")

    sentry_sdk.flush(timeout=5)

    # Unreachable by ordinary invocation: the DSN is compiled in and `cli.main` always
    # calls `setup_sentry()`, so there is no supported way to run with Sentry
    # unconfigured. This guards an SDK that returns nothing, and its tests drive it
    # through a mocked `sentry_sdk`. No environment variable switches the DSN off, and
    # this command must not name one.
    if event_id is None:
        typer.echo(
            "Error: Sentry returned no event id, so nothing was captured and "
            "there is nothing to look up. This is reported as a failure rather "
            "than the success this command used to print unconditionally. The "
            "DSN is compiled in and cli.main always initialises Sentry, so "
            "reaching this normally means the SDK was not initialised in this "
            "process.",
            err=True,
        )
        raise typer.Exit(code=ExitCode.INPUT)

    typer.echo(f"Captured event id: {event_id}")
    typer.echo(
        "This id proves the event was CAPTURED AND QUEUED — not that it reached "
        "Sentry. Look the id up in the Sentry console to confirm delivery.",
        err=True,
    )
