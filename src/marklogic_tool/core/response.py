"""Multipart/mixed response parser for MarkLogic /v1/eval responses."""

from __future__ import annotations

from dataclasses import dataclass

from marklogic_tool.core.exceptions import ParseError


@dataclass(frozen=True, slots=True)
class EvalResult:
    """A single result from a MarkLogic eval response."""

    primitive_type: str
    value: str
    content_type: str


def parse_multipart_mixed(content_type: str, body: bytes) -> list[EvalResult]:
    """Parse a multipart/mixed response into a list of EvalResult."""
    boundary = _extract_boundary(content_type)
    if not boundary:
        raise ParseError("Missing boundary in Content-Type header")

    parts = _split_parts(body, boundary)
    return [_parse_part(part) for part in parts]


def _extract_boundary(content_type: str) -> str | None:
    for segment in content_type.split(";"):
        segment = segment.strip()
        if segment.lower().startswith("boundary="):
            return segment[len("boundary=") :]
    return None


def _split_parts(body: bytes, boundary: str) -> list[bytes]:
    delimiter = f"--{boundary}".encode()
    terminator = f"--{boundary}--".encode()

    segments = body.split(delimiter)
    parts: list[bytes] = []

    for segment in segments[1:]:
        stripped = segment.strip()
        if not stripped or stripped == b"--":
            continue
        if stripped.startswith(b"--"):
            continue
        clean = segment
        if clean.endswith(terminator):
            clean = clean[: -len(terminator)]
        parts.append(clean)

    return parts


def _parse_part(raw: bytes) -> EvalResult:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ParseError("Cannot decode response part as UTF-8") from e

    header_end = text.find("\r\n\r\n")
    if header_end == -1:
        header_end = text.find("\n\n")
        if header_end == -1:
            raise ParseError("Malformed multipart part: no header/body separator")
        separator_len = 2
    else:
        separator_len = 4

    header_block = text[:header_end]
    body = text[header_end + separator_len :]

    primitive_type = "string"
    part_content_type = "text/plain"

    for line in header_block.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            continue
        name, _, val = line.partition(":")
        name_lower = name.strip().lower()
        if name_lower == "x-primitive":
            primitive_type = val.strip()
        elif name_lower == "content-type":
            part_content_type = val.strip()

    return EvalResult(
        primitive_type=primitive_type,
        value=body.rstrip("\r\n"),
        content_type=part_content_type,
    )
