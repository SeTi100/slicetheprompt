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

from build123d import (Align, Axis, Box, Circle, Cone, Cylinder, Line, Pos, RegularPolygon, ThreePointArc, Vector,
                       Wire, chamfer, extrude, make_face, offset)

from slicetheprompt.config import load_config

_CFG = load_config()
_MATERIAL = _CFG.get("default_material", "pla")
WARNINGS: list[str] = []          # wird vom Build in build.json übernommen
FEATURES: list[dict] = []         # semantisches Feature-Tagging

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


def outer(size: float, material: str | None = None) -> float:
    """Zu modellierendes Aussenmass, damit es gedruckt `size` misst (aus der Kalibrierung;
    ohne Kalibrierung unverändert). Für Aussenmasse, die irgendwo hineinpassen müssen."""
    return size - 2 * tol(material).get("outer_offset", 0.0)


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


# ---------------------------------------------------------------- Semantische Features & Schnitte

def _unwrap(p):
    if hasattr(p, "part") and not hasattr(p, "volume"):
        return p.part
    return p


_MAX_ANCHORS = 12


def _anchor_faces(result, before=None) -> list:
    """Flächen, an denen sich ein Feature später wiederfinden lässt.

    Features werden dort gemessen, wo sie entstehen. Verschiebt oder dreht model.py den Körper danach
    (z. B. `Pos(70, 0, 0) * rad(...)`) und vereint ihn mit anderen, behalten unveränderte Flächen ihre
    OpenCascade-Identität (TShape), nur ihre Lage ändert sich. Der Build sucht diese Flächen im
    fertigen Teil und verschiebt das Feature mit (slicetheprompt/_cad_runner.py).
    Bevorzugt werden Flächen, die der Schnitt neu erzeugt hat (liegen direkt am Feature)."""
    try:
        faces = list(result.faces())
        if before is not None:
            old = set(before.faces())          # build123d: Hash/Gleichheit über IsSame (TShape + Lage)
            new = [f for f in faces if f not in old]
            if new:
                faces = new
        return [f.wrapped for f in faces[:_MAX_ANCHORS]]
    except Exception:  # Verfolgung ist nur Komfort für Studio, nie ein Build-Fehler
        return []


def tag(name: str, shape, role: str = "feature"):
    """Registriert einen benannten Körper in FEATURES und gibt shape unverändert zurück.
    role="duenn": Bereich ist bewusst dünner als min_wall (Norm-Profil, Gitterstege, gewünschte dünne Wand) –
    die Prüfung meldet dort dann nur einen Hinweis statt eines Fehlers."""
    s = _unwrap(shape)
    bb = s.bounding_box()
    vol = float(getattr(s, "volume", 0.0))
    ctr = bb.center()
    feat = {
        "name": name,
        "role": role,
        "bbox_min": [round(float(bb.min.X), 4), round(float(bb.min.Y), 4), round(float(bb.min.Z), 4)],
        "bbox_max": [round(float(bb.max.X), 4), round(float(bb.max.Y), 4), round(float(bb.max.Z), 4)],
        "center": [round(float(ctr.X), 4), round(float(ctr.Y), 4), round(float(ctr.Z), 4)],
        "volume_mm3": round(vol, 4),
        "_shape": s,
        "_anchors": _anchor_faces(s),
    }
    FEATURES.append(feat)
    return shape


def cut_feature(part, name: str, cutter):
    """Zieht cutter von part ab, misst Abtrag und registriert den Schnitt in FEATURES.
    Erkennt verfehlte Schnitte (empty_cut) und warnt bei Teilabtrag."""
    p = _unwrap(part)
    c = _unwrap(cutter)
    vol_before = float(getattr(p, "volume", 0.0))
    res = p - c
    vol_after = float(getattr(res, "volume", 0.0))
    removed = max(0.0, vol_before - vol_after)

    cbb = c.bounding_box()
    c_vol = float(getattr(c, "volume", 0.0))
    c_ctr = cbb.center()

    feat = {
        "name": name,
        "role": "cut",
        "bbox_min": [round(float(cbb.min.X), 4), round(float(cbb.min.Y), 4), round(float(cbb.min.Z), 4)],
        "bbox_max": [round(float(cbb.max.X), 4), round(float(cbb.max.Y), 4), round(float(cbb.max.Z), 4)],
        "center": [round(float(c_ctr.X), 4), round(float(c_ctr.Y), 4), round(float(c_ctr.Z), 4)],
        "cutter_volume_mm3": round(c_vol, 4),
        "removed_volume_mm3": round(removed, 4),
        "_shape": c,
        "_anchors": _anchor_faces(res, p),
    }

    if removed < 0.01:
        feat["empty_cut"] = True
        WARNINGS.append(f"Schnitt '{name}' hat kein Material abgetragen (liegt ausserhalb des Körpers!)")
    elif c_vol > 0 and removed < 0.15 * c_vol:
        feat["partial_cut"] = True
        WARNINGS.append(f"Schnitt '{name}' schneidet nur den Rand an ({round(removed, 2)} mm³ von {round(c_vol, 2)} mm³)")

    FEATURES.append(feat)
    return res


sub_tag = cut_feature


def clear_features() -> None:
    """Setzt die globale Feature-Liste zurück (z. B. für Tests)."""
    FEATURES.clear()


# ---------------------------------------------------------------- Körper

def pin(d: float, height: float, material: str | None = None):
    """Stift, der gedruckt den Durchmesser d hat."""
    return Cylinder(pin_d(d, material) / 2, height, align=_C)


def sector_face(r_out: float, a_start: float, a_end: float, r_in: float = 0.0):
    """Kreissektor bzw. Ringsektor als 2D-Fläche in der XY-Ebene (Mitte im Ursprung).
    Winkel in Grad, gegen den Uhrzeigersinn ab +X (a_end > a_start); ≥ 360° ergibt Kreis bzw. Ring."""
    span = a_end - a_start
    if span <= 0:
        raise ValueError("sector: a_end muss grösser als a_start sein")
    if r_in < 0 or r_in >= r_out:
        raise ValueError("sector: 0 ≤ r_in < r_out")
    if span >= 360 - 1e-9:
        return Circle(r_out) - Circle(r_in) if r_in > 0 else Circle(r_out)

    def P(r, a):
        a = math.radians(a)
        return Vector(r * math.cos(a), r * math.sin(a))

    am = a_start + span / 2
    if r_in > 0:
        edges = [Line(P(r_in, a_start), P(r_out, a_start)), ThreePointArc(P(r_out, a_start), P(r_out, am), P(r_out, a_end)),
                 Line(P(r_out, a_end), P(r_in, a_end)), ThreePointArc(P(r_in, a_end), P(r_in, am), P(r_in, a_start))]
    else:
        o = Vector(0, 0)
        edges = [Line(o, P(r_out, a_start)), ThreePointArc(P(r_out, a_start), P(r_out, am), P(r_out, a_end)),
                 Line(P(r_out, a_end), o)]
    return make_face(Wire(edges))


def sector(r_out: float, a_start: float, a_end: float, height: float, r_in: float = 0.0):
    """Kreis-/Ringsektor als Körper von z = 0 bis height (Hebel, Wippen, Segmente). Winkel wie sector_face."""
    return extrude(sector_face(r_out, a_start, a_end, r_in), amount=height)


def bottom_inset(part, inset: float = 0.15, height: float | None = None):
    """Rückt die untersten Schichten (Standard 2 × Schichthöhe) um `inset` nach innen – Ersatz für die
    Fase bei feinen Konturen (Zähne, Rippen, Schrift), an denen eine Fase nicht möglich ist."""
    p = _unwrap(part)
    h = height if height is not None else 2 * _CFG["rules"]["layer_height"]
    bb = p.bounding_box()
    zmin = bb.min.Z
    bottom = [f for f in p.faces() if abs(f.center().Z - zmin) < 1e-4 and f.normal_at().Z < -0.99]
    if not bottom:
        return part
    slab = Pos(bb.center().X, bb.center().Y, zmin) * Box(bb.size.X + 2, bb.size.Y + 2, h, align=_C)
    inner = extrude(offset(bottom, amount=-inset), amount=h)
    if abs(inner.bounding_box().min.Z - zmin) > 1e-4:     # Fläche zeigt nach unten → Extrusion nach -Z
        inner = Pos(0, 0, zmin - inner.bounding_box().min.Z) * inner
    return (p - slab) + (inner & p)


def bottom_chamfer(part, size: float | None = None):
    """Fase an allen Kanten der untersten Ebene (gegen Elefantenfuss), auch an Bohrungen auf z = 0 –
    dort deshalb keine eigene Fase/Kegel schneiden. Geht die Fase nicht (Kanten zu kurz, z. B. Zähne),
    wird stattdessen die unterste Schicht eingerückt (bottom_inset)."""
    size = size if size is not None else _CFG["rules"]["bottom_chamfer"]
    zmin = part.bounding_box().min.Z
    edges = part.edges().filter_by_position(Axis.Z, zmin - 1e-4, zmin + 1e-4)
    if not edges:
        return part
    try:
        return chamfer(edges, size)
    except Exception as exc:  # z. B. Kanten zu kurz
        inset = min(size, 0.15)
        try:
            res = bottom_inset(part, inset)
            WARNINGS.append(f"bottom_chamfer({size}) nicht möglich ({type(exc).__name__}) – stattdessen unterste "
                            f"Schichten um {inset} mm eingerückt (bottom_inset)")
            return res
        except Exception as exc2:
            WARNINGS.append(f"bottom_chamfer({size}) nicht möglich: {exc}; bottom_inset ebenfalls nicht: {exc2}")
            return part


# ---------------------------------------------------------------- Schablone (Passungs-Testdruck)

def load_model(model_py):
    """Führt ein anderes model.py aus und gibt seinen Körper zurück (`part`, bei `bodies` alle vereint)."""
    import runpy
    import sys
    from pathlib import Path

    from build123d import Compound

    model_py = Path(model_py).resolve()
    if str(model_py.parent) not in sys.path:
        sys.path.insert(0, str(model_py.parent))
    ns = runpy.run_path(str(model_py), run_name="__slicetheprompt_quelle__")
    if ns.get("bodies"):
        shapes = [_unwrap(b) for b in ns["bodies"].values()]
        return Compound(children=shapes)
    part = ns.get("part")
    if part is None:
        raise RuntimeError(f"{model_py} definiert weder 'part' noch 'bodies'")
    part = _unwrap(part)
    if isinstance(part, (list, tuple)):
        part = Compound(children=list(part))
    return part


def stencil(part, z=0.0, height: float = 2.0, gap: float = 5.0):
    """Dünner Passungs-Testdruck: Scheibe(n) von z bis z + height aus `part`, auf z = 0 gelegt.

    z: Höhe (oder Liste von Höhen) im Modell, ab der geschnitten wird – dort, wo die Passungen liegen.
    Mehrere Scheiben werden in X nebeneinander gelegt (Abstand `gap`). Geschnittene Unterseiten
    (z > 0) bekommen wieder eine bottom_chamfer. Gibt (Körper, Liste der Scheiben-Infos) zurück."""
    from build123d import Compound

    p = _unwrap(part)
    zs = [float(v) for v in (z if isinstance(z, (list, tuple)) else [z])]
    bb = p.bounding_box()
    pad = 1.0
    slices, info = [], []
    x_next = None
    for z0 in zs:
        if not (bb.min.Z - 1e-6 <= z0 < bb.max.Z):
            raise ValueError(f"Schnitthöhe z = {z0} liegt ausserhalb des Teils ({bb.min.Z:.2f} … {bb.max.Z:.2f})")
        z1 = min(z0 + height, bb.max.Z)
        cutter = Pos(bb.center().X, bb.center().Y, z0) * Box(bb.size.X + 2 * pad, bb.size.Y + 2 * pad, z1 - z0,
                                                             align=_C)
        s = p & cutter
        vol = float(getattr(s, "volume", 0.0))
        if vol < 0.01:
            raise ValueError(f"Bei z = {z0} … {z1} ist kein Material – andere Höhe wählen")
        s = Pos(0, 0, -z0) * s
        if z0 > bb.min.Z + 1e-6:
            s = bottom_chamfer(s)
        sbb = s.bounding_box()
        if x_next is not None:
            s = Pos(x_next - sbb.min.X, 0, 0) * s
            sbb = s.bounding_box()
        x_next = sbb.max.X + gap
        slices.append(s)
        info.append({"z_von": round(z0, 3), "z_bis": round(z1, 3), "hoehe": round(z1 - z0, 3),
                     "volumen_mm3": round(float(s.volume), 2),
                     "groesse_mm": [round(sbb.size.X, 2), round(sbb.size.Y, 2), round(sbb.size.Z, 2)]})
    out = slices[0] if len(slices) == 1 else Compound(children=slices)
    return out, info


_NAMES = dir()
__all__ = [n for n in _NAMES if not n.startswith("_") and n not in ("annotations", "math", "load_config")]
