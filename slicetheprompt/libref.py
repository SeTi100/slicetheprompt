"""Kurzübersicht der CAD-Bausteine in lib/ (Signatur + erste Docstring-Zeile).

Wird per `ast` gelesen, nicht importiert: kein build123d-Start im MCP-Server, und die Übersicht
ist immer so aktuell wie die Dateien. Agenten müssen lib/*.py dafür nicht mehr selbst lesen.
"""

from __future__ import annotations

import ast
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"


def _signature(fn: ast.FunctionDef) -> str:
    a = fn.args
    pos = a.posonlyargs + a.args
    defaults = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
    parts = [arg.arg if d is None else f"{arg.arg}={ast.unparse(d)}" for arg, d in zip(pos, defaults)]
    if a.vararg:
        parts.append(f"*{a.vararg.arg}")
    elif a.kwonlyargs:
        parts.append("*")
    for arg, d in zip(a.kwonlyargs, a.kw_defaults):
        parts.append(arg.arg if d is None else f"{arg.arg}={ast.unparse(d)}")
    return f"{fn.name}({', '.join(parts)})"


def module_ref(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            doc = " ".join((ast.get_docstring(node) or "").strip().split("\n\n")[0].split())
            out.append(f"{_signature(node)} – {doc}" if doc else _signature(node))
    return out


def lib_reference() -> dict[str, list[str]]:
    """{"lib.parts": ["fit(size, material=None) – …", …], "lib.gears": […]}"""
    return {f"lib.{p.stem}": module_ref(p) for p in sorted(LIB_DIR.glob("*.py")) if not p.stem.startswith("_")}
