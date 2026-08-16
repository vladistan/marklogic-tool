"""Declaration loader.

The load-bearing case is the duplicated `roles:` block: plain YAML resolves it by
last-key-wins and silently discards the first block, which would drop role
definitions out of a security declaration without a word.
"""

import pytest

from marklogic_tool.core.exceptions import ExitCode
from marklogic_tool.deploy.errors import (
    DeclarationError,
    DeclarationUsageError,
    DuplicateKeyError,
)
from marklogic_tool.deploy.loader import parse_declaration, read_declaration_file

VALID = """
version: 1
target:
  hosts: [ml-01.example.test]
databases:
  - name: content
roles:
  - name: writer
"""


def test_valid_declaration_loads():
    data = parse_declaration(VALID, source="valid.yaml")
    assert data["version"] == 1
    assert data["target"]["hosts"] == ["ml-01.example.test"]
    assert data["databases"][0]["name"] == "content"


def test_duplicate_top_level_key_is_rejected_with_line_number():
    text = (
        "version: 1\ntarget:\n  hosts: [h]\nroles:\n  - name: a\nroles:\n  - name: b\n"
    )
    with pytest.raises(DuplicateKeyError) as excinfo:
        parse_declaration(text, source="dup.yaml")
    message = str(excinfo.value)
    assert "'roles'" in message
    assert "line 6" in message
    assert "dup.yaml" in message


def test_duplicate_roles_block_is_not_last_key_wins():
    """The first block must not be silently discarded."""
    text = "version: 1\ntarget:\n  hosts: [h]\nroles:\n  - name: first\nroles:\n  - name: second\n"
    with pytest.raises(DuplicateKeyError):
        parse_declaration(text, source="dup.yaml")


def test_duplicate_nested_key_is_rejected():
    text = "version: 1\ntarget:\n  hosts: [a]\n  hosts: [b]\n"
    with pytest.raises(DuplicateKeyError) as excinfo:
        parse_declaration(text, source="nested.yaml")
    assert "'hosts'" in str(excinfo.value)
    assert "line 4" in str(excinfo.value)


def test_duplicate_key_deep_inside_a_sequence_is_rejected():
    text = "version: 1\ntarget:\n  hosts: [h]\nroles:\n  - name: a\n    name: b\n"
    with pytest.raises(DuplicateKeyError) as excinfo:
        parse_declaration(text, source="deep.yaml")
    assert "'name'" in str(excinfo.value)


def test_duplicate_key_error_is_config_shaped():
    text = "a: 1\na: 2\n"
    with pytest.raises(DuplicateKeyError) as excinfo:
        parse_declaration(text, source="d.yaml")
    assert excinfo.value.exit_code == ExitCode.INPUT


def test_invalid_yaml_is_distinct_from_duplicate_key():
    with pytest.raises(DeclarationError) as excinfo:
        parse_declaration("version: 1\n  bad: [indent\n", source="broken.yaml")
    assert not isinstance(excinfo.value, DuplicateKeyError)
    assert "broken.yaml" in str(excinfo.value)


def test_missing_file_is_invocation_shaped_and_invalid_file_is_config_shaped(tmp_path):
    """The two failures must be distinguishable, and by exit code, not just wording."""
    missing = tmp_path / "nope.yaml"
    with pytest.raises(DeclarationUsageError) as missing_err:
        read_declaration_file(missing)
    assert missing_err.value.exit_code == ExitCode.USAGE
    assert "no such file" in str(missing_err.value)

    invalid = tmp_path / "bad.yaml"
    invalid.write_text("version: 1\n  bad: [indent\n", encoding="utf-8")
    with pytest.raises(DeclarationError) as invalid_err:
        read_declaration_file(invalid)
    assert invalid_err.value.exit_code == ExitCode.INPUT


def test_directory_instead_of_file_is_invocation_shaped(tmp_path):
    with pytest.raises(DeclarationUsageError):
        read_declaration_file(tmp_path)


def test_no_yaml_tag_can_construct_an_arbitrary_python_object():
    text = "version: !!python/object/apply:os.system ['echo pwned']\n"
    with pytest.raises(DeclarationError):
        parse_declaration(text, source="evil.yaml")


def test_python_name_tag_is_also_refused():
    text = "version: !!python/name:os.system\n"
    with pytest.raises(DeclarationError):
        parse_declaration(text, source="evil.yaml")


def test_empty_document_is_refused_rather_than_treated_as_empty_declaration():
    with pytest.raises(DeclarationError) as excinfo:
        parse_declaration("", source="empty.yaml")
    assert "empty" in str(excinfo.value)


def test_non_mapping_top_level_is_refused():
    with pytest.raises(DeclarationError) as excinfo:
        parse_declaration("- just\n- a list\n", source="list.yaml")
    assert "list" in str(excinfo.value)


def test_reads_a_real_file(tmp_path):
    path = tmp_path / "decl.yaml"
    path.write_text(VALID, encoding="utf-8")
    assert read_declaration_file(path)["version"] == 1


def test_merge_key_cannot_smuggle_in_a_duplicate():
    text = "base: &base\n  name: a\ntarget:\n  <<: *base\n  name: b\n"
    with pytest.raises(DuplicateKeyError):
        parse_declaration(text, source="merge.yaml")


def test_merge_key_refusal_explains_itself():
    """standard YAML permits this override, so the refusal must say it is meant."""
    text = "base: &base\n  name: a\ntarget:\n  <<: *base\n  name: b\n"
    with pytest.raises(DuplicateKeyError) as excinfo:
        parse_declaration(text, source="merge.yaml")
    assert "`<<`" in str(excinfo.value)


def test_plain_duplicate_does_not_mention_merge_keys():
    """The clause must vary with the cause, or it is decoration rather than a message."""
    with pytest.raises(DuplicateKeyError) as excinfo:
        parse_declaration("a: 1\na: 2\n", source="plain.yaml")
    assert "`<<`" not in str(excinfo.value)
