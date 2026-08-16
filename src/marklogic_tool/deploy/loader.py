"""Declaration loading.

The loader wraps `yaml.safe_load` in a SafeLoader that refuses a duplicate mapping key.
Plain YAML takes the last key silently, so a repeated `roles:` loses a whole block.

This module reads files only.
"""

from pathlib import Path
from typing import Any

import yaml

from marklogic_tool.deploy.errors import (
    DeclarationError,
    DeclarationUsageError,
    DuplicateKeyError,
)


class _DuplicateKey(Exception):
    """Internal marker carrying position, raised from inside the YAML constructor.

    The constructor cannot name the file, so the public entry points catch this and
    re-raise a `DuplicateKeyError` carrying file, line and key together.
    """

    def __init__(
        self, key: str, line: int, column: int, *, from_merge: bool = False
    ) -> None:
        super().__init__(key)
        self.key = key
        self.line = line
        self.column = column
        self.from_merge = from_merge


class DeclarationLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys at any nesting depth.

    Subclassing `SafeLoader` (never `Loader`/`UnsafeLoader`) is what guarantees no
    YAML tag can construct an arbitrary Python object.
    """


def _construct_mapping(
    loader: DeclarationLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    # a duplicate introduced through a `<<` merge key is refused on the
    # same footing as one written out literally. Standard YAML would let the explicit
    # key silently override the merged one; a deploy declaration is a security
    # surface, so a surprising-but-loud refusal beats a quiet key collision.
    # Noted before flattening, because flatten_mapping consumes the merge nodes.
    had_merge = any(
        key_node.tag == "tag:yaml.org,2002:merge" for key_node, _ in node.value
    )
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise _DuplicateKey(
                str(key),
                key_node.start_mark.line + 1,
                key_node.start_mark.column + 1,
                from_merge=had_merge,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


DeclarationLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def parse_declaration(text: str, *, source: str) -> dict[str, Any]:
    """Parse declaration YAML text into a plain mapping.

    `source` names the origin for error messages; it need not be a real path.
    """
    try:
        data = yaml.load(text, Loader=DeclarationLoader)  # noqa: S506 - SafeLoader subclass
    except _DuplicateKey as dup:
        # The merge-key clause is deliberate, not an oversight: without it the
        # refusal reads as a bug, because standard YAML permits the override.
        merge_clause = (
            "A key merged in through `<<` counts as a declaration here, so the "
            "usual YAML rule that an explicit key silently overrides a merged "
            "one is refused too. "
            if dup.from_merge
            else ""
        )
        raise DuplicateKeyError(
            f"Failed to read the declaration {source}: "
            f"the key {dup.key!r} is declared more than once "
            f"(line {dup.line}, column {dup.column}). "
            f"{merge_clause}"
            f"Refusing rather than applying YAML's last-one-wins rule, which would "
            f"silently discard the earlier block. "
            f"Remove or rename the duplicate {dup.key!r} entry."
        ) from None
    except yaml.YAMLError as exc:
        raise DeclarationError(
            f"Failed to parse the declaration {source}: {exc}. "
            f"Refusing to guess at the intended structure. "
            f"Fix the YAML syntax at the position named above."
        ) from exc

    if data is None:
        raise DeclarationError(
            f"Failed to read the declaration {source}: the document is empty. "
            f"Refusing to treat an empty file as an empty declaration, because that "
            f"would plan a no-op against a server the operator meant to configure. "
            f"Declare at least `version:` and `target.hosts:`."
        )
    if not isinstance(data, dict):
        raise DeclarationError(
            f"Failed to read the declaration {source}: the top level is "
            f"{type(data).__name__}, not a mapping. "
            f"Refusing to interpret it, because every declared kind is a top-level key. "
            f"Wrap the document in `version:` / `target:` / `databases:` keys."
        )
    return data


def read_declaration_file(path: Path) -> dict[str, Any]:
    """Read and parse a declaration file.

    A missing file is invocation-shaped (the operator named a path that is not
    there) and exits 2. Anything wrong with the content is config-shaped and exits 3.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise DeclarationUsageError(
            f"Failed to open the declaration {path}: no such file. "
            f"Refusing to continue rather than searching for a similarly named file. "
            f"Check the path given on the command line."
        ) from None
    except IsADirectoryError:
        raise DeclarationUsageError(
            f"Failed to open the declaration {path}: it is a directory, not a file. "
            f"Refusing to guess which file inside it was meant. "
            f"Name the declaration file itself."
        ) from None
    except OSError as exc:
        raise DeclarationError(
            f"Failed to read the declaration {path}: {exc.strerror}. "
            f"Refusing to continue without the declared state. "
            f"Check the file's permissions and try again."
        ) from exc
    except UnicodeDecodeError as exc:
        raise DeclarationError(
            f"Failed to decode the declaration {path}: it is not valid UTF-8. "
            f"Refusing to continue, because a mis-decoded object name would target "
            f"the wrong object. "
            f"Re-save the file as UTF-8."
        ) from exc

    return parse_declaration(text, source=str(path))
