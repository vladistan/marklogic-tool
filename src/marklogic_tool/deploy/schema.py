"""The declaration schema. It uses a user vocabulary with no Manage names.

An absent key is not an explicit empty. `default_permissions: []` is subtractive drift. An
omitted key means do not touch. `model_fields_set` keeps that difference.
"""

from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)

from marklogic_tool.core.secrets import GRAMMAR, is_valid_reference
from marklogic_tool.deploy.errors import (
    DeclarationError,
    DeniedPropertyError,
    SecretReferenceError,
)
from marklogic_tool.deploy.mapping import denied_properties_matching

"""Secret grammar is owned by `core/secrets.py`.

This module validates only that a password is a well-formed *reference*, so a literal
can be refused at schema load; it never resolves one. It once carried its own copy of the
accepted schemes; now that `is_valid_reference`
exists — written, per its own docstring, so schema validation has one implementation to
call rather than its own copy — the duplicate is retired. A security grammar that lives
in two places is a rule only where it happens to be imported.
"""


def _reject_denied(values: list[str] | dict[str, Any], hatch: str, owner: str) -> None:
    """Refuse an escape hatch that reaches a security or data-affecting property."""
    for entry in values:
        hits = denied_properties_matching(entry)
        if hits:
            named = ", ".join(hits)
            raise DeniedPropertyError(
                f"Failed to load app server {owner!r}: "
                f"`{hatch}` entry {entry!r} covers the protected "
                f"{'property' if len(hits) == 1 else 'properties'} {named}. "
                f"Refusing at schema load rather than at apply time, because "
                f"suppressing a security or data-affecting property would reproduce "
                f"the very drift this tool exists to detect. "
                f"Narrow the pattern so it cannot match {named}."
            )


class PermissionSpec(BaseModel):
    """One `{role, capabilities}` pair inside `default_permissions`."""

    model_config = ConfigDict(extra="forbid")

    role: str
    capabilities: list[str]


class RoleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    # A role create-body key that a real deployment sets on every role. It was mappable
    # but not declarable until a full declaration was loaded and surfaced the gap.
    description: str | None = None
    inherits: list[str] = []
    default_permissions: list[PermissionSpec] = []


class UserSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    roles: list[str] = []
    password: str | None = None
    default_permissions: list[PermissionSpec] = []

    @field_validator("password")
    @classmethod
    def _require_secret_reference(cls, value: str | None) -> str | None:
        # Raises SecretReferenceError, not ValueError, so pydantic propagates it
        # untouched: a wrapped ValidationError would bury the guidance, and this
        # refusal must never tempt an operator into pasting the literal back in.
        if value is None:
            return None
        if is_valid_reference(value):
            return value
        raise SecretReferenceError(
            "Failed to load a user's password: it is not a usable secret reference. "
            "Refusing so the secret never has to live in the declaration, where it "
            "would reach version control, and so an empty reference is never resolved "
            "to an empty password. "
            f"Use a reference of the form {GRAMMAR}."
        )


class DatabaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    schema_database: str | None = None
    # first-class fields, not entries in a free-form dict. A free-form dict sits
    # OUTSIDE the deny-list's reach, which is the bypass shape — and `uri_lexicon`
    # is a prerequisite of the exhaustive unpermissioned scan, so a server this tool
    # deploys must be able to declare it or verify cannot gate what deploy produced.
    triple_index: bool | None = None
    collection_lexicon: bool | None = None
    uri_lexicon: bool | None = None
    directory_creation: str | None = None
    indexes: dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def _guide_misplaced_default_permissions(cls, data: Any) -> Any:
        # `extra="forbid"` would already refuse this, but with a generic message.
        # Default permissions are a role/user concept; saying so is the difference
        # between a refusal an operator can act on and one they have to decode.
        if isinstance(data, dict) and "default_permissions" in data:
            name = data.get("name", "<unnamed>")
            raise DeclarationError(
                f"Failed to load database {name!r}: it declares "
                f"`default_permissions`, which is not a database property. "
                f"Refusing rather than ignoring the key, because an operator who "
                f"believes permissions are being set here would ship a database "
                f"whose documents get none. "
                f"Declare `default_permissions` on the `roles:` or `users:` that "
                f"should carry it."
            )
        return data


class AppServerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    port: int | None = None
    database: str | None = None
    modules_database: str | None = None
    authentication: str | None = None
    ignore_properties: list[str] = []
    extra_properties: dict[str, Any] = {}

    @model_validator(mode="after")
    def _enforce_deny_list(self) -> "AppServerSpec":
        _reject_denied(self.ignore_properties, "ignore_properties", self.name)
        _reject_denied(self.extra_properties, "extra_properties", self.name)
        return self


class RestApiSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    port: int | None = None
    database: str | None = None
    modules_database: str | None = None


class TargetSpec(BaseModel):
    """The host allowlist. Required: pre-flight refuses a host absent from it."""

    model_config = ConfigDict(extra="forbid")

    hosts: list[str]


class Declaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    target: TargetSpec
    rest_apis: list[RestApiSpec] = []
    databases: list[DatabaseSpec] = []
    app_servers: list[AppServerSpec] = []
    roles: list[RoleSpec] = []
    users: list[UserSpec] = []


def load_declaration(
    raw: dict[str, Any], *, source: str = "declaration"
) -> Declaration:
    """Validate a parsed mapping against the vocabulary.

    The guided refusals raise their own error types from the validators and pass through
    here. Everything else arrives as a `ValidationError`, and the tool wraps it once.
    """
    try:
        return Declaration.model_validate(raw)
    except ValidationError as exc:
        raise DeclarationError(
            f"Failed to validate the declaration {source}: {exc.error_count()} "
            f"problem(s) found.\n{exc}\n"
            f"Refusing to plan against a declaration the tool cannot read exactly. "
            f"Correct the fields named above."
        ) from exc
