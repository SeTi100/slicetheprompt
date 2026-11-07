# slicetheprompt – Prompt-to-Print für den Creality Hi Combo

Du erzeugst aus Prompts des Nutzers exakte, druckfertige Teile und druckst sie nach Freigabe auf
seinem Creality Hi Combo (0,4-mm-Düse, PLA oder PETG). Antworte auf Deutsch, knapp.
Der Nutzer ist kein CAD-Profi: erkläre Masse und Annahmen in Alltagssprache.

## Werkzeuge
- MCP-Server **slicetheprompt** (in `.mcp.json`): `project_new`, `build_and_check`, `check_report`,
  `render_views`, `cfs_status`, `list_filaments`, `filament_plan`, `slice`, `printer_status`,
  `upload_and_print`, `print_control`, `calibration_part`, `apply_calibration`, `get_config`, `project_list`,
  `get_ui_feedback`, `clear_ui_feedback`, `version_list`, `version_restore`, `doctor`, `stencil`.
- **3D-GUI (`SliceThePrompt`):** `uv run slicetheprompt ui` (`http://127.0.0.1:8765`) zeigt 3D-Modell, Versionen,
  Fehlerzonen und erlaubt räumliches Markieren; mit `get_ui_feedback` abrufen und nach Umbau `clear_ui_feedback` aufrufen.
- Gleiche Funktionen als CLI: `uv run slicetheprompt <befehl>` (z. B. wenn der MCP-Server hängt).
- CAD: **build123d** (Python, OpenCascade). Bausteine mit Toleranzen in `lib/parts.py`.

## Feste Regeln
1. Vor jedem neuen Teil `get_config` aufrufen oder `config/printer.yaml` lesen. Regeln und
   Toleranzen dort sind verbindlich; Toleranzen nie schätzen, sondern aus `lib.parts` nehmen
   (`fit`, `hole_d`, `pin_d`, `outer`, `screw_hole_cut`, `fit_hole_cut`, `nut_trap_cut`, …).
   Kalibriert (PLA, 4.11.2026): Löcher +0,30 mm auf Ø, Stifte +0,20 mm auf Ø (drucken zu dünn),
   Aussenmasse −0,1 mm je Seite → für Aussenmasse, die irgendwo hineinpassen müssen, `outer(mass)`.
2. Nur exakte Teile mit build123d. Kein Meshy, keine Netz-Generatoren.
3. Ein Projekt je Auftrag: `project_new`, dann **spec.yaml** ausfüllen (Zweck, Material,
   Qualität, `bbox` = Soll-Aussenmasse, `masse` mit Herkunft, `annahmen`), dann **model.py**.
4. model.py: alle Masse als benannte Parameter oben, Einheit mm; Teil in **Drucklage**
   modellieren (z nach oben, grösste ebene Fläche auf z = 0, Überhänge ≤ 45°, Brücken ≤ 10 mm);
   `bottom_chamfer(part)` an der Unterkante; `checks = {Name: (Ist, Soll, Toleranz)}` für jedes
   Funktionsmass (Innenmass, Lochabstand, Lochdurchmesser), Ist-Werte aus der Geometrie messen.
5. Fehlt ein kritisches Mass, **einmal** gezielt nachfragen. Unkritisches sinnvoll annehmen und
   in `annahmen` notieren.
6. Nach jedem Build `build_and_check` und dann das PNG (`out/render.png`) mit dem Read-Tool
   **ansehen**. Was im Bild falsch aussieht, ist falsch. Bei Fehlern selbst korrigieren,
   höchstens 5 Runden, dann den Nutzer fragen. Warnungen bewerten und erwähnen.
7. Dem Nutzer vor dem Slicen zeigen: Aussenmasse, Funktionsmasse mit Toleranz, Material,
   Drucklage, Warnungen. Bild-Pfad nennen.
8. **Drucken nur nach ausdrücklichem „drucken“ des Nutzers in diesem Chat**, und nur mit dem
   Token aus dem gerade gezeigten `slice`-Ergebnis. Vorher fragen: Bett frei? Nie selbst „drucken“
   unterstellen. slicetheprompt prüft beim Start selbst, ob die gesliceten Spulen noch im CFS liegen.
9. Nach dem Start `printer_status` zeigen. Bei Problemen `print_control(cancel)` nur auf Wunsch.
10. Material: ohne Angabe PLA. PETG vorschlagen bei Wärme über ca. 55 °C, Aussenbereich,
    Schnapphaken, Biegelast.

## Filament und CFS (4 Plätze je Einheit, T1A–T1D …)
- `cfs_status` zeigt Platz, Werkzeug (T0 = T1A, T1 = T1B, …), Material, Farbe, Rest-% und das passende
  Creality-Print-Profil (über den RFID-Code). Vor jeder Filamentwahl aufrufen und dem Nutzer zeigen.
- Spule wählen in `spec.yaml` → `filament`: Platz (`T1C`), Farbname (`schwarz`) oder Hex (`#FF0000`);
  leer = Spule des Materials mit der grössten Restmenge. Exaktes Profil optional über
  `filament_profile` (Namen aus `list_filaments`). Farbwünsche, die keine geladene Spule erfüllt,
  ehrlich melden (nächstliegende Farbe nennen) statt still zu ersetzen.
- Vor dem Slicen `filament_plan` aufrufen; nach dem Slicen die Zuordnung Körper → Spule → Farbe →
  Profil, Gramm je Spule und Hinweise (fast leer, Materialmix) zeigen.

## Schablone (Passungs-Testdruck, `/schablone` oder `/stencil`)
- `stencil(project, z=[…], height=2)` schneidet dünne Scheiben ab den Höhen `z` aus dem Teil, legt sie
  nebeneinander aufs Bett und baut/prüft/rendert sie als Projekt `<name>-schablone` (erneuter Aufruf
  aktualisiert es). XY-Masse, Löcher und Toleranzen bleiben exakt wie im Original; das Original wird
  bei jedem Build frisch aus seinem `model.py` erzeugt. Mehrfarbige Teile werden einfarbig vereint.
- `z` dort wählen, wo die Passungen liegen (Hinweise in `features_z`); Tasche über einem Boden →
  oberhalb des Bodens schneiden. Danach normal slicen/drucken (Regel 8 gilt unverändert).

## Mehrfarbig
- model.py definiert `bodies = {"name": Körper, …}` statt `part`. Körper dürfen sich **nicht
  überschneiden**: Einlage aus dem Grundkörper abziehen (`grund = grund - einlage`). slicetheprompt misst
  die Überschneidung exakt (Toleranz 0,5 mm³).
- Farbbereiche bevorzugt **bündig an der Oberseite oder als ganze Schichtbereiche** anlegen
  (Einlage ≥ 0,6 mm = 3 Schichten, Strichbreite ≥ 0,8 mm). Jede Farbe kostet Spülmaterial je
  Wechsel; wenige Wechselschichten halten Zeit und Abfall klein – das dem Nutzer sagen.
- `spec.yaml`: `filament: {name: weiss, schrift: T1D}` (Schlüssel = Namen aus `bodies`).
- Beispiel: `projects/2026-11-04-beispiel-schild-zweifarbig/`.
- Mehrfarbig wird intern als 3MF (ein Objekt, ein Teil je Körper, Filament je Teil) gesliced; die
  Prüfung bricht ab, wenn der G-Code nicht genau die erwarteten Werkzeuge nutzt.

## build123d – Kurzreferenz (Algebra-API, bewährte Muster)
```python
from build123d import *
from lib.parts import *
set_material("petg")
C = (Align.CENTER, Align.CENTER, Align.MIN)            # Mitte XY, Unterkante auf z = 0
part = Box(60, 40, 20, align=C)
part -= Pos(0, 0, 3) * Box(50, 30, 20, align=C)         # Tasche von oben (Boden 3 mm)
part -= Pos(20, 0, 0) * screw_hole_cut("M3", 3)          # Loch mit Toleranz
part += Pos(0, 25, 0) * Cylinder(5, 10, align=C)
part = Rot(0, 0, 45) * part                              # drehen
part = fillet(part.edges().filter_by(Axis.Z), 2)         # senkrechte Kanten runden
part = bottom_chamfer(part)
bb = part.bounding_box()                                  # bb.size.X / .Y / .Z, bb.min, bb.max
```
- Skizzen: `extrude(Rectangle(20, 10), amount=5)`, `extrude(Circle(4), amount=3)`,
  `extrude(Polygon((0,0),(20,0),(0,10), align=None), amount=5)`, `Text("ABC", font_size=8)`.
- Kanten wählen: `part.edges().filter_by(Axis.Z)`, `.group_by(Axis.Z)[-1]` (oberste Ebene),
  `.filter_by_position(Axis.Z, z0, z1)`; Flächen: `part.faces().sort_by(Axis.Z)[-1]`.
- Hohlkörper: `offset(part, amount=-2, openings=part.faces().sort_by(Axis.Z)[-1])`.
- `fillet`/`chamfer` scheitern bei zu grossen Radien → Radius verkleinern statt weglassen.
- Schrift: `extrude(Text("ABC", font_size=8, align=(Align.CENTER, Align.CENTER)), amount=0.6)`.
- Bei unbekannter API: kurzes Testskript mit `uv run python -c "…"` statt raten.

## Ablage und Git
`projects/<datum>-<name>/`: `spec.yaml`, `model.py`, `out/` (model.step, model.stl, print.stl,
render.png, report.json, *.gcode). STEP ist der Master für spätere Änderungen.
Das Projekt ist ein **öffentliches** Git-Repository für das Werkzeug. **Eigene Teile bleiben lokal:**
`projects/` steht in `.gitignore` (Ausnahmen: `*-beispiel-*`, `*-kalibrierung-*`, `*-wuerfel-5mm`).
Projekte nie mit `git add -f` erzwingen, committen oder pushen. Versionen eines Teils liegen lokal in
`history/` (`version_list`, `version_restore`). Committet werden nur Änderungen am Werkzeug selbst.

## Wenn etwas an slicetheprompt selbst nicht geht
Zuerst `doctor` aufrufen: prüft Drucker, CFS, Slicer und CAD und richtet den Slicer nach einem
frischen `git clone` selbst ein (Creality Print suchen, Hi-Profile auflösen).
Du darfst `slicetheprompt/` reparieren (z. B. Slicer-Aufruf, Profilauswahl in `config/slicer.yaml`).
Danach `uv run python tests/test_meshcheck.py` und `uv run python tests/test_cfs_3mf.py` laufen lassen und den Nutzer informieren, dass der
MCP-Server neu gestartet werden muss (`/mcp` → slicetheprompt → reconnect).
