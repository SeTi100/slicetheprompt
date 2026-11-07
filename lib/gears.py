"""Evolventen-Stirnräder (Aussen- und Innenverzahnung) für den 3D-Druck.

Masse in mm, Eingriffswinkel 20°. Flanken als Splines (4 Kanten je Zahn), damit auch
Räder mit vielen Zähnen schnell bauen. Innenverzahnung: `gear_face` des Gegenstücks als
Schneider abziehen (Zahnlücke des Hohlrads = Zahn eines Aussenrads gleicher Zähnezahl).
"""
from __future__ import annotations

import math

from build123d import (Align, Box, Cylinder, Face, Line, Pos, Rot, Spline, ThreePointArc, Vector, Wire, extrude,
                       offset)

from lib.parts import hole_d

ALPHA = math.radians(20)
_C = (Align.CENTER, Align.CENTER, Align.MIN)


def inv(a: float) -> float:
    return math.tan(a) - a


def zahnmasse(z: int, m: float, x: float = 0.0, spiel: float = 0.0, kopfspiel: float = 0.25) -> dict:
    """Radien und Zahndicke eines Aussenrads mit Profilverschiebung x.
    spiel = Abzug an der Zahndicke (Bogen am Teilkreis) für dieses Rad."""
    r = z * m / 2
    return {
        "r": r,
        "r_kopf": r + m * (1 + x),
        "r_fuss": r - m * (1 + kopfspiel - x),
        "s": math.pi * m / 2 + 2 * x * m * math.tan(ALPHA) - spiel,
    }


def gear_face(z: int, m: float, r_kopf: float, r_fuss: float, s_teil: float, n_flanke: int = 10) -> Face:
    """Umriss eines Aussenzahnrads als Fläche: je Zahn Fussbogen, 2 Flanken-Splines, Kopfbogen.
    s_teil = Zahndicke (Bogen) am Teilkreis."""
    r = z * m / 2
    rb = r * math.cos(ALPHA)
    halb = s_teil / (2 * r) + inv(ALPHA)          # halber Zahnwinkel
    r_start = max(rb, r_fuss)
    flanke = []                                    # (Radius, Winkel ab Zahnmitte), innen -> aussen
    for i in range(n_flanke + 1):
        rr = r_start + (r_kopf - r_start) * i / n_flanke
        flanke.append((rr, halb - inv(math.acos(min(1.0, rb / rr)))))
    w_fuss = flanke[0][1]                          # unter dem Grundkreis radial weiter bis r_fuss
    teil = 2 * math.pi / z

    def P(rr, w):
        return Vector(rr * math.cos(w), rr * math.sin(w))

    edges = []
    for k in range(z):
        c = k * teil
        rechts = [P(rr, c - w) for rr, w in flanke]
        links = [P(rr, c + w) for rr, w in reversed(flanke)]
        if r_fuss < rb:
            rechts.insert(0, P(r_fuss, c - w_fuss))
            links.append(P(r_fuss, c + w_fuss))
            edges += [Line(rechts[0], rechts[1]), Line(links[-2], links[-1])]
            edges += [Spline(*rechts[1:]), Spline(*links[:-1])]
        else:
            edges += [Spline(*rechts), Spline(*links)]
        edges.append(ThreePointArc(rechts[-1], P(r_kopf, c), links[0]))
        edges.append(ThreePointArc(links[-1], P(r_fuss, c + teil / 2), P(r_fuss, c + teil - w_fuss)))
    return Face(Wire(edges))


def gear_solid(face: Face, h: float, stufe: float = 0.0):
    """Extrusion von z = 0; `stufe` > 0 setzt die unterste Schicht (0,4 mm) um diesen Betrag
    nach innen (Ersatz für die Fase gegen Elefantenfuss), < 0 nach aussen (für Schneider)."""
    if stufe == 0:
        return extrude(face, amount=h)
    unten = extrude(offset(face, amount=-stufe), amount=0.4)
    return unten + Pos(0, 0, 0.4) * extrude(face, amount=h - 0.4)


def d_welle_cut(d: float, flach: float, h: float, material: str | None = None):
    """Bohrung für Welle mit Abflachung (Ø d, Mass über Abflachung `flach`), von z = 0 nach oben.
    Abflachung in +Y; beide Masse mit dem Loch-Zuschlag des Materials."""
    r = hole_d(d, material) / 2
    flach_y = flach - d / 2 + (r - d / 2)
    return Cylinder(r, h, align=_C) - Pos(0, flach_y + 10, -1) * Box(20, 20, h + 2, align=_C)


def made_cut(z: float, laenge: float, d: float = 2.5):
    """Radiales Kernloch für eine Madenschraube (Standard M3 selbstschneidend) in +Y, Mitte auf Höhe z."""
    return Pos(0, 0, z) * Rot(-90, 0, 0) * Cylinder(d / 2, laenge, align=_C)
