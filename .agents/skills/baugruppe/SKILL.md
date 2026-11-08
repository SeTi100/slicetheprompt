---
name: baugruppe
description: >-
  Setzt mehrere Teil-Projekte als Baugruppe (assembly.py) zusammen, prüft benannte Stellungen (Posen)
  exakt auf Überschneidungen und Abstände und zeigt sie im Studio (Posen, Explosion, Geister).
  Verwenden, wenn der Nutzer mehrere Teile zusammen sehen, einen Zusammenbau oder Verstellweg prüfen
  oder eine Baugruppe anlegen will (oder /baugruppe bzw. $baugruppe schreibt).
---

# Baugruppe

1. **Teile bestimmen:** `project_list`. Jedes Teil braucht `out/model.step` → sonst zuerst `build_and_check`.
2. **Projekt:** vorhandenes Projekt mit `assembly.py` ändern, sonst `assembly_new(name, parts)`
   (CLI: `uv run slicetheprompt baugruppe-neu <name> <teil…>`).
3. **assembly.py ausfüllen** (Format im Kopf von `slicetheprompt/assembly.py`):
   - Masse der Teile mit `parameter(projekt)` lesen, nie abschreiben.
   - Je Teil die Umkehrung der Drucklage (letzte Zeilen von `model.py`) als Konstante, z. B.
     `part = mirror(Pos(0, 0, H) * k, about=Plane.XZ)` → `pos(0, 0, -H) * spiegel("XZ")`.
   - `platzierung(p)` → je Teil `Tf` (`pos`, `rot`, `spiegel`, verkettet mit `*`), bei `anzahl > 1` Liste.
   - `POSEN`: Endlagen und Mitte jedes Verstellwegs.
   - `geister(p)` → `Geist(form, lage=…, farbe=…, folgt="Teil")` für Bezugskörper (Handy, Okular, Schrauben).
   - `KONTAKT = {("Teil", "Geist"): 0.3}` nur für gewollten Klemmkontakt.
   Fehlt ein Mass für die Lage, **einmal** gebündelt nachfragen.
4. **Bauen:** `build_assembly(project)` (CLI: `uv run slicetheprompt baugruppe <projekt>`), dann
   `out/render.png` ansehen. Liegt ein Teil sichtbar falsch, Platzierung korrigieren (höchstens 5 Runden).
5. **Melden** je Pose: frei / Klemmkontakt mit Tiefe / Kollision (Paar, mm³), engste Spalte, Hinweise.
   Bei Kollision Teile nicht selbst ändern, sondern Ursache und Vorschlag nennen.
   Studio: `uv run slicetheprompt ui --project <baugruppe>` (Pose per `&pose=<Nr>`).
