---
description: Mehrere Teil-Projekte als Baugruppe zusammensetzen, Posen auf Kollision prüfen und im Studio zeigen
argument-hint: <name> <teile…> [Posen, Geister, erlaubter Klemmkontakt]
---
Baue eine Baugruppe nach AGENTS.md, Abschnitt „Baugruppe“:

Auftrag: $ARGUMENTS

1. Teil-Projekte bestimmen (`project_list`). Jedes Teil muss gebaut sein (`out/model.step`);
   sonst zuerst `build_and_check` je Teil.
2. Gibt es schon ein Projekt mit `assembly.py` für diesen Auftrag, dieses ändern; sonst
   `assembly_new(name, parts)` (CLI: `uv run slicetheprompt baugruppe-neu <name> <teil…>`).
3. `assembly.py` ausfüllen:
   - `parameter(projekt)` für Masse der Teile (nie abschreiben),
   - je Teil die Umkehrung der Drucklage (letzte Zeilen von `model.py`) als Konstante,
   - `platzierung(p)` mit `pos`/`rot`/`spiegel` (bei `anzahl > 1` eine Liste),
   - `POSEN` mit den Endlagen und der Mitte jedes Verstellwegs,
   - `geister(p)` für Bezugskörper, `KONTAKT` nur für gewollten Klemmkontakt.
   - Bewegt sich etwas (Schlitten, Kurbel, Knopf auf Gewinde): `GELENKE` statt nur Posen –
     `Fest(boden)`, dann je Teil ein Gelenk zu seinem Träger (`Drehung`, `Schiebung`, `Schraube` …)
     mit `grenzen`; keine Formeln. Posen dürfen Gelenkwerte sein (`{"gelenke": {"Kurbel": 90}}`).
   Fehlt ein Mass für die Lage (z. B. Abstand zwischen zwei Teilen), **einmal** gebündelt fragen.
4. `build_assembly(project)` aufrufen, `out/render.png` mit Read ansehen. Stimmt die Lage eines Teils
   offensichtlich nicht (schwebt, steckt quer), Platzierung korrigieren (höchstens 5 Runden).
5. Dem Nutzer je Pose zeigen: frei / Klemmkontakt mit Tiefe / Kollision (Paar, mm³), engste Spalte,
   Hinweise (veraltete Teile). Bei Kollision die Teile **nicht** selbst ändern, sondern Ursache und
   Vorschlag nennen. Ende mit dem Studio-Link:
   `uv run slicetheprompt ui --project <baugruppe>` → `http://127.0.0.1:8765/?project=<baugruppe>`.
   Bei 3D-Feedback auf einer Baugruppe (`get_ui_feedback`) direkt `target_project` und `part_print_xyz` / `part_bbox_print` aus den Annotationen nutzen und mit `build_and_check(project=target_project, also_assembly=baugruppe, clear_feedback=True)` in einem Schritt bauen.
