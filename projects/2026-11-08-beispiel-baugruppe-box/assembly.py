"""Beispiel-Baugruppe: Box (beispiel-box-m3) mit Steckdeckel (beispiel-deckel) und vier M3-Schrauben

Zeigt das Baugruppen-Format: Teile aus zwei Projekten, Platzierung aus der Drucklage, drei Posen
(zu, offen, verkantet) und Schrauben als Geisterkörper. Die Pose „verkantet“ kollidiert absichtlich:
so sieht eine rote Überschneidung im Studio aus.

Vorher beide Teile bauen:  uv run slicetheprompt run beispiel-box-m3   und   … run beispiel-deckel
Dann:                      uv run slicetheprompt baugruppe beispiel-baugruppe-box
"""
from build123d import *
from slicetheprompt.assembly import *

BOX = parameter("2026-11-04-beispiel-box-m3")
DECKEL = parameter("2026-11-08-beispiel-deckel")

TEILE = {
    "Box": Teil("2026-11-04-beispiel-box-m3", farbe="#4E79A7", explosion=(0, 0, -1)),
    "Deckel": Teil("2026-11-08-beispiel-deckel", farbe="#F28E2B", explosion=(0, 0, 1)),
}

POSEN = {
    "zu": {"heben": 0, "kippen_x": 0, "kippen_y": 0},
    "offen": {"heben": 25, "kippen_x": -25, "kippen_y": 0},
    "verkantet (Kollision)": {"heben": 0, "kippen_x": 0, "kippen_y": 4},
}

# Deckel wird mit der Platte nach unten gedruckt: umdrehen, dann Platte auf den Boxrand legen
DECKEL_UMGEDREHT = rot(180, 0, 0)
DECKEL_AUF_BOX = BOX.H + DECKEL.PLATTE


def platzierung(p):
    deckel = (pos(0, 0, DECKEL_AUF_BOX + p["heben"]) * rot(p["kippen_x"], p["kippen_y"], 0)
              * DECKEL_UMGEDREHT)
    return {"Box": pos(), "Deckel": deckel}      # Box liegt wie gedruckt


def _schrauben():
    """4 × M3 von unten durch die Bodenlöcher (Kopf Ø5,5 × 2, Schaft Ø3 × 8)."""
    s = None
    for x in (-BOX.RASTER_X / 2, BOX.RASTER_X / 2):
        for y in (-BOX.RASTER_Y / 2, BOX.RASTER_Y / 2):
            one = Pos(x, y, -2) * Cylinder(2.75, 2, align=(Align.CENTER, Align.CENTER, Align.MIN))
            one += Pos(x, y, 0) * Cylinder(1.5, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
            s = one if s is None else s + one
    return s


def geister(p):
    return {"Schrauben M3": Geist(_schrauben(), farbe="#C0C7D1", folgt="Box")}


KONTAKT = {}
IGNORIEREN = []
GRENZE_MM3 = 0.5
