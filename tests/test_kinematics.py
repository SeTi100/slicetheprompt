"""Tests des Gelenk-Lösers (Python und Studio-JavaScript) und der Netz-Kollision.

  * Kurbeltrieb: Kolbenweg = Lehrbuchformel, 1 Freiheitsgrad, ohne mechanismus-eigene Formel
  * Viergelenk (Kurbelschwinge): Schliessbedingung erfüllt, Koppelwinkel = Geometrie
  * Schraube (Vorschub = Steigung), Getriebe (Übersetzung), Grenzen, Freiheitsgrade mit Freeze
  * Baugruppe mit GELENKE: Pose per Gelenkwert gelöst; widersprüchliche Platzierung wird gemeldet;
    exakte Prüfung einer bewegten Stellung (check_state)
  * JavaScript (Node, falls installiert): gleiche Ergebnisse wie Python; Netz-Kollision duldet
    aufliegende Flächen, erkennt 0,1 mm Eindringen, Gegenprobe mit Spalt

Direkt:   uv run python tests/test_kinematics.py
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from slicetheprompt import kinematics as K  # noqa: E402

R_, L_ = 20.0, 60.0


def kurbeltrieb(frozen=None):
    J = [K.Fest("Rahmen").to_json(),
         K.Drehung("Kurbel", "Rahmen", achse=(0, 0, 1), punkt=(0, 0, 0), name="Kurbel").to_json(),
         K.Drehung("Pleuel", "Kurbel", achse=(0, 0, 1), punkt=(R_, 0, 0)).to_json(),
         K.Drehung("Kolben", "Pleuel", achse=(0, 0, 1), punkt=(R_ + L_, 0, 0)).to_json(),
         K.Schiebung("Kolben", "Rahmen", achse=(1, 0, 0), name="Kolbenweg", grenzen=(-41, 1)).to_json()]
    m = K.Mechanismus(["Rahmen", "Kurbel", "Pleuel", "Kolben"], J, frozen=frozen)
    m.set_centers({"Kurbel": (R_ / 2, 0, 0), "Pleuel": (R_ + L_ / 2, 0, 0), "Kolben": (R_ + L_, 0, 0)})
    return m, J


def kolbenweg_formel(th_deg):
    th = math.radians(th_deg)
    return R_ * math.cos(th) + math.sqrt(L_ ** 2 - (R_ * math.sin(th)) ** 2) - (R_ + L_)


def test_kurbeltrieb_formel_und_freiheitsgrad():
    m, _ = kurbeltrieb()
    assert m.dof() == 1
    for th in (30, 90, 150, 180, 250, 360):
        res = m.solve({"Kurbel": th})
        assert res["ok"], res
        assert abs(res["gelenke"]["Kolbenweg"] - kolbenweg_formel(th)) < 1e-6, (th, res["gelenke"])
    # Kolben fest → nichts mehr beweglich (geschlossene Kette blockiert). Ausserhalb des Totpunkts
    # prüfen: bei 0° liegen Kurbel und Pleuel in einer Linie, dort ist die Kette momentan beweglich.
    m.solve({"Kurbel": 90})
    _, J = kurbeltrieb()
    disp = {b: m.displacement(b) for b in m.bodies}
    m2 = K.Mechanismus(m.bodies, K.move_joints(J, disp), frozen={"Kolben"})
    m2.set_centers({b: m.point(b, m.c0[b]) for b in m.bodies})
    assert m2.dof() == 0
    m3 = K.Mechanismus(m.bodies, K.move_joints(J, disp))
    m3.set_centers({b: m.point(b, m.c0[b]) for b in m.bodies})
    assert m3.dof() == 1


def test_viergelenk():
    # Kurbelschwinge: Steg 0→(80,0), Kurbel 20, Koppel 70, Schwinge 50
    a, b, c, d = 20.0, 70.0, 50.0, 80.0
    # Grundstellung: Kurbel bei 90°, Koppelpunkt aus Geometrie
    A = np.array([0, a, 0.0])
    D = np.array([d, 0, 0.0])
    # Schnittpunkt der Kreise (A, b) und (D, c), oberer
    dist = np.linalg.norm(D - A)
    x = (b ** 2 - c ** 2 + dist ** 2) / (2 * dist)
    h = math.sqrt(b ** 2 - x ** 2)
    e = (D - A) / dist
    B = A + x * e + h * np.array([-e[1], e[0], 0])
    J = [K.Fest("Steg").to_json(),
         K.Drehung("Kurbel", "Steg", punkt=(0, 0, 0), name="Antrieb").to_json(),
         K.Drehung("Koppel", "Kurbel", punkt=tuple(A)).to_json(),
         K.Drehung("Schwinge", "Koppel", punkt=tuple(B)).to_json(),
         K.Drehung("Schwinge", "Steg", punkt=tuple(D), name="Schwinge").to_json()]
    m = K.Mechanismus(["Steg", "Kurbel", "Koppel", "Schwinge"], J)
    m.set_centers({"Kurbel": A / 2, "Koppel": (A + B) / 2, "Schwinge": (B + D) / 2})
    assert m.dof() == 1
    for drive in (20, 60, 120):
        res = m.solve({"Antrieb": drive})
        assert res["ok"], res
        # Längen bleiben: Koppel- und Schwingenlänge aus den bewegten Gelenkpunkten
        pA = m.point("Kurbel", A)
        pB = m.point("Schwinge", B)
        assert abs(np.linalg.norm(pB - pA) - b) < 1e-6
        assert abs(np.linalg.norm(pB - D) - c) < 1e-6
        ang = math.degrees(math.atan2(pA[1], pA[0])) - 90
        assert abs(((ang - drive) + 180) % 360 - 180) < 1e-6


def test_schraube_getriebe_grenzen():
    J = [K.Fest("Rahmen").to_json(),
         K.Schraube("Mutter", "Rahmen", achse=(0, 0, 1), punkt=(0, 0, 0), steigung=0.7, name="Gewinde").to_json(),
         K.Drehung("Rad1", "Rahmen", punkt=(30, 0, 0), name="Rad1").to_json(),
         K.Drehung("Rad2", "Rahmen", punkt=(60, 0, 0), name="Rad2", grenzen=(-90, 90)).to_json(),
         K.Getriebe("Rad1", "Rad2", -0.5).to_json()]
    m = K.Mechanismus(["Rahmen", "Mutter", "Rad1", "Rad2"], J)
    m.set_centers({"Mutter": (0, 0, 0), "Rad1": (30, 0, 0), "Rad2": (60, 0, 0)})
    assert m.dof() == 2                                   # Gewinde + Zahnradpaar
    res = m.solve({"Gewinde": 720})
    z = m.displacement("Mutter")[2, 3]
    assert res["ok"] and abs(z - 1.4) < 1e-6            # 2 Umdrehungen × 0,7 mm
    res = m.solve({"Rad1": 100})
    assert res["ok"] and abs(res["gelenke"]["Rad2"] + 50) < 1e-6
    # Grenze: Rad2 höchstens −90° → Antrieb 300° nicht erreichbar
    res = m.solve({"Rad1": 300})
    assert not res["ok"] or abs(res["gelenke"]["Rad2"]) <= 90 + 1e-6


def test_move_joints_keeps_reference():
    m, J = kurbeltrieb()
    m.solve({"Kurbel": 90})
    disp = {b: m.displacement(b) for b in m.bodies}
    m2 = K.Mechanismus(m.bodies, K.move_joints(J, disp))
    m2.set_centers({b: m.point(b, m.c0[b]) for b in m.bodies})
    c = m2.coords()
    assert abs(c["Kurbel"] - 90) < 1e-6 and abs(c["Kolbenweg"] - kolbenweg_formel(90)) < 1e-6
    assert max(abs(v) for v in m2.residuals((m2.R, m2.c), extra=False)) < 1e-9


# ---------------------------------------------------------------------------------------------
# Baugruppe mit Gelenken
# ---------------------------------------------------------------------------------------------
def test_assembly_with_joints_and_check_state():
    from build123d import Align, Box, export_step

    from slicetheprompt import assembly as A
    from slicetheprompt import config as cfg_mod

    MIN3 = (Align.MIN, Align.MIN, Align.MIN)
    root = Path(tempfile.mkdtemp(prefix="stp_kin_"))
    old = cfg_mod.PROJECTS_DIR
    cfg_mod.PROJECTS_DIR = root
    try:
        for name, shape in (("2026-01-01-schiene", Box(100, 10, 5, align=MIN3)),
                            ("2026-01-01-schlitten", Box(10, 10, 10, align=MIN3))):
            (root / name / "out").mkdir(parents=True)
            (root / name / "model.py").write_text("part = None\n", encoding="utf-8")
            time.sleep(0.01)
            export_step(shape, str(root / name / "out" / "model.step"))
        d = root / "2026-01-01-baugruppe-schiene"
        (d / "out").mkdir(parents=True)
        (d / "assembly.py").write_text('''
from slicetheprompt.assembly import *
TEILE = {"Schiene": Teil("2026-01-01-schiene"), "Schlitten": Teil("2026-01-01-schlitten"),
         "Anschlag": Teil("2026-01-01-schlitten")}
POSEN = {"Start": {}, "40 mm": {"gelenke": {"Weg": 40}}, "falsch": {"y": 3}}
def platzierung(p):
    return {"Schiene": pos(), "Schlitten": pos(0, p.get("y", 0), 5), "Anschlag": pos(80, 0, 5)}
GELENKE = [Fest("Schiene"), Fest("Anschlag", "Schiene"),
           Schiebung("Schlitten", "Schiene", achse=(1, 0, 0), name="Weg", grenzen=(0, 90))]
''', encoding="utf-8")
        res = A.build_assembly(d.name)
        aj = json.loads((d / "out" / "assembly.json").read_text(encoding="utf-8"))
        kin = aj["kinematik"]
        assert kin["freiheitsgrade"] == 1 and kin["boden"] == ["Schiene"]
        p40 = aj["posen"][1]
        sl = [i for i in p40["instanzen"] if i["name"] == "Schlitten"][0]
        assert abs(sl["matrix"][3] - 40) < 1e-6 and abs(p40["gelenkwerte"]["Weg"] - 40) < 1e-6
        # Pose „falsch“ verschiebt den Schlitten quer zur Schiebung → Hinweis
        assert any("falsch" in h and "Weg" in h for h in res["hinweise"]), res["hinweise"]
        # exakte Prüfung einer bewegten Stellung: Schlitten 75 mm weiter → steckt 5 mm im Anschlag
        D = np.eye(4)
        D[0, 3] = 75.0
        r = A.check_state(d.name, 0, {"Schlitten": D.ravel().tolist()})
        bad = [c for c in r["checks"] if not c["ok"]]
        assert bad and abs(bad[0]["volumen_mm3"] - 500.0) < 1e-3, r
        # Gegenprobe: 69 mm → 1 mm Spalt, frei
        D[0, 3] = 69.0
        r = A.check_state(d.name, 0, {"Schlitten": D.ravel().tolist()})
        assert r["ok"]
        gap = [a for a in r["abstaende"] if {a["a"], a["b"]} == {"Schlitten", "Anschlag"}][0]
        assert abs(gap["mm"] - 1.0) < 1e-6
    finally:
        cfg_mod.PROJECTS_DIR = old
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------------------------
# JavaScript (Studio) – gleiche Rechnung, Netz-Kollision
# ---------------------------------------------------------------------------------------------
JS_TEST = r"""
import { pathToFileURL } from "node:url";
const ui = process.argv[2];
const { Mechanism, freezePlan } = await import(pathToFileURL(ui + "/kinematics.js").href);
const { buildBVH, meshesPenetrate } = await import(pathToFileURL(ui + "/collide.js").href);
const input = JSON.parse(process.argv[3]);
const out = {};
// Kurbeltrieb
const m = new Mechanism(input.kin, input.world);
out.dof = m.dof();
out.kolben = {};
for (const th of [30, 90, 180, 250]) out.kolben[th] = m.solve({ drives: { Kurbel: th } }).gelenke.Kolbenweg;
// Auswahl: nur Kurbel → Pleuel fährt auf der Kurbel mit, Kolben steht → blockiert (Totpunkt meiden: 90°)
const w90 = {};
for (const b of input.kin.koerper) w90[b] = m.worldOf(b);
const m90 = new Mechanism(input.kin, w90);
m90.solve({ drives: { Kurbel: 90 } });
for (const b of input.kin.koerper) w90[b] = m90.worldOf(b);
const p1 = freezePlan(input.kin, new Set(["Kurbel"]));
out.plan_kurbel = { locks: p1.locks, frozen: [...p1.frozen].sort(), dof: new Mechanism(input.kin, w90, p1.frozen, p1.locks).dof() };
const p3 = freezePlan(input.kin, new Set(["Kurbel", "Pleuel", "Kolben"]));
const m3 = new Mechanism(input.kin, w90, p3.frozen, p3.locks);
out.plan_alle = { dof: m3.dof(), kolben: m3.solve({ drives: { Kurbel: 150 } }).gelenke.Kolbenweg };
// Netze: Quader 10×10×10 auf Quader 20×20×5
function box(x0, y0, z0, x1, y1, z1) {
  const v = [[x0,y0,z0],[x1,y0,z0],[x1,y1,z0],[x0,y1,z0],[x0,y0,z1],[x1,y0,z1],[x1,y1,z1],[x0,y1,z1]];
  const f = [[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],[1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]];
  return new Float32Array(f.flatMap((t) => t.flatMap((i) => v[i])));
}
const I = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1];
const T = (x, y, z) => [1,0,0,x, 0,1,0,y, 0,0,1,z, 0,0,0,1];
const plate = buildBVH(box(0, 0, 0, 20, 20, 5));
const cube = buildBVH(box(0, 0, 0, 10, 10, 10));
out.aufliegend = !!meshesPenetrate(cube, T(5, 5, 5), plate, I, 0.02);
out.seitlich_buendig = !!meshesPenetrate(cube, T(20, 5, 0), plate, I, 0.02);
out.eingedrungen_01 = !!meshesPenetrate(cube, T(5, 5, 4.9), plate, I, 0.02);
out.eingedrungen_001 = !!meshesPenetrate(cube, T(5, 5, 4.99), plate, I, 0.02);
out.spalt = !!meshesPenetrate(cube, T(5, 5, 5.5), plate, I, 0.02);
out.durch = !!meshesPenetrate(cube, T(5, 5, -2), plate, I, 0.02);
console.log(JSON.stringify(out));
"""


def test_javascript_matches_python_and_collision():
    node = shutil.which("node")
    if not node:
        print("   (übersprungen: node nicht installiert)")
        return
    _, J = kurbeltrieb()
    names = ["Rahmen", "Kurbel", "Pleuel", "Kolben"]
    eye = np.eye(4).ravel().tolist()
    kin = {"gelenke": K.prepare(J, names), "koerper": names,
           "grundstellung": {n: eye for n in names},
           "zentren": {"Rahmen": [0, 0, 0], "Kurbel": [R_ / 2, 0, 0], "Pleuel": [R_ + L_ / 2, 0, 0],
                       "Kolben": [R_ + L_, 0, 0]}}
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "t.mjs"
        script.write_text(JS_TEST, encoding="utf-8")
        proc = subprocess.run([node, str(script), str(ROOT / "slicetheprompt" / "ui"),
                               json.dumps({"kin": kin, "world": {n: eye for n in names}})],
                              capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["dof"] == 1
    for th, v in out["kolben"].items():
        assert abs(v - kolbenweg_formel(float(th))) < 1e-6, (th, v)
    assert out["plan_kurbel"]["locks"] == [["Pleuel", "Kurbel"]] and out["plan_kurbel"]["frozen"] == ["Kolben"]
    assert out["plan_kurbel"]["dof"] == 0
    assert out["plan_alle"]["dof"] == 1 and abs(out["plan_alle"]["kolben"] - kolbenweg_formel(150)) < 1e-6
    assert out["aufliegend"] is False and out["seitlich_buendig"] is False
    assert out["eingedrungen_01"] is True and out["eingedrungen_001"] is False
    assert out["spalt"] is False and out["durch"] is True


def run_all_tests() -> int:
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
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
