"""beispiel-kurbeltrieb-kolben: Kolben (Gleitstein) mit Kolbenbolzen für das Pleuelauge.

Alle Masse in mm. Drucklage = Lage in der Grundstellung (Kurbel auf 0°), Unterseite auf z = 0:
Bolzen bei x = R + L.
"""
from build123d import *
from lib.parts import *

set_material("pla")
C = (Align.CENTER, Align.CENTER, Align.MIN)

# ---- Parameter ----
R, L = 20.0, 60.0
LAENGE, BREITE, DICKE = 20.0, 21.0, 4.0     # Breite 21 in 22 breiter Führung (0,5 Spiel je Seite)
BOLZEN, BOLZEN_H = 6.0, 6.0

# ---- Geometrie ----
part = Pos(R + L, 0, 0) * Box(LAENGE, BREITE, DICKE, align=C)
part = bottom_chamfer(part)
part += tag("kolbenbolzen", Pos(R + L, 0, DICKE) * Cylinder(pin_d(BOLZEN) / 2, BOLZEN_H, align=C))

# ---- Selbstprüfung ----
bb = part.bounding_box()
bolzen = (part & Pos(R + L, 0, DICKE + 3) * Box(10, 10, 0.01, align=C)).bounding_box()
checks = {
    "Breite": (bb.size.Y, BREITE, 0.01),
    "Bolzen bei x = R + L": (bolzen.center().X, R + L, 0.01),
    "Bolzen Ø": (bolzen.size.X, pin_d(BOLZEN), 0.01),
}
