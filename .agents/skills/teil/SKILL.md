---
name: teil
description: >-
  Erzeugt ein druckfertiges, masshaltiges 3D-Druck-Funktionsteil (ein- oder mehrfarbig) mit build123d,
  prüft Druckbarkeit und Toleranzen und rendert eine 4-Ansichten-Vorschau ohne zu drucken. Verwenden,
  wenn der Nutzer ein neues Teil konstruieren, modellieren oder anpassen möchte (oder /teil schreibt).
---

# Teil konstruieren, prüfen und rendern

Erzeuge ein druckfertiges, masshaltiges Teil für den Creality Hi Combo nach `AGENTS.md`:

1. **Konfiguration & 3D-Feedback prüfen:** `get_config` lesen. Bei Änderungen an einem bestehenden Projekt zuerst prüfen, ob räumliches Feedback aus `SliceThePrompt` vorliegt (`get_ui_feedback` / `out/ui_feedback.json`). Falls ja: `screenshot` (`out/ui_feedback.png`) mit `read_file` ansehen, die exakten `model_xyz` / `delta_model_mm` / `bbox_model`-Werte nutzen und nach erfolgreichem Umbau `clear_ui_feedback` aufrufen. Fehlt ein kritisches Mass, stelle genau **eine** gebündelte Rückfrage und warte.
2. **Projekt anlegen:** `project_new` (`uv run slicetheprompt new <name> --material <pla|petg> --desc "..."`). Standard ist PLA; PETG bei Wärme > 55 °C, Aussenbereich, Schnapphaken oder Biegelast.
3. **`spec.yaml` ausfüllen:** `beschreibung`, `material`, `qualitaet`, `bbox` (Soll-Aussenmasse), `masse` mit Herleitung (z. B. „Innenbreite 32,5 = Gerät 32 + 2 × 0,25 Spiel PETG“), `annahmen`. Bei Farbwunsch `cfs_status` (`uv run slicetheprompt cfs`) prüfen und `filament` setzen (Platz, Farbname oder `{körper: farbe}`).
4. **`model.py` schreiben:** Parameter oben, Drucklage (grösste ebene Fläche auf `z = 0`), Toleranzen aus `lib.parts`, `bottom_chamfer`, `checks` für alle Funktionsmasse. Wichtige Teilbereiche und Schnitte bevorzugt mit `tag("name", shape)` bzw. `cut_feature(part, "loch_1", cutter)` aus `lib.parts` benennen (macht sie in `SliceThePrompt` auswählbar und erkennt verfehlte Schnitte automatisch). Bei Mehrfarbdruck `bodies = {"name": Körper, …}` ohne Überschneidung (`grund = grund - einlage`).
5. **Bauen & prüfen:** `build_and_check` (`uv run slicetheprompt run <projekt>`) → bei Fehlern selbst korrigieren und wiederholen (maximal 5 Runden).
6. **Sichtkontrolle:** `out/render.png` zwingend mit `read_file` ansehen und kritisch auf Geometriefehler, schwebende Teile oder falsche Lage prüfen.
7. **Zusammenfassung zeigen:** Aussenmasse, Funktionsmasse mit Toleranz, Material, Drucklage, eventuelle Warnungen und Link zu `out/render.png`. Schliesse mit dem Hinweis, dass das Teil jetzt geslicet und gedruckt werden kann.
