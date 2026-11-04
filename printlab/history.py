"""Versionshistorie und Snapshots für printlab Studio (WP-1)."""

from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

import numpy as np

from .config import project_dir
from .stlio import read_stl


def _compute_snapshot_hash(d: Path) -> str:
    """Computes SHA-256 hash of model.py + spec.yaml + out/print.stl."""
    h = hashlib.sha256()
    for rel_path in ("model.py", "spec.yaml", "out/print.stl"):
        p = d / rel_path
        if p.exists():
            h.update(p.read_bytes())
        else:
            h.update(b"__missing__")
    return h.hexdigest()


def _compute_snapshot_hash_for_dir(v_dir: Path) -> str:
    """Computes SHA-256 hash of snapshot files."""
    h = hashlib.sha256()
    for fname in ("model.py", "spec.yaml", "print.stl"):
        p = v_dir / fname
        if p.exists():
            h.update(p.read_bytes())
        else:
            h.update(b"__missing__")
    return h.hexdigest()


def _mesh_size(stl_path: Path) -> list[float]:
    try:
        tris = read_stl(stl_path)
        pts = tris.reshape(-1, 3)
        return [float(round(v, 3)) for v in (pts.max(0) - pts.min(0))]
    except Exception:
        return [0.0, 0.0, 0.0]


def _mesh_volume_from_file(stl_path: Path) -> float:
    try:
        tris = read_stl(stl_path)
        v0 = tris[:, 0, :]
        v1 = tris[:, 1, :]
        v2 = tris[:, 2, :]
        vol = np.sum(v0 * np.cross(v1, v2)) / 6.0
        return float(round(abs(vol), 3))
    except Exception:
        return 0.0


def list_versions(project: str) -> list[dict[str, Any]]:
    """Reads all history/v*/meta.json sorted chronologically by number."""
    p_dir = project_dir(project)
    hist_dir = p_dir / "history"
    if not hist_dir.exists():
        return []

    versions: list[dict[str, Any]] = []
    for sub in hist_dir.iterdir():
        if sub.is_dir() and sub.name.startswith("v"):
            meta_path = sub / "meta.json"
            if meta_path.exists():
                try:
                    data = json.loads(meta_path.read_text(encoding="utf-8"))
                    versions.append(data)
                except Exception:
                    pass

    versions.sort(key=lambda m: (m.get("number", 0), m.get("version", "")))
    return versions


def save_snapshot(project: str, reason: str = "build_and_check") -> dict[str, Any]:
    """Creates projects/<project>/history/vNNN/ snapshot with meta.json and artifact copies.

    Deduplicates via SHA-256 hash of model.py + spec.yaml + out/print.stl against latest version.
    Retains at most 50 versions per project.
    """
    p_dir = project_dir(project)
    hist_dir = p_dir / "history"
    hist_dir.mkdir(parents=True, exist_ok=True)

    curr_hash = _compute_snapshot_hash(p_dir)
    existing_versions = list_versions(str(p_dir))

    # SHA-256 Deduplication check
    if existing_versions:
        latest = existing_versions[-1]
        latest_v_dir = hist_dir / latest["version"]
        latest_hash = latest.get("sha256")
        if not latest_hash:
            latest_hash = _compute_snapshot_hash_for_dir(latest_v_dir)

        if curr_hash == latest_hash:
            return latest

    # Determine next version number
    if existing_versions:
        next_num = max(v.get("number", 0) for v in existing_versions) + 1
    else:
        next_num = 1

    v_name = f"v{next_num:03d}"
    v_dir = hist_dir / v_name
    v_dir.mkdir(parents=True, exist_ok=True)

    # Copy files
    model_file = p_dir / "model.py"
    if model_file.exists():
        shutil.copy2(model_file, v_dir / "model.py")

    spec_file = p_dir / "spec.yaml"
    if spec_file.exists():
        shutil.copy2(spec_file, v_dir / "spec.yaml")

    out_dir = p_dir / "out"
    if out_dir.exists():
        for f in out_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, v_dir / f.name)

    # Read build and report data for metadata extraction
    build_data: dict[str, Any] = {}
    if (out_dir / "build.json").exists():
        try:
            build_data = json.loads((out_dir / "build.json").read_text(encoding="utf-8"))
        except Exception:
            pass

    report_data: dict[str, Any] = {}
    if (out_dir / "report.json").exists():
        try:
            report_data = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
        except Exception:
            pass

    passed = report_data.get("passed", build_data.get("valid", True))
    errors_count = len(report_data.get("errors", []))
    warnings_count = len(report_data.get("warnings", [])) if "warnings" in report_data else len(build_data.get("warnings", []))

    # Extract bodies
    raw_bodies = build_data.get("bodies") or report_data.get("bodies") or []
    if isinstance(raw_bodies, list):
        body_names = [b["name"] if isinstance(b, dict) and "name" in b else str(b) for b in raw_bodies]
    elif isinstance(raw_bodies, dict):
        body_names = list(raw_bodies.keys())
    else:
        body_names = []

    # Extract size_mm
    size_mm = build_data.get("size_mm") or report_data.get("model", {}).get("size_mm") or report_data.get("print", {}).get("size_mm")
    if not size_mm or len(size_mm) != 3:
        if (out_dir / "print.stl").exists():
            size_mm = _mesh_size(out_dir / "print.stl")
        elif (out_dir / "model.stl").exists():
            size_mm = _mesh_size(out_dir / "model.stl")
        else:
            size_mm = [0.0, 0.0, 0.0]
    size_mm = [float(round(v, 3)) for v in size_mm]

    # Extract volume_mm3
    volume_mm3 = build_data.get("volume_mm3") or report_data.get("model", {}).get("volume_mm3")
    if volume_mm3 is None or volume_mm3 <= 0:
        if (out_dir / "print.stl").exists():
            volume_mm3 = _mesh_volume_from_file(out_dir / "print.stl")
        elif (out_dir / "model.stl").exists():
            volume_mm3 = _mesh_volume_from_file(out_dir / "model.stl")
        else:
            volume_mm3 = 0.0
    volume_mm3 = float(round(volume_mm3, 3))

    # Extract parameters from model.py
    params_dict: dict[str, Any] = {}
    if model_file.exists():
        try:
            from . import params as params_mod
            extracted = params_mod.extract_params(model_file)
            params_dict = {p["name"]: p["value"] for p in extracted}
        except Exception:
            pass

    meta = {
        "version": v_name,
        "number": next_num,
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "reason": reason,
        "passed": bool(passed),
        "size_mm": size_mm,
        "volume_mm3": volume_mm3,
        "errors_count": int(errors_count),
        "warnings_count": int(warnings_count),
        "bodies": body_names,
        "params": params_dict,
        "sha256": curr_hash,
    }

    (v_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    # Retention enforcement: keep at most 50 versions per project
    all_version_dirs: list[tuple[int, Path]] = []
    for sub in hist_dir.iterdir():
        if sub.is_dir() and sub.name.startswith("v") and (sub / "meta.json").exists():
            try:
                num = int(sub.name[1:])
                all_version_dirs.append((num, sub))
            except ValueError:
                pass

    all_version_dirs.sort(key=lambda pair: pair[0])
    if len(all_version_dirs) > 50:
        excess = len(all_version_dirs) - 50
        for _, old_dir in all_version_dirs[:excess]:
            shutil.rmtree(old_dir, ignore_errors=True)

    return meta


def restore_version(project: str, version: str) -> dict[str, Any]:
    """Restores historical version and runs pipeline to re-check."""
    p_dir = project_dir(project)
    hist_dir = p_dir / "history"
    v_dir = hist_dir / version
    if not v_dir.exists():
        raise FileNotFoundError(f"Version '{version}' not found in {hist_dir}")
    if not (v_dir / "model.py").exists():
        raise FileNotFoundError(f"Version '{version}' does not contain model.py")

    curr_model = p_dir / "model.py"
    versions = list_versions(str(p_dir))
    if versions and curr_model.exists():
        latest_v = versions[-1]["version"]
        latest_model = hist_dir / latest_v / "model.py"
        if latest_model.exists():
            if curr_model.read_bytes() != latest_model.read_bytes():
                save_snapshot(str(p_dir), reason="auto_backup_before_restore")
        else:
            save_snapshot(str(p_dir), reason="auto_backup_before_restore")

    shutil.copy2(v_dir / "model.py", curr_model)
    if (v_dir / "spec.yaml").exists():
        shutil.copy2(v_dir / "spec.yaml", p_dir / "spec.yaml")

    from . import pipeline
    return pipeline.run_all(str(p_dir), reason=f"restore_{version}")


def diff_code(project: str, v_old: str, v_new: str) -> str:
    """Returns unified diff between v_old/model.py and v_new/model.py."""
    p_dir = project_dir(project)
    hist_dir = p_dir / "history"

    if v_old in ("current", "working", "head"):
        p_old = p_dir / "model.py"
    else:
        p_old = hist_dir / v_old / "model.py"

    if v_new in ("current", "working", "head"):
        p_new = p_dir / "model.py"
    else:
        p_new = hist_dir / v_new / "model.py"

    if not p_old.exists():
        raise FileNotFoundError(f"Version '{v_old}' model.py not found ({p_old})")
    if not p_new.exists():
        raise FileNotFoundError(f"Version '{v_new}' model.py not found ({p_new})")

    old_lines = p_old.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines = p_new.read_text(encoding="utf-8").splitlines(keepends=True)

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"{v_old}/model.py",
        tofile=f"{v_new}/model.py",
    )
    return "".join(diff)
