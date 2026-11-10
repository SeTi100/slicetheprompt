"""beispiel-kurbeltrieb-pleuel: Pleuelstange mit zwei Augen (Kurbelzapfen und Kolbenbolzen).

Alle Masse in mm. Drucklage = Lage in der Grundstellung (Kurbel auf 0°), Unterseite auf z = 0:
Auge 1 bei x = R, Auge 2 bei x = R + L.
"""
from build123d import *
from lib.parts import *

set_material("pla")
C = (Align.CENTER, Align.CENTER, Align.MIN)

# ---- Parameter ----
R, L = 20.0, 60.0          # Kurbelradius, Pleuellänge (Augenabstand)
DICKE = 4.0
AUGE = 14.0                # Auge aussen Ø
ZAPFEN = 6.0               # Zapfen Ø (Soll) → Bohrung mit Spiel
STANGE_B = 8.0

# ---- Geometrie ----
part = Pos(R + L / 2, 0, 0) * Box(L, STANGE_B, DICKE, align=C)
for x in (R, R + L):
    part += Pos(x, 0, 0) * Cylinder(AUGE / 2, DICKE, align=C)
for i, x in enumerate((R, R + L), 1):
    part = cut_feature(part, f"auge_{i}", Pos(x, 0, -1) * fit_hole_cut(ZAPFEN, DICKE + 2))
part = bottom_chamfer(part)

# ---- Selbstprüfung ----
loecher = sorted((Pos(R + L / 2, 0, 2) * Box(L + 20, 20, 0.01, align=C) - part).solids(),
                 key=lambda s: s.volume)[:2]
mitten = sorted(s.bounding_box().center().X for s in loecher)
checks = {
    "Augenabstand": (mitten[1] - mitten[0], L, 0.01),
    "Bohrung Ø": (loecher[0].bounding_box().size.Y, hole_d(fit(ZAPFEN)), 0.01),
    "Dicke": (part.bounding_box().size.Z, DICKE, 0.01),
}
