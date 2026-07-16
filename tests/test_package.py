"""Tests for package import and version."""


def test_package_imports():
    import marklogic_tool

    assert marklogic_tool.__version__


def test_version_format():
    from marklogic_tool import __version__

    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
