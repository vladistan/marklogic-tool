"""Errors the deploy decision core raises.

These inherit from the refusal hierarchy in `core/exceptions.py`, so the exit-code mapping
lives in one place. They stay named, so the deploy layer catches a declaration problem alone.
"""

from marklogic_tool.core.exceptions import (
    BlockedError,
    ConfigurationError,
    InvocationError,
)


class DataAffectingRefusal(BlockedError):
    """A change destroys or orphans data, so the tool refuses it.

    This differs from a `blocked` status. Blocked means the operator can fix the declaration
    or pass `--force`. This refusal `--force` never reaches.
    """


class DeclarationUsageError(InvocationError):
    """Invocation-shaped refusal: the operator named something that is not there."""


class DeclarationError(ConfigurationError):
    """Config-shaped refusal: the declaration itself is wrong."""


class DuplicateKeyError(DeclarationError):
    """A mapping declared the same key twice.

    This is the feature's own failure mode: YAML's last-key-wins silently
    discards a whole `roles:` block, so it is refused rather than resolved.
    """


class DeniedPropertyError(DeclarationError):
    """An escape hatch tried to reach a security or data-affecting property.

    refusal triggers when a glob INTERSECTS the deny-list, not only
    on exact match, so `"*"` cannot bypass it.
    """


class SecretReferenceError(DeclarationError):
    """A password was given as a literal, or as an unusable reference."""


class UnmappedPropertyError(DeclarationError):
    """A property has no Manage name in this tool, so the tool refuses to map it.

    A guessed Manage name in a security mapping makes a declaration stop doing what it says.
    """


class DependencyCycleError(DeclarationError):
    """The declared objects form a dependency cycle."""


class DanglingReferenceError(DeclarationError):
    """A declared object references something that is not declared."""
