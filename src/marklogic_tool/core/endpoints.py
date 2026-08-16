"""Turn an endpoint intent and an identity into a configured client.

App-Services and the REST instance address different content databases. So a refusal names
the missing key instead of picking a port number.
"""

from enum import StrEnum

from marklogic_tool.core.client import MarkLogicClient
from marklogic_tool.core.config import ProfileSettings
from marklogic_tool.core.exceptions import ConfigurationError
from marklogic_tool.core.identity import Credential
from marklogic_tool.core.manage_client import ManageClient


class Endpoint(StrEnum):
    QUERY = "query"
    REST = "rest"
    MANAGE = "manage"


def client_for(
    profile: ProfileSettings,
    endpoint: Endpoint,
    credential: Credential,
    timeout: int | None = None,
) -> MarkLogicClient | ManageClient:
    port = require_port(profile, endpoint)

    if endpoint is Endpoint.MANAGE:
        return ManageClient(
            profile,
            credential=credential,
            port=port,
            timeout=timeout,
        )

    return MarkLogicClient(
        profile,
        credential=credential,
        port=port,
        timeout=timeout,
    )


def require_port(profile: ProfileSettings, endpoint: Endpoint) -> int:
    """Resolve the port for an endpoint. Refuse, and name the missing key.

    This is public, so a command can check the endpoint before it resolves a secret.
    """
    if endpoint is Endpoint.MANAGE:
        return profile.manage_port
    if endpoint is Endpoint.QUERY:
        return profile.port
    return _rest_port(profile)


def _rest_port(profile: ProfileSettings) -> int:
    if profile.rest_port is None:
        raise ConfigurationError(
            f"This command reads over the REST instance, but profile key "
            f"'rest_port' is not set for host '{profile.host}'. It is refused "
            "rather than resolved from 'port', because the REST instance and "
            "App-Services address different content databases and the count "
            "would be of the wrong corpus. Set rest_port in the profile, or "
            "export ML_REST_PORT."
        )
    return profile.rest_port
