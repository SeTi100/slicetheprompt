"""Tests der CAD-Bausteine (lib/), der Baustein-Übersicht und der Körper-Anzahl in spec.yaml.
python -m pytest tests  (oder direkt ausführen)."""

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build123d import Align, Box, Cylinder, Pos, extrude  # noqa: E402

from lib import parts  # noqa: E402
from lib.gears import gear_face, zahnmasse  # noqa: E402
from meshgen import box  # noqa: E402
from slicetheprompt import meshcheck as mc  # noqa: E402
from slicetheprompt.libref import lib_reference  # noqa: E402

C = (Align.CENTER, Align.CENTER, Align.MIN)
CFG = {
    "printer": {"usable": {"x": 250, "y": 250, "z": 290}},
    "rules": {"min_wall": 0.84, "functional_wall": 1.6, "max_overhang_deg": 45, "max_bridge": 10,
              "min_bed_contact_mm2": 50},
}


def test_sector_volumes():
    assert abs(parts.sector(50, 150, 250, 4).volume - math.pi * 50 ** 2 * 100 / 360 * 4) < 0.01
    ring = parts.sector(53.5, 150, 250, 7, r_in=42)
    assert abs(ring.volume - math.pi * (53.5 ** 2 - 42 ** 2) * 100 / 360 * 7) < 0.01
    assert abs(parts.sector(10, 0, 360, 2, r_in=5).volume - math.pi * 75 * 2) < 0.01
    bb = parts.sector(10, 0, 90, 1).bounding_box()          # Viertel im 1. Quadranten
    assert bb.min.X > -1e-6 and bb.min.Y > -1e-6 and abs(bb.max.X - 10) < 1e-6


def test_bottom_inset_with_hole():
    p = Box(20, 20, 5, align=C) - Cylinder(4, 5, align=C)
    q = parts.bottom_inset(p, 0.2, height=0.4)
    ring = (19.6 ** 2 - math.pi * 4.2 ** 2)
    assert q.is_valid and len(q.solids()) == 1
    assert abs((p.volume - q.volume) - (p.volume / 5 - ring) * 0.4) < 0.01
    assert abs(q.bounding_box().min.Z) < 1e-6


def test_bottom_chamfer_falls_back_to_inset():
    parts.WARNINGS.clear()
    zm = zahnmasse(16, 1.0)
    g = extrude(gear_face(16, 1.0, zm["r_kopf"], zm["r_fuss"], zm["s"]), amount=7)
    g2 = parts.bottom_chamfer(g, 1.2)                      # 1,2 mm Fase geht an Modul-1-Zähnen nicht
    assert g2.is_valid and g2.volume < g.volume
    assert any("bottom_inset" in w for w in parts.WARNINGS), parts.WARNINGS


def test_lib_reference_lists_bausteine():
    ref = lib_reference()
    names = [s.split("(")[0] for s in ref["lib.parts"]]
    assert {"sector", "bottom_chamfer", "screw_hole_cut", "stencil"} <= set(names)
    assert any(s.startswith("gear_solid(face, h, stufe=0.0)") for s in ref["lib.gears"])


def test_koerper_in_spec():
    t = np.concatenate([box(10, 10, 3), box(10, 10, 3) + [20, 0, 0], box(10, 10, 3) + [40, 0, 0]])
    count = lambda rep: next(c for c in rep.checks if c.rule == "Anzahl Körper")
    warn = count(mc.analyse(t, CFG))
    assert not warn.ok and warn.severity == "warn"
    assert count(mc.analyse(t, CFG, spec={"koerper": 3})).ok
    bad = count(mc.analyse(t, CFG, spec={"koerper": 2}))
    assert not bad.ok and bad.severity == "error"


def test_zone_summary_manifold():
    from slicetheprompt.pipeline import zone_summary
    t = box(4, 4, 4)
    diag = {"open_edges": [{"label": "2 offene / nicht-manifolde Kanten", "severity": "error",
                            "bbox_model": [0, 0, 0, 1, 1, 1], "bbox_print": [0, 0, 0, 1, 1, 1], "tri_indices": [0]}],
            "features": []}
    failed = [{"rule": "manifold (keine Kante an >2 Flächen)"}]
    z = zone_summary(diag, t, failed)
    assert len(z) == 1 and "manifold" in z[0]["hinweis"]
    assert zone_summary(diag, t, []) == []                 # ohne fehlgeschlagene Regel keine Zone


def test_worker_reports_errors_as_result():
    from slicetheprompt.mcp_server import _worker, code_stand
    r = _worker("check", "gibt-es-nicht-xyz")
    assert r["ok"] is False and "nicht gefunden" in r["error"]
    assert {"server_gestartet", "veraltet"} <= set(code_stand())


CFG_W = {**CFG, "rules": {**CFG["rules"], "layer_height": 0.2, "line_width": 0.42}}


def _mesh(part):
    import tempfile

    from build123d import export_stl

    from slicetheprompt.stlio import read_stl
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "m.stl"
        export_stl(part, str(f), tolerance=0.01, angular_tolerance=0.1)
        return read_stl(f)


def _rule(rep, text):
    return [c for c in rep.checks if text in c.rule]


def test_wall_classes_detail_edge_wall():
    from build123d import Polygon, Rot
    # Platte mit Gravur: Nuten 0,5 breit, 0,6 tief, Teilung 1 mm → Stege 0,5 = Oberflächendetail
    plate = Box(30, 20, 3, align=C)
    for i in range(-10, 11):
        plate -= Pos(i, 0, 2.4) * Box(0.5, 10, 1, align=C)
    rep = mc.analyse(_mesh(plate), CFG_W)
    assert _rule(rep, "Mindestwandstärke")[0].ok
    assert not _rule(rep, "Oberflächendetails")[0].ok
    # Keil mit 30°-Schneide → spitze Kante, keine Wand
    keil = Rot(90, 0, 0) * extrude(Polygon((0, 0), (20, 0), (0, 20 * math.tan(math.radians(30))), align=None), 10)
    rep = mc.analyse(_mesh(keil), CFG_W)
    assert _rule(rep, "Mindestwandstärke")[0].ok
    assert not _rule(rep, "Spitze Kanten")[0].ok
    # frei stehende Finne 0,5 mm → echte Wand, Fehler
    finne = Box(20, 20, 2, align=C) + Box(0.5, 10, 10, align=C)
    rep = mc.analyse(_mesh(finne), CFG_W)
    assert not _rule(rep, "Mindestwandstärke")[0].ok
    # dieselbe Finne mit role="duenn" → nur Hinweis
    rep = mc.analyse(_mesh(finne), CFG_W, thin_boxes=[((-0.25, -5, 0), (0.25, 5, 10))])
    assert _rule(rep, "Mindestwandstärke")[0].ok
    assert _rule(rep, "Bewusst dünn")[0].severity == "info"


def test_zone_groups_and_checks_ge():
    from slicetheprompt.pipeline import zone_summary
    t = box(4, 4, 4)
    z = lambda x: {"label": "Oberflächendetail (0.50 mm < 0.84 mm, ≤ 3 Schichten)", "severity": "warn",
                   "klasse": "detail", "min_mm": 0.5 + x / 100, "bbox_model": [x, 0, 2, x + 0.1, 1, 3],
                   "bbox_print": [x, 0, 2, x + 0.1, 1, 3], "tri_indices": [x]}
    diag = {"thin_walls": [z(i) for i in range(3)],
            "features": [{"name": "striche", "role": "cut", "bbox_model": [-1, -1, 1, 5, 2, 4]}]}
    out = zone_summary(diag, t, [{"rule": "Oberflächendetails dünner als min_wall (≤ 3 Schichten)"}])
    assert len(out) == 1 and out[0]["art"].startswith("3 × Oberflächendetail") and out[0]["feature"] == "striche"
    # checks mit ">=": über den CAD-Runner
    import json
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        m = Path(td) / "model.py"
        m.write_text("from build123d import *\npart = Box(10, 10, 2)\n"
                     "checks = {'Rand': (3.2, '>=', 3), 'Höhe': (2.0, '<=', 1.5), 'Breite': (10.0, 10, 0.01)}\n",
                     encoding="utf-8")
        subprocess.run([sys.executable, "-m", "slicetheprompt._cad_runner", str(m), td], capture_output=True,
                       cwd=Path(__file__).resolve().parents[1], timeout=120)
        ch = {c["name"]: c for c in json.loads((Path(td) / "build.json").read_text(encoding="utf-8"))["checks"]}
    assert ch["Rand"]["ok"] and ch["Rand"]["soll"] == "≥ 3"
    assert not ch["Höhe"]["ok"] and ch["Höhe"]["soll"] == "≤ 1.5"
    assert ch["Breite"]["ok"]


def test_inlay_depth_and_stroke():
    grund = Box(40, 20, 3, align=C)
    duenn = Pos(0, 0, 2.7) * Box(10, 0.3, 0.3, align=C)      # 0,3 tief, 0,3 breit → beides Fehler
    rep = mc.analyse_bodies({"grund": _mesh(grund - duenn), "schrift": _mesh(duenn)}, CFG_W)
    fehler = {c.rule for c in rep.checks if not c.ok and c.severity == "error"}
    assert "[schrift] Einlage-Tiefe" in fehler and "[schrift] Strichbreite (1. Perzentil)" in fehler


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
