"""Slicen mit Spulenwahl (CFS) und Freigabe-Token; Druckstart nur für genau den gezeigten G-Code.

Werkzeug-Nummer = logischer CFS-Platz: T0 = T1A, T1 = T1B, … (siehe printlab.cfs).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import secrets
import shutil
import time
from pathlib import Path

from . import cfs as cfsmod
from .config import STATE_DIR, load_config, material_cfg, project_dir
from .moonraker import Moonraker, PrinterError
from .pipeline import body_names, body_slug, load_spec, render
from .slicer import filament_file, gcode_info, gcode_safety, slice_model
from .stlio import read_stl
from .threemf import write_multipart_3mf

APPROVALS = STATE_DIR / "approvals.json"
TOKEN_TTL_S = 6 * 3600
BUSY = {"printing", "paused"}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load() -> dict:
    if APPROVALS.exists():
        return json.loads(APPROVALS.read_text(encoding="utf-8"))
    return {}


def _save(d: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    APPROVALS.write_text(json.dumps(d, indent=2), encoding="utf-8")


def _per_body(value, names: list[str]) -> dict:
    """spec-Wert (None | str | dict) → {Körper: Wert}."""
    if isinstance(value, dict):
        return {n: value.get(n) for n in names}
    return {n: value for n in names}


def plan_filaments(project: str, material: str | None = None, filament=None, profile=None) -> dict:
    """Spulen und Profile für ein Projekt bestimmen (ohne zu slicen)."""
    cfg = load_config()
    d = project_dir(project)
    spec = load_spec(d)
    material = (material or spec.get("material") or cfg.get("default_material", "pla")).lower()
    material_cfg(cfg, material)
    names = body_names(d) or ["_teil"]
    want = _per_body(filament if filament is not None else spec.get("filament"), names)
    prof_want = _per_body(profile if profile is not None else spec.get("filament_profile"), names)
    from .slicer import slicer_cfg
    s = slicer_cfg()
    catalog = s.get("catalog") or {}
    default_by_type = {k: v for k, v in (s.get("filament") or {}).items()}

    notes = []
    try:
        state = Moonraker().cfs()
    except PrinterError as exc:
        state = {"connected": False, "slots": [], "error": str(exc)}
    use_cfs = state.get("connected") and any(sl["loaded"] for sl in state["slots"])
    if not use_cfs:
        notes.append("CFS nicht erreichbar oder leer – Werkzeug T0 und Standardprofil, Spule vor dem Start prüfen.")
    if use_cfs and not state.get("map_identity"):
        notes.append("CFS-Platzzuordnung (map) ist umgestellt – Werkzeugnummern wurden entsprechend umgerechnet.")

    plan = []
    for n in names:
        w = want[n]
        if use_cfs:
            explicit_slot = isinstance(w, str) and re.fullmatch(r"T[1-4][A-Da-d]", w.strip(), re.I)
            slot = cfsmod.choose_slot(w, None if explicit_slot else material, state)
            prof = prof_want[n] or cfsmod.match_profile(slot, catalog, default_by_type)
            plan.append({"body": n, "slot": slot["slot"], "tool": slot["tool"], "type": slot["type"],
                         "color": slot["color"], "color_name": slot["color_name"], "code": slot["code"],
                         "remain_pct": slot["remain_pct"], "profile": prof})
        else:
            prof = prof_want[n] or default_by_type.get(material)
            plan.append({"body": n, "slot": None, "tool": 0, "type": material.upper(), "color": None,
                         "color_name": None, "code": None, "remain_pct": None, "profile": prof})
    types = {p["type"] for p in plan}
    if len(types) > 1:
        notes.append(f"Materialmix {', '.join(sorted(types))} – Haftung zwischen den Materialien ist schlecht.")
    for p in plan:
        if p["remain_pct"] is not None and p["remain_pct"] < 15:
            notes.append(f"Spule {p['slot']} ist fast leer ({p['remain_pct']} %).")
    return {"project": d.name, "material": material, "multicolor": names != ["_teil"], "plan": plan,
            "notes": notes, "cfs": state}


def slice_project(project: str, material: str | None = None, quality: str | None = None,
                  filament=None, profile=None) -> dict:
    cfg = load_config()
    d = project_dir(project)
    spec = load_spec(d)
    quality = (quality or spec.get("qualitaet") or "standard").lower()
    report_p = d / "out" / "report.json"
    if not report_p.exists():
        raise RuntimeError("Erst prüfen (build_and_check), dann slicen")
    report = json.loads(report_p.read_text(encoding="utf-8"))
    if not report.get("passed"):
        raise RuntimeError("Prüfung nicht bestanden – Fehler zuerst beheben: "
                           + "; ".join(e["rule"] for e in report.get("errors", [])))

    fp = plan_filaments(project, material, filament, profile)
    plan = fp["plan"]
    work = d / "out" / "slice"
    work.mkdir(parents=True, exist_ok=True)

    # Filamentliste positionsgenau: Position i = Werkzeug T(i-1); Lücken mit dem ersten Profil füllen
    max_tool = max(p["tool"] for p in plan)
    by_tool = {p["tool"]: filament_file(p["profile"], p["color"], work) for p in plan}
    filler = by_tool[plan[0]["tool"]]
    fil_files = [by_tool.get(t, filler) for t in range(max_tool + 1)]

    if fp["multicolor"]:
        bodies = [(p["body"], read_stl(d / "out" / f"print_{body_slug(p['body'])}.stl"), p["tool"] + 1)
                  for p in plan]
        model = write_multipart_3mf(work / f"{d.name}.3mf", bodies, object_name=d.name)
        res = slice_model(model, fil_files, quality, outdir=work)
    else:
        res = slice_model(d / "out" / "print.stl", fil_files, quality, filament_ids=[plan[0]["tool"] + 1],
                          outdir=work)

    tag = "_".join(sorted({p["type"].lower() for p in plan}))
    final = d / "out" / f"{d.name}_{tag}_{quality}.gcode"
    shutil.copyfile(res["gcode"], final)
    info = gcode_info(final)
    expected = {p["tool"]: p["type"] for p in plan}
    problems = gcode_safety(info, cfg, fp["material"], expected_tools=expected)

    # Vorschau in den echten Spulenfarben
    cols = {p["body"]: p["color"] for p in plan if p["color"]}
    if cols:
        try:
            render(project, colors=cols if fp["multicolor"] else {"_single": plan[0]["color"]})
        except Exception:
            pass

    token = None
    if not problems:
        token = secrets.token_hex(3)
        ap = _load()
        ap[token] = {"gcode": str(final), "sha256": _sha(final), "created": time.time(), "project": d.name,
                     "quality": quality, "used": False,
                     "slots": [{k: p[k] for k in ("body", "slot", "tool", "type", "code", "color")} for p in plan]}
        _save(ap)
    return {
        "gcode": str(final), "quality": quality,
        "filamente": [{"körper": (p["body"] if fp["multicolor"] else None), "spule": p["slot"],
                       "werkzeug": f"T{p['tool']}", "material": p["type"],
                       "farbe": f"{p['color_name']} {p['color']}" if p["color"] else None,
                       "rest_%": p["remain_pct"], "profil": p["profile"]} for p in plan],
        "time": info["time"], "filament_g": info["filament_g"], "filament_g_per_tool": info["filament_g_per_tool"],
        "filament_m": info["filament_m"], "layers": info["layers"], "layer_height": info["layer_height"],
        "tool_changes": info["tool_changes"], "max_nozzle_temp": info["max_nozzle_temp"],
        "max_bed_temp": info["max_bed_temp"], "hinweise": fp["notes"], "blocked": problems,
        "approval_token": token, "png": str(d / "out" / "render.png"),
        "slicer_dropped_options": res.get("dropped_options"),
        "hinweis": "Erst nach ausdrücklichem 'drucken' des Nutzers upload_and_print mit diesem Token aufrufen.",
    }


def _check_slots(entry: dict, state: dict) -> list[str]:
    """Liegen beim Start noch dieselben Spulen auf denselben Werkzeugen?"""
    probs = []
    if not any(s.get("slot") for s in entry.get("slots", [])):
        return probs  # ohne CFS gesliced
    if not state.get("connected"):
        return ["CFS nicht verbunden"]
    by_slot = {s["slot"]: s for s in state["slots"]}
    for exp in entry["slots"]:
        cur = by_slot.get(exp["slot"])
        if not cur or not cur["loaded"]:
            probs.append(f"Spule {exp['slot']} ist nicht mehr geladen")
            continue
        if cur["tool"] != exp["tool"]:
            probs.append(f"Spule {exp['slot']} liegt jetzt auf T{cur['tool']} statt T{exp['tool']}")
        if (cur["type"] or "").upper() != (exp["type"] or "").upper() or cur["code"] != exp["code"]:
            probs.append(f"Spule {exp['slot']}: jetzt {cur['type']} ({cur['code']}), gesliced für "
                         f"{exp['type']} ({exp['code']})")
        if exp.get("color") and cur["color"] and cur["color"].upper() != exp["color"].upper():
            probs.append(f"Spule {exp['slot']}: Farbe jetzt {cur['color']}, gesliced für {exp['color']}")
        if cur.get("remain_pct") is not None and cur["remain_pct"] <= 0:
            probs.append(f"Spule {exp['slot']} ist leer")
    return probs


def print_gcode(token: str) -> dict:
    ap = _load()
    entry = ap.get(token)
    if not entry:
        raise PermissionError("Unbekannter Freigabe-Token – erst slicen")
    if entry["used"]:
        raise PermissionError("Token wurde schon verwendet – für einen neuen Druck erneut slicen")
    if time.time() - entry["created"] > TOKEN_TTL_S:
        raise PermissionError("Token abgelaufen (älter als 6 h) – erneut slicen")
    gcode = Path(entry["gcode"])
    if not gcode.exists() or _sha(gcode) != entry["sha256"]:
        raise PermissionError("G-Code wurde nach dem Slicen verändert – erneut slicen")
    cfg = load_config()
    expected = {s["tool"]: s["type"] for s in entry.get("slots", [])} or None
    problems = gcode_safety(gcode_info(gcode), cfg, None, expected_tools=expected)
    if problems:
        raise PermissionError("Sicherheitsprüfung: " + "; ".join(problems))
    m = Moonraker()
    st = m.status()
    if st["klipper"] not in (None, "ready"):
        raise PrinterError(f"Klipper nicht bereit: {st['klipper']} {st.get('message') or ''}")
    if st["state"] in BUSY:
        raise PrinterError(f"Drucker ist beschäftigt ({st['state']}: {st['file']})")
    slot_probs = _check_slots(entry, m.cfs())
    if slot_probs:
        raise PermissionError("Spulen haben sich seit dem Slicen geändert: " + "; ".join(slot_probs)
                              + " – erneut slicen")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    remote = re.sub(r"[^A-Za-z0-9_.-]", "_", f"stp_{stamp}_{gcode.name}")
    path = m.upload(gcode, remote)
    m.start(path)
    entry["used"] = True
    entry["started"] = time.time()
    entry["remote"] = path
    _save(ap)
    time.sleep(2)
    try:
        st = m.status()
    except PrinterError:
        st = {}
    return {"started": True, "remote_file": path, "slots": entry.get("slots"), "status": st}
