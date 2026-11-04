"""Tests der Netzprüfung ohne CAD-Kern:  python -m pytest tests  (oder direkt ausführen)."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from meshgen import box, voxels_to_tris  # noqa: E402
from printlab import meshcheck as mc  # noqa: E402
from printlab.stlio import read_stl, write_stl  # noqa: E402

CFG = {
    "printer": {"usable": {"x": 250, "y": 250, "z": 290}},
    "rules": {"min_wall": 0.84, "functional_wall": 1.6, "max_overhang_deg": 45, "max_bridge": 10,
              "min_bed_contact_mm2": 50},
}


def test_cube_closed_and_sized(tmp_path=Path("/tmp")):
    t = box(20, 20, 20)
    p = tmp_path / "cube.stl"
    write_stl(p, t)
    t2 = read_stl(p)
    rep = mc.analyse(t2, CFG, spec={"bbox": [20, 20, 20]}, print_orientation=True)
    d = rep.as_dict()
    assert d["passed"], d["errors"]
    assert d["metrics"]["size_mm"] == [20, 20, 20]
    assert abs(d["metrics"]["volume_mm3"] - 8000) < 1e-6
    assert d["metrics"]["print"]["bed_contact_mm2"] == 400


def test_thin_wall_detected():
    # Box 20x20x10 mit 1 mm Wand (Voxel 0.5 mm -> 2 Voxel), oben offen
    v = np.ones((40, 40, 20), bool)
    v[2:-2, 2:-2, 2:] = False
    t = voxels_to_tris(v, pitch=0.5)
    rep = mc.analyse(t, CFG, print_orientation=True).as_dict()
    wt = rep["metrics"]["wall_thickness"]
    assert 0.9 <= wt["p1"] <= 1.05, wt
    warn_rules = [w["rule"] for w in rep["warnings"]]
    assert any("Funktionsteil" in r for r in warn_rules)


def test_long_bridge_and_open_mesh():
    # Brücke: zwei Pfeiler 5x10x10, 20 mm Abstand, Deckel 30x10x2
    v = np.zeros((30, 10, 12), bool)
    v[0:5, :, 0:10] = True
    v[25:30, :, 0:10] = True
    v[:, :, 10:12] = True
    t = voxels_to_tris(v)
    rep = mc.analyse(t, CFG, print_orientation=True).as_dict()
    bridges = rep["metrics"]["print"]["bridges"]
    assert bridges and bridges[0]["span_mm"] == 20.0 and not bridges[0]["ok"], bridges
    # Tasche mit Decke, ringsum gestützt: 6 x 30 Hohlraum -> Spannweite 6
    v = np.ones((40, 20, 10), bool)
    v[5:35, 7:13, 2:6] = False
    rep3 = mc.analyse(voxels_to_tris(v), CFG, print_orientation=True).as_dict()
    b3 = rep3["metrics"]["print"]["bridges"]
    assert b3 and abs(b3[0]["span_mm"] - 6.0) < 0.01 and b3[0]["ok"], b3
    # offenes Netz
    t_open = t[:-2]
    rep2 = mc.analyse(t_open, CFG).as_dict()
    assert not rep2["passed"]


def test_orientation_prefers_flat():
    # Platte 40x40x3 hochkant modelliert -> soll flach gelegt werden
    t = box(40, 3, 40)
    name, R, scores = mc.best_orientation(t, CFG["rules"])
    t2 = mc.rotate(t, R)
    size = t2.reshape(-1, 3).max(0) - t2.reshape(-1, 3).min(0)
    assert abs(size[2] - 3) < 1e-6, (name, scores)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
