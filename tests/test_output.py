"""Tests for output layer — TTY detection, formatters, and rendering."""

import json
import sys
from unittest.mock import patch

from marklogic_tool.cli import OutputFormat
from marklogic_tool.output.detect import detect_output_format
from marklogic_tool.output.formatters import (
    format_json,
    format_raw,
    format_table,
    render,
)

# --- TTY Detection ---


def test_detect_returns_table_for_tty():
    with patch.object(sys.stdout, "isatty", return_value=True):
        assert detect_output_format() == OutputFormat.table


def test_detect_returns_json_for_piped():
    with patch.object(sys.stdout, "isatty", return_value=False):
        assert detect_output_format() == OutputFormat.json


# --- JSON Formatter ---


def test_format_json_produces_valid_json():
    data = [{"name": "doc1", "uri": "/test.xml"}]
    result = format_json(data)
    parsed = json.loads(result)
    assert parsed == data


def test_format_json_is_indented():
    data = [{"key": "value"}]
    result = format_json(data)
    assert "\n" in result
    assert "  " in result


def test_format_json_empty_list():
    result = format_json([])
    assert json.loads(result) == []


def test_format_json_handles_non_serializable():
    from datetime import datetime

    data = [{"ts": datetime(2025, 1, 1, 12, 0)}]
    result = format_json(data)
    parsed = json.loads(result)
    assert "2025" in parsed[0]["ts"]


# --- Raw Formatter ---


def test_format_raw_passes_through():
    content = "<doc>hello</doc>"
    assert format_raw(content) == content


def test_format_raw_preserves_whitespace():
    content = "  indented\n\tnewline"
    assert format_raw(content) == content


# --- Table Formatter ---


def test_format_table_renders_headers():
    data = [{"Name": "doc1", "URI": "/test.xml"}]
    result = format_table(data)
    assert "Name" in result
    assert "URI" in result


def test_format_table_renders_values():
    data = [{"Name": "doc1", "URI": "/test.xml"}]
    result = format_table(data)
    assert "doc1" in result
    assert "/test.xml" in result


def test_format_table_multiple_rows():
    data = [
        {"id": "1", "name": "first"},
        {"id": "2", "name": "second"},
    ]
    result = format_table(data)
    assert "first" in result
    assert "second" in result


def test_format_table_empty_data():
    assert format_table([]) == ""


def test_format_table_with_title():
    data = [{"column_name": "value_here"}]
    result = format_table(data, title="Results")
    assert "Results" in result


# --- Render Function ---


def test_render_json_to_stdout(capsys):
    data = [{"key": "value"}]
    render(data, "json")
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed == data


def test_render_raw_string_to_stdout(capsys):
    render("raw content here", "raw")
    captured = capsys.readouterr()
    assert captured.out.strip() == "raw content here"


def test_render_table_to_stdout(capsys):
    data = [{"Name": "test", "Value": "123"}]
    render(data, "table")
    captured = capsys.readouterr()
    assert "Name" in captured.out
    assert "test" in captured.out


def test_render_table_string_to_stdout(capsys):
    render("plain text", "table")
    captured = capsys.readouterr()
    assert "plain text" in captured.out
