"""Kalibrier-Testteil: Löcher Ø5/Ø8/Ø10, Stift Ø8, Block 20 × 20 × 10 – alles OHNE Ausgleich.

Nach dem Druck mit dem Messschieber messen (Löcher in der Mitte der Plattendicke):
  block_x, block_y, block_z, loch5, loch8, loch10, stift8
"""
from build123d import *

plate = Box(70, 20, 4, align=(Align.MIN, Align.MIN, Align.MIN))
block = Pos(50, 0, 0) * Box(20, 20, 10, align=(Align.MIN, Align.MIN, Align.MIN))
pin = Pos(43.5, 10, 4) * Cylinder(4, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
part = plate + block + pin
for x, d in ((8, 5.0), (19, 8.0), (32, 10.0)):
    part -= Pos(x, 10, 0) * Cylinder(d / 2, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))

bb = part.bounding_box()
checks = {"Länge": (bb.size.X, 70, 0.01), "Breite": (bb.size.Y, 20, 0.01), "Höhe": (bb.size.Z, 12, 0.01)}
