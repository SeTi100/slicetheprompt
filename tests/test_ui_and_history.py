"""Comprehensive automated test suite for SliceThePrompt (WP-1 bis WP-5).

Covers all 5 mandatory test functions specified in AGENT.md §5.2:
1. test_snapshots_and_restore:
   Snapshot creation under temp project, meta.json schema, SHA-256 deduplication,
   version rollback restoring model.py and geometry.
2. test_ast_params:
   AST parameter extraction and update for int/float, negative numbers (e.g. X = -15.0),
   tuple unpacking (L, B, H = 60.0, 25.0, 4.0), and inline comment preservation.
3. test_empty_cut_detection:
   Missed cut outside workpiece triggers warning, exports ghost_<slug>.stl, and
   injects a failing check (ok=False) causing check() to fail.
4. test_diagnostics_3d_and_coord_transform:
   Wall thickness and overhang indices populated, and exact mathematical inverse
   coordinate transformation p_model = (p_print - s) @ R across all 6 ORIENTATIONS (tol < 1e-6).
5. test_ui_http_endpoints_and_feedback:
   ThreadingHTTPServer on port 0, GET /, GET /api/projects, GET /api/projects/<p>/state,
   POST /api/projects/<p>/feedback with base64 PNG decoding, and path-traversal prevention.

Run standalone:
    python tests/test_ui_and_history.py
Or via pytest:
    python -m pytest tests/test_ui_and_history.py
"""

from __future__ import annotations

import base64
import json
import math
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np

# Ensure project root and tests directory are in sys.path
_ROOT = Path(__file__).resolve().parents[1]
_TESTS_DIR = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from meshgen import box, voxels_to_tris  # noqa: E402
from slicetheprompt import meshcheck as mc  # noqa: E402
from slicetheprompt.config import load_config  # noqa: E402
from slicetheprompt.stlio import read_stl, write_stl  # noqa: E402

# 1x1 transparent PNG in Base64 (valid binary PNG with magic bytes \x89PNG\r\n\x1a\n)
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
TINY_PNG_DATA_URL = f"data:image/png;base64,{TINY_PNG_B64}"
PNG_MAGIC_BYTES = b"\x89PNG\r\n\x1a\n"


def _create_minimal_project(proj_dir: Path, name: str = "test_project", l: float = 40.0, b: float = 30.0, h: float = 10.0) -> None:
    """Helper to initialize a valid project structure on disk."""
    proj_dir.mkdir(parents=True, exist_ok=True)
    out_dir = proj_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    spec_content = f"""\
name: {name}
beschreibung: "Test Project for Unit Testing"
material: pla
qualitaet: standard
menge: 1
filament: null
bbox: [{l}, {b}, {h}]
bbox_tol: 0.1
masse: []
annahmen: []
"""
    (proj_dir / "spec.yaml").write_text(spec_content, encoding="utf-8")

    model_code = f"""\
\"\"\"{name}: Test CAD Model\"\"\"
from build123d import *
from lib.parts import *

set_material("pla")
C = (Align.CENTER, Align.CENTER, Align.MIN)

# Parameters
L, B, H = {l}, {b}, {h}

part = Box(L, B, H, align=C)
checks = {{"Laenge": (L, {l}, 0.05)}}
"""
    (proj_dir / "model.py").write_text(model_code, encoding="utf-8")


# ==============================================================================
# 1. test_snapshots_and_restore
# ==============================================================================

def test_snapshots_and_restore():
    """WP-1: Snapshot creation, meta.json schema, SHA-256 deduplication, version rollback."""
    from slicetheprompt import history, pipeline

    with tempfile.TemporaryDirectory(prefix="slicetheprompt_test_snap_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str).resolve()
        _create_minimal_project(tmp_dir, name="snap_test", l=40.0, b=30.0, h=10.0)

        # 1. First build creates initial state & snapshot
        res1 = pipeline.run_all(str(tmp_dir))
        assert res1 is not None, "pipeline.run_all must return result dict"

        # Check snapshot folder exists
        hist_dir = tmp_dir / "history"
        v1_dir = hist_dir / "v001"
        assert v1_dir.exists(), f"Snapshot folder {v1_dir} must exist after first build"
        assert (v1_dir / "meta.json").exists(), "meta.json must exist in v001"

        # Validate meta.json schema as specified in AGENT.md §1.1
        meta1 = json.loads((v1_dir / "meta.json").read_text(encoding="utf-8"))
        assert meta1["version"] == "v001", f"Expected version v001, got {meta1.get('version')}"
        assert meta1["number"] == 1, f"Expected number 1, got {meta1.get('number')}"
        assert "timestamp" in meta1, "meta.json must include timestamp"
        assert meta1["reason"] in ("build_and_check", "initial", "auto_backup"), f"Unexpected reason: {meta1.get('reason')}"
        assert "passed" in meta1, "meta.json must contain passed flag"
        assert "size_mm" in meta1 and len(meta1["size_mm"]) == 3, "size_mm must be [x, y, z]"
        assert "volume_mm3" in meta1 and meta1["volume_mm3"] > 0, "volume_mm3 must be positive"
        assert "errors_count" in meta1, "errors_count required"
        assert "warnings_count" in meta1, "warnings_count required"
        assert "params" in meta1, "params required in meta.json"

        # Check required artifact copies in v001
        for required_file in ("model.py", "spec.yaml", "model.stl", "print.stl"):
            fpath = v1_dir / required_file
            assert fpath.exists(), f"Expected artifact copy {required_file} in {v1_dir}"

        # 2. SHA-256 Deduplication: Re-running build on identical code should NOT duplicate snapshot
        res2 = pipeline.run_all(str(tmp_dir))
        v2_dir = hist_dir / "v002"
        assert not v2_dir.exists(), "Re-running identical build must NOT create a duplicate snapshot v002"
        versions_after_dup = history.list_versions(str(tmp_dir))
        assert len(versions_after_dup) == 1, f"Expected exactly 1 version after identical build, got {len(versions_after_dup)}"

        # 3. Modify model.py and re-run -> creates v002
        new_code = (tmp_dir / "model.py").read_text(encoding="utf-8").replace("L, B, H = 40.0, 30.0, 10.0", "L, B, H = 55.0, 30.0, 10.0")
        (tmp_dir / "model.py").write_text(new_code, encoding="utf-8")

        res3 = pipeline.run_all(str(tmp_dir))
        assert v2_dir.exists(), "Modified code must create snapshot v002"
        meta2 = json.loads((v2_dir / "meta.json").read_text(encoding="utf-8"))
        assert meta2["version"] == "v002"
        assert meta2["number"] == 2

        versions = history.list_versions(str(tmp_dir))
        assert len(versions) == 2, f"Expected 2 versions chronologically, got {len(versions)}"
        assert versions[0]["version"] == "v001"
        assert versions[1]["version"] == "v002"

        # 4. Code diff check
        diff = history.diff_code(str(tmp_dir), "v001", "v002")
        assert "40.0" in diff and "55.0" in diff, f"diff_code must show change from 40.0 to 55.0. Diff:\n{diff}"

        # 5. Version Rollback: Restore v001
        # First introduce dirty uncommitted modification
        (tmp_dir / "model.py").write_text(new_code.replace("55.0", "999.0"), encoding="utf-8")
        restore_res = history.restore_version(str(tmp_dir), "v001")
        assert restore_res is not None, "restore_version must return result"

        # Check that model.py was restored to v001 parameters
        restored_code = (tmp_dir / "model.py").read_text(encoding="utf-8")
        assert "40.0" in restored_code and "999.0" not in restored_code, "model.py must be restored to v001 state"

        # Check restored build geometry matches v001
        build_info = json.loads((tmp_dir / "out" / "build.json").read_text(encoding="utf-8"))
        assert abs(build_info["size_mm"][0] - 40.0) < 0.1, f"Restored geometry X size must match 40.0 mm, got {build_info['size_mm'][0]}"

        # 6. Retention limit check: Max 50 versions
        # Create synthetic versions to verify pruning does not crash and respects limit 50
        for i in range(len(versions) + 1, 53):
            v_synth = hist_dir / f"v{i:03d}"
            v_synth.mkdir(exist_ok=True)
            synth_meta = dict(meta1)
            synth_meta["version"] = f"v{i:03d}"
            synth_meta["number"] = i
            (v_synth / "meta.json").write_text(json.dumps(synth_meta), encoding="utf-8")
            (v_synth / "model.py").write_text(restored_code, encoding="utf-8")
            (v_synth / "spec.yaml").write_text((tmp_dir / "spec.yaml").read_text(encoding="utf-8"), encoding="utf-8")
            (v_synth / "print.stl").write_text("dummy", encoding="utf-8")

        all_v = history.list_versions(str(tmp_dir))
        assert len(all_v) <= 52, "Synthetic versions populated"

        # Trigger save_snapshot with cleanup
        (tmp_dir / "model.py").write_text(restored_code.replace("40.0", "42.0"), encoding="utf-8")
        snap_final = history.save_snapshot(str(tmp_dir), reason="test_retention")
        final_versions = history.list_versions(str(tmp_dir))
        assert len(final_versions) <= 50, f"History must enforce max 50 versions limit, got {len(final_versions)}"


# ==============================================================================
# 2. test_ast_params
# ==============================================================================

def test_ast_params():
    """WP-1: AST parameter extraction and update for scalars, negatives, tuples, and inline comments."""
    from slicetheprompt import params

    sample_code = """\
# Header Docstring
\"\"\"Model for parameter tuning test.\"\"\"
from build123d import *
from lib.parts import *

# Scalar Parameters
L = 60.0
WAND: float = 2.5  # Wandstaerke in mm
HOEHE = 15  # Hoehe ganzzahlig
ANZ_LOECHER: int = 4

# Negative Numbers
OFFSET_X = -15.0  # Negativer X Offset
DELTA_Y: float = -3.5

# Tuple Unpacking
BOX_L, BOX_B, BOX_H = 100.0, 50.0, 20.0  # Box Abmessungen

# Geometry section - Anything at or below this must NOT be extracted as parameters!
part = Box(L, BOX_B, HOEHE)
checks = {"Laenge": (L, 60.0, 0.05)}
other_var = 123.45
"""

    with tempfile.TemporaryDirectory(prefix="slicetheprompt_test_params_") as tmp_dir:
        model_file = Path(tmp_dir) / "model.py"
        model_file.write_text(sample_code, encoding="utf-8")

        # 1. Extraction Test
        extracted = params.extract_params(model_file)
        assert isinstance(extracted, list), "extract_params must return a list of dicts"

        param_map = {p["name"]: p for p in extracted}

        # Check scalars
        assert "L" in param_map, "Param L must be extracted"
        assert abs(param_map["L"]["value"] - 60.0) < 1e-6
        assert param_map["L"]["is_int"] is False

        assert "WAND" in param_map, "Param WAND with type annotation must be extracted"
        assert abs(param_map["WAND"]["value"] - 2.5) < 1e-6
        assert "Wandstaerke" in param_map["WAND"]["comment"], "Inline comment for WAND must be captured"

        assert "HOEHE" in param_map, "Param HOEHE must be extracted"
        assert param_map["HOEHE"]["value"] == 15
        assert param_map["HOEHE"]["is_int"] is True
        assert "Hoehe ganzzahlig" in param_map["HOEHE"]["comment"]

        assert "ANZ_LOECHER" in param_map, "Param ANZ_LOECHER must be extracted"
        assert param_map["ANZ_LOECHER"]["value"] == 4
        assert param_map["ANZ_LOECHER"]["is_int"] is True

        # Check negative numbers (ast.UnaryOp with USub)
        assert "OFFSET_X" in param_map, "Negative parameter OFFSET_X must be extracted"
        assert abs(param_map["OFFSET_X"]["value"] - (-15.0)) < 1e-6, f"Expected -15.0, got {param_map['OFFSET_X']['value']}"
        assert "Negativer X Offset" in param_map["OFFSET_X"]["comment"]

        assert "DELTA_Y" in param_map, "Negative float DELTA_Y must be extracted"
        assert abs(param_map["DELTA_Y"]["value"] - (-3.5)) < 1e-6

        # Check tuple unpacking
        for name, expected_val in [("BOX_L", 100.0), ("BOX_B", 50.0), ("BOX_H", 20.0)]:
            assert name in param_map, f"Tuple parameter {name} must be extracted"
            assert abs(param_map[name]["value"] - expected_val) < 1e-6, f"{name} value mismatch"
            assert "Box Abmessungen" in param_map[name]["comment"]

        # Check boundary cutoff: variables after 'part =' must NOT be extracted
        assert "checks" not in param_map, "checks variable must be ignored"
        assert "other_var" not in param_map, "Variables after geometry boundary must be ignored"

        # 2. Update Test
        updates = {
            "L": 75.0,
            "OFFSET_X": -22.5,
            "BOX_B": 55.0,
            "HOEHE": 18,
        }
        res_updates = params.update_params(model_file, updates)
        assert res_updates is not None, "update_params must return updated param list"

        updated_code = model_file.read_text(encoding="utf-8")

        # Verify surgical updates in code content
        assert "L = 75.0" in updated_code, "L must be updated to 75.0"
        assert "OFFSET_X = -22.5  # Negativer X Offset" in updated_code, "OFFSET_X must be -22.5 with comment preserved"
        assert "HOEHE = 18  # Hoehe ganzzahlig" in updated_code, "HOEHE must be 18 with comment preserved"
        assert "BOX_L, BOX_B, BOX_H = 100.0, 55.0, 20.0" in updated_code, "BOX_B in tuple must be updated to 55.0 while BOX_L and BOX_H remain unchanged"
        assert "WAND: float = 2.5  # Wandstaerke in mm" in updated_code, "Untouched parameter WAND must stay unchanged"

        # Verify 100% syntactical validity by AST re-parsing
        import ast
        tree = ast.parse(updated_code)
        assert tree is not None, "Updated code must parse cleanly without syntax errors"

        # Re-extract and verify all updated values
        re_extracted = {p["name"]: p for p in params.extract_params(model_file)}
        assert abs(re_extracted["L"]["value"] - 75.0) < 1e-6
        assert abs(re_extracted["OFFSET_X"]["value"] - (-22.5)) < 1e-6
        assert abs(re_extracted["BOX_B"]["value"] - 55.0) < 1e-6
        assert re_extracted["HOEHE"]["value"] == 18


# ==============================================================================
# 3. test_empty_cut_detection
# ==============================================================================

def test_empty_cut_detection():
    """WP-2: Empty cut outside workpiece triggers warning, exports ghost_<slug>.stl, injects check ok=False."""
    from lib import parts
    from slicetheprompt import pipeline

    # 1. Direct lib.parts unit tests for tag and cut_feature
    assert hasattr(parts, "tag"), "lib.parts must define tag(name, shape, role)"
    assert hasattr(parts, "cut_feature"), "lib.parts must define cut_feature(part, name, cutter)"
    assert hasattr(parts, "sub_tag"), "lib.parts must define sub_tag alias"
    assert hasattr(parts, "FEATURES"), "lib.parts must define FEATURES list"

    # Reset globals
    parts.FEATURES.clear()
    parts.WARNINGS.clear()

    from build123d import Box, Cylinder, Pos

    base = Box(30, 30, 10)
    # A) Valid cut inside base
    cutter_valid = Cylinder(3, 15)
    cut_res = parts.cut_feature(base, "bohrung_mitte", cutter_valid)
    assert cut_res is not None
    feat_valid = [f for f in parts.FEATURES if f["name"] == "bohrung_mitte"][0]
    assert feat_valid["removed_volume_mm3"] > 10.0, "Valid cut must remove significant material"
    assert not feat_valid.get("empty_cut", False), "Valid cut must not be empty_cut"

    # B) Missed / empty cut completely outside base
    cutter_outside = Pos(100, 0, 0) * Cylinder(3, 15)
    parts.cut_feature(base, "bohrung_daneben", cutter_outside)
    feat_empty = [f for f in parts.FEATURES if f["name"] == "bohrung_daneben"][0]
    assert feat_empty.get("empty_cut") is True, "Missed cut must have empty_cut: True"
    assert feat_empty["removed_volume_mm3"] < 0.01, "Missed cut removed volume must be < 0.01 mm³"
    assert any("bohrung_daneben" in w for w in parts.WARNINGS), "Warning must be recorded for empty cut"

    # 2. Subprocess pipeline test with empty cut
    with tempfile.TemporaryDirectory(prefix="slicetheprompt_test_cut_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str).resolve()
        tmp_dir.mkdir(parents=True, exist_ok=True)
        (tmp_dir / "out").mkdir(parents=True, exist_ok=True)

        spec_content = """\
name: empty_cut_proj
beschreibung: "Test empty cut detection"
material: pla
qualitaet: standard
menge: 1
filament: null
bbox: [30, 30, 10]
bbox_tol: 0.1
masse: []
annahmen: []
"""
        (tmp_dir / "spec.yaml").write_text(spec_content, encoding="utf-8")

        model_code = """\
from build123d import *
from lib.parts import *

set_material("pla")
C = (Align.CENTER, Align.CENTER, Align.MIN)

part = Box(30, 30, 10, align=C)
# Cut that misses the workpiece
part = cut_feature(part, "loch_daneben", Pos(80, 0, 0) * hole_cut(4, 10))
checks = {"Dummy": (1, 1, 0.01)}
"""
        (tmp_dir / "model.py").write_text(model_code, encoding="utf-8")

        # Run pipeline
        res = pipeline.run_all(str(tmp_dir))
        out_dir = tmp_dir / "out"

        # Check build.json contains empty cut feature and failing check
        build_json_file = out_dir / "build.json"
        assert build_json_file.exists(), "build.json must exist"
        build_data = json.loads(build_json_file.read_text(encoding="utf-8"))

        checks = build_data.get("checks", [])
        cut_checks = [c for c in checks if "loch_daneben" in c["name"]]
        assert len(cut_checks) > 0, "A check for empty cut 'loch_daneben' must be injected into checks"
        assert cut_checks[0]["ok"] is False, f"Empty cut check must fail with ok=False, got {cut_checks[0]}"

        # Check ghost STL exported
        ghost_stl = out_dir / "ghost_loch_daneben.stl"
        assert ghost_stl.exists(), f"Ghost STL {ghost_stl} must be exported for empty cut"
        assert ghost_stl.stat().st_size > 0, "Ghost STL must not be empty"

        # Check pipeline.prepare rotated ghost STL to ghost_*_print.stl
        ghost_print_stl = out_dir / "ghost_loch_daneben_print.stl"
        assert ghost_print_stl.exists(), f"Prepared ghost STL {ghost_print_stl} must exist in print coordinates"

        # Check report.json marks build as not passed
        report_file = out_dir / "report.json"
        assert report_file.exists(), "report.json must exist"
        report_data = json.loads(report_file.read_text(encoding="utf-8"))
        assert report_data["passed"] is False, "Pipeline check must report passed=False when empty cut check fails"


def test_features_follow_transforms():
    """Features eines später verschobenen/gedrehten Körpers landen an ihrer Lage im fertigen Teil."""
    import subprocess

    model_code = """\
from build123d import *
from lib.parts import *
C = (Align.CENTER, Align.CENTER, Align.MIN)

def rad(name):
    k = Cylinder(10, 5, align=C)
    return cut_feature(k, f"{name}_loch", Pos(0, 0, -1) * Cylinder(2, 7, align=C))

a = rad("a")
b = Pos(40, 0, 0) * rad("b")
c = Rot(0, 0, 90) * (Pos(0, 30, 0) * rad("c"))    # (0, 30) gedreht um 90° -> (-30, 0)
part = a + b + c
"""
    with tempfile.TemporaryDirectory(prefix="slicetheprompt_test_feat_") as tmp:
        tmp_dir = Path(tmp)
        (tmp_dir / "model.py").write_text(model_code, encoding="utf-8")
        subprocess.run([sys.executable, "-m", "slicetheprompt._cad_runner", str(tmp_dir / "model.py"),
                        str(tmp_dir / "out")], cwd=str(_TESTS_DIR.parent), capture_output=True, check=True)
        build = json.loads((tmp_dir / "out" / "build.json").read_text(encoding="utf-8"))
        feats = {f["name"]: f for f in build["features"]}
        for name, (x, y) in {"a_loch": (0, 0), "b_loch": (40, 0), "c_loch": (-30, 0)}.items():
            cx, cy, _ = feats[name]["center"]
            assert abs(cx - x) < 0.01 and abs(cy - y) < 0.01, f"{name}: Mitte {feats[name]['center']} statt ({x}, {y})"
        assert feats["b_loch"]["bbox_min"][0] == 38.0 and feats["b_loch"]["bbox_max"][0] == 42.0
        assert not any(k.startswith("_") for f in build["features"] for k in f), "interne Felder in build.json"


# ==============================================================================
# 4. test_diagnostics_3d_and_coord_transform
# ==============================================================================

def test_diagnostics_3d_and_coord_transform():
    """WP-2: Wall thickness & overhang indices in diagnostics_3d, and exact inverse coordinate math."""
    # 1. Mathematical Inverse Coordinate Transformation across all 6 ORIENTATIONS
    orientations = mc.ORIENTATIONS
    expected_orientations = [
        "wie modelliert",
        "kopfüber (X 180°)",
        "X +90°",
        "X -90°",
        "Y +90°",
        "Y -90°",
    ]
    for exp_name in expected_orientations:
        assert exp_name in orientations, f"Orientation '{exp_name}' must be defined in meshcheck.ORIENTATIONS"

    # Diverse test points in model space (origin, corners, negative coordinates, float fractions)
    test_points = np.array([
        [0.0, 0.0, 0.0],
        [42.5, -18.25, 7.125],
        [-35.0, 20.0, -12.0],
        [120.345, -85.123, 14.567],
        [-0.001, -0.002, -0.003],
    ])
    bed_shift = np.array([130.0, 130.0, 0.0])

    for name, R in orientations.items():
        # Verify R is an orthogonal rotation matrix: R.T @ R == I, det(R) == 1
        assert R.shape == (3, 3), f"Rotation matrix for {name} must be 3x3"
        eye_diff = np.abs(R.T @ R - np.eye(3))
        assert np.max(eye_diff) < 1e-7, f"Rotation matrix for {name} is not orthogonal"
        det = np.linalg.det(R)
        assert abs(det - 1.0) < 1e-7, f"Determinant of rotation matrix for {name} must be +1, got {det}"

        for p_model in test_points:
            # Forward: p_rot = p_model @ R.T, p_print = p_rot + s
            p_rot = p_model @ R.T
            p_print = p_rot + bed_shift

            # Exact inverse contract from AGENT.md §1 & §2.2:
            # p_model = (p_print - s) @ R
            p_recovered = (p_print - bed_shift) @ R

            err = np.max(np.abs(p_recovered - p_model))
            assert err < 1e-6, (
                f"Inverse transform failed for orientation '{name}'. "
                f"Expected: {p_model}, Recovered: {p_recovered}, Error: {err}"
            )

        # Normal vector transformation: n_print = n_model @ R.T, n_model = n_print @ R
        normals = np.array([[1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, 1.0]])
        for n_model in normals:
            n_print = n_model @ R.T
            n_recovered = n_print @ R
            assert np.max(np.abs(n_recovered - n_model)) < 1e-6, f"Normal recovery failed for {name}"

    # 2. 3D Diagnostics Verification (diagnostics_3d)
    if not hasattr(mc, "diagnostics_3d"):
        raise AttributeError("slicetheprompt.meshcheck has no attribute 'diagnostics_3d'")

    cfg = load_config()

    # Generate test mesh with thin wall (< 0.84 mm)
    v_thin = np.ones((40, 40, 20), bool)
    v_thin[1:-1, 1:-1, 1:] = False
    tris_thin = voxels_to_tris(v_thin, pitch=0.5)

    build_data = {
        "features": [
            {
                "name": "bodenplatte",
                "role": "feature",
                "bbox_min": [-10, -10, 0],
                "bbox_max": [10, 10, 2],
                "center": [0, 0, 1],
                "volume_mm3": 800.0,
            }
        ]
    }
    print_info = {
        "orientation": "wie modelliert",
        "rotation_matrix": np.eye(3).tolist(),
        "shift": [130.0, 130.0, 0.0],
    }

    diag = mc.diagnostics_3d(tris_thin, cfg, build_data=build_data, print_info=print_info)
    assert isinstance(diag, dict), "diagnostics_3d must return dict"

    # Check mandatory top-level keys
    for k in ("thin_walls", "overhangs", "bad_bridges", "open_edges", "floating_parts", "features"):
        assert k in diag, f"diagnostics_3d result missing key: {k}"

    # Check thin_walls populated
    assert len(diag["thin_walls"]) > 0, "Thin wall mesh must generate thin_walls diagnostics"
    tw = diag["thin_walls"][0]
    assert tw["kind"] == "thin_wall"
    assert tw["severity"] == "error"
    assert "tri_indices" in tw and len(tw["tri_indices"]) > 0, "thin_walls item must include tri_indices"
    assert "bbox_print" in tw and len(tw["bbox_print"]) == 6, "bbox_print must be 6 floats"
    assert "bbox_model" in tw and len(tw["bbox_model"]) == 6, "bbox_model must be 6 floats"
    assert "center_print" in tw and len(tw["center_print"]) == 3
    assert "center_model" in tw and len(tw["center_model"]) == 3

    # Check transformed features
    feats = diag["features"]
    assert len(feats) == 1, "Features should be carried over into diagnostics_3d"
    assert "bbox_print" in feats[0], "Feature must include transformed bbox_print"


# ==============================================================================
# 5. test_ui_http_endpoints_and_feedback
# ==============================================================================

def test_ui_http_endpoints_and_feedback():
    """WP-3 & WP-5: Ephemeral HTTP server, REST endpoints, base64 PNG decoding, path-traversal prevention."""
    from slicetheprompt import mcp_server, ui_server

    with tempfile.TemporaryDirectory(prefix="slicetheprompt_test_server_") as tmp_root:
        proj_dir = Path(tmp_root) / "test_api_proj"
        _create_minimal_project(proj_dir, name="test_api_proj", l=50.0, b=30.0, h=10.0)

        # Create dummy artifacts in out/
        out_dir = proj_dir / "out"
        (out_dir / "report.json").write_text(json.dumps({"passed": True, "errors": [], "warnings": []}), encoding="utf-8")
        (out_dir / "build.json").write_text(json.dumps({"size_mm": [50.0, 30.0, 10.0], "checks": []}), encoding="utf-8")
        (out_dir / "print.json").write_text(json.dumps({"rotation_matrix": np.eye(3).tolist(), "shift": [130, 130, 0]}), encoding="utf-8")
        (out_dir / "print.stl").write_bytes(b"solid dummy\nendsolid dummy\n")

        # Start server on port 0 (ephemeral OS-assigned port)
        if hasattr(ui_server, "create_server"):
            server = ui_server.create_server(host="127.0.0.1", port=0, projects_dir=Path(tmp_root))
        elif hasattr(ui_server, "StudioServer"):
            server = ui_server.StudioServer(("127.0.0.1", 0), projects_dir=Path(tmp_root))
        else:
            handler_cls = getattr(ui_server, "StudioHandler", getattr(ui_server, "UIHandler", None))
            assert handler_cls is not None, "ui_server must export StudioHandler or create_server"
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
            server.projects_dir = Path(tmp_root)

        port = server.server_address[1]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        base_url = f"http://127.0.0.1:{port}"

        try:
            # 1. GET / (HTML Studio Delivery)
            req_root = urllib.request.Request(f"{base_url}/")
            with urllib.request.urlopen(req_root, timeout=5) as resp:
                assert resp.status == 200, f"GET / returned status {resp.status}"
                ct = resp.headers.get("Content-Type", "")
                assert "text/html" in ct, f"Expected text/html, got {ct}"
                body = resp.read().decode("utf-8", errors="ignore")
                assert len(body) > 0, "Index HTML must not be empty"

            # 2. GET /api/projects
            req_projects = urllib.request.Request(f"{base_url}/api/projects")
            with urllib.request.urlopen(req_projects, timeout=5) as resp:
                assert resp.status == 200
                data = json.loads(resp.read().decode("utf-8"))
                assert isinstance(data, list), "GET /api/projects must return list"

            # 3. GET /api/projects/<project>/state
            req_state = urllib.request.Request(f"{base_url}/api/projects/{proj_dir.name}/state")
            with urllib.request.urlopen(req_state, timeout=5) as resp:
                assert resp.status == 200
                st = json.loads(resp.read().decode("utf-8"))
                assert "spec" in st, "state must contain spec"
                assert "model_code" in st, "state must contain model_code"
                assert "build" in st, "state must contain build"
                assert "print_info" in st, "state must contain print_info"

            # 4. GET /api/projects/<project>/files/<path> (valid file)
            req_file = urllib.request.Request(f"{base_url}/api/projects/{proj_dir.name}/files/out/report.json")
            with urllib.request.urlopen(req_file, timeout=5) as resp:
                assert resp.status == 200
                fcontent = json.loads(resp.read().decode("utf-8"))
                assert fcontent.get("passed") is True

            # 5. Path-Traversal Protection
            traversal_paths = [
                f"{base_url}/api/projects/{proj_dir.name}/files/../../secret.txt",
                f"{base_url}/api/projects/{proj_dir.name}/files/../model.py",
                f"{base_url}/api/projects/{proj_dir.name}/files/%2e%2e/%2e%2e/pipeline.py",
            ]
            for bad_url in traversal_paths:
                try:
                    with urllib.request.urlopen(bad_url, timeout=3) as resp:
                        assert False, f"Path traversal request {bad_url} succeeded with {resp.status}! Must be blocked with 403 or 404."
                except urllib.error.HTTPError as exc:
                    assert exc.code in (400, 403, 404), f"Expected 403/404 for path traversal, got {exc.code}"

            # 6. POST /api/projects/<project>/feedback (Save 3D feedback + Base64 PNG decoding)
            feedback_payload = {
                "general_note": "Mittelwand um 90° drehen.",
                "view_mode": "print",
                "based_on_version": "v001",
                "screenshot_data_url": TINY_PNG_DATA_URL,
                "annotations": [
                    {
                        "id": 1,
                        "type": "pin",
                        "body": "_teil",
                        "feature": "mittelwand",
                        "model_xyz": [0.0, 12.5, 10.0],
                        "model_normal": [1.0, 0.0, 0.0],
                        "print_xyz": [130.0, 142.5, 10.0],
                        "note": "Diese Wand drehen.",
                    }
                ],
            }
            post_data = json.dumps(feedback_payload).encode("utf-8")
            req_fb = urllib.request.Request(
                f"{base_url}/api/projects/{proj_dir.name}/feedback",
                data=post_data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req_fb, timeout=5) as resp:
                assert resp.status in (200, 201), f"POST feedback returned {resp.status}"
                fb_res = json.loads(resp.read().decode("utf-8"))
                assert fb_res.get("ok") is True

            # Verify files written to disk
            fb_json_path = out_dir / "ui_feedback.json"
            fb_png_path = out_dir / "ui_feedback.png"
            assert fb_json_path.exists(), "ui_feedback.json must be written to disk"
            assert fb_png_path.exists(), "ui_feedback.png must be written to disk"

            # Verify PNG decoding & magic bytes
            png_bytes = fb_png_path.read_bytes()
            assert len(png_bytes) > 0, "Decoded PNG file must not be empty"
            assert png_bytes.startswith(PNG_MAGIC_BYTES), "Decoded PNG must start with PNG magic bytes"

            # 7. MCP Tools Verification: get_ui_feedback & clear_ui_feedback
            fb_mcp = mcp_server.get_ui_feedback(str(proj_dir))
            assert fb_mcp is not None, "mcp_server.get_ui_feedback must return feedback dict"
            assert "screenshot" in fb_mcp, "mcp feedback must contain screenshot path"
            assert Path(fb_mcp["screenshot"]).exists(), "Screenshot referenced in MCP result must exist"

            clear_res = mcp_server.clear_ui_feedback(str(proj_dir))
            assert clear_res.get("ok") is True, "clear_ui_feedback must return ok: True"
            assert not fb_json_path.exists(), "ui_feedback.json must be removed after clear"
            assert not fb_png_path.exists(), "ui_feedback.png must be removed after clear"

            # 8. GET /api/printer (Read-only status with fallback)
            req_printer = urllib.request.Request(f"{base_url}/api/printer")
            with urllib.request.urlopen(req_printer, timeout=5) as resp:
                assert resp.status == 200
                printer_data = json.loads(resp.read().decode("utf-8"))
                assert "connected" in printer_data, "Printer response must include connected status"

        finally:
            server.shutdown()
            server.server_close()


# ==============================================================================
# Standalone CLI Test Runner
# ==============================================================================

def run_all_tests() -> int:
    """Run all 5 test functions standalone and output clear summary."""
    suites = [
        ("test_snapshots_and_restore", test_snapshots_and_restore),
        ("test_ast_params", test_ast_params),
        ("test_empty_cut_detection", test_empty_cut_detection),
        ("test_features_follow_transforms", test_features_follow_transforms),
        ("test_diagnostics_3d_and_coord_transform", test_diagnostics_3d_and_coord_transform),
        ("test_ui_http_endpoints_and_feedback", test_ui_http_endpoints_and_feedback),
    ]

    print("=" * 72)
    print("Running SliceThePrompt Comprehensive Test Suite (WP-1 bis WP-5)")
    print("=" * 72)

    passed = 0
    failed = 0
    pending = 0

    for idx, (name, fn) in enumerate(suites, 1):
        print(f"[{idx}/{len(suites)}] {name} ... ", end="", flush=True)
        t0 = time.time()
        try:
            fn()
            elapsed = time.time() - t0
            print(f"PASS ({elapsed:.2f}s)")
            passed += 1
        except (ModuleNotFoundError, ImportError, AttributeError) as exc:
            elapsed = time.time() - t0
            print(f"PENDING / MISSING MODULE ({elapsed:.2f}s): {exc}")
            pending += 1
        except Exception as exc:
            elapsed = time.time() - t0
            print(f"FAIL ({elapsed:.2f}s): {type(exc).__name__}: {exc}")
            failed += 1

    print("=" * 72)
    print(f"Summary: {passed} PASSED, {failed} FAILED, {pending} PENDING IMPLEMENTATION")
    print("=" * 72)

    return 0 if (failed == 0 and pending == 0) else 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
