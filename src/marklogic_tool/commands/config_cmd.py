"""Config subcommand — show active profile with masked credentials."""

import typer

from marklogic_tool.core.config import load_app_config, resolve_profile
from marklogic_tool.core.exceptions import ConfigurationError

config_app = typer.Typer(help="Configuration management.", no_args_is_help=True)


@config_app.command("list")
def config_list() -> None:
    """List all available configuration profiles.

    Examples:

        marklogic-tool config list

        marklogic-tool -P staging config list
    """
    try:
        app_config = load_app_config()
    except ConfigurationError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    if not app_config.profiles:
        typer.echo("No profiles configured.", err=True)
        return

    for name in sorted(app_config.profiles.keys()):
        marker = " (default)" if name == app_config.default_profile else ""
        typer.echo(f"  {name}{marker}")


@config_app.command("show")
def config_show(
    ctx: typer.Context,
    profile: str | None = typer.Option(None, "--profile", "-P", hidden=True),
) -> None:
    """Display the active profile configuration (credentials masked).

    Examples:

        marklogic-tool config show

        marklogic-tool -P staging config show
    """
    parent_obj = ctx.ensure_object(dict)
    if profile is not None:
        parent_obj["profile"] = profile
    profile_name: str | None = parent_obj.get("profile")

    try:
        resolved = resolve_profile(profile_name=profile_name)
    except ConfigurationError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    typer.echo(f"  host:        {resolved.host}")
    typer.echo(f"  port:        {resolved.port}")
    typer.echo(f"  username:    {resolved.username}")
    typer.echo(f"  password:    {'*' * 8}")
    typer.echo(f"  auth_method: {resolved.auth_method}")
    typer.echo(f"  timeout:     {resolved.timeout}s")
