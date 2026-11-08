"""beispiel-deckel: Steckdeckel für die Beispiel-Box (beispiel-box-m3), Teil der Beispiel-Baugruppe.

Alle Masse in mm. Drucklage: Platte auf z = 0, Steckrand zeigt nach oben.
"""
from build123d import *
from lib.parts import *

set_material("pla")
C = (Align.CENTER, Align.CENTER, Align.MIN)

# ---- Parameter ----
L, B = 60.0, 40.0                 # wie Box aussen
PLATTE = 2.0
BOX_INNEN_L, BOX_INNEN_B = 56.0, 36.0     # Box innen (60 − 2 × 2 Wand)
RAND_H, RAND_WAND = 4.0, 1.6
RAND_L = outer(BOX_INNEN_L - 2 * tol()["clearance"])   # passt mit Spiel in die Box
RAND_B = outer(BOX_INNEN_B - 2 * tol()["clearance"])

# ---- Geometrie ----
part = Box(L, B, PLATTE, align=C)
rand = Pos(0, 0, PLATTE) * Box(RAND_L, RAND_B, RAND_H, align=C)
rand -= Pos(0, 0, PLATTE) * Box(RAND_L - 2 * RAND_WAND, RAND_B - 2 * RAND_WAND, RAND_H, align=C)
part += tag("steckrand", rand)
part = bottom_chamfer(part)

# ---- Selbstprüfung ----
bb = part.bounding_box()
schnitt = (part & Pos(0, 0, PLATTE + 2) * Box(100, 100, 0.01, align=C)).bounding_box()
checks = {
    "Länge": (bb.size.X, L, 0.01),
    "Breite": (bb.size.Y, B, 0.01),
    "Höhe": (bb.size.Z, PLATTE + RAND_H, 0.01),
    "Steckrand aussen X": (schnitt.size.X, RAND_L, 0.01),
    "Steckrand aussen Y": (schnitt.size.Y, RAND_B, 0.01),
    # modelliertes Spiel = Passungsspiel + Kalibrier-Korrektur aus outer() (gedruckt: Passungsspiel)
    "Spiel je Seite X (modelliert)": ((BOX_INNEN_L - schnitt.size.X) / 2,
                                      tol()["clearance"] + tol().get("outer_offset", 0.0), 0.01),
}
