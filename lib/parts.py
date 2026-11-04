"""Bausteine für druckbare Teile (build123d), mit Toleranzen aus config/printer.yaml + calibration.yaml.

Einbinden in model.py:
    from build123d import *
    from lib.parts import *
    set_material("petg")

Alle Funktionen liefern Körper in mm; "…_cut" liefert Körper zum Abziehen (part -= …).
Ausrichtung: Unterseite auf z = 0, Mitte auf x = y = 0, sofern nicht anders angegeben.
"""

from __future__ import annotations

import math

from build123d import (Align, Axis, Box, Cone, Cylinder, Pos, RegularPolygon, chamfer, extrude)

from printlab.config import load_config

_CFG = load_config()
_MATERIAL = _CFG.get("default_material", "pla")
WARNINGS: list[str] = []          # wird vom Build in build.json übernommen

# Durchgangslöcher (ISO 273, fein) und Sechskantmuttern (Schlüsselweite, Höhe)
SCREW_CLEARANCE = {"M2": 2.2, "M2.5": 2.7, "M3": 3.2, "M4": 4.3, "M5": 5.3, "M6": 6.4}
NUT = {"M2": (4.0, 1.6), "M2.5": (5.0, 2.0), "M3": (5.5, 2.4), "M4": (7.0, 3.2), "M5": (8.0, 4.0),
       "M6": (10.0, 5.0)}
HEAD_D = {"M2": 3.8, "M2.5": 4.5, "M3": 5.5, "M4": 7.0, "M5": 8.5, "M6": 10.0}   # Zylinderkopf ISO 4762
HEAD_H = {"M2": 2.0, "M2.5": 2.5, "M3": 3.0, "M4": 4.0, "M5": 5.0, "M6": 6.0}
_C = (Align.CENTER, Align.CENTER, Align.MIN)


def set_material(material: str) -> None:
    global _MATERIAL
    if material.lower() not in _CFG["materials"]:
        raise ValueError(f"Material '{material}' unbekannt")
    _MATERIAL = material.lower()


def tol(material: str | None = None) -> dict:
    """Toleranzen des Materials: clearance, press, hole_add, pin_sub, accuracy (mm)."""
    return dict(_CFG["materials"][(material or _MATERIAL).lower()])


def rules() -> dict:
    return dict(_CFG["rules"])


# ---------------------------------------------------------------- Masse

def fit(size: float, material: str | None = None) -> float:
    """Innenmass einer Aufnahme für ein Objekt der Grösse `size` (Spielpassung, beidseitig)."""
    return size + 2 * tol(material)["clearance"]


def hole_d(d: float, material: str | None = None) -> float:
    """Zu modellierender Durchmesser, damit ein gedrucktes Loch `d` misst."""
    return d + tol(material)["hole_add"]


def pin_d(d: float, material: str | None = None) -> float:
    """Zu modellierender Durchmesser, damit ein gedruckter Stift `d` misst."""
    return d - tol(material)["pin_sub"]


# ---------------------------------------------------------------- Körper zum Abziehen

def hole_cut(d: float, depth: float, material: str | None = None):
    """Rundes Loch (Soll-Ø d), von z = 0 nach oben."""
    return Cylinder(hole_d(d, material) / 2, depth, align=_C)


def screw_hole_cut(size: str, depth: float, material: str | None = None):
    """Durchgangsloch für metrische Schraube, z. B. 'M3'."""
    return hole_cut(SCREW_CLEARANCE[size], depth, material)


def fit_hole_cut(d_part: float, depth: float, press: bool = False, material: str | None = None):
    """Loch für ein eingestecktes Rundteil mit Aussen-Ø d_part (Spiel- oder Presspassung)."""
    t = tol(material)
    d = d_part - 2 * t["press"] if press else d_part + 2 * t["clearance"]
    return hole_cut(d, depth, material)


def counterbore_cut(size: str, depth: float, head_depth: float | None = None, material: str | None = None):
    """Schraubloch mit Senkung für Zylinderkopf; Kopf oben (bei z = depth)."""
    hd = head_depth if head_depth is not None else HEAD_H[size] + 0.3
    head = Pos(0, 0, depth - hd) * Cylinder(hole_d(HEAD_D[size] + 0.6, material) / 2, hd, align=_C)
    return screw_hole_cut(size, depth, material) + head


def countersink_cut(screw_d: float, head_d: float, depth: float, material: str | None = None):
    """Senkloch 90° (z. B. Holzschraube 4 mm: screw_d=4.3, head_d=8.0); Kopf oben bei z = depth."""
    r_hole = hole_d(screw_d, material) / 2
    r_head = hole_d(head_d, material) / 2
    h = r_head - r_hole
    cone = Pos(0, 0, depth - h) * Cone(r_hole, r_head, h, align=_C)
    return Cylinder(r_hole, depth, align=_C) + cone


def nut_trap_cut(size: str, depth: float | None = None, material: str | None = None):
    """Sechskant-Mutternfalle (Schlüsselweite + Spiel), von z = 0 nach oben."""
    flats, h = NUT[size]
    flats = flats + 2 * tol(material)["clearance"]
    r = flats / math.sqrt(3)        # Umkreisradius des Sechsecks
    return extrude(RegularPolygon(r, 6), amount=depth if depth is not None else h + 0.3)


def slot_cut(length: float, width: float, depth: float, material: str | None = None):
    """Langloch (Mittelpunktabstand `length` in X, Soll-Breite `width`)."""
    w = hole_d(width, material)
    body = Box(length, w, depth, align=_C)
    ends = Pos(-length / 2, 0, 0) * Cylinder(w / 2, depth, align=_C) + Pos(length / 2, 0, 0) * Cylinder(
        w / 2, depth, align=_C)
    return body + ends


# ---------------------------------------------------------------- Körper

def pin(d: float, height: float, material: str | None = None):
    """Stift, der gedruckt den Durchmesser d hat."""
    return Cylinder(pin_d(d, material) / 2, height, align=_C)


def bottom_chamfer(part, size: float | None = None):
    """Fase an allen Kanten der untersten Ebene (gegen Elefantenfuss)."""
    size = size if size is not None else _CFG["rules"]["bottom_chamfer"]
    zmin = part.bounding_box().min.Z
    edges = part.edges().filter_by_position(Axis.Z, zmin - 1e-4, zmin + 1e-4)
    if not edges:
        return part
    try:
        return chamfer(edges, size)
    except Exception as exc:  # z. B. Kanten zu kurz
        WARNINGS.append(f"bottom_chamfer({size}) nicht möglich: {exc}")
        return part


_NAMES = dir()
__all__ = [n for n in _NAMES if not n.startswith("_") and n not in ("annotations", "math", "load_config")]
