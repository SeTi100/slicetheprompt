---
description: Dünnen Passungs-Testdruck (Schablone) aus einem Teil erzeugen, um Löcher und Passungen günstig zu prüfen
argument-hint: <projekt> [Höhe/Bereich der Passung, Dicke]
---
Erzeuge eine Schablone (dünner Passungs-Testdruck) nach CLAUDE.md, Abschnitt „Schablone“:

Auftrag: $ARGUMENTS

1. Projekt bestimmen (ohne Angabe: zuletzt bearbeitetes Projekt aus `project_list`). Ist es noch nicht
   gebaut, zuerst `build_and_check`.
2. Schnitthöhen `z` wählen: dort, wo die Passungen liegen (Löcher, Taschen, Stifte, Verzahnung).
   Hilfe: Parameter in `model.py`, `features_z` aus dem Ergebnis. Standard `z = [0]` (Unterseite).
   Eine Tasche über einem Boden prüft man oberhalb des Bodens (z. B. z = Bodendicke + 1).
   Dicke `height` Standard 2 mm (mind. 0,6 mm).
3. `stencil(project, z, height)` aufrufen (CLI: `uv run slicetheprompt schablone <projekt> --z 0 12 --hoehe 2`).
4. `out/render.png` der Schablone mit Read ansehen. Warnung „Anzahl Körper“ ist bei mehreren
   Scheiben oder getrennten Teilen normal.
5. Dem Nutzer zeigen: welche Scheiben (z-Bereich, Grösse), was er damit prüft, Materialanteil
   (`material_anteil_pct`), Bildpfad. Ende mit: „Mit /drucken <schablonen-projekt> slice ich sie.“
