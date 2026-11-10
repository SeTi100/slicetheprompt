"""beispiel-kurbeltrieb-kurbel: Kurbel mit Nabe (dreht auf dem Lagerzapfen des Rahmens) und Kurbelzapfen.

Alle Masse in mm. Drucklage: Arm auf z = 0, Nabenachse bei x = y = 0, Kurbelzapfen bei x = R.
"""
from build123d import *
from lib.parts import *

set_material("pla")
C = (Align.CENTER, Align.CENTER, Align.MIN)

# ---- Parameter ----
R = 20.0                  # Kurbelradius (Nabe → Kurbelzapfen)
DICKE = 4.0
NABE = 16.0               # Nabe aussen Ø
LAGER = 8.0               # Lagerzapfen des Rahmens (Soll) → Bohrung mit Spiel (fit_hole_cut: Spiel + Loch-Zuschlag)
ZAPFEN = 6.0              # Kurbelzapfen Ø (Soll) für das Pleuelauge
ZAPFEN_H = 6.0            # über dem Arm
ENDE = 12.0               # Armende Ø
ARM_B = 10.0

# ---- Geometrie ----
part = Cylinder(NABE / 2, DICKE, align=C)
part += Pos(R / 2, 0, 0) * Box(R, ARM_B, DICKE, align=C)
part += Pos(R, 0, 0) * Cylinder(ENDE / 2, DICKE, align=C)
part = cut_feature(part, "lagerbohrung", Pos(0, 0, -1) * fit_hole_cut(LAGER, DICKE + 2))
part = bottom_chamfer(part)
part += tag("kurbelzapfen", Pos(R, 0, DICKE) * Cylinder(pin_d(ZAPFEN) / 2, ZAPFEN_H, align=C))

# ---- Selbstprüfung ----
bb = part.bounding_box()
loch = (Pos(0, 0, 2) * Box(12, 12, 0.01, align=C) - part).solids()
loch = min(loch, key=lambda s: s.center().length).bounding_box()
zapfen = (part & Pos(R, 0, DICKE + 3) * Box(10, 10, 0.01, align=C)).bounding_box()
checks = {
    "Kurbelradius": (zapfen.center().X, R, 0.01),
    "Lagerbohrung Ø": (loch.size.X, hole_d(fit(LAGER)), 0.01),
    "Kurbelzapfen Ø": (zapfen.size.X, pin_d(ZAPFEN), 0.01),
    "Höhe": (bb.size.Z, DICKE + ZAPFEN_H, 0.01),
}
