---
name: teil
description: >-
  Erzeugt ein druckfertiges, masshaltiges 3D-Druck-Funktionsteil (ein- oder mehrfarbig) mit build123d,
  prüft Druckbarkeit und Toleranzen und rendert eine 4-Ansichten-Vorschau ohne zu drucken. Verwenden,
  wenn der Nutzer ein neues Teil konstruieren, modellieren oder anpassen möchte (oder /teil schreibt).
---

# Teil konstruieren, prüfen und rendern

Erzeuge ein druckfertiges, masshaltiges Teil für den Creality Hi Combo nach `AGENTS.md`:

1. **Vorbereiten:** `get_config` lesen (CLI: `uv run slicetheprompt config`) – `bausteine` listet alle Funktionen
   aus `lib/` mit Kurzbeschreibung, die Dateien nicht extra öffnen. Gehört das Teil zu bestehenden Projekten
   (`project_list`), deren `spec.yaml` für gemeinsame Masse lesen. Bei Änderungen an einem bestehenden Projekt
   prüfen, ob räumliches Feedback aus `SliceThePrompt` vorliegt (`get_ui_feedback`). Falls ja: `screenshot`
   (`out/ui_feedback.png`) ansehen, `model_xyz` / `delta_model_mm` / `bbox_model` umsetzen und nach erfolgreichem
   Umbau `clear_ui_feedback` aufrufen.
2. **Fehlende Masse:** Fehlt Kritisches, genau **eine** gebündelte Rückfrage stellen und warten. Merkliste je Teiltyp:
   - Halter/Gehäuse/Aufnahme: Masse des Geräts (L × B × H), Spiel gewünscht?, Befestigung (Schraube, Klebeband, Klemmen), Kabel-/Tastenöffnungen.
   - Zahnrad/Getriebe: Modul, Zähnezahlen, Achsabstand, Wellen-Ø (rund/D-Welle), Befestigung (Madenschraube, Pressen), Zahnbreite.
   - Lager/Welle/Rohr: Ø des Gegenstücks (gemessen?), Spiel- oder Presspassung, Länge.
   - Platte/Flansch/Lochbild: Lochabstände, Schraubengrösse, Senkung oben/unten, Dicke.
   - Schwenk-/Drehteil: Drehpunkt, Schwenkwinkel, Radius, Endanschläge.
   - Immer: Last/Wärme/aussen (→ Material), Bauraum oder Freiräume.
3. **Projekt anlegen:** `project_new` (`uv run slicetheprompt new <name> --material <pla|petg> --desc "..."`).
   Standard ist PLA; PETG bei Wärme > 55 °C, Aussenbereich, Schnapphaken oder Biegelast. Hauptteil und
   Gegenstück je eigenes Projekt.
4. **`spec.yaml` ausfüllen:** `beschreibung`, `material`, `qualitaet`, `bbox` (Soll-Aussenmasse), `masse` mit
   Herleitung (z. B. „Innenbreite 32,5 = Gerät 32 + 2 × 0,25 Spiel PETG“), `annahmen`. Mehrere getrennte Teile
   in einem Druck: `koerper: <Anzahl>` (keine Verbindungsstege bauen). Bei Farbwunsch `cfs_status` prüfen und
   `filament` setzen (Platz, Farbname oder `{körper: farbe}`); Drucker offline → ohne Filamentwahl weiter.
5. **`model.py` schreiben:** Parameter oben, Drucklage (grösste ebene Fläche auf `z = 0`), Toleranzen und
   Bausteine aus `lib.parts`/`lib.gears` (z. B. `sector`, `slot_cut`, `gear_face`) – keine Hilfsfunktionen aus
   anderen Projekten kopieren. `bottom_chamfer` fast alle Kanten auf z = 0 inkl. Bohrungen, dort keine eigenen
   Fasen/Kegel; Zahnräder: `gear_solid(…, stufe=0.15)`. `checks` für alle Funktionsmasse. Wichtige Bereiche mit
   `tag("name", shape)` und Schnitte mit `cut_feature(part, "loch_1", cutter)` benennen – dann nennt der
   Prüfbericht bei Fehlern das Feature. Mehrfarbig: `bodies = {"name": Körper, …}` ohne Überschneidung.
6. **Bauen und prüfen:** `build_and_check` (`uv run slicetheprompt run <projekt>`). Bei Fehlern oder Warnungen
   `report.zones` lesen (Ort in Modellkoordinaten, Fläche, Feature, Hinweis), korrigieren und wiederholen
   (maximal 5 Runden). `out/diagnostics_3d.json` nur öffnen, wenn `zones` nicht reicht.
7. **Sichtkontrolle:** `out/render.png` zwingend ansehen und kritisch auf Geometriefehler, schwebende Teile
   oder falsche Lage prüfen.
8. **Zusammenfassung zeigen:** Aussenmasse, Funktionsmasse mit Toleranz, Material, Drucklage, Warnungen und
   Pfad zu `out/render.png`. Hat das Teil Passungen (Lager, Welle, Lochbild, Zahneingriff), eine Schablone
   anbieten (Skill `schablone`, `stencil` mit passenden Höhen `z`) statt Testteile von Hand zu bauen.
   Schliesse mit dem Hinweis, dass das Teil jetzt gesliced und gedruckt werden kann.
