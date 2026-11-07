"""Führt ein build123d-Modellskript aus und exportiert STEP + STL.

Aufruf (durch slicetheprompt.cad):  python -m slicetheprompt._cad_runner <model.py> <outdir>

Vertrag für model.py:
  * einfarbig:   Variable `part` (build123d Part/Solid/Compound oder BuildPart)
  * mehrfarbig:  Variable `bodies` = {"name": Körper, …}  – Körper dürfen sich NICHT überschneiden
                 (Einlagen aus dem Grundkörper abziehen). Welche Spule/Farbe je Körper: spec.yaml → filament
  * optional `checks`: dict  Name -> (Ist, Soll, Toleranz)   z. B. {"Loch Ø": (hole_d, 3.45, 0.01)}
"""

from __future__ import annotations

import json
import re
import runpy
import sys
import time
import traceback
from pathlib import Path


def _slug(s: str) -> str:
    s = s.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_") or "koerper"


def _unwrap(p):
    if hasattr(p, "part") and not hasattr(p, "volume"):
        return p.part  # BuildPart-Kontext
    return p


def main() -> int:
    model = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("body_*.stl"):
        old.unlink()
    for old in out.glob("ghost_*.stl"):
        old.unlink()
    lp_pre = sys.modules.get("lib.parts")
    if lp_pre:
        if hasattr(lp_pre, "FEATURES"):
            lp_pre.FEATURES.clear()
        if hasattr(lp_pre, "WARNINGS"):
            lp_pre.WARNINGS.clear()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))          # damit "from lib.parts import ..." geht
    sys.path.insert(0, str(model.parent))
    result: dict = {"ok": False, "model": str(model)}
    t0 = time.time()
    try:
        from build123d import Compound, export_step, export_stl

        ns = runpy.run_path(str(model), run_name="__slicetheprompt__")
        bodies_in = ns.get("bodies")
        bodies: dict = {}
        if bodies_in:
            if not isinstance(bodies_in, dict) or not bodies_in:
                raise RuntimeError("'bodies' muss ein dict {name: Körper} sein")
            for name, shape in bodies_in.items():
                bodies[str(name)] = _unwrap(shape)
            part = Compound(children=list(bodies.values()))
        else:
            part = ns.get("part")
            if part is None:
                raise RuntimeError("model.py muss 'part' (einfarbig) oder 'bodies' (mehrfarbig) definieren")
            part = _unwrap(part)
            if isinstance(part, (list, tuple)):
                part = Compound(children=list(part))

        bb = part.bounding_box()
        solids = part.solids() if hasattr(part, "solids") else []
        try:
            valid = bool(part.is_valid())
        except Exception:  # ältere/neuere API
            valid = None
        result.update({
            "bbox_min": [round(bb.min.X, 4), round(bb.min.Y, 4), round(bb.min.Z, 4)],
            "size_mm": [round(bb.size.X, 4), round(bb.size.Y, 4), round(bb.size.Z, 4)],
            "volume_mm3": round(float(part.volume), 3),
            "solids": len(solids),
            "valid": valid,
        })

        checks = []
        for name, val in (ns.get("checks") or {}).items():
            ist, soll, tol = (list(val) + [0.01])[:3]
            checks.append({"name": name, "ist": round(float(ist), 4), "soll": float(soll),
                           "tol": float(tol), "ok": abs(float(ist) - float(soll)) <= float(tol)})

        # Mehrfarbig: jeden Körper einzeln exportieren und Überschneidungen prüfen
        if bodies:
            names = list(bodies)
            result["bodies"] = []
            for name in names:
                b = bodies[name]
                f = out / f"body_{_slug(name)}.stl"
                export_stl(b, str(f), tolerance=0.01, angular_tolerance=0.1)
                bbb = b.bounding_box()
                result["bodies"].append({"name": name, "file": str(f), "volume_mm3": round(float(b.volume), 3),
                                         "size_mm": [round(bbb.size.X, 3), round(bbb.size.Y, 3),
                                                     round(bbb.size.Z, 3)]})
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    try:
                        v = float((bodies[names[i]] & bodies[names[j]]).volume)
                    except Exception:
                        v = 0.0
                    checks.append({"name": f"keine Überschneidung {names[i]} / {names[j]} (mm³)",
                                   "ist": round(v, 3), "soll": 0.0, "tol": 0.5, "ok": v <= 0.5})
        lp = sys.modules.get("lib.parts")
        raw_features = list(getattr(lp, "FEATURES", []))
        features_clean = []
        for feat in raw_features:
            f_clean = {k: v for k, v in feat.items() if k != "_shape"}
            if feat.get("empty_cut"):
                slug = _slug(feat["name"])
                ghost_path = out / f"ghost_{slug}.stl"
                if "_shape" in feat and feat["_shape"] is not None:
                    export_stl(feat["_shape"], str(ghost_path), tolerance=0.01, angular_tolerance=0.1)
                f_clean["ghost_stl"] = f"ghost_{slug}.stl"
                checks.append({
                    "name": f"Schnitt '{feat['name']}' trifft Körper",
                    "ist": feat.get("removed_volume_mm3", 0.0),
                    "soll": "> 0.01 mm³",
                    "tol": 0.0,
                    "ok": False,
                })
            features_clean.append(f_clean)
        result["features"] = features_clean
        result["checks"] = checks
        result["warnings"] = list(getattr(lp, "WARNINGS", [])) + list(ns.get("WARNINGS") or [])

        export_step(part, str(out / "model.step"))
        export_stl(part, str(out / "model.stl"), tolerance=0.01, angular_tolerance=0.1)
        result["files"] = {"step": str(out / "model.step"), "stl": str(out / "model.stl")}
        result["ok"] = True
    except Exception as exc:  # Fehler strukturiert an Claude zurückgeben
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc(limit=8)
    result["seconds"] = round(time.time() - t0, 2)
    (out / "build.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
