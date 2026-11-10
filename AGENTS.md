# slicetheprompt – Arbeitsregeln für KI-Agenten (Claude Code, Codex, Antigravity, …)

> Einzige Quelle der Regeln. Claude Code liest sie über `CLAUDE.md` (`@AGENTS.md`) mit ein;
> dort stehen nur Claude-spezifische Ergänzungen.

Du erzeugst aus Prompts des Nutzers exakte, druckfertige Teile und druckst sie nach Freigabe auf
seinem Creality Hi Combo (0,4-mm-Düse, PLA oder PETG). Antworte auf Deutsch, knapp.
Der Nutzer ist kein CAD-Profi: erkläre Masse und Annahmen in Alltagssprache.

## Werkzeuge
- MCP-Server **slicetheprompt** (Claude Code: `.mcp.json`; Codex/Antigravity: `uv run slicetheprompt install codex`
  bzw. `… install antigravity`): `project_new`, `build_and_check`, `check_report`, `render_views`, `cfs_status`,
  `list_filaments`, `filament_plan`, `slice`, `printer_status`, `upload_and_print`, `print_control`,
  `calibration_part`, `apply_calibration`, `get_config`, `project_list`, `get_ui_feedback`, `clear_ui_feedback`,
  `version_list`, `version_restore`, `doctor`, `stencil`, `assembly_new`, `build_assembly`,
  `printer_details`, `print_cost`, `filament_price`.
- Gleiche Funktionen als CLI (falls der MCP-Server hängt oder noch nicht neu geladen ist):
  `uv run slicetheprompt <befehl>`, z. B. `config`, `new`, `run`, `schablone`, `cfs`, `slice`.
- **3D-GUI (`SliceThePrompt`):** `uv run slicetheprompt ui` (`http://127.0.0.1:8765`) zeigt 3D-Modell, Versionen
  und Fehlerzonen. Markiert der Nutzer dort Stellen per Pin/Box/Pfeil, liefert `get_ui_feedback` die exakten
  Modellkoordinaten (`model_xyz`, `delta_model_mm`, `bbox_model`) samt Screenshot `out/ui_feedback.png`;
  nach dem Umbau `clear_ui_feedback` aufrufen.
- Abläufe: `teil`, `drucken`, `kalibrieren`, `status`, `schablone`, `baugruppe` (Claude Code: `.claude/commands/`,
  andere: `.agents/skills/` – Änderungen in beiden nachziehen).
- CAD: **build123d** (Python, OpenCascade). Bausteine mit Toleranzen in `lib/parts.py`, Zahnräder in
  `lib/gears.py`. `get_config` listet unter `bausteine` alle Funktionen mit Kurzbeschreibung –
  die Dateien dafür **nicht** extra lesen.

## Feste Regeln
1. Vor jedem neuen Teil `get_config` aufrufen. Regeln und Toleranzen dort (inkl. Kalibrierung aus
   `config/calibration.yaml`) sind verbindlich; Toleranzen nie schätzen, sondern aus `lib.parts` nehmen
   (`fit`, `hole_d`, `pin_d`, `outer`, `screw_hole_cut`, `fit_hole_cut`, `nut_trap_cut`, …).
   Aussenmasse, die irgendwo hineinpassen müssen: `outer(mass)`.
2. Nur exakte Teile mit build123d. Kein Meshy, keine Netz-Generatoren.
3. Ein Projekt je Teil: `project_new`, dann **spec.yaml** ausfüllen (Zweck, Material, Qualität,
   `bbox` = Soll-Aussenmasse, `masse` mit Herkunft, `annahmen`), dann **model.py**.
   Gegenrad, Halter usw. zum selben Auftrag = eigenes Projekt (`<name>-ritzel`), damit jedes Teil
   einzeln gesliced werden kann. Testdrucke zu einem Teil siehe „Schablone“.
4. model.py: alle Masse als benannte Parameter oben, Einheit mm; Teil in **Drucklage**
   modellieren (z nach oben, grösste ebene Fläche auf z = 0, Überhänge ≤ 45°, Brücken ≤ 10 mm);
   `bottom_chamfer(part)` an der Unterkante; `checks = {Name: (Ist, Soll, Toleranz)}` für jedes
   Funktionsmass (Innenmass, Lochabstand, Lochdurchmesser), Ist-Werte aus der Geometrie messen;
   Mindest-/Höchstmasse als `(Ist, ">=", Min)` bzw. `(Ist, "<=", Max)`.
   - `bottom_chamfer` fast **alle** Kanten auf z = 0, auch Bohrungen. Dort keine eigene Fase und keinen
     Kegel schneiden – das ergibt einen Überhang. Fasen an Bohrungen oben sind in Ordnung.
   - Zahnräder (und andere feine Konturen): `gear_solid(face, h, stufe=0.15)` statt `bottom_chamfer`.
     Scheitert `bottom_chamfer` trotzdem, rückt es die unterste Schicht selbst ein (Warnung, kein Fehler).
   - Mehrere getrennte Teile in einem Druck sind erlaubt: in spec.yaml `koerper: <Anzahl>` setzen,
     keine Verbindungsstege bauen.
   - Bewusst dünner als `min_wall` (Norm-Profil, Gitterstege): Bereich mit `tag(name, körper, role="duenn")`
     markieren. Wünscht der Nutzer eine dünne Wand (z. B. 1,2 mm), in spec.yaml `wand_soll: 1.2`.
5. Fehlt ein kritisches Mass, **einmal** gezielt und gebündelt nachfragen (Merkliste im Ablauf `teil`).
   Unkritisches sinnvoll annehmen und in `annahmen` notieren.
6. Nach jedem Build `build_and_check` und dann das PNG (`out/render.png`) **ansehen**. Was im Bild
   falsch aussieht, ist falsch. Bei Fehlern selbst korrigieren, höchstens 5 Runden, dann den Nutzer
   fragen. `report.zones` fasst gleichartige Fehlerzonen zusammen (Anzahl, Ort der grössten in
   Modellkoordinaten, Fläche, benanntes Feature, Hinweis zur Ursache) – erst wenn das nicht reicht,
   `out/diagnostics_3d.json` lesen. Dünne Stellen sind eingeordnet: echte Wand (Fehler), spitze Kante oder
   Oberflächendetail (Warnung), `role="duenn"` (Hinweis).
   Warnungen bewerten und erwähnen.
7. Dem Nutzer vor dem Slicen zeigen: Aussenmasse, Funktionsmasse mit Toleranz, Material,
   Drucklage, Warnungen. Bild-Pfad nennen.
8. **Drucken nur nach ausdrücklichem „drucken“ des Nutzers in diesem Chat**, und nur mit dem
   Token aus dem gerade gezeigten `slice`-Ergebnis. Vorher `printer_status` prüfen und fragen:
   Bett frei? Nie selbst „drucken“ unterstellen. slicetheprompt prüft beim Start selbst, ob die
   gesliceten Spulen noch im CFS liegen.
9. Nach dem Start `printer_status` zeigen. Bei Problemen `print_control(cancel)` nur auf Wunsch.
10. Material: ohne Angabe PLA. PETG vorschlagen bei Wärme über ca. 55 °C, Aussenbereich,
    Schnapphaken, Biegelast.

## Wiederverwenden statt neu schreiben
- Erst in `bausteine` nachsehen (z. B. `sector`/`sector_face` für Kreis- und Ringsektoren, `slot_cut`,
  `gear_face`, `d_welle_cut`). Hilfsfunktionen nicht von Projekt zu Projekt kopieren: Braucht ein
  Auftrag eine Funktion, die allgemein nützlich ist, dem Nutzer vorschlagen, sie in `lib/` aufzunehmen.
- Testdrucke (Passung, Lochbild, Zahneingriff) nicht von Hand nachbauen, sondern mit `stencil` aus dem
  Originalteil schneiden – dann stimmen die Masse automatisch.

## Filament und CFS (4 Plätze je Einheit, T1A–T1D …)
- `cfs_status` zeigt Platz, Werkzeug (T0 = T1A, T1 = T1B, …), Material, Farbe, Rest-% und das passende
  Creality-Print-Profil (über den RFID-Code). Vor jeder Filamentwahl aufrufen und dem Nutzer zeigen.
  Ist der Drucker offline, ohne Filamentwahl weiterkonstruieren und das sagen.
- Spule wählen in `spec.yaml` → `filament`: Platz (`T1C`), Farbname (`schwarz`) oder Hex (`#FF0000`);
  leer = Spule des Materials mit der grössten Restmenge. Exaktes Profil optional über
  `filament_profile` (Namen aus `list_filaments`). Farbwünsche, die keine geladene Spule erfüllt,
  ehrlich melden (nächstliegende Farbe nennen) statt still zu ersetzen.
- Vor dem Slicen `filament_plan` aufrufen; nach dem Slicen die Zuordnung Körper → Spule → Farbe →
  Profil, Gramm je Spule und Hinweise (fast leer, Materialmix) zeigen.

## Kosten (Ablauf in `drucken`)
- `slice` liefert `kosten` (CHF): Filament je Spule + Strom + Verschleiss (+ optional Fehldruck-Zuschlag);
  `print_cost(project)` rechnet es für den letzten Slice neu. Annahmen (Strompreis, Leistung, Verschleiss,
  Standardpreis je Material) stehen in `config/printer.yaml` → `kosten`.
- Rollenpreise lokal in `config/preise.yaml` (nicht im Git), gepflegt mit `filament_price(action="set", name=…,
  preis=… | url=…, material/code/farbe/slot=…)` oder im Studio (Tab „Kosten“). Der genaueste passende Eintrag gilt.
- `url` nur Digitec/Galaxus. Deren Bot-Schutz blockt Abrufe meist (HTTP 403): dann den Preis manuell setzen
  (Nutzer fragen oder selbst im Browser nachsehen) – den Schutz **nie** umgehen.
- `printer_details` liefert Zusatzsensoren, Lüfter, Filamentsensoren, Druckstatistik, Kamera-URL, Firmware.

## Schablone (Passungs-Testdruck, Ablauf `schablone`)
- `stencil(project, z=[…], height=2)` (CLI: `uv run slicetheprompt schablone <projekt> --z 0 12 --hoehe 2`)
  schneidet dünne Scheiben ab den Höhen `z` aus dem Teil, legt sie nebeneinander aufs Bett und
  baut/prüft/rendert sie als Projekt `<name>-schablone` (erneuter Aufruf aktualisiert es). XY-Masse,
  Löcher und Toleranzen bleiben exakt wie im Original; das Original wird bei jedem Build frisch aus
  seinem `model.py` erzeugt. Mehrfarbige Teile werden einfarbig vereint.
- `z` dort wählen, wo die Passungen liegen (Hinweise in `features_z`); Tasche über einem Boden →
  oberhalb des Bodens schneiden. Danach normal slicen/drucken (Regel 8 gilt unverändert).
- Nach jedem Teil mit Passungen (Lager, Bohrung für Welle, Lochbild, Zahneingriff) von sich aus eine
  Schablone anbieten.

## Baugruppe (mehrere Teile zusammen, Ablauf `baugruppe`)
- Eigenes Projekt mit `assembly.py` statt `model.py` (`assembly_new(name, parts)`, CLI
  `uv run slicetheprompt baugruppe-neu <name> <teil…>`). Wird nicht gedruckt, nur angesehen und geprüft.
- `TEILE = {Name: Teil(projekt, anzahl=…, farbe=…, explosion=(x, y, z))}`, `POSEN = {Name: {Werte}}`,
  `platzierung(p)` → je Teil `Tf` (bei `anzahl > 1` Liste) **von der Drucklage** (`out/model.step`) ins
  Baugruppen-System: `pos`, `rot`, `spiegel("XZ")`, verkettet mit `*`. Die Umkehrung der letzten Zeilen
  von `model.py` (z. B. `part = mirror(Pos(0,0,H) * k)` → `pos(0,0,-H) * spiegel("XZ")`) einmal als
  Konstante notieren. Masse der Teile mit `parameter(projekt)` lesen, nie abschreiben.
- `geister(p)` → `{Name: Geist(form, lage=Tf, farbe=…, folgt="Teil")}` für Bezugskörper (Handy, Okular,
  Schrauben). `KONTAKT = {("Teil", "Geist"): 0.3}` erlaubt Eindringen bis 0,3 mm (Klemmkontakt),
  sonst gilt jede Überschneidung über `GRENZE_MM3` (0,5) als Fehler. `IGNORIEREN` für bewusste Paare.
- `build_assembly(project)` nach jedem Umbau eines Teils (erst das Teil mit `build_and_check`). Danach
  `out/render.png` ansehen und je Pose melden: frei / Klemmkontakt (Tiefe) / Kollision (Paar, mm³).
  Bei Kollision Teile nicht stillschweigend ändern, sondern Ursache und Vorschlag nennen.
- **Gelenke** (`GELENKE = [...]`) statt fester Stellungen, wenn sich etwas bewegt: `Fest(a)` (Boden),
  `Fest(a, b)`, `Drehung`, `Schiebung`, `Zylinder`, `Kugel`, `Ebene`, `Schraube(…, steigung=0.7)`,
  `Getriebe(gelenk_a, gelenk_b, verhaeltnis)`, je mit `grenzen=(min, max)`. Achsen und Punkte in
  Baugruppen-Koordinaten der Grundstellung (= erste Pose). Keine Formeln für Mechanismen schreiben –
  der Löser ergibt Kurbeltrieb, Viergelenk usw. selbst. Posen dürfen Gelenkwerte sein:
  `{"gelenke": {"Kurbel": 90}}`. Hinweis „Pose … verletzt Gelenk …“ heisst: platzierung() und
  GELENKE passen nicht zusammen → Achse/Punkt prüfen.
- Studio zeigt Baugruppen mit Posen-Auswahl, Explosion, Ein/Aus je Teil; mit Gelenken zusätzlich
  **✥ Bewegen** (Auswahl folgt ihren Gelenken, Rest fest, Teile auf einem gewählten Träger fahren mit,
  Anschlag bei Kontakt bzw. bei der KONTAKT-Tiefe, „Exakt prüfen“).
  `get_ui_feedback` enthält die Pose (`pose`) und bei bewegten Teilen `gelenkwerte`; Koordinaten sind
  Baugruppen-Koordinaten.

## Fast-Path für 3D-UI-Feedback (Ziel: 3 Runden, keine Suchschleifen)
Wenn der Nutzer 3D-Feedback aus `SliceThePrompt` umsetzen lässt (`get_ui_feedback`), gilt dieser strikte 3-Runden-Ablauf:
1. **Runde 1 – Kontext + Bild in EINEM parallelen Schritt:**
   Rufe `get_ui_feedback` und `read_file("projects/<projekt>/out/ui_feedback.png")` **gleichzeitig im selben Schritt** auf (Schema-Datei vorher NICHT lesen!):
   - `call_mcp_tool`: `ServerName="slicetheprompt"`, `ToolName="get_ui_feedback"`, `Arguments={"project": "<projekt>"}`
   - `get_ui_feedback` liefert `model_py_numbered` (kompletter Code mit Zeilennummern), `params`, `spec`, `used_in_assemblies` sowie bei Baugruppen `target_project` und `part_print_xyz` (fertig in Einzelteil-Drucklage!) direkt mit.
   - **Verboten:** `model.py` oder `spec.yaml` nochmal extra mit `read_file` öffnen, `project_list` aufrufen oder mit `git log`/`git grep`/`find` im Repo suchen.
2. **Runde 2 – Änderung + Auto-Build:**
   - **Fall A (reine Parameter-/Maßänderung):** Kein `edit_file` nötig! Direkt in einem Aufruf ändern, Teil + alle zugehörigen Baugruppen bauen und Feedback löschen:
     `call_mcp_tool`: `ServerName="slicetheprompt"`, `ToolName="build_and_check"`, `Arguments={"project": "<teil>", "params": {"PARAM_NAME": 16.0}, "spec": {"bbox": [x, y, z]}}`
   - **Fall B (Geometrie-/Code-Änderung):** `edit_file` auf `model.py` (Dateien sind LF-normiert; kurze Zielblöcke nutzen; Prüf-Geometrie in `checks` immer aus Parametern ableiten, nie Zahlen hartcodieren!) und `build_and_check(project="<teil>")` aufrufen (`also_assembly="auto"` und `clear_feedback=True` sind Standard!).
3. **Runde 3 – Sichtkontrolle & Kurzbericht:**
   `out/render.png` des Teils (und bei Kollisionen/Lageänderungen der Baugruppe) mit `read_file` ansehen und Ergebnis knapp melden.
- **Kein Task-Polling:** Bei `run_command` für Tests `WaitMsBeforeAsync: 10000` nutzen. Läuft ein Befehl im Hintergrund, Turn beenden (System weckt automatisch auf) – nie `manage_task(status)` in einer Schleife aufrufen.

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
part = fillet(part.edges().filter_by(Axis.Z), 2)         # senkrechte Kanten runden (vor dem Anbauen)
part = cut_feature(part, "loch_1", Pos(20, 0, 0) * screw_hole_cut("M3", 3))   # benannter Schnitt
part += Pos(0, 22, 0) * Cylinder(5, 10, align=C)         # Anbau überlappt (nur Berühren = 2 Körper)
part = Rot(0, 0, 45) * part                              # drehen
part = bottom_chamfer(part)
bb = part.bounding_box()                                  # bb.size.X / .Y / .Z, bb.min, bb.max
```
- Skizzen sind 2D-Flächen und lassen sich addieren/abziehen: `extrude(Circle(30) + Rectangle(20, 80), amount=4)`,
  `extrude(Polygon((0,0),(20,0),(0,10), align=None), amount=5)`, `sector_face(r, a0, a1, r_in)`.
- Sektor als Körper: `sector(30, 150, 250, 4, r_in=20)` = Ringsektor 150°…250° (ab +X, gegen den Uhrzeiger), 4 mm hoch.
- Schrift: `extrude(Text("ABC", font_size=8, align=(Align.CENTER, Align.CENTER)), amount=0.6)`.
- Kanten wählen: `part.edges().filter_by(Axis.Z)`, `.group_by(Axis.Z)[-1]` (oberste Ebene),
  `.filter_by_position(Axis.Z, z0, z1)`; Flächen: `part.faces().sort_by(Axis.Z)[-1]`.
- Hohlkörper: `offset(part, amount=-2, openings=part.faces().sort_by(Axis.Z)[-1])`.
- `fillet`/`chamfer` scheitern bei zu grossen Radien → Radius verkleinern statt weglassen.
- Fallen: `Polygon`-Punkte gegen den Uhrzeiger angeben (sonst zeigt die Fläche nach −Z und `extrude` geht nach
  unten); `shape.is_valid` ist eine Eigenschaft; `Vector` hat `.X/.Y/.Z` und `tuple(v)`, kein `to_tuple()`;
  `a & b - c` heisst `a & (b - c)` → klammern; `face.center()` ist der Flächenschwerpunkt, nicht die Lochachse;
  Testskripte mit Umlauten/„×“ in der Windows-Konsole: `PYTHONUTF8=1` setzen.
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
Danach `uv run python tests/test_meshcheck.py`, `uv run python tests/test_cfs_3mf.py` und (bei Änderungen
an `lib/`) `uv run python tests/test_lib.py`, bei Baugruppen/Gelenken `tests/test_assembly.py` und
`tests/test_kinematics.py` ausführen.
