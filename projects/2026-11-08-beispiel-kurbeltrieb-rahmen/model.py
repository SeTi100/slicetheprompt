"""beispiel-kurbeltrieb-rahmen: Grundplatte mit Lagerzapfen für die Kurbel und Führung für den Kolben.

Alle Masse in mm. Drucklage = Lage in der Baugruppe: Platte auf z = 0, Kurbelachse bei x = y = 0.
"""
from build123d import *
from lib.parts import *

set_material("pla")
C = (Align.CENTER, Align.CENTER, Align.MIN)
MIN3 = (Align.MIN, Align.MIN, Align.MIN)

# ---- Parameter ----
PLATTE = (-30.0, 120.0, -25.0, 25.0, 4.0)     # x0, x1, y0, y1, Dicke
ZAPFEN = 8.0                 # Lagerzapfen Ø (Soll) für die Kurbelnabe
ZAPFEN_H = 4.0               # über der Platte = Kurbeldicke (höher streift das Pleuel bei 180°)
SCHIENE_X = (30.0, 115.0)    # Kolbenführung
SCHIENE_INNEN = 11.0         # halbe lichte Weite (Kolben 21 breit → 0,5 Spiel je Seite)
SCHIENE_B, SCHIENE_H = 4.0, 4.0

# ---- Geometrie ----
x0, x1, y0, y1, t = PLATTE
part = Pos(x0, y0, 0) * Box(x1 - x0, y1 - y0, t, align=MIN3)
part += tag("lagerzapfen", Pos(0, 0, t) * Cylinder(pin_d(ZAPFEN) / 2, ZAPFEN_H, align=C))
for s in (-1, 1):
    y = s * (SCHIENE_INNEN + SCHIENE_B / 2)
    part += tag(f"schiene_{'links' if s > 0 else 'rechts'}",
                Pos((SCHIENE_X[0] + SCHIENE_X[1]) / 2, y, t)
                * Box(SCHIENE_X[1] - SCHIENE_X[0], SCHIENE_B, SCHIENE_H, align=C))
part = bottom_chamfer(part)

# ---- Selbstprüfung ----
bb = part.bounding_box()
zapfen = (part & Pos(0, 0, t + 3) * Box(20, 20, 0.01, align=C)).bounding_box()
gasse = Pos(70, 0, t + 2) * Box(1, 40, 0.01, align=C) - part
gasse_y = min(gasse.solids(), key=lambda s: abs(s.center().Y)).bounding_box()
checks = {
    "Länge": (bb.size.X, x1 - x0, 0.01),
    "Breite": (bb.size.Y, y1 - y0, 0.01),
    "Lagerzapfen Ø": (zapfen.size.X, pin_d(ZAPFEN), 0.01),
    "Führung lichte Weite": (gasse_y.size.Y, 2 * SCHIENE_INNEN, 0.01),
}
