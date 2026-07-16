"""Test subcommands — Sentry verification."""

import typer

test_app = typer.Typer(help="Diagnostic test commands.", no_args_is_help=True)


@test_app.command("sentry")
def test_sentry() -> None:
    """Send a test error and span to Sentry for verification."""
    import sentry_sdk

    typer.echo("Sending test error to Sentry...", err=True)
    try:
        raise RuntimeError("marklogic-tool test-sentry verification")
    except Exception as e:
        sentry_sdk.capture_exception(e)

    typer.echo("Sending test transaction to Sentry...", err=True)
    with sentry_sdk.start_transaction(op="test", name="test-sentry") as txn:
        with sentry_sdk.start_span(op="test.span", description="test span"):
            pass
        txn.set_status("ok")

    sentry_sdk.flush(timeout=5)
    typer.echo("Done. Check Sentry console for the test event.", err=True)
