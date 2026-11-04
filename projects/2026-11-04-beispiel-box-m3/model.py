"""beispiel-box-m3: offene Box mit vier M3-Löchern im Boden (Abnahmetest Phase 1).

Alle Masse in mm. Drucklage: Öffnung oben, Boden auf z = 0.
"""
from build123d import *
from lib.parts import *

set_material("pla")
C = (Align.CENTER, Align.CENTER, Align.MIN)

# ---- Parameter ----
L, B, H = 60.0, 40.0, 15.0
WAND, BODEN = 2.0, 2.0
RASTER_X, RASTER_Y = 50.0, 30.0

# ---- Geometrie ----
part = Box(L, B, H, align=C)
part -= Pos(0, 0, BODEN) * Box(L - 2 * WAND, B - 2 * WAND, H, align=C)
for x in (-RASTER_X / 2, RASTER_X / 2):
    for y in (-RASTER_Y / 2, RASTER_Y / 2):
        part -= Pos(x, y, -0.5) * screw_hole_cut("M3", BODEN + 1.0)
part = bottom_chamfer(part)

# ---- Selbstprüfung ----
bb = part.bounding_box()
boden = [f for f in part.faces().filter_by(Axis.Z) if abs(f.center().Z - BODEN) < 1e-6]
checks = {
    "Länge": (bb.size.X, L, 0.01),
    "Breite": (bb.size.Y, B, 0.01),
    "Höhe": (bb.size.Z, H, 0.01),
    "Innenboden auf z = 2": (len(boden), 1, 0),
    "Volumen (Kontrolle)": (part.volume, L * B * H - (L - 2 * WAND) * (B - 2 * WAND) * (H - BODEN)
                            - 4 * 3.14159265 * (hole_d(3.2) / 2) ** 2 * BODEN, 40.0),  # Fase ≈ 30 mm³
}
