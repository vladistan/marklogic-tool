"""TTY detection and output format auto-selection."""

import sys

from marklogic_tool.cli import OutputFormat


def detect_output_format() -> OutputFormat:
    """Auto-select output format based on whether stdout is a TTY."""
    if sys.stdout.isatty():
        return OutputFormat.table
    return OutputFormat.json
