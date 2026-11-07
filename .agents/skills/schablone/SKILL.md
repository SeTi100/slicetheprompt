---
name: schablone
description: >-
  Erzeugt aus einem bestehenden Teil eine Schablone (engl. stencil): eine oder mehrere dünne Scheiben
  (Standard 2 mm) als schnellen, günstigen Passungs-Testdruck für Löcher, Stifte, Taschen und Verzahnungen.
  Verwenden, wenn der Nutzer Passungen vor dem vollen Druck testen will, eine Schablone, einen Stencil
  oder einen dünnen Testdruck möchte (oder /schablone bzw. /stencil schreibt).
---

# Schablone (Passungs-Testdruck)

1. **Projekt bestimmen:** ohne Angabe das zuletzt bearbeitete aus `project_list`. Noch nicht gebaut →
   zuerst `build_and_check`.
2. **Schnitthöhen wählen:** `z` dort, wo die Passungen liegen (Parameter in `model.py`, Hinweise in
   `features_z`). Standard `[0]` = Unterseite. Tasche über einem Boden → oberhalb des Bodens schneiden.
   Dicke `height` Standard 2 mm (mindestens 3 Schichten = 0,6 mm).
3. **Erzeugen:** `stencil(project, z, height)`
   (CLI: `uv run slicetheprompt schablone <projekt> --z 0 12 --hoehe 2`, Alias `stencil`).
   Legt `projects/<datum>-<name>-schablone/` an bzw. aktualisiert sie; baut, prüft und rendert sofort.
4. **Render ansehen** (`out/render.png`). Die Warnung „Anzahl Körper“ ist bei mehreren Scheiben normal.
5. **Dem Nutzer zeigen:** Scheiben (z-Bereich, Grösse), was damit geprüft wird, Materialanteil
   (`material_anteil_pct`), Bildpfad. Drucken danach wie im Skill `drucken` mit dem Schablonen-Projekt.
