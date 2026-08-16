"""Config subcommand — show active profile with masked credentials."""

import tomllib

import typer

from marklogic_tool.core import secrets
from marklogic_tool.core.config import (
    config_path,
    load_app_config,
    resolve_profile,
)
from marklogic_tool.core.exceptions import ConfigurationError, ExitCode

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


@config_app.command("add")
def config_add(
    profile: str = typer.Option(..., "--profile", help="Name for the new profile."),
    host: str = typer.Option(..., "--host", help="MarkLogic host name."),
    username: str = typer.Option(..., "--username", help="MarkLogic user name."),
    password_ref: str = typer.Option(
        ...,
        "--password-ref",
        help=(
            "Secret REFERENCE for the password: ssm:<path>, env:<VAR> or "
            "profile:<name>. Never a literal password."
        ),
    ),
    port: int = typer.Option(8000, "--port", help="App-Services / query port."),
    manage_port: int = typer.Option(8002, "--manage-port", help="Management port."),
    rest_port: int | None = typer.Option(
        None, "--rest-port", help="REST instance port. No default; unset unless given."
    ),
    auth_method: str = typer.Option("digest", "--auth-method", help="digest or basic."),
) -> None:
    """Add a profile, so the operator does not edit config.toml by hand.

    The tool stores the password as a reference, never as a literal.
    """
    if not password_ref.startswith(secrets.SCHEMES):
        schemes = ", ".join(secrets.SCHEMES)
        typer.echo(
            f"Error: --password-ref must be a reference beginning with one of: "
            f"{schemes}. It looks like a literal password was passed. Literals "
            "are refused rather than written to disk: a password in config.toml "
            "outlives the command that put it there, and lands in backups and "
            "screen shares. Export it and pass env:<VAR> instead.",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USAGE)

    path = config_path()
    try:
        existing = load_app_config(path) if path.exists() else None
    except ConfigurationError as e:
        typer.echo(f"Error: {e.message}", err=True)
        raise typer.Exit(code=e.exit_code) from None

    if existing is not None and profile in existing.profiles:
        typer.echo(
            f"Error: profile '{profile}' already exists in {path}. It is not "
            "overwritten, because that would silently repoint a profile other "
            "commands are using. Remove it by hand, or choose another name.",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USAGE)

    block = _profile_block(
        profile,
        host=host,
        port=port,
        manage_port=manage_port,
        rest_port=rest_port,
        username=username,
        password_ref=password_ref,
        auth_method=auth_method,
    )

    try:
        _verify_round_trip(
            block,
            {
                "host": host,
                "port": port,
                "manage_port": manage_port,
                "rest_port": rest_port,
                "username": username,
                "password": password_ref,
                "auth_method": auth_method,
            },
            profile,
        )
    except _RoundTripError as e:
        typer.echo(
            f"Error: refusing to write profile '{profile}' — {e}. Nothing was "
            "written. A config.toml that cannot be parsed back would break every "
            "command and could only be repaired by hand, which is what this "
            "command exists to avoid. Check the value for control characters.",
            err=True,
        )
        raise typer.Exit(code=ExitCode.USAGE) from None

    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with open(path, "a", encoding="utf-8") as f:
        if is_new:
            f.write(f'default_profile = "{profile}"\n')
        f.write(block)
    if is_new:
        path.chmod(0o600)

    typer.echo(f"Added profile '{profile}' to {path}")
    typer.echo(
        f"  password: stored by reference as {password_ref} — no literal written.",
        err=True,
    )


def _profile_block(name: str, **fields: object) -> str:
    """Render one `[profiles.<name>]` block.

    This writes TOML by hand, because the tool has no TOML writer. Every value is a name, a
    port or a reference, so nothing sensitive passes through here.
    """
    password_ref = fields["password_ref"]
    lines = [
        f"\n[profiles.{name}]",
        f"host = {_toml_str(fields['host'])}",
        f"port = {int(fields['port'])}",  # type: ignore[call-overload]
        f"manage_port = {int(fields['manage_port'])}",  # type: ignore[call-overload]
    ]
    if fields["rest_port"] is not None:
        lines.append(f"rest_port = {int(fields['rest_port'])}")  # type: ignore[call-overload]
    lines += [
        f"username = {_toml_str(fields['username'])}",
        f"password = {_toml_str(password_ref)}",
        f"auth_method = {_toml_str(fields['auth_method'])}",
        "",
    ]
    return "\n".join(lines)


# TOML basic strings forbid raw control characters. Escaping only backslash and
# quote let `--username $'ad\nmin'` write a literal newline, after which tomllib
# refuses the whole file and every command dies at exit 1 — recoverable only by
# hand-editing config.toml, which is the exact thing `config add` exists to avoid.
_TOML_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _toml_str(value: object) -> str:
    """Render a TOML basic string, escaping everything TOML forbids raw."""
    out: list[str] = ['"']
    for char in str(value):
        if char in _TOML_ESCAPES:
            out.append(_TOML_ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _verify_round_trip(block: str, expected: dict[str, object], name: str) -> None:
    """Parse the rendered block. Refuse if the values do not come back.

    Nothing reaches the disk unless tomllib returns the values that went in. A value this
    renderer cannot represent becomes a refusal.
    """
    try:
        parsed = tomllib.loads(block)
    except tomllib.TOMLDecodeError as e:
        raise _RoundTripError(f"the rendered profile is not valid TOML ({e})") from e

    section = parsed.get("profiles", {}).get(name)
    if section is None:
        raise _RoundTripError("the rendered profile did not parse back")

    for key, value in expected.items():
        if value is None:
            continue
        if section.get(key) != value:
            raise _RoundTripError(
                f"'{key}' did not survive the round trip "
                f"({section.get(key)!r} != {value!r})"
            )


class _RoundTripError(Exception):
    """A rendered profile that does not parse back to what went in."""
