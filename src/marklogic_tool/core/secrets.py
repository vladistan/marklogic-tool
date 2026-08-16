"""The single place a secret materialises.

Everything else handles `SecretStr`. The tool remembers each value in `_KNOWN_SECRETS`, so
`redact()` strips it from logs, Sentry events and the AWS CLI stderr.
"""

import os
import shutil
import subprocess
from collections.abc import Mapping

from pydantic import SecretStr

from marklogic_tool.core.exceptions import ConfigurationError

ENV_SCHEME = "env:"
SSM_SCHEME = "ssm:"
PROFILE_SCHEME = "profile:"

# The closed set of reference schemes. A value starting with one of these is a
# reference to be resolved; anything else is a literal.
SCHEMES = (ENV_SCHEME, SSM_SCHEME, PROFILE_SCHEME)

GRAMMAR = f"{ENV_SCHEME}VAR, {SSM_SCHEME}/path or {PROFILE_SCHEME}NAME"

REDACTED = "***"

_KNOWN_SECRETS: set[str] = set()


def register_secret(value: str) -> None:
    if value:
        _KNOWN_SECRETS.add(value)


def redact(text: str) -> str:
    """Strip every known secret value from outbound text.

    Longest first, so a secret that contains another secret as a substring does
    not leave a readable tail behind.
    """
    for known in sorted(_KNOWN_SECRETS, key=len, reverse=True):
        text = text.replace(known, REDACTED)
    return text


def is_valid_reference(reference: str) -> bool:
    """Check the reference grammar. Do not resolve it.

    The tool strips the location before the emptiness test, so `env:` and `env:"   "` both
    fail here rather than at resolve time.
    """
    return reference.startswith((ENV_SCHEME, SSM_SCHEME, PROFILE_SCHEME)) and bool(
        reference.partition(":")[2].strip()
    )


def _materialise(value: str, reference: str) -> SecretStr:
    stripped = value.strip()
    if not stripped:
        raise ConfigurationError(
            f"Secret reference '{reference}' resolved to an empty value. "
            "An empty password is refused rather than attempted. "
            "Set the referenced value, or point the reference somewhere else."
        )
    register_secret(stripped)
    return SecretStr(stripped)


def resolve(
    reference: str, *, identities: Mapping[str, str] | None = None
) -> SecretStr:
    """Resolve a SecretRef into a SecretStr, or refuse."""
    if reference.startswith(ENV_SCHEME):
        return _resolve_env(reference[len(ENV_SCHEME) :], reference)
    if reference.startswith(SSM_SCHEME):
        return _resolve_ssm(reference[len(SSM_SCHEME) :], reference)
    if reference.startswith(PROFILE_SCHEME):
        return _resolve_profile(reference[len(PROFILE_SCHEME) :], reference, identities)
    raise ConfigurationError(_grammar_refusal(reference))


def _grammar_refusal(reference: str) -> str:
    scheme, separator, _ = reference.partition(":")
    if separator:
        return f"Unknown secret scheme '{scheme}:'. Supported forms are {GRAMMAR}."
    return (
        "A literal secret value is refused; secrets are resolved by indirection "
        f"so they never reach argv, logs or config in plaintext. Use {GRAMMAR}."
    )


def _resolve_env(variable: str, reference: str) -> SecretStr:
    if variable not in os.environ:
        raise ConfigurationError(
            f"Environment variable '{variable}' is not set, so secret reference "
            f"'{reference}' cannot be resolved. Export '{variable}' and re-run."
        )
    return _materialise(os.environ[variable], reference)


def _resolve_profile(
    name: str, reference: str, identities: Mapping[str, str] | None
) -> SecretStr:
    if not identities:
        raise ConfigurationError(
            f"Secret reference '{reference}' needs an identities table, but the "
            "active profile declares none. Add an [profiles.<name>.identities] "
            f"entry mapping '{name}' to a secret reference ({GRAMMAR})."
        )
    if name not in identities:
        available = ", ".join(sorted(identities))
        raise ConfigurationError(
            f"Identity '{name}' is not declared in the profile identities table. "
            f"Declared identities: {available}."
        )
    return resolve(identities[name], identities=None)


def _resolve_ssm(path: str, reference: str) -> SecretStr:
    if shutil.which("aws") is None:
        raise ConfigurationError(
            f"Secret reference '{reference}' needs the 'aws' CLI, which is not on "
            "PATH. Install the AWS CLI v2, or use an env: reference instead."
        )

    completed = subprocess.run(  # noqa: S603 — fixed argv, no shell, secret is on stdout
        [
            "aws",
            "ssm",
            "get-parameter",
            "--name",
            path,
            "--with-decryption",
            "--query",
            "Parameter.Value",
            "--output",
            "text",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if completed.returncode != 0:
        raise ConfigurationError(
            f"Reading SSM parameter '{path}' failed "
            f"(aws exited {completed.returncode}): {redact(completed.stderr).strip()}. "
            "Check the parameter path and the caller's IAM permissions."
        )

    return _materialise(completed.stdout, reference)
