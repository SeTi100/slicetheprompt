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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
