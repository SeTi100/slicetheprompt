"""beispiel-schild-zweifarbig: Platte mit bündig eingelegter Schrift in zweiter Farbe.

Alle Masse in mm. Drucklage: Platte flach, Schrift oben. Zwei Körper ohne Überschneidung:
die Schrift wird aus der Platte ausgespart und als eigener Körper eingesetzt.
"""
from build123d import *
from lib.parts import *

set_material("pla")
C = (Align.CENTER, Align.CENTER, Align.MIN)

# ---- Parameter ----
L, B, H = 60.0, 25.0, 4.0
TEXT = "SLICE"
SCHRIFT_H = 10.0          # Schriftgrösse
TIEFE = 0.6               # Einlagetiefe = 3 Schichten à 0,2 mm

# ---- Geometrie ----
platte = bottom_chamfer(Box(L, B, H, align=C))
schrift = Pos(0, 0, H - TIEFE) * extrude(
    Text(TEXT, font_size=SCHRIFT_H, align=(Align.CENTER, Align.CENTER)), amount=TIEFE)
platte = platte - schrift

bodies = {"platte": platte, "schrift": schrift}

# ---- Selbstprüfung ----
bb = (platte + schrift).bounding_box()
sb = schrift.bounding_box()
checks = {
    "Länge": (bb.size.X, L, 0.01),
    "Breite": (bb.size.Y, B, 0.01),
    "Höhe": (bb.size.Z, H, 0.01),
    "Schrift bündig oben": (sb.max.Z, H, 0.001),
    "Schrift-Tiefe": (sb.size.Z, TIEFE, 0.001),
}
