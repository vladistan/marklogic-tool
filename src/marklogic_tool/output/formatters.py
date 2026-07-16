"""Output formatters — Rich tables, JSON, and raw passthrough."""

import json
from typing import Any

from rich.console import Console
from rich.table import Table


def format_json(data: list[dict[str, Any]]) -> str:
    """Format data as indented JSON suitable for jq."""
    return json.dumps(data, indent=2, default=str)


def format_raw(content: str) -> str:
    """Pass content through unchanged."""
    return content


def format_table(
    data: list[dict[str, Any]],
    *,
    title: str | None = None,
) -> str:
    """Format data as a Rich table and return as a string."""
    if not data:
        return ""

    console = Console()
    table = Table(show_header=True, header_style="bold", title=title)

    headers = list(data[0].keys())
    for header in headers:
        table.add_column(header)

    for row in data:
        table.add_row(*(str(row.get(h, "")) for h in headers))

    with console.capture() as capture:
        console.print(table)
    return capture.get().rstrip()


def render(
    data: list[dict[str, Any]] | str,
    fmt: str,
    *,
    title: str | None = None,
) -> None:
    """Render data to stdout in the specified format."""
    if fmt == "json" or fmt == "raw":
        if isinstance(data, str):
            print(data)
        else:
            print(format_json(data))
    elif fmt == "table":
        if isinstance(data, str):
            print(data)
        else:
            output = format_table(data, title=title)
            if output:
                print(output)
