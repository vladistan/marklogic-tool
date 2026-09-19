"""Tests for the unconstrained-delete safety guard on `eval` sources."""

from marklogic_tool.core.safety import dangerous_eval_reason


def test_collection_delete_is_flagged():
    reason = dangerous_eval_reason("xdmp:collection-delete('foo')")
    assert reason is not None
    assert "collection-delete" in reason


def test_database_delete_is_flagged():
    reason = dangerous_eval_reason("xdmp:database-delete(xdmp:database('taxes'))")
    assert reason is not None


def test_directory_delete_root_is_flagged():
    reason = dangerous_eval_reason('xdmp:directory-delete("/", "infinity")')
    assert reason is not None
    assert "root directory" in reason


def test_directory_delete_scoped_is_not_flagged():
    reason = dangerous_eval_reason('xdmp:directory-delete("/archive/2020/", "infinity")')
    assert reason is None


def test_cts_delete_with_no_args_is_flagged():
    reason = dangerous_eval_reason("cts:search-delete()")
    assert reason is not None


def test_node_delete_on_root_is_flagged():
    reason = dangerous_eval_reason("xdmp:node-delete(/)")
    assert reason is not None
    assert "node-delete" in reason


def test_node_delete_scoped_is_not_flagged():
    reason = dangerous_eval_reason("xdmp:node-delete(doc('/foo.xml')/bar)")
    assert reason is None


def test_embedded_unconstrained_sparql_update_is_flagged():
    reason = dangerous_eval_reason(
        "sem:sparql-update('DELETE WHERE { ?s ?p ?o }')"
    )
    assert reason is not None
    assert "sem:sparql-update" in reason


def test_embedded_constrained_sparql_update_is_not_flagged():
    reason = dangerous_eval_reason(
        "sem:sparql-update('DELETE WHERE { ?s a <http://example.org/Foo> }')"
    )
    assert reason is None


def test_ordinary_xquery_is_not_flagged():
    reason = dangerous_eval_reason("xdmp:document-get('/foo.xml')")
    assert reason is None


def test_ordinary_javascript_is_not_flagged():
    reason = dangerous_eval_reason("cts.doc('/foo.json')")
    assert reason is None
