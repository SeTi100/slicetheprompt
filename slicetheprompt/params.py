"""AST parameter extraction and in-place updating for slicetheprompt CAD models."""

from __future__ import annotations

import ast
import io
from pathlib import Path
import tokenize
from typing import Any


def _extract_comments(source_code: str) -> dict[int, str]:
    """Extract inline comments per line number using standard tokenize."""
    comments: dict[int, str] = {}
    try:
        tokens = tokenize.tokenize(io.BytesIO(source_code.encode("utf-8")).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                comment_text = tok.string.lstrip("#").strip()
                # Store comment by line number (1-indexed)
                comments[tok.start[0]] = comment_text
    except Exception:
        pass
    return comments


def _parse_param_value(node: ast.AST) -> tuple[float | int, bool] | None:
    """Parse numeric literal or UnaryOp into (value, is_int) or None if not numeric."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return None
        if isinstance(node.value, int):
            return node.value, True
        if isinstance(node.value, float):
            return node.value, False
    elif isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.USub):
            val_info = _parse_param_value(node.operand)
            if val_info is not None:
                val, is_int = val_info
                return -val, is_int
        elif isinstance(node.op, ast.UAdd):
            return _parse_param_value(node.operand)
    return None


def _is_cutoff_target(name: str) -> bool:
    """Check if target variable name is the geometry cutoff boundary."""
    return name in ("part", "bodies", "checks")


def _is_cutoff_node(stmt: ast.AST) -> bool:
    """Check if statement marks the geometry cutoff boundary."""
    if isinstance(stmt, ast.Assign):
        for target in stmt.targets:
            if isinstance(target, ast.Name) and _is_cutoff_target(target.id):
                return True
            if isinstance(target, (ast.Tuple, ast.List)):
                for elt in target.elts:
                    if isinstance(elt, ast.Name) and _is_cutoff_target(elt.id):
                        return True
    elif isinstance(stmt, ast.AnnAssign):
        if isinstance(stmt.target, ast.Name) and _is_cutoff_target(stmt.target.id):
            return True
    return False


def _collect_params_and_nodes(
    model_path: Path | str,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    """Parse model file and return (param_list, param_nodes_map, source_lines)."""
    path = Path(model_path)
    source_code = path.read_text(encoding="utf-8")
    lines = source_code.splitlines(keepends=True)
    tree = ast.parse(source_code, filename=str(path))
    comments = _extract_comments(source_code)

    params: list[dict[str, Any]] = []
    node_map: dict[str, dict[str, Any]] = {}

    for stmt in tree.body:
        if _is_cutoff_node(stmt):
            break

        if isinstance(stmt, ast.Assign):
            # Check for single assignment: Name = Constant / UnaryOp
            if len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                name = stmt.targets[0].id
                parsed = _parse_param_value(stmt.value)
                if parsed is not None:
                    val, is_int = parsed
                    item = {
                        "name": name,
                        "value": val,
                        "is_int": is_int,
                        "line": stmt.lineno,
                        "comment": comments.get(stmt.lineno, ""),
                    }
                    params.append(item)
                    node_map[name] = {"info": item, "node": stmt.value}
            # Check for tuple/list unpacking: A, B, C = 1.0, 2.0, 3.0
            elif len(stmt.targets) == 1 and isinstance(stmt.targets[0], (ast.Tuple, ast.List)):
                target = stmt.targets[0]
                if isinstance(stmt.value, (ast.Tuple, ast.List)):
                    if len(target.elts) == len(stmt.value.elts):
                        for t_elt, v_elt in zip(target.elts, stmt.value.elts):
                            if isinstance(t_elt, ast.Name):
                                name = t_elt.id
                                parsed = _parse_param_value(v_elt)
                                if parsed is not None:
                                    val, is_int = parsed
                                    item = {
                                        "name": name,
                                        "value": val,
                                        "is_int": is_int,
                                        "line": stmt.lineno,
                                        "comment": comments.get(stmt.lineno, ""),
                                    }
                                    params.append(item)
                                    node_map[name] = {"info": item, "node": v_elt}

        elif isinstance(stmt, ast.AnnAssign):
            if isinstance(stmt.target, ast.Name) and stmt.value is not None:
                name = stmt.target.id
                parsed = _parse_param_value(stmt.value)
                if parsed is not None:
                    val, is_int = parsed
                    item = {
                        "name": name,
                        "value": val,
                        "is_int": is_int,
                        "line": stmt.lineno,
                        "comment": comments.get(stmt.lineno, ""),
                    }
                    params.append(item)
                    node_map[name] = {"info": item, "node": stmt.value}

    return params, node_map, lines


def extract_params(model_path: Path | str) -> list[dict[str, Any]]:
    """Extract tunable parameters defined at top of model.py before geometry boundary.

    Returns:
        List of dicts: [{"name": str, "value": float | int, "is_int": bool, "line": int, "comment": str}]
    """
    params, _, _ = _collect_params_and_nodes(model_path)
    return params


def update_params(model_path: Path | str, updates: dict[str, float | int]) -> list[dict[str, Any]]:
    """Surgically update parameter values in-place using AST line and column offsets.

    Preserves 100% of formatting, comments, and untouched lines.
    Replaces literals in reverse order (bottom-right to top-left).
    """
    path = Path(model_path)
    params, node_map, lines = _collect_params_and_nodes(path)

    # Validate update keys
    for k in updates:
        if k not in node_map:
            raise KeyError(f"Parameter '{k}' not found in {path}")

    # Build replacement descriptors
    replacements: list[dict[str, Any]] = []
    for name, new_val in updates.items():
        entry = node_map[name]
        node = entry["node"]
        is_int = entry["info"]["is_int"]

        # Format replacement string
        if is_int:
            new_text = str(int(round(new_val)))
        else:
            val_f = float(new_val)
            s = str(val_f)
            if "." not in s and "e" not in s and "E" not in s:
                s += ".0"
            new_text = s

        replacements.append({
            "start_line": node.lineno,
            "start_col": node.col_offset,
            "end_line": node.end_lineno,
            "end_col": node.end_col_offset,
            "new_text": new_text,
        })

    # Sort descending by (start_line, start_col) to avoid invalidating column offsets
    replacements.sort(key=lambda r: (r["start_line"], r["start_col"]), reverse=True)

    for rep in replacements:
        s_line = rep["start_line"] - 1
        s_col = rep["start_col"]
        e_line = rep["end_line"] - 1
        e_col = rep["end_col"]
        new_text = rep["new_text"]

        if s_line == e_line:
            orig = lines[s_line]
            lines[s_line] = orig[:s_col] + new_text + orig[e_col:]
        else:
            lines[s_line] = lines[s_line][:s_col] + new_text + lines[e_line][e_col:]
            del lines[s_line + 1 : e_line + 1]

    # Write updated source code
    path.write_text("".join(lines), encoding="utf-8")

    # Return freshly extracted parameters
    return extract_params(path)
