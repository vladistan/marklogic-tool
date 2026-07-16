"""Tests for multipart/mixed response parser."""

import pytest

from marklogic_tool.core.exceptions import ParseError
from marklogic_tool.core.response import EvalResult, parse_multipart_mixed

SINGLE_RESULT = (
    b"--ML_BOUNDARY\r\n"
    b"Content-Type: text/plain\r\n"
    b"X-Primitive: string\r\n"
    b"\r\n"
    b"Documents\r\n"
    b"--ML_BOUNDARY--\r\n"
)

MULTIPLE_RESULTS = (
    b"--ML_BOUNDARY\r\n"
    b"Content-Type: text/plain\r\n"
    b"X-Primitive: integer\r\n"
    b"\r\n"
    b"1\r\n"
    b"--ML_BOUNDARY\r\n"
    b"Content-Type: text/plain\r\n"
    b"X-Primitive: integer\r\n"
    b"\r\n"
    b"2\r\n"
    b"--ML_BOUNDARY\r\n"
    b"Content-Type: text/plain\r\n"
    b"X-Primitive: integer\r\n"
    b"\r\n"
    b"3\r\n"
    b"--ML_BOUNDARY--\r\n"
)

BOOLEAN_RESULT = (
    b"--SOMEBOUNDARY\r\n"
    b"Content-Type: text/plain\r\n"
    b"X-Primitive: boolean\r\n"
    b"\r\n"
    b"true\r\n"
    b"--SOMEBOUNDARY--\r\n"
)

JSON_RESULT = (
    b"--ML_BOUNDARY\r\n"
    b"Content-Type: application/json\r\n"
    b"X-Primitive: node()\r\n"
    b"\r\n"
    b'{"name": "test"}\r\n'
    b"--ML_BOUNDARY--\r\n"
)


def test_parse_single_result():
    ct = "multipart/mixed; boundary=ML_BOUNDARY"
    results = parse_multipart_mixed(ct, SINGLE_RESULT)

    assert len(results) == 1
    assert results[0].value == "Documents"
    assert results[0].primitive_type == "string"
    assert results[0].content_type == "text/plain"


def test_parse_multiple_results():
    ct = "multipart/mixed; boundary=ML_BOUNDARY"
    results = parse_multipart_mixed(ct, MULTIPLE_RESULTS)

    assert len(results) == 3
    assert results[0].value == "1"
    assert results[1].value == "2"
    assert results[2].value == "3"
    assert all(r.primitive_type == "integer" for r in results)


def test_parse_reads_x_primitive_header():
    ct = "multipart/mixed; boundary=SOMEBOUNDARY"
    results = parse_multipart_mixed(ct, BOOLEAN_RESULT)

    assert results[0].primitive_type == "boolean"
    assert results[0].value == "true"


def test_parse_reads_content_type_header():
    ct = "multipart/mixed; boundary=ML_BOUNDARY"
    results = parse_multipart_mixed(ct, JSON_RESULT)

    assert results[0].content_type == "application/json"
    assert results[0].primitive_type == "node()"


def test_parse_empty_response():
    ct = "multipart/mixed; boundary=ML_BOUNDARY"
    body = b"--ML_BOUNDARY--\r\n"
    results = parse_multipart_mixed(ct, body)

    assert results == []


def test_parse_malformed_no_boundary():
    ct = "multipart/mixed"
    with pytest.raises(ParseError, match="Missing boundary"):
        parse_multipart_mixed(ct, b"some data")


def test_parse_malformed_no_header_separator():
    ct = "multipart/mixed; boundary=ML_BOUNDARY"
    body = b"--ML_BOUNDARY\r\nno separator here\r\n--ML_BOUNDARY--\r\n"
    with pytest.raises(ParseError, match="no header/body separator"):
        parse_multipart_mixed(ct, body)


def test_eval_result_is_immutable():
    r = EvalResult(primitive_type="string", value="test", content_type="text/plain")
    with pytest.raises(AttributeError):
        r.value = "changed"
