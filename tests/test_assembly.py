"""Tests der Baugruppen (assembly.py → build_assembly → out/assembly.json → Studio-Zustand).

Alles synthetisch in einem temporären projects/-Ordner (Quader als Teile, kein Slicer, kein Drucker):
  * Format: TEILE/POSEN/platzierung, falsche Anzahl Lagen wird abgelehnt, parameter() liest nur den Kopf
  * Transformationen: Tf entspricht build123d Pos/Rot, Spiegelung, Umkehrung, Tf * Körper
  * Kollisionen: Überschneidung erkannt (Volumen, Tiefe, rote STL) – Gegenprobe mit Spalt (exakter Abstand)
  * Klemmkontakt: erlaubte Eindringtiefe trennt ok / Fehler
  * Geister: gleiche Form in mehreren Posen nur einmal gespeichert, Lage als Matrix
  * Studio: Projektliste erkennt Baugruppen, Zustand enthält URLs

Direkt:   uv run python tests/test_assembly.py
pytest:   uv run python -m pytest tests/test_assembly.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from build123d import Align, Box, Cylinder, Pos, Rot, export_step  # noqa: E402

from slicetheprompt import assembly as A  # noqa: E402
from slicetheprompt import config as cfg_mod  # noqa: E402

C = (Align.CENTER, Align.CENTER, Align.MIN)
MIN3 = (Align.MIN, Align.MIN, Align.MIN)


# ---------------------------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------------------------
class TmpProjects:
    """Temporärer projects/-Ordner; config.PROJECTS_DIR wird dafür umgebogen."""

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="stp_asm_"))
        self.old = cfg_mod.PROJECTS_DIR
        cfg_mod.PROJECTS_DIR = self.dir
        return self.dir

    def __exit__(self, *exc):
        cfg_mod.PROJECTS_DIR = self.old
        shutil.rmtree(self.dir, ignore_errors=True)


def make_part(root: Path, name: str, shape, params: str = "") -> Path:
    """Teil-Projekt mit model.py (nur Kopf) und out/model.step (Drucklage)."""
    d = root / name
    (d / "out").mkdir(parents=True)
    (d / "model.py").write_text(
        f'"""{name}"""\nfrom build123d import *\n{params}\npart = Box(1, 1, 1)\n', encoding="utf-8")
    time.sleep(0.01)
    export_step(shape, str(d / "out" / "model.step"))
    return d


def make_assembly(root: Path, name: str, body: str) -> Path:
    d = root / name
    (d / "out").mkdir(parents=True)
    (d / "assembly.py").write_text('"""Test-Baugruppe"""\nfrom build123d import *\n'
                                   'from slicetheprompt.assembly import *\n' + body, encoding="utf-8")
    return d


def two_boxes(root: Path) -> None:
    make_part(root, "2026-01-01-wuerfel-a", Box(10, 10, 10, align=MIN3), "L = 10.0\nH = 10.0\nKANTE = (1.0, 2.0)")
    make_part(root, "2026-01-01-wuerfel-b", Box(10, 10, 10, align=MIN3), "L = 10.0")


# ---------------------------------------------------------------------------------------------
# Transformationen
# ---------------------------------------------------------------------------------------------
def test_tf_matches_build123d():
    p = np.array([3.0, -2.0, 5.0])
    for loc in (Pos(1, 2, 3), Rot(0, 90, 0), Rot(30, 40, 50), Pos(5, 0, 0) * Rot(0, 0, 90)):
        tf = A.Tf(loc)
        box = loc * Pos(*p) * Box(0.001, 0.001, 0.001)
        c = box.center()
        assert np.allclose(tf * p, (c.X, c.Y, c.Z), atol=1e-6), (loc, tf * p, c)
    # rot()/pos() sind die gleichen Konventionen wie build123d
    assert np.allclose(A.rot(30, 40, 50).m, A.Tf(Rot(30, 40, 50)).m, atol=1e-12)
    assert np.allclose((A.pos(1, 2, 3) * A.rot(0, 0, 90)).m, A.Tf(Pos(1, 2, 3) * Rot(0, 0, 90)).m, atol=1e-12)
    # Location * Tf und Tf * Location ergeben Tf
    assert isinstance(Pos(1, 0, 0) * A.rot(0, 0, 90), A.Tf)
    assert isinstance(A.rot(0, 0, 90) * Pos(1, 0, 0), A.Tf)
    # Umkehrung
    t = A.pos(4, 5, 6) * A.rot(10, 20, 30) * A.spiegel("XZ")
    assert np.allclose((t * t.inv()).m, np.eye(4), atol=1e-12)


def test_tf_mirror_and_shape():
    s = A.spiegel("XZ")
    assert s.det < 0 and np.allclose(s * (1.0, 2.0, 3.0), (1, -2, 3))
    box = Box(10, 20, 5, align=MIN3)                       # y 0…20
    m = s * box
    bb = m.bounding_box()
    assert abs(m.volume - 1000.0) < 1e-6
    assert abs(bb.min.Y + 20) < 1e-6 and abs(bb.max.Y) < 1e-6
    assert m.is_valid
    # Spiegeln zweimal = Ausgangslage
    back = s * m
    assert abs((back & box).volume - 1000.0) < 1e-6
    # gedreht + verschoben bleibt deckungsgleich mit build123d
    t = A.pos(7, 0, 0) * A.rot(0, 0, 90)
    a, b = t * box, Pos(7, 0, 0) * Rot(0, 0, 90) * box
    assert abs((a & b).volume - 1000.0) < 1e-6


# ---------------------------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------------------------
def test_format_and_validation():
    with TmpProjects() as root:
        two_boxes(root)
        p = A.parameter("wuerfel-a")
        assert p.L == 10.0 and p.KANTE == (1.0, 2.0) and not hasattr(p, "part")
        d = make_assembly(root, "2026-01-01-baugruppe-x", '''
TEILE = {"A": Teil("2026-01-01-wuerfel-a"), "B": Teil("2026-01-01-wuerfel-b", anzahl=2)}
POSEN = {"eins": {"dx": 20}}
def platzierung(p):
    return {"A": pos(), "B": [pos(p["dx"], 0, 0)]}      # B braucht 2 Lagen
''')
        assert A.is_assembly(d)
        asm = A.load_assembly(d)
        try:
            A.instances_for_pose(asm, {"dx": 20})
            raise AssertionError("falsche Anzahl Lagen nicht erkannt")
        except ValueError as exc:
            assert "2 Lage" in str(exc)
        (d / "assembly.py").write_text((d / "assembly.py").read_text(encoding="utf-8").replace(
            '[pos(p["dx"], 0, 0)]', '[pos(p["dx"], 0, 0), pos(40, 0, 0)]'), encoding="utf-8")
        insts = A.instances_for_pose(A.load_assembly(d), {"dx": 20})
        assert [i["name"] for i in insts] == ["A", "B 1", "B 2"]
        # Vorlage anlegen
        r = A.new_assembly("neu", ["wuerfel-a", "wuerfel-b"])
        src = (root / r["project"] / "assembly.py").read_text(encoding="utf-8")
        assert 'Teil("2026-01-01-wuerfel-a")' in src and "def platzierung" in src
        A.load_assembly(root / r["project"])                # Vorlage ist lauffähig


# ---------------------------------------------------------------------------------------------
# Kollisionen
# ---------------------------------------------------------------------------------------------
COLLIDE = '''
TEILE = {"A": Teil("2026-01-01-wuerfel-a", farbe="#ff0000"), "B": Teil("2026-01-01-wuerfel-b")}
POSEN = {"ueberlappt": {"dx": 9.0}, "spalt": {"dx": 10.5}, "gespiegelt": {"dx": -10.5, "m": True}}
def platzierung(p):
    b = pos(p["dx"], 0, 0) * (spiegel("YZ") if p.get("m") else Tf())
    return {"A": pos(), "B": b}
def geister(p):
    return {"Stift": Geist(Cylinder(1, 30), lage=pos(5, 5, 5), farbe="#888888")}
KONTAKT = {("A", "Stift"): 0.0}
GRENZE_MM3 = 0.5
'''


def test_collision_detected_and_counter_check():
    with TmpProjects() as root:
        two_boxes(root)
        d = make_assembly(root, "2026-01-01-baugruppe-k", COLLIDE)
        res = A.build_assembly(d.name)
        aj = json.loads((d / "out" / "assembly.json").read_text(encoding="utf-8"))
        posen = {p["name"]: p for p in aj["posen"]}

        # 1) Überlappung 1 × 10 × 10 mm: erkannt, Volumen und Tiefe exakt, rote STL vorhanden
        u = posen["ueberlappt"]
        assert not u["ok"]
        ov = [x for x in u["ueberschneidungen"] if {x["a"], x["b"]} == {"A", "B"}]
        assert len(ov) == 1 and abs(ov[0]["volumen_mm3"] - 100.0) < 1e-3
        assert (d / "out" / ov[0]["stl"]).stat().st_size > 84
        assert any(not c["ok"] and c["paar"] == "A + B" for c in u["checks"])

        # 2) Gegenprobe: 0,5 mm Spalt → keine Überschneidung, exakter Abstand 0,5
        s = posen["spalt"]
        assert not [x for x in s["ueberschneidungen"] if {x["a"], x["b"]} == {"A", "B"}]
        gap = [a for a in s["abstaende"] if {a["a"], a["b"]} == {"A", "B"}][0]
        assert gap["genau"] and abs(gap["mm"] - 0.5) < 1e-6

        # 3) gespiegelte Platzierung: Matrix mit det < 0, Teil liegt bei x −20,5…−10,5
        g = posen["gespiegelt"]
        inst_b = [i for i in g["instanzen"] if i["name"] == "B"][0]
        assert inst_b["gespiegelt"] and abs(inst_b["bbox"][0][0] + 20.5) < 1e-3 and abs(inst_b["bbox"][1][0] + 10.5) < 1e-3
        tf = A.Tf(inst_b["matrix"])
        assert tf.det < 0

        # 4) Geist „Stift“ steckt 5 mm tief in A: Klemmkontakt 0 mm erlaubt → Fehler in allen Posen
        for p in aj["posen"]:
            st = [x for x in p["ueberschneidungen"] if "Stift" in (x["a"], x["b"]) and "A" in (x["a"], x["b"])]
            assert st and st[0]["art"] == "klemmkontakt" and not st[0]["ok"]
            assert st[0]["tiefe_mm"] > 4.0
        # gleiche Geist-Form in allen Posen → eine Datei
        files = {gg["stl"] for p in aj["posen"] for gg in p["geister"]}
        assert len(files) == 1
        assert not res["ok"] and res["png"] and Path(res["png"]).exists()


def test_contact_limit_separates_ok_and_error():
    with TmpProjects() as root:
        two_boxes(root)
        body = '''
TEILE = {"A": Teil("2026-01-01-wuerfel-a"), "B": Teil("2026-01-01-wuerfel-b")}
POSEN = {"0,2 tief": {"dx": 9.8}, "1,0 tief": {"dx": 9.0}}
def platzierung(p):
    return {"A": pos(), "B": pos(p["dx"], 0, 0)}
KONTAKT = {("A", "B"): 0.3}
'''
        d = make_assembly(root, "2026-01-01-baugruppe-c", body)
        A.build_assembly(d.name)
        aj = json.loads((d / "out" / "assembly.json").read_text(encoding="utf-8"))
        flach, tief = aj["posen"]
        assert flach["ok"] and abs(flach["ueberschneidungen"][0]["tiefe_mm"] - 0.2) < 0.01
        assert not tief["ok"] and abs(tief["ueberschneidungen"][0]["tiefe_mm"] - 1.0) < 0.01


def test_penetration_depth_slab():
    a = Box(20, 20, 10, align=MIN3)
    b = Pos(0, 0, 9.7) * Box(20, 20, 10, align=MIN3)
    inter = A.intersection(a, b)
    assert abs(A.penetration_depth(inter, a, b) - 0.3) < 1e-3
    # schräger Keil auf runder Kante (wie Handy-Lippe): Tiefe < Hüllquader-Seite
    cyl = Pos(0, 0, 0) * Rot(0, 90, 0) * Cylinder(5, 20)
    blk = Pos(0, 0, 4.6) * Box(30, 30, 10, align=C)
    inter = A.intersection(cyl, blk)
    assert abs(A.penetration_depth(inter, cyl, blk) - 0.4) < 0.02


# ---------------------------------------------------------------------------------------------
# Studio
# ---------------------------------------------------------------------------------------------
def test_studio_lists_assembly_and_state():
    from slicetheprompt import ui_server

    with TmpProjects() as root:
        two_boxes(root)
        d = make_assembly(root, "2026-01-01-baugruppe-k", COLLIDE)
        items = ui_server.list_projects_summary(root)
        it = [x for x in items if x["project"] == d.name][0]
        assert it["kind"] == "assembly" and it["built"] is False
        A.build_assembly(d.name)
        it = [x for x in ui_server.list_projects_summary(root) if x["project"] == d.name][0]
        assert it["passed"] is False and it["errors_count"] >= 1 and it["pose_count"] == 3
        st = ui_server.build_project_state(d)
        assert st["kind"] == "assembly"
        aj = st["assembly"]
        assert all(t["url"].startswith(f"/api/projects/{d.name}/files/out/assembly/") for t in aj["teile"])
        assert all(g["url"] for p in aj["posen"] for g in p["geister"])
        assert all(u["url"] for p in aj["posen"] for u in p["ueberschneidungen"])
        assert "def platzierung" in st["model_code"]


def run_all_tests() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        t0 = time.time()
        try:
            t()
            print(f"OK    {t.__name__} ({time.time() - t0:.1f} s)")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"FEHLER {t.__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} Tests ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
