"""Projektablauf: anlegen → bauen (CAD) → prüfen → rendern → druckfertig machen.

Einfarbig: out/model.stl → out/print.stl
Mehrfarbig: out/body_<name>.stl je Körper → out/print_<name>.stl (gleiche Drehung/Verschiebung) + out/print.stl
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import yaml

from . import meshcheck as mc
from .config import PROJECTS_DIR, load_config, material_cfg, project_dir
from .render import render as render_png
from .stlio import read_stl, write_stl

SPEC_TEMPLATE = """\
# Spezifikation – von Claude aus dem Prompt erstellt, von dir geprüft.
name: {name}
beschreibung: "{beschreibung}"
material: {material}        # pla | petg
qualitaet: standard         # fein (0.12) | standard (0.20) | grob (0.28)
menge: 1
koerper: null               # getrennte Teile in einem Druck (z. B. 3 Testringe); leer = ein zusammenhängender Körper
wand_soll: null             # vom Nutzer gewünschte Wandstärke unter 1,6 mm (z. B. 1.2) – Warnung richtet sich danach
# Filament/Spule: leer = passende Spule automatisch (meiste Restmenge).
#   einfarbig:  filament: T1C        oder  filament: schwarz   oder  filament: "#FF0000"
#   mehrfarbig: filament: {{grundkoerper: weiss, schrift: T1D}}   (Schlüssel = Namen in bodies)
filament: null
# Soll-Aussenmasse (Bounding Box im Modell-Koordinatensystem) in mm, wird automatisch geprüft
bbox: null                  # z. B. [72.5, 36.5, 40]
bbox_tol: 0.05
# wichtige Masse und woher sie kommen
masse: []
# Annahmen, die Claude getroffen hat
annahmen: []
"""

MODEL_TEMPLATE = '''\
"""{name}: {beschreibung}

Alle Masse in mm. Das Teil wird in DRUCKLAGE modelliert: Z nach oben, grösste ebene Fläche auf z = 0.
Einfarbig: Variable `part`. Mehrfarbig: `bodies = {{"name": Körper, …}}` ohne Überschneidungen.
"""
from build123d import *
from lib.parts import *          # Toleranzen und Bausteine aus config/printer.yaml

set_material("{material}")
C = (Align.CENTER, Align.CENTER, Align.MIN)

# ---- Parameter (aus spec.yaml) ----
L, B, H = 40.0, 30.0, 10.0

# ---- Geometrie ----
part = Box(L, B, H, align=C)
part = bottom_chamfer(part)

# ---- Selbstprüfung: Name -> (Ist, Soll, Toleranz) ----
bb = part.bounding_box()
checks = {{
    "Länge": (bb.size.X, L, 0.01),
    "Breite": (bb.size.Y, B, 0.01),
    "Höhe": (bb.size.Z, H, 0.01),
}}
'''

PALETTE = ["#4682B4", "#E8E8E8", "#C0392B", "#27AE60", "#F1C40F", "#8E44AD", "#E67E22", "#2C3E50"]


def slugify(name: str) -> str:
    s = name.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:40] or "teil"


def body_slug(name: str) -> str:
    return slugify(name).replace("-", "_") or "koerper"


def new_project(name: str, material: str = "pla", beschreibung: str = "") -> dict:
    cfg = load_config()
    material_cfg(cfg, material)  # validiert
    slug = slugify(name)
    d = PROJECTS_DIR / f"{dt.date.today():%Y-%m-%d}-{slug}"
    if d.exists():
        raise FileExistsError(f"Projekt existiert schon: {d}")
    (d / "out").mkdir(parents=True)
    (d / "spec.yaml").write_text(
        SPEC_TEMPLATE.format(name=slug, beschreibung=beschreibung.replace('"', "'"), material=material.lower()),
        encoding="utf-8")
    (d / "model.py").write_text(
        MODEL_TEMPLATE.format(name=slug, beschreibung=beschreibung or name, material=material.lower()),
        encoding="utf-8")
    return {"project": d.name, "path": str(d), "spec": str(d / "spec.yaml"), "model": str(d / "model.py")}


def load_spec(d: Path) -> dict:
    p = d / "spec.yaml"
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def build_info(d: Path) -> dict:
    bj = d / "out" / "build.json"
    return json.loads(bj.read_text(encoding="utf-8")) if bj.exists() else {}


def body_names(d: Path) -> list[str]:
    """Namen der Farbkörper (leer = einfarbig)."""
    return [b["name"] for b in build_info(d).get("bodies") or []]


def build(project: str, timeout: int = 180) -> dict:
    d = project_dir(project)
    out = d / "out"
    out.mkdir(exist_ok=True)
    for pat in ("model.stl", "model.step", "print*.stl", "render*.png", "report.json", "print.json", "body_*.stl", "build.json", "ghost_*.stl"):
        for f in out.glob(pat):
            for _ in range(5):
                try:
                    f.unlink()
                    break
                except PermissionError:
                    time.sleep(0.05)
                except FileNotFoundError:
                    break
            else:
                try:
                    f.unlink(missing_ok=True)
                except (PermissionError, OSError):
                    pass
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "slicetheprompt._cad_runner", str(d / "model.py"), str(out)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            cwd=str(Path(__file__).resolve().parents[1]),
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Build nach {timeout} s abgebrochen (Geometrie zu komplex?)"}
    if proc.returncode != 0:
        return {"ok": False, "error": f"CAD Runner Fehler (Exit {proc.returncode}): {proc.stderr or proc.stdout}", "project": d.name}
    res = build_info(d) or {"ok": False, "error": "kein build.json erzeugt"}
    if not res.get("ok"):
        res["stderr"] = proc.stderr[-3000:]
    res["project"] = d.name
    return res


def _load_bodies(d: Path, prefix: str) -> dict[str, np.ndarray]:
    """prefix 'body' (Modelllage) oder 'print' (Drucklage)."""
    return {n: read_stl(d / "out" / f"{prefix}_{body_slug(n)}.stl") for n in body_names(d)}


def prepare(project: str, orientation: str = "wie modelliert") -> dict:
    """Drucklage festlegen, aufs Bett legen, out/print.stl (und print_<körper>.stl) schreiben.
    orientation: 'wie modelliert' | 'auto' | einer der Namen aus meshcheck.ORIENTATIONS"""
    cfg = load_config()
    d = project_dir(project)
    src = d / "out" / "model.stl"
    if not src.exists():
        raise FileNotFoundError("Erst bauen: out/model.stl fehlt")
    names = body_names(d)
    if names:
        bodies = _load_bodies(d, "body")
        tris = np.concatenate(list(bodies.values()))
    else:
        tris = read_stl(src)
    scores = None
    if orientation == "auto":
        orientation, R, scores = mc.best_orientation(tris, cfg["rules"])
    else:
        if orientation not in mc.ORIENTATIONS:
            raise ValueError(f"Unbekannte Lage '{orientation}'. Erlaubt: auto, {', '.join(mc.ORIENTATIONS)}")
        R = mc.ORIENTATIONS[orientation]
    bed = cfg["printer"]["bed"]
    rot = mc.rotate(tris, R)
    placed = mc.place_on_bed(rot, (bed["x"], bed["y"]))
    shift = (placed - rot).reshape(-1, 3)[0]
    write_stl(d / "out" / "print.stl", placed, header=f"slicetheprompt {d.name}")
    files = {}
    for n in names:
        t = mc.rotate(bodies[n], R) + shift
        f = d / "out" / f"print_{body_slug(n)}.stl"
        write_stl(f, t, header=f"slicetheprompt {d.name} {n}")
        files[n] = str(f)

    # Ghost-STLs verfehlter Schnitte in Drucklage transformieren
    for old_gp in (d / "out").glob("ghost_*_print.stl"):
        try:
            old_gp.unlink()
        except (PermissionError, OSError):
            pass
    ghost_files = {}
    for g_path in sorted((d / "out").glob("ghost_*.stl")):
        if g_path.name.endswith("_print.stl"):
            continue
        g_tris = read_stl(g_path)
        g_placed = mc.rotate(g_tris, R) + shift
        out_ghost = d / "out" / f"{g_path.stem}_print.stl"
        write_stl(out_ghost, g_placed, header=f"slicetheprompt {d.name} {g_path.stem}")
        ghost_files[g_path.stem] = str(out_ghost)

    info = {
        "orientation": orientation,
        "candidates": scores,
        "bodies": files,
        "rotation_matrix": R.tolist(),
        "shift": [float(v) for v in shift],
        "ghosts": ghost_files,
    }
    (d / "out" / "print.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"print_stl": str(d / "out" / "print.stl"), **info}


def check(project: str) -> dict:
    """Prüft Modell (Masse, Spezifikation) und Drucklage. Schreibt out/report.json."""
    cfg = load_config()
    d = project_dir(project)
    spec = load_spec(d)
    out = d / "out"
    if not (out / "model.stl").exists():
        raise FileNotFoundError("Erst bauen: out/model.stl fehlt")
    if not (out / "print.json").exists():
        prepare(project)
    names = body_names(d)
    bi = build_info(d)
    po = json.loads((out / "print.json").read_text(encoding="utf-8"))
    R = po.get("rotation_matrix")
    thin_boxes = [(f["bbox_min"], f["bbox_max"]) for f in bi.get("features", [])
                  if f.get("role") == "duenn" and f.get("bbox_min")]
    if names:
        model_rep = mc.analyse_bodies(_load_bodies(d, "body"), cfg, spec=spec,
                                      print_bodies=_load_bodies(d, "print"), R=R, thin_boxes=thin_boxes)
        print_checks = []
        print_metrics = model_rep.metrics.get("print", {})
        print_size = model_rep.metrics["size_mm"]
    else:
        model_rep = mc.analyse(read_stl(out / "model.stl"), cfg, spec=spec, R=R, thin_boxes=thin_boxes)
        print_rep = mc.analyse(read_stl(out / "print.stl"), cfg, print_orientation=True, walls=False)
        seen = {c.rule for c in model_rep.checks}
        print_checks = [c for c in print_rep.checks if c.rule not in seen]
        print_metrics = print_rep.metrics.get("print", {})
        print_size = print_rep.metrics["size_mm"]

    for c in bi.get("checks", []):
        limit = c["soll"] if isinstance(c["soll"], str) else f"{c['soll']} ± {c['tol']}"
        model_rep.add(f"Mass: {c['name']}", c["ok"], f"{c['ist']}", limit)
    if bi.get("valid") is False:
        model_rep.add("CAD-Körper gültig (OCCT)", False, "ungültig", "gültig")
    for w in bi.get("warnings", []):
        model_rep.add("CAD-Hinweis", False, w, "", severity="warn")

    all_checks = model_rep.checks + print_checks
    errors = [c.as_dict() for c in all_checks if not c.ok and c.severity == "error"]
    warnings = [c.as_dict() for c in all_checks if not c.ok and c.severity == "warn"]
    report = {
        "project": d.name,
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": [c.as_dict() for c in all_checks],
        "model": model_rep.metrics,
        "print": {"orientation": po.get("orientation"), **print_metrics, "size_mm": print_size},
        "material": spec.get("material", cfg.get("default_material", "pla")),
        "bodies": names,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=float),
                                     encoding="utf-8")
    print_tris = read_stl(out / "print.stl")
    diag = mc.diagnostics_3d(print_tris, cfg, build_data=bi, print_info=po)
    (out / "diagnostics_3d.json").write_text(json.dumps(diag, indent=2, ensure_ascii=False, default=float),
                                             encoding="utf-8")
    report["zones"] = zone_summary(diag, print_tris, errors + warnings)
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=float),
                                     encoding="utf-8")
    return report


# Fehlerzone (diagnostics_3d) → zugehörige Prüfregel(n) (Teilstring) und Hinweis zur Behebung.
# Dünne Stellen je Klasse (meshcheck.classify_thin): eigene Regel und eigener Hinweis.
_ZONE_KINDS = {
    "open_edges": (("wasserdicht", "manifold", "Normalen"),
                   "Netz offen/nicht-manifold – meist sich nur berührende Körper oder Naht eines Sweeps; überlappen "
                   "lassen, Abstand geben oder Anfangswinkel drehen"),
    "floating_parts": ((), "Körper ohne Bettkontakt – absenken oder mit dem Rest verbinden"),
    "thin_walls:wand": (("Mindestwandstärke",),
                        "Wand verstärken (≥ min_wall) oder Detail weglassen. Gewollt dünn (Norm-Profil, Gitter)? "
                        "Bereich mit tag(name, körper, role=\"duenn\") markieren"),
    "thin_walls:kante": (("Spitze Kanten",), "Spitze Aussenkante (< 90°): mit 45°-Fase stumpf machen"),
    "thin_walls:detail": (("Oberflächendetails",),
                          "Oberflächendetail druckt als 1 Linie: Stege ≥ 2 Linien (0,84 mm) oder Teilung grösser"),
    "bad_bridges": (("Brücken",), "Brücke kürzen, 45°-Fase darunter oder andere Drucklage"),
    "overhangs": (("Überhang",), "45°-Fase/Rundung darunter oder andere Drucklage"),
}
_HINT_BOTTOM = "an der Unterkante: eigene Fase/Kegel an z = 0 entfernen, bottom_chamfer fast dort schon an"
_HINT_HOLE = ("Decke eines waagrechten Lochs/einer Aussparung: Tropfenform (45°-Spitze nach oben) oder "
              "Drucklage drehen")


def _fmt_range(bb: list[float]) -> str:
    return ", ".join(f"{ax} {bb[i]:.1f}…{bb[i + 3]:.1f}" for i, ax in enumerate("xyz"))


def _zone_feature(z: dict, feats: list[dict]) -> dict | None:
    """Benanntes Feature zur Zone: zuerst das kleinste, das die Zonenmitte enthält, sonst das kleinste
    überlappende."""
    bm = z["bbox_model"]
    c = [(bm[i] + bm[i + 3]) / 2 for i in range(3)]
    vol = lambda f: float(np.prod([f["bbox_model"][i + 3] - f["bbox_model"][i] + 1e-3 for i in range(3)]))
    inside = [f for f in feats if all(f["bbox_model"][i] - 0.2 <= c[i] <= f["bbox_model"][i + 3] + 0.2
                                      for i in range(3))]
    if inside:
        return min(inside, key=vol)
    near = [f for f in feats if all(f["bbox_model"][i] - 0.5 <= bm[i + 3] and bm[i] <= f["bbox_model"][i + 3] + 0.5
                                    for i in range(3))]
    return min(near, key=vol) if near else None


def zone_summary(diag: dict, print_tris: np.ndarray, failed: list[dict], limit: int = 8) -> list[dict]:
    """Kurzfassung der Fehlerzonen für den Agenten – nur für Regeln, die im Bericht anschlagen.
    Gleichartige Zonen (Art, Klasse, Feature) werden zusammengefasst: Anzahl, Gesamtfläche, dünnste Stelle,
    Ort der grössten Zone, Feature und ein Hinweis passend zur Ursache."""
    rules = [c["rule"] for c in failed]
    feats = [f for f in diag.get("features", []) if f.get("bbox_model")]
    areas = mc.face_areas(print_tris)
    groups: dict[tuple, dict] = {}
    for key, (rule_parts, hint) in _ZONE_KINDS.items():
        kind, _, klasse = key.partition(":")
        if rule_parts and not any(x in r for x in rule_parts for r in rules):
            continue
        for z in diag.get(kind, []):
            if klasse and z.get("klasse", "wand") != klasse:
                continue
            area = float(areas[z["tri_indices"]].sum()) if z.get("tri_indices") else 0.0
            f = _zone_feature(z, feats)
            zh = hint
            if kind == "overhangs":
                if z["bbox_print"][2] < 0.6:
                    zh = _HINT_BOTTOM
                elif f and f.get("role") == "cut":
                    zh = _HINT_HOLE
            g = groups.setdefault((key, f["name"] if f else None, zh), {
                "sev": z.get("severity"), "n": 0, "area": 0.0, "big": (0.0, z), "min": None})
            g["n"] += 1
            g["area"] += area
            if area >= g["big"][0]:
                g["big"] = (area, z)
            if z.get("min_mm") is not None:
                g["min"] = z["min_mm"] if g["min"] is None else min(g["min"], z["min_mm"])
    out = []
    for (key, feat, hint), g in groups.items():
        z = g["big"][1]
        art = z["label"]
        if g["n"] > 1:
            art = f"{g['n']} × {art}" if g["min"] is None else f"{g['n']} × {art.split(' (')[0]} (ab {g['min']:.2f} mm)"
        e = {"art": art, "ort": _fmt_range(z["bbox_model"]) + (" (grösste)" if g["n"] > 1 else ""),
             "flaeche_mm2": round(g["area"], 1), "hinweis": hint}
        if feat:
            e["feature"] = feat
        out.append((g["sev"] != "error", -g["area"], e))
    out.sort(key=lambda t: t[:2])
    res = [e for *_, e in out[:limit]]
    if len(out) > limit:
        res.append({"art": f"… {len(out) - limit} weitere Gruppen", "ort": "", "hinweis": "out/diagnostics_3d.json"})
    return res


def _body_colors(d: Path, names: list[str]) -> dict[str, str]:
    """Vorschaufarben: Farbangabe aus spec.yaml (Name/Hex), sonst Palette."""
    from .cfs import COLOR_NAMES, _hex
    fil = load_spec(d).get("filament")
    out = {}
    for i, n in enumerate(names):
        want = fil.get(n) if isinstance(fil, dict) else (fil if len(names) == 1 else None)
        h = None
        if isinstance(want, str):
            h = COLOR_NAMES.get(want.lower()) or _hex(want)
        out[n] = f"#{h}" if h else PALETTE[i % len(PALETTE)]
    return out


def render(project: str, which: str = "print", colors: dict | None = None) -> dict:
    cfg = load_config()
    d = project_dir(project)
    names = body_names(d)
    prefix = "print" if which == "print" else "body"
    png = d / "out" / ("render.png" if which == "print" else "render_model.png")
    title = d.name + (" – Drucklage" if which == "print" else " – Modell")
    bed = cfg["printer"]["bed"] if which == "print" else None
    if names:
        cols = colors or _body_colors(d, names)
        parts = [(read_stl(d / "out" / f"{prefix}_{body_slug(n)}.stl"), cols[n], n) for n in names]
        render_png(parts, png, title=title, bed=bed)
    else:
        src = d / "out" / ("print.stl" if which == "print" else "model.stl")
        if not src.exists():
            raise FileNotFoundError(f"{src.name} fehlt – erst bauen/vorbereiten")
        col = (colors or {}).get("_single") or _body_colors(d, ["_single"])["_single"]
        render_png([(read_stl(src), col, None)], png, title=title, bed=bed)
    return {"png": str(png)}


def run_all(project: str, orientation: str | None = None, reason: str = "build_and_check") -> dict:
    """Bauen, (optional) Lage wählen, prüfen, rendern – ein Durchlauf."""
    b = build(project)
    if not b.get("ok"):
        return {"stage": "build", **b}
    p = prepare(project, orientation or "wie modelliert")
    img = render(project)          # vor der Prüfung: Bild gibt es auch, wenn die Prüfung abbricht
    r = check(project)
    from . import history
    snap = history.save_snapshot(project, reason=reason)
    return {"stage": "done",
            "build": {k: b.get(k) for k in ("size_mm", "volume_mm3", "solids", "valid", "checks", "bodies")},
            "orientation": p["orientation"], "report": _short(r), "png": img["png"],
            "version": snap["version"]}


def _short(r: dict) -> dict:
    return {
        "passed": r["passed"],
        "errors": [f"{e['rule']}: {e['value']} (Soll {e['limit']})" for e in r["errors"]],
        "warnings": [f"{w['rule']}: {w['value']} (Soll {w['limit']})" for w in r["warnings"]],
        "size_mm": r["model"]["size_mm"],
        "wall_thickness_p1": (r["model"].get("wall_thickness") or {}).get("p1_wand",
                                                                       (r["model"].get("wall_thickness") or {}).get("p1")),
        "bed_contact_mm2": r["print"].get("bed_contact_mm2"),
        "bodies": r.get("bodies") or None,
        "zones": r.get("zones") or None,
    }


STENCIL_SUFFIX = "-schablone"

STENCIL_MODEL = '''\
"""Schablone (Passungs-Testdruck) von {quelle}

Dünne Scheibe(n) aus dem Originalteil, um Löcher, Stifte und Passungen mit wenig Material und Zeit
zu prüfen. Das Original wird bei jedem Build frisch aus seinem model.py erzeugt – Änderungen dort
landen automatisch auch hier. Angelegt mit `slicetheprompt schablone`.
"""
from pathlib import Path

from build123d import *
from lib.parts import *

set_material("{material}")

# ---- Parameter ----
QUELLE = Path(__file__).resolve().parents[1] / "{quelle}" / "model.py"
Z = {z!r}              # Schnitthöhen im Original (mm), je eine Scheibe
HOEHE = {hoehe!r}           # Dicke je Scheibe (mm)

# ---- Geometrie ----
part, SCHEIBEN = stencil(load_model(QUELLE), z=Z, height=HOEHE)

# ---- Selbstprüfung ----
checks = {{f"Scheibe z={{s['z_von']}}: Dicke": (s["groesse_mm"][2], s["hoehe"], 0.01) for s in SCHEIBEN}}
'''


def _stencil_dir(src: Path) -> Path | None:
    base = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", src.name)
    found = sorted(d for d in PROJECTS_DIR.glob(f"*-{base}{STENCIL_SUFFIX}") if d.is_dir())
    return found[-1] if found else None


def new_stencil(project: str, z: float | list[float] = 0.0, hoehe: float = 2.0) -> dict:
    """Schablone (dünner Passungs-Testdruck) aus einem Projekt: legt <projekt>-schablone an
    (oder aktualisiert sie), baut, prüft und rendert sie. Danach normal slicen und drucken."""
    cfg = load_config()
    src = project_dir(project)
    if src.name.endswith(STENCIL_SUFFIX):
        raise ValueError("Das ist schon eine Schablone – bitte das Originalprojekt angeben")
    if not (src / "model.py").exists():
        raise FileNotFoundError(f"{src.name}: model.py fehlt")
    zs = [round(float(v), 3) for v in (z if isinstance(z, (list, tuple)) else [z])] or [0.0]
    hoehe = round(float(hoehe), 3)
    lh = cfg["rules"]["layer_height"]
    if hoehe < 3 * lh:
        raise ValueError(f"Schablone zu dünn: mindestens {3 * lh:.1f} mm (3 Schichten)")
    spec = load_spec(src)
    material = (spec.get("material") or cfg.get("default_material", "pla")).lower()
    z_txt = ", ".join(f"{v:g}" for v in zs)

    d = _stencil_dir(src)
    created = d is None
    if created:
        p = new_project(re.sub(r"^\d{4}-\d{2}-\d{2}-", "", src.name) + STENCIL_SUFFIX, material,
                        f"Schablone von {src.name}")
        d = Path(p["path"])
    (d / "model.py").write_text(
        STENCIL_MODEL.format(quelle=src.name, material=material, z=zs if len(zs) > 1 else zs[0], hoehe=hoehe),
        encoding="utf-8")
    fil = spec.get("filament")
    st_spec = {
        "name": d.name[11:],
        "beschreibung": f"Schablone (Passungs-Testdruck) von {src.name}: {hoehe:g} mm dick bei z = {z_txt}",
        "schablone_von": src.name,
        "material": material,
        "qualitaet": spec.get("qualitaet") or "standard",
        "menge": 1,
        "filament": fil if isinstance(fil, str) else None,
        "bbox": None,
        "bbox_tol": 0.05,
        "masse": [f"Scheibe(n) {hoehe:g} mm dick ab z = {z_txt} mm aus dem Original, Masse in XY unverändert"],
        "annahmen": ["Nur zum Prüfen der Passungen; Toleranzen und Lochmasse identisch mit dem Original"]
                    + (["Mehrfarbiges Original: Schablone einfarbig (alle Körper vereint)"]
                       if isinstance(fil, dict) else []),
    }
    (d / "spec.yaml").write_text(
        "# Schablone – von 'slicetheprompt schablone' erzeugt. Parameter in model.py (Z, HOEHE).\n"
        + yaml.safe_dump(st_spec, allow_unicode=True, sort_keys=False), encoding="utf-8")

    res = run_all(d.name, reason=f"schablone z={z_txt} h={hoehe:g}")
    res["project"] = d.name
    res["quelle"] = src.name
    res["neu_angelegt"] = created
    bi = build_info(d)
    src_vol = build_info(src).get("volume_mm3")
    if bi.get("volume_mm3") and src_vol:
        res["material_anteil_pct"] = round(100 * bi["volume_mm3"] / src_vol, 1)
    cuts = [f for f in build_info(src).get("features") or [] if f.get("role") in ("cut", "feature")]
    if cuts:
        res["features_z"] = [{"name": f["name"], "z": [f["bbox_min"][2], f["bbox_max"][2]]} for f in cuts]
    return res


def list_projects() -> list[dict]:
    out = []
    for d in sorted(PROJECTS_DIR.glob("*")):
        if d.is_dir() and (d / "model.py").exists():
            gc = sorted((d / "out").glob("*.gcode"))
            out.append({"project": d.name, "built": (d / "out" / "model.stl").exists(),
                        "bodies": body_names(d) or None, "gcode": [g.name for g in gc]})
    return out


def mesh_stats(path: str) -> dict:
    t = read_stl(path)
    pts = t.reshape(-1, 3)
    return {"triangles": int(len(t)), "size_mm": [round(float(v), 3) for v in pts.max(0) - pts.min(0)]}
