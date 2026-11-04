"""Miniatur-Spielwürfel 5 mm mit modernen quadratischen Würfelaugen.

Klassische Anordnung (Augensumme gegenüberliegender Seiten = 7):
- Unten  (z = 0):   1
- Oben   (z = 5):   6
- Vorne  (y = -2.5): 2
- Hinten (y = +2.5): 5
- Links  (x = -2.5): 3
- Rechts (x = +2.5): 4
"""

from build123d import *
from lib.parts import *

set_material("pla")
C = (Align.CENTER, Align.CENTER, Align.MIN)

# ---- Parameter ----
A = 5.0             # Würfel-Kantenlänge (mm)
PIP_S = 0.55        # Kantenlänge der quadratischen Würfelaugen (mm)
PIP_DEPTH = 0.30    # Tiefe der Vertiefungen (mm)
PIP_SPAN = 1.35     # Abstand vom Zentrum für Mehrfach-Punkte (mm)
EDGE_CHAMFER = 0.25 # Kantenfase (mm)

# Grundkörper (5x5x5 mm, z von 0 bis A)
part = Box(A, A, A, align=C)

# 1. Seite 1: Unten (z = 0, Blick nach oben in +Z)
cut_1 = Pos(0, 0, 0) * Box(PIP_S, PIP_S, PIP_DEPTH, align=C)
part = cut_feature(part, "pips_seite_1", cut_1)

# 2. Seite 6: Oben (z = A, Blick nach unten in -Z)
cuts_6 = [Pos(x, y, A - PIP_DEPTH) * Box(PIP_S, PIP_S, PIP_DEPTH + 0.05, align=C)
          for x in (-PIP_SPAN, PIP_SPAN) for y in (-PIP_SPAN, 0, PIP_SPAN)]
part = cut_feature(part, "pips_seite_6", Compound(cuts_6))

# 3. Seite 2: Vorne (y = -A/2 = -2.5, Schneidet in +Y Richtung)
cuts_2 = [
    Pos(-PIP_SPAN, -A/2, A/2 - PIP_SPAN) * Rot(-90, 0, 0) * Box(PIP_S, PIP_S, PIP_DEPTH + 0.05, align=C),
    Pos(PIP_SPAN, -A/2, A/2 + PIP_SPAN) * Rot(-90, 0, 0) * Box(PIP_S, PIP_S, PIP_DEPTH + 0.05, align=C),
]
part = cut_feature(part, "pips_seite_2", Compound(cuts_2))

# 4. Seite 5: Hinten (y = +A/2 = +2.5, Schneidet in -Y Richtung)
cuts_5 = [Pos(0, A/2, A/2) * Rot(90, 0, 0) * Box(PIP_S, PIP_S, PIP_DEPTH + 0.05, align=C)]
for x in (-PIP_SPAN, PIP_SPAN):
    for z_off in (-PIP_SPAN, PIP_SPAN):
        cuts_5.append(Pos(x, A/2, A/2 + z_off) * Rot(90, 0, 0) * Box(PIP_S, PIP_S, PIP_DEPTH + 0.05, align=C))
part = cut_feature(part, "pips_seite_5", Compound(cuts_5))

# 5. Seite 3: Links (x = -A/2 = -2.5, Schneidet in +X Richtung)
cuts_3 = [
    Pos(-A/2, 0, A/2) * Rot(0, 90, 0) * Box(PIP_S, PIP_S, PIP_DEPTH + 0.05, align=C),
    Pos(-A/2, -PIP_SPAN, A/2 - PIP_SPAN) * Rot(0, 90, 0) * Box(PIP_S, PIP_S, PIP_DEPTH + 0.05, align=C),
    Pos(-A/2, PIP_SPAN, A/2 + PIP_SPAN) * Rot(0, 90, 0) * Box(PIP_S, PIP_S, PIP_DEPTH + 0.05, align=C),
]
part = cut_feature(part, "pips_seite_3", Compound(cuts_3))

# 6. Seite 4: Rechts (x = +A/2 = +2.5, Schneidet in -X Richtung)
cuts_4 = [Pos(A/2, y, A/2 + z_off) * Rot(0, -90, 0) * Box(PIP_S, PIP_S, PIP_DEPTH + 0.05, align=C)
          for y in (-PIP_SPAN, PIP_SPAN) for z_off in (-PIP_SPAN, PIP_SPAN)]
part = cut_feature(part, "pips_seite_4", Compound(cuts_4))

# Zarte Kantenfase
try:
    part = chamfer(part.edges(), EDGE_CHAMFER)
except Exception:
    pass

part = bottom_chamfer(part, 0.15)

# ---- Selbstprüfung ----
bb = part.bounding_box()
checks = {
    "Kantenlänge X": (bb.size.X, A, 0.05),
    "Kantenlänge Y": (bb.size.Y, A, 0.05),
    "Kantenlänge Z": (bb.size.Z, A, 0.05),
}
