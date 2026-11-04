"""Toleranz-Kalibrierung: Testteil erzeugen, gemessene Werte auswerten, calibration.yaml schreiben."""

from __future__ import annotations

import datetime as dt
from statistics import mean

from .config import CALIBRATION_YAML, _read_yaml, load_config, material_cfg, write_yaml
from .pipeline import new_project

CAL_MODEL = '''\
"""Kalibrier-Testteil: Löcher Ø5/Ø8/Ø10, Stift Ø8, Block 20 × 20 × 10 – alles OHNE Ausgleich.

Nach dem Druck mit dem Messschieber messen (Löcher in der Mitte der Plattendicke):
  block_x, block_y, block_z, loch5, loch8, loch10, stift8
"""
from build123d import *

plate = Box(70, 20, 4, align=(Align.MIN, Align.MIN, Align.MIN))
block = Pos(50, 0, 0) * Box(20, 20, 10, align=(Align.MIN, Align.MIN, Align.MIN))
pin = Pos(43.5, 10, 4) * Cylinder(4, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
part = plate + block + pin
for x, d in ((8, 5.0), (19, 8.0), (32, 10.0)):
    part -= Pos(x, 10, 0) * Cylinder(d / 2, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))

bb = part.bounding_box()
checks = {"Länge": (bb.size.X, 70, 0.01), "Breite": (bb.size.Y, 20, 0.01), "Höhe": (bb.size.Z, 12, 0.01)}
'''

NOMINAL = {"block_x": 20.0, "block_y": 20.0, "block_z": 10.0, "loch5": 5.0, "loch8": 8.0, "loch10": 10.0,
           "stift8": 8.0}


def create_calibration_project(material: str) -> dict:
    p = new_project(f"kalibrierung-{material}", material, "Toleranz-Testteil")
    from pathlib import Path
    Path(p["model"]).write_text(CAL_MODEL, encoding="utf-8")
    spec = Path(p["spec"])
    spec.write_text(spec.read_text(encoding="utf-8").replace("bbox: null", "bbox: [70, 20, 12]"),
                    encoding="utf-8")
    p["messen"] = list(NOMINAL)
    return p


def apply_calibration(material: str, measured: dict[str, float]) -> dict:
    cfg = load_config()
    mat = material_cfg(cfg, material)["name"]
    missing = [k for k in NOMINAL if k not in measured]
    if missing:
        raise ValueError(f"Fehlende Messwerte: {', '.join(missing)}")
    m = {k: float(measured[k]) for k in NOMINAL}
    dev = {k: round(m[k] - NOMINAL[k], 3) for k in NOMINAL}
    hole_add = round(mean(-dev[k] for k in ("loch5", "loch8", "loch10")), 3)  # Löcher drucken meist zu klein
    pin_sub = round(max(dev["stift8"], 0.0), 3)                                  # Stifte meist zu dick
    outer = round(mean((dev["block_x"], dev["block_y"])) / 2, 3)                 # je Seite
    accuracy = round(max(abs(v) for v in dev.values()), 3)
    cal = _read_yaml(CALIBRATION_YAML)
    cal.setdefault("materials", {})
    entry = cal["materials"].get(mat, {})
    entry.update({"hole_add": max(hole_add, 0.0), "pin_sub": pin_sub, "outer_offset": outer,
                  "accuracy": accuracy})
    cal["materials"][mat] = entry
    cal.setdefault("history", []).append({"date": dt.date.today().isoformat(), "material": mat,
                                          "measured": m, "deviation": dev})
    write_yaml(CALIBRATION_YAML, cal, header="Von 'printlab applycal' geschrieben. Überlagert printer.yaml.")
    return {"material": mat, "deviation_mm": dev, "new": entry}
