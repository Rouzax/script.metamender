"""The shipped add-on must run on Python 3.9.

OSMC's Kodi 21 (Debian 11) runs Python 3.9, so the oldest Python the add-on
meets is older than the one the tests run on. These checks catch what 3.9
rejects without needing a 3.9 interpreter: syntax newer than 3.9, and a
`X | Y` type union evaluated at runtime. Annotations are fine, because every
module defers them with `from __future__ import annotations`.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SHIPPED = sorted(
    [ROOT / "default.py", ROOT / "service.py", ROOT / "context.py", ROOT / "context_details.py"]
    + list((ROOT / "resources").rglob("*.py"))
)
_FLOOR = (3, 9)


def _annotation_nodes(tree: ast.AST) -> set[int]:
    """ids of every node inside an annotation, which 3.9 never evaluates here."""
    inside: set[int] = set()
    for node in ast.walk(tree):
        parts = [getattr(node, "annotation", None), getattr(node, "returns", None)]
        if isinstance(node, ast.arguments):
            args = node.posonlyargs + node.args + node.kwonlyargs + [node.vararg, node.kwarg]
            parts += [a.annotation for a in args if a is not None]
        for part in parts:
            if part is not None:
                inside.update(id(n) for n in ast.walk(part))
    return inside


def _is_type(node: ast.AST) -> bool:
    """True for an operand that reads as a type: None, a builtin or class name, a generic."""
    if isinstance(node, ast.Constant):
        return node.value is None
    if isinstance(node, ast.Name):
        return node.id in {"int", "str", "float", "bool", "bytes", "dict", "list",
                           "tuple", "set", "frozenset", "type", "object"} or node.id[:1].isupper()
    if isinstance(node, ast.Attribute):
        return node.attr[:1].isupper()
    if isinstance(node, ast.Subscript):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _is_type(node.left) or _is_type(node.right)
    return False


def runtime_unions(source: str) -> list[int]:
    """Line numbers of `X | Y` type unions outside annotations."""
    tree = ast.parse(source)
    skip = _annotation_nodes(tree)
    return sorted(
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
        and id(node) not in skip and _is_type(node.left) and _is_type(node.right)
    )


def test_finds_a_runtime_union() -> None:
    assert runtime_unions("from __future__ import annotations\nA = tuple[int | None, str]\n") == [2]


def test_ignores_annotations_and_bitwise_or() -> None:
    source = (
        "from __future__ import annotations\n"
        "x: int | None = None\n"
        "def f(a: str | None) -> dict[str, int] | None:\n"
        "    return None\n"
        "flags = 1 | 2\n"
    )
    assert runtime_unions(source) == []


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: str(p.relative_to(ROOT)))
def test_parses_as_python_3_9(path: Path) -> None:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=_FLOOR)


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_runtime_type_union(path: Path) -> None:
    assert runtime_unions(path.read_text(encoding="utf-8")) == []
