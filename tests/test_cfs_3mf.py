"""Tests für CFS-Auswertung, Spulenwahl, Mehrkörper-Prüfung und 3MF-Export (ohne Drucker/Slicer)."""

import json
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from meshgen import voxels_to_tris  # noqa: E402
from slicetheprompt import cfs, meshcheck as mc, threemf  # noqa: E402
from slicetheprompt.slicer import gcode_info, gcode_safety  # noqa: E402

# Zustand wie vom Hi gemeldet (4 PLA-Spulen in CFS 1)
BOX = {
    "state": "connect",
    "same_material": [["101001", "06667AB", ["T1A"], "PLA"], ["101001", "0FFFFFF", ["T1B"], "PLA"],
                      ["101001", "09D432C", ["T1C"], "PLA"], ["101001", "0000000", ["T1D"], "PLA"]],
    "map": {s: s for s in cfs.SLOT_ORDER},
    "T1": {"state": "connect", "sn": "X", "remain_len": ["100", "100", "65", "100"],
           "color_value": ["06667AB", "0FFFFFF", "09D432C", "0000000"],
           "material_type": ["101001", "101001", "101001", "101001"]},
    "T2": {"state": "None", "sn": "-1"}, "T3": {"state": "None", "sn": "-1"}, "T4": {"state": "None", "sn": "-1"},
}
CFG = {
    "printer": {"usable": {"x": 250, "y": 250, "z": 290}},
    "rules": {"min_wall": 0.84, "functional_wall": 1.6, "max_overhang_deg": 45, "max_bridge": 10,
              "min_bed_contact_mm2": 50, "min_inlay": 0.4},
    "limits": {"max_nozzle_temp": 260, "max_bed_temp": 85},
}


def test_parse_and_choose():
    st = cfs.parse_box(BOX)
    assert [s["slot"] for s in st["slots"]] == ["T1A", "T1B", "T1C", "T1D"]
    assert [s["tool"] for s in st["slots"]] == [0, 1, 2, 3]
    assert cfs.choose_slot("schwarz", "pla", st)["slot"] == "T1D"
    assert cfs.choose_slot("weiss", "pla", st)["slot"] == "T1B"
    assert cfs.choose_slot("t1c", None, st)["slot"] == "T1C"
    try:
        cfs.choose_slot(None, "petg", st)
        raise AssertionError("PETG darf nicht gefunden werden")
    except LookupError:
        pass
    cat = {"Hyper PLA @Creality Hi 0.2 nozzle": {"filament_id": "01001"},
           "Hyper PLA @Creality Hi 0.4 nozzle": {"filament_id": "01001"}}
    assert cfs.match_profile(st["slots"][0], cat, {}) == "Hyper PLA @Creality Hi 0.4 nozzle"


def test_remapped_slots():
    box = dict(BOX, map={**BOX["map"], "T1A": "T1C", "T1C": "T1A"})
    st = cfs.parse_box(box)
    assert not st["map_identity"]
    assert next(s for s in st["slots"] if s["slot"] == "T1C")["tool"] == 0


def _inlay_bodies():
    v = np.ones((80, 40, 8), bool)
    v[20:60, 14:26, 7:] = False
    ins = np.zeros((80, 40, 8), bool)
    ins[20:60, 14:26, 7:] = True
    return {"platte": voxels_to_tris(v, 0.5), "schrift": voxels_to_tris(ins, 0.5)}


def test_bodies_analysis():
    b = _inlay_bodies()
    rep = mc.analyse_bodies(b, CFG, spec={"bbox": [40, 20, 4]}, print_bodies=b).as_dict()
    assert rep["passed"], rep["errors"]
    # Einlage liegt auf der Platte auf → kein Überhang
    assert rep["metrics"]["print"]["overhang_mm2_without_ok_bridges"] < 1.0, rep["metrics"]["print"]


def test_3mf_roundtrip():
    b = _inlay_bodies()
    with tempfile.TemporaryDirectory() as td:
        p = threemf.write_multipart_3mf(Path(td) / "t.3mf", [("platte", b["platte"], 2), ("schrift", b["schrift"], 4)])
        threemf.embed_project_settings(p, {"from": "project", "filament_colour": ["#FFFFFF", "#000000"]}, "7.3.1")
        parts = threemf.read_parts(p)
        assert [(x["name"], x["extruder"]) for x in parts] == [("platte", 2), ("schrift", 4)]
        assert all(x["triangles"] > 0 for x in parts)
        with zipfile.ZipFile(p) as z:
            assert "Metadata/creality.config" in z.namelist()
            assert json.loads(z.read("Metadata/project_settings.config"))["from"] == "project"
            assert b"Creality_Print V7.3.1" in z.read("3D/3dmodel.model")


def test_gcode_tools():
    with tempfile.TemporaryDirectory() as td:
        g = Path(td) / "t.gcode"
        g.write_text("M104 S220\nM140 S60\nT1\nG1 X1\nT3\nG1 X2\nT1\n; filament used [g] = 1.0, 2.0, 3.0, 4.0\n"
                     "; printer_settings_id = Creality Hi 0.4 nozzle\n; filament_type = PLA;PLA;PLA;PLA\n")
        info = gcode_info(g)
        assert info["tools_used"] == [1, 3] and info["tool_changes"] == 2
        assert gcode_safety(info, CFG, expected_tools={1: "PLA", 3: "PLA"}) == []
        assert gcode_safety(info, CFG, expected_tools={0: "PLA"})   # falsche Zuordnung wird erkannt


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
