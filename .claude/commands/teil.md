---
description: Exaktes Funktionsteil aus einer Beschreibung erzeugen, prüfen und rendern (ohne zu drucken)
argument-hint: <Beschreibung mit Massen, Material optional>
---
Erzeuge ein druckfertiges, masshaltiges Teil für den Creality Hi nach CLAUDE.md:

Auftrag: $ARGUMENTS

1. `get_config` lesen. Bei Änderungen an einem bestehenden Projekt `get_ui_feedback` prüfen (falls vorhanden: `screenshot` mit Read ansehen, `model_xyz` / `delta_model_mm` / `bbox_model` umsetzen und nach erfolgreichem Build `clear_ui_feedback` aufrufen). Fehlt ein kritisches Mass, stelle genau eine gebündelte Rückfrage und warte.
2. `project_new` mit kurzem Namen und Material (Standard PLA, PETG nach Regel 10).
3. `spec.yaml` ausfüllen: beschreibung, material, qualitaet, bbox (Soll-Aussenmasse), masse mit
   Herleitung (z. B. „Innenbreite 32,5 = Gerät 32 + 2 × 0,25 Spiel PETG“), annahmen.
   Farbwunsch? `cfs_status` aufrufen und `filament` setzen (Platz, Farbname oder je Körper).
4. `model.py` schreiben: Parameter oben, Drucklage, Toleranzen aus `lib.parts`, `bottom_chamfer`,
   `checks` für alle Funktionsmasse. Wichtige Wände/Bohrungen gerne mit `tag("name", ...)` und
   `cut_feature(part, "loch_1", ...)` benennen. Mehrfarbig: `bodies = {name: Körper}` ohne Überschneidung
   (siehe CLAUDE.md, Abschnitt Mehrfarbig).
5. `build_and_check` → bei Fehlern korrigieren und wiederholen (max. 5 Runden).
6. `out/render.png` mit Read ansehen und kritisch prüfen.
7. Zusammenfassung an den Nutzer: Aussenmasse, Funktionsmasse, Material, Drucklage, Warnungen,
   Bildpfad. Ende mit: „Mit /drucken slice ich das Teil und zeige Druckzeit und Filament.“
