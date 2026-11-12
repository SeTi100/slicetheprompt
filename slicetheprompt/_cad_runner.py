"""Führt ein build123d-Modellskript aus und exportiert STEP + STL.

Aufruf (durch slicetheprompt.cad):  python -m slicetheprompt._cad_runner <model.py> <outdir>

Vertrag für model.py:
  * einfarbig:   Variable `part` (build123d Part/Solid/Compound oder BuildPart)
  * mehrfarbig:  Variable `bodies` = {"name": Körper, …}  – Körper dürfen sich NICHT überschneiden
                 (Einlagen aus dem Grundkörper abziehen). Welche Spule/Farbe je Körper: spec.yaml → filament
  * optional `checks`: dict  Name -> (Ist, Soll, Toleranz)   z. B. {"Loch Ø": (hole_d, 3.45, 0.01)}
                                    oder (Ist, ">=", Min) / (Ist, "<=", Max), z. B. {"Rand": (rand, ">=", 3)}
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


def _find_part(ns: dict):
    """Findet `part` oder fällt auf den letzten BuildPart/Shape im Namespace zurück."""
    p = ns.get("part")
    if p is not None:
        return p
    from build123d import BuildPart, Shape
    for key in ("p", "builder", "result", "model", "solid", "obj", "teil", "cube", "box"):
        cand = ns.get(key)
        if cand is not None and not isinstance(cand, type) and (
            isinstance(cand, (BuildPart, Shape)) or hasattr(cand, "part")
        ):
            return cand
    for key in reversed(list(ns.keys())):
        if key.startswith("_"):
            continue
        cand = ns[key]
        if isinstance(cand, type):
            continue
        if isinstance(cand, BuildPart) or (isinstance(cand, Shape) and hasattr(cand, "volume")):
            return cand
    return None


def _relocate_features(features: list[dict], final_shape) -> None:
    """Verschiebt Features an ihre Lage im fertigen Teil.

    lib.parts misst ein Feature dort, wo es entsteht, und merkt sich einige Flächen (`_anchors`).
    Wurde der Körper danach verschoben/gedreht (z. B. `Pos(70, 0, 0) * rad(...)`), findet sich dieselbe
    Fläche (gleiche TShape) im fertigen Teil mit anderer Lage; deren Differenz ist die Transformation
    des Features. Ohne Treffer (Fläche durch spätere Schnitte verändert) bleibt das Feature, wie es war."""
    from build123d import Location

    from OCP.gp import gp_Pnt, gp_TrsfForm
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    final_faces = []
    exp = TopExp_Explorer(final_shape.wrapped, TopAbs_FACE)
    while exp.More():
        final_faces.append(exp.Current())
        exp.Next()

    for feat in features:
        trsf = None
        for anchor in feat.get("_anchors") or []:
            hit = next((f for f in final_faces if f.IsPartner(anchor)), None)
            if hit is not None:
                trsf = hit.Location().Transformation().Multiplied(anchor.Location().Transformation().Inverted())
                break
        if trsf is None:
            continue
        t = trsf.TranslationPart()
        if trsf.Form() == gp_TrsfForm.gp_Identity or (trsf.Form() == gp_TrsfForm.gp_Translation
                                                      and abs(t.X()) + abs(t.Y()) + abs(t.Z()) < 1e-6):
            continue
        lo, hi = feat["bbox_min"], feat["bbox_max"]
        pts = [gp_Pnt(x, y, z).Transformed(trsf)
               for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
        xs, ys, zs = [p.X() for p in pts], [p.Y() for p in pts], [p.Z() for p in pts]
        feat["bbox_min"] = [round(min(xs), 4), round(min(ys), 4), round(min(zs), 4)]
        feat["bbox_max"] = [round(max(xs), 4), round(max(ys), 4), round(max(zs), 4)]
        c = gp_Pnt(*feat["center"]).Transformed(trsf)
        feat["center"] = [round(c.X(), 4), round(c.Y(), 4), round(c.Z(), 4)]
        if feat.get("_shape") is not None:
            try:
                feat["_shape"] = Location(trsf) * feat["_shape"]
            except Exception:
                pass
        feat["moved"] = True


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
            part = _find_part(ns)
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
            val = list(val)
            if len(val) == 3 and isinstance(val[1], str):          # (Ist, ">=", Min) oder (Ist, "<=", Max)
                ist, op, grenze = float(val[0]), val[1].strip(), float(val[2])
                if op not in (">=", "≥", "<=", "≤"):
                    raise ValueError(f"checks['{name}']: Vergleich '{op}' unbekannt (erlaubt: >=, <=)")
                ok = ist >= grenze if op in (">=", "≥") else ist <= grenze
                zeichen = "≥" if op in (">=", "≥") else "≤"
                checks.append({"name": name, "ist": round(ist, 4), "soll": f"{zeichen} {grenze:g}", "tol": 0.0,
                               "ok": ok})
                continue
            ist, soll, tol = (val + [0.01])[:3]
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
        try:
            _relocate_features(raw_features, part)
        except Exception as exc:  # nur Komfort für Studio, nie ein Build-Fehler
            result.setdefault("notes", []).append(f"Feature-Lage nicht verfolgt: {exc}")
        features_clean = []
        for feat in raw_features:
            f_clean = {k: v for k, v in feat.items() if not k.startswith("_")}
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
        # `from lib.parts import *` holt dieselbe Liste ins Modell – doppelte Einträge entfernen
        result["warnings"] = list(dict.fromkeys(list(getattr(lp, "WARNINGS", [])) + list(ns.get("WARNINGS") or [])))

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
    for _s in (sys.stdout, sys.stderr):          # Windows-Pipe sonst cp1252 → Absturz bei „−“, „×“ …
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8")
    sys.exit(main())
