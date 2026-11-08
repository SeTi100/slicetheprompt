"""Beispiel-Baugruppe Kurbeltrieb: Drehung der Kurbel wird über das Pleuel zur geraden Kolbenbewegung

Zeigt Gelenke statt fester Stellungen: Rahmen ist Boden, Kurbel dreht auf dem Lagerzapfen,
Pleuel hängt drehbar an Kurbel und Kolben, der Kolben gleitet in der Führung. Das ist eine
geschlossene Kette – keine Formel für den Kolbenweg, der Löser findet die Lage aus den Gelenken.
Posen sind Kurbelwinkel; im Studio mit „Bewegen“ die Kurbel ziehen oder den Regler nutzen.

Vorher die vier Teile bauen:
    uv run slicetheprompt run beispiel-kurbeltrieb-rahmen   (… -kurbel, -pleuel, -kolben)
Dann:
    uv run slicetheprompt baugruppe beispiel-kurbeltrieb
"""
from build123d import *
from slicetheprompt.assembly import *

P = "2026-11-08-beispiel-kurbeltrieb-"
RAHMEN = parameter(P + "rahmen")
PLEUEL = parameter(P + "pleuel")
R, L = PLEUEL.R, PLEUEL.L
T = RAHMEN.PLATTE[4]                     # Plattendicke = Höhe der Kurbel-/Kolbenunterseite

TEILE = {
    "Rahmen": Teil(P + "rahmen", farbe="#4E79A7"),
    "Kurbel": Teil(P + "kurbel", farbe="#F28E2B", explosion=(0, 0, 1)),
    "Pleuel": Teil(P + "pleuel", farbe="#59A14F", explosion=(0, 0, 2)),
    "Kolben": Teil(P + "kolben", farbe="#B07AA1", explosion=(0, 0, 1)),
}

# Grundstellung: Kurbel auf 0° (alle Teile wie gedruckt, nur angehoben)
def platzierung(p):
    return {
        "Rahmen": pos(),
        "Kurbel": pos(0, 0, T),
        "Pleuel": pos(0, 0, T + 4),          # liegt auf dem Kurbelarm (4 dick)
        "Kolben": pos(0, 0, T),
    }


GELENKE = [
    Fest("Rahmen"),
    Drehung("Kurbel", "Rahmen", achse=(0, 0, 1), punkt=(0, 0, 0), name="Kurbel"),
    Drehung("Pleuel", "Kurbel", achse=(0, 0, 1), punkt=(R, 0, 0), name="Kurbelzapfen"),
    Drehung("Kolben", "Pleuel", achse=(0, 0, 1), punkt=(R + L, 0, 0), name="Kolbenbolzen"),
    Schiebung("Kolben", "Rahmen", achse=(1, 0, 0), name="Kolbenweg", grenzen=(-2 * R - 1, 1)),
]

POSEN = {
    "Kurbel 0°": {},
    "Kurbel 60°": {"gelenke": {"Kurbel": 60}},
    "Kurbel 90°": {"gelenke": {"Kurbel": 90}},
    "Kurbel 180°": {"gelenke": {"Kurbel": 180}},
    "Kurbel 270°": {"gelenke": {"Kurbel": 270}},
}

KONTAKT = {}
IGNORIEREN = []
GRENZE_MM3 = 0.5
