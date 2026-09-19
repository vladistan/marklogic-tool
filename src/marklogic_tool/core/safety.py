"""Detect XQuery/JavaScript `eval` sources that act with no scoping constraint.

Heuristic text scan, not an XQuery/JS parser. A false positive costs a
`--force` re-run. A false negative slips through uncaught.
"""

import re

_DROP_OR_CLEAR = re.compile(r"\b(DROP|CLEAR)\b", re.IGNORECASE)
_DELETE = re.compile(r"\bDELETE\b", re.IGNORECASE)
_WHERE_OPEN = re.compile(r"\bWHERE\s*\{", re.IGNORECASE)

_ROOT_SCOPED_CALL = re.compile(
    r"\bxdmp:(collection-delete|database-delete)\s*\(", re.IGNORECASE
)
_DIRECTORY_DELETE_ROOT = re.compile(
    r"""xdmp:directory-delete\s*\(\s*["']/["']\s*,\s*["']infinity["']""",
    re.IGNORECASE,
)
_CTS_DELETE_NO_ARGS = re.compile(r"\bcts:[\w-]*-delete\s*\(\s*\)", re.IGNORECASE)
_NODE_DELETE = re.compile(r"\bxdmp:node-delete\s*\(\s*([^)]*)\)", re.IGNORECASE)
_NODE_DELETE_ROOT_ARGS = {"", ".", "/", "/*", "collection()", "doc()"}
_SPARQL_UPDATE_CALL = re.compile(
    r"""\bsem:sparql-update\s*\(\s*["'](.*?)["']""", re.IGNORECASE | re.DOTALL
)
_BOUND_TERM = re.compile(r"<[^>]*>|\"[^\"]*\"|'[^']*'|\b\w+:\w+")


def dangerous_eval_reason(source: str) -> str | None:
    """Return why `source` looks unconstrained, or None if it looks fine."""
    if _ROOT_SCOPED_CALL.search(source):
        return (
            "calls xdmp:collection-delete or xdmp:database-delete, which "
            "removes an entire collection/database"
        )

    if _DIRECTORY_DELETE_ROOT.search(source):
        return 'calls xdmp:directory-delete("/", "infinity"), which removes the entire root directory'

    if _CTS_DELETE_NO_ARGS.search(source):
        return "calls a cts:*-delete function with no constraining query argument"

    node_delete = _NODE_DELETE.search(source)
    if node_delete and node_delete.group(1).strip() in _NODE_DELETE_ROOT_ARGS:
        return (
            "calls xdmp:node-delete with no constraining path beyond the "
            "document/collection root"
        )

    sparql_call = _SPARQL_UPDATE_CALL.search(source)
    if sparql_call:
        reason = _unconstrained_sparql_reason(sparql_call.group(1))
        if reason:
            return f"embedded sem:sparql-update: {reason}"

    return None


def _unconstrained_sparql_reason(update_text: str) -> str | None:
    drop_or_clear = _DROP_OR_CLEAR.search(update_text)
    if drop_or_clear:
        verb = drop_or_clear.group(1).upper()
        return f"{verb} removes an entire graph with no per-triple constraint"

    if not _DELETE.search(update_text):
        return None

    body = _where_body(update_text)
    if body is None:
        return None

    if _BOUND_TERM.search(body):
        return None

    if any(clause.strip() for clause in re.split(r"[.;{}]", body) if clause.split()):
        return (
            "DELETE WHERE matches every triple via an all-variable "
            "pattern with no bound term"
        )

    return None


def _where_body(text: str) -> str | None:
    match = _WHERE_OPEN.search(text)
    if match is None:
        return None
    depth = 0
    start = match.end() - 1
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i]
    return None
