"""The identity the tool makes a request as. The caller injects it.

`--as-user` authenticates directly as that user. It never amps. Every failure is a refusal,
because the only fallback is the admin credential.
"""

import os
from dataclasses import dataclass

from pydantic import SecretStr

from marklogic_tool.core import secrets
from marklogic_tool.core.config import ProfileSettings
from marklogic_tool.core.exceptions import ConfigurationError

PROFILE_SOURCE = "profile"
ENV_OVERLAY_SOURCE = "env:ML_USERNAME"


@dataclass(frozen=True, slots=True)
class Credential:
    username: str
    secret: SecretStr
    source: str

    def __repr__(self) -> str:
        return f"Credential(username={self.username!r}, source={self.source!r})"

    def __str__(self) -> str:
        return self.__repr__()


def resolve_identity(
    profile: ProfileSettings,
    *,
    as_user: str | None = None,
    as_user_secret: str | None = None,
) -> Credential:
    if as_user is None:
        return Credential(
            username=profile.username,
            secret=profile.password,
            source=(
                ENV_OVERLAY_SOURCE if "ML_USERNAME" in os.environ else PROFILE_SOURCE
            ),
        )

    reference = as_user_secret or profile.identities.get(as_user)
    if reference is None:
        declared = ", ".join(sorted(profile.identities)) or "none"
        raise ConfigurationError(
            f"No secret reference is declared for identity '{as_user}', so it "
            "cannot be authenticated as. Declared identities: "
            f"{declared}. Add it under [profiles.<name>.identities], or pass "
            "--as-user-secret with a reference. This is refused rather than "
            "resolved with the profile credential."
        )

    return Credential(
        username=as_user,
        secret=secrets.resolve(reference, identities=profile.identities),
        source=f"as-user:{as_user}",
    )
