"""Projektablauf: anlegen → bauen (CAD) → prüfen → rendern → druckfertig machen.

Einfarbig: out/model.stl → out/print.stl
Mehrfarbig: out/body_<name>.stl je Körper → out/print_<name>.stl (gleiche Drehung/Verschiebung) + out/print.stl
"""

from __future__ import annotations

import datetime as dt
import json
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
    if names:
        model_rep = mc.analyse_bodies(_load_bodies(d, "body"), cfg, spec=spec,
                                      print_bodies=_load_bodies(d, "print"))
        print_checks = []
        print_metrics = model_rep.metrics.get("print", {})
        print_size = model_rep.metrics["size_mm"]
    else:
        model_rep = mc.analyse(read_stl(out / "model.stl"), cfg, spec=spec)
        print_rep = mc.analyse(read_stl(out / "print.stl"), cfg, print_orientation=True)
        seen = {c.rule for c in model_rep.checks}
        print_checks = [c for c in print_rep.checks if c.rule not in seen]
        print_metrics = print_rep.metrics.get("print", {})
        print_size = print_rep.metrics["size_mm"]

    bi = build_info(d)
    for c in bi.get("checks", []):
        model_rep.add(f"Mass: {c['name']}", c["ok"], f"{c['ist']}", f"{c['soll']} ± {c['tol']}")
    if bi.get("valid") is False:
        model_rep.add("CAD-Körper gültig (OCCT)", False, "ungültig", "gültig")
    for w in bi.get("warnings", []):
        model_rep.add("CAD-Hinweis", False, w, "", severity="warn")

    all_checks = model_rep.checks + print_checks
    errors = [c.as_dict() for c in all_checks if not c.ok and c.severity == "error"]
    warnings = [c.as_dict() for c in all_checks if not c.ok and c.severity == "warn"]
    po = json.loads((out / "print.json").read_text(encoding="utf-8"))
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
    return report


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
    r = check(project)
    img = render(project)
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
        "wall_thickness_p1": r["model"].get("wall_thickness", {}).get("p1"),
        "bed_contact_mm2": r["print"].get("bed_contact_mm2"),
        "bodies": r.get("bodies") or None,
    }


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
