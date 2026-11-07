---
description: Exaktes Funktionsteil aus einer Beschreibung erzeugen, prüfen und rendern (ohne zu drucken)
argument-hint: <Beschreibung mit Massen, Material optional>
---
Erzeuge ein druckfertiges, masshaltiges Teil für den Creality Hi nach AGENTS.md:

Auftrag: $ARGUMENTS

1. **Vorbereiten:** `get_config` lesen – `bausteine` listet alle Funktionen aus `lib/`, die Dateien nicht extra
   öffnen. Gehört das Teil zu bestehenden Projekten (`project_list`), deren `spec.yaml` für gemeinsame Masse lesen.
   Bei Änderungen an einem bestehenden Projekt `get_ui_feedback` prüfen (falls vorhanden: `screenshot` mit Read
   ansehen, `model_xyz` / `delta_model_mm` / `bbox_model` umsetzen, nach erfolgreichem Build `clear_ui_feedback`).
2. **Fehlende Masse:** Fehlt Kritisches, **eine** gebündelte Rückfrage stellen und warten. Merkliste je Teiltyp:
   - Halter/Gehäuse/Aufnahme: Masse des Geräts (L × B × H), Spiel gewünscht?, Befestigung (Schraube, Klebeband, Klemmen), Kabel-/Tastenöffnungen.
   - Zahnrad/Getriebe: Modul, Zähnezahlen, Achsabstand, Wellen-Ø (rund/D-Welle), Befestigung (Madenschraube, Pressen), Zahnbreite.
   - Lager/Welle/Rohr: Ø des Gegenstücks (gemessen?), Spiel- oder Presspassung, Länge.
   - Platte/Flansch/Lochbild: Lochabstände, Schraubengrösse, Senkung oben/unten, Dicke.
   - Schwenk-/Drehteil: Drehpunkt, Schwenkwinkel, Radius, Endanschläge.
   - Immer: Last/Wärme/aussen (→ Material), Bauraum oder Freiräume.
3. **Projekt anlegen:** `project_new` mit kurzem Namen und Material (Standard PLA, PETG nach Regel 10).
   Hauptteil und Gegenstück je eigenes Projekt.
4. **`spec.yaml` ausfüllen:** beschreibung, material, qualitaet, bbox (Soll-Aussenmasse), masse mit Herleitung
   (z. B. „Innenbreite 32,5 = Gerät 32 + 2 × 0,25 Spiel PETG“), annahmen. Mehrere getrennte Teile in einem
   Druck: `koerper: <Anzahl>`. Farbwunsch? `cfs_status` aufrufen und `filament` setzen (Platz, Farbname oder je Körper).
5. **`model.py` schreiben:** Parameter oben, Drucklage, Toleranzen und Bausteine aus `lib.parts`/`lib.gears`
   (z. B. `sector`, `slot_cut`, `gear_face`), `bottom_chamfer` (an z = 0 keine eigenen Fasen; Zahnräder:
   `gear_solid(…, stufe=0.15)`), `checks` für alle Funktionsmasse. Wichtige Bereiche mit `tag("name", …)` und
   Schnitte mit `cut_feature(part, "loch_1", …)` benennen – dann nennt der Prüfbericht bei Fehlern das Feature.
   Mehrfarbig: `bodies = {name: Körper}` ohne Überschneidung (siehe AGENTS.md, Abschnitt Mehrfarbig).
6. **Bauen und prüfen:** `build_and_check` → bei Fehlern `report.zones` lesen (Ort, Fläche, Feature, Hinweis),
   korrigieren und wiederholen (max. 5 Runden).
7. **Sichtkontrolle:** `out/render.png` mit Read ansehen und kritisch prüfen.
8. **Zusammenfassung:** Aussenmasse, Funktionsmasse mit Toleranz, Material, Drucklage, Warnungen, Bildpfad.
   Hat das Teil Passungen (Lager, Welle, Lochbild, Zahneingriff), eine Schablone anbieten (`/schablone`,
   mit passenden Höhen `z`). Ende mit: „Mit /drucken slice ich das Teil und zeige Druckzeit und Filament.“
