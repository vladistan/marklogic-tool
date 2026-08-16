"""The verification wire contract, rendered for a person or for an agent.

Checkpoints gate on this JSON, so it is a versioned contract. It carries `identity_source`,
because an identity without its source is not auditable.
"""

from typing import Any

from marklogic_tool.output.formatters import format_json, format_table
from marklogic_tool.verify.measure import TRUNCATED, Measurement

UNPERMISSIONED_SCHEMA = "marklogic-tool/unpermissioned/1"

ELAPSED_PLACEHOLDER = "<elapsed>"


def build_report(
    measurement: Measurement,
    *,
    identity: str,
    identity_source: str,
    endpoint: str,
    gate: bool,
    exit_code: int,
) -> dict[str, Any]:
    """Assemble the contract. Every count arrives with its provenance."""
    return {
        "schema": UNPERMISSIONED_SCHEMA,
        "database": measurement.database,
        "identity": identity,
        "identity_source": identity_source,
        "endpoint": endpoint,
        "method": measurement.method,
        "evidence": measurement.evidence,
        "findings_are_sound": measurement.findings_are_sound,
        "unpermissioned": measurement.unpermissioned,
        "total_scanned": measurement.total_scanned,
        "uris": list(measurement.uris),
        "uris_requested": measurement.uris_requested,
        "uris_listing": measurement.uris_listing,
        "elapsed_seconds": round(measurement.elapsed_seconds, 3),
        "gate": gate,
        "exit_code": exit_code,
    }


def normalise_for_golden(report: dict[str, Any]) -> dict[str, Any]:
    """Replace the one field that cannot be reproduced byte-for-byte."""
    return {**report, "elapsed_seconds": ELAPSED_PLACEHOLDER}


def render(report: dict[str, Any], output_fmt: str) -> None:
    if output_fmt == "json":
        print(format_json([report]))
        return

    rows = [
        {"field": "database", "value": str(report["database"])},
        {"field": "identity", "value": str(report["identity"])},
        {"field": "identity source", "value": str(report["identity_source"])},
        {"field": "endpoint", "value": str(report["endpoint"])},
        {"field": "method", "value": str(report["method"])},
        {"field": "evidence", "value": "yes" if report["evidence"] else "no"},
        {"field": "unpermissioned", "value": str(report["unpermissioned"])},
        {"field": "total scanned", "value": str(report["total_scanned"])},
    ]
    output = format_table(rows, title="Unpermissioned Documents")
    if output:
        print(output)

    uris = report["uris"]
    if uris:
        for uri in uris:
            print(uri)
        if report["uris_listing"] == TRUNCATED:
            print(
                f"... {len(uris)} of {report['unpermissioned']} offending URIs shown."
            )
