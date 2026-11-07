# Testparcours – Auswertung (Lauf 1, 7.11.2026)

16 Aufträge, 19 Projekte (A1, C2 und C4 als Teil + Gegenstück), 4 Strecken parallel.
Quelle: `projects/*-tp-*/protokoll.md` (lokal).

## Ergebnis in Zahlen

| | |
| --- | --- |
| bestanden | 17 von 19 Projekten. Nicht bestanden: C3 Riemenscheibe, D2 Lineal – beide nur an der Mindestwand-Prüfung, geometrisch nicht lösbar |
| Build-Läufe | ≈ 37, davon ≈ 18 fehlgeschlagen |
| Ursachen der Fehlläufe | 7 × Wandprüfung zu grob (C1, C3, D2) · 5 × eigene Messung in `checks` falsch (C2, C3, D3) · 3 × Gewinde-Netz (A1) · 3 × Sonstiges |
| Testskripte | ≈ 40 – meist zum Messen, für API-Fragen und Passungstests |
| MCP-Timeouts | 3 (B1, D2) – Prüfung dauerte 5–6 min, gemeinsamer Server |

**Zwei Drittel der Fehlläufe kommen von zwei Lücken: fehlende Mess-Helfer und eine Wandprüfung, die nicht
zwischen tragender Wand, Kante und Oberflächendetail unterscheidet.** Beides betrifft alle Bereiche, nicht
eine Teilart.

## Zuordnung – nur 2 neue Regeln

Etwa 70 Reibungspunkte wurden gemeldet. Nach Gruppierung:

| Gruppe | Strecken | Art | Massnahme |
| --- | --- | --- | --- |
| **G1 Messen** – Spalt, Loch-Ø, Lochabstand, Aussen-Ø, Radius aus der Geometrie (`face.center()`/`Face.radius` sind Fallen) | A B C D (11 Teile) | baustein | Modul `lib/measure.py`: `gaps_along`, `find_holes`, `section_size(part, z, near)`; Docstrings sagen, wofür |
| **G2 `checks` nur `Ist = Soll ± Tol`** – Mindestmasse („Rand ≥ 3“) nur per `min()`-Trick | D (+ B3) | pruefung | `checks` erlauben `(ist, ">=", min)` und `(ist, "<=", max)` |
| **G3 Wandprüfung zu grob** – spitze Kante gilt als 0,07-mm-Wand; Gravurstege, Norm-Zahnprofile, Gitterstege, bestellte 1,2-mm-Wand | A B C D | pruefung | Kanten mit Winkel < 90° als „spitze Kante – stumpf machen“; dünne Stellen ≤ 3 Schichten auf Vollmaterial = Warnung „Oberflächendetail“; `tag(…, role="duenn")` bzw. spec `wand_soll` → Info statt Fehler |
| **G4 Zonen** – Kürzung nach Reihenfolge, keine Gruppierung, falsches Feature, `manifold` ohne Zone (**Fehler aus PR #4**), Hinweis passt nicht zu Löchern | A B C D | pruefung | je Art die grössten zeigen, Gleichartiges zusammenfassen („20 × Wand 0,74 bei r 5,9“), enthaltendes Feature statt nächstes, Hinweise je Ursache (Lochdecke → Tropfen, spitze Kante → Fase) |
| **G5 Prüfung langsam / MCP** – 5–6 min bei 77–110 k Dreiecken, gemeinsamer Server der Desktop-App, veralteter Code unbemerkt | A B C D | pruefung + infra | Prüfung und Render im Unterprozess; Zonensuche begrenzen; Render vor der langen Prüfung; `get_config` zeigt Code-Stand und warnt bei veraltetem Server |
| **G6 Render taugt nicht zur Sichtkontrolle** – flache/lange Teile, Innenliegendes, Unterseite fehlt | A B C D | pruefung | auf das Teil zoomen, Ansicht von unten, Schnitt durch benannte Features, Kanten dunkler |
| **G7 Waagrechte Löcher → Überhang** | A B C | baustein | `teardrop_cut(d, laenge, achse)`, `countersink_cut(…, axis=…)`, `made_cut(tropfen=True)` |
| **G8 Projekte, die zusammengehören** – Regel 3 vs. `koerper` widersprüchlich; Parameter teilen per `runpy`; Variante vs. Änderung unklar | A C D | **regel** + baustein | Regel R2 unten; `load_project(name)` liefert Parameter + Körper (räumt FEATURES auf); spec `basis:` |
| **G9 Drucklage nach Funktion** – „grösste Fläche unten“ bricht Haken; Fallen/Senkungen müssen nach oben öffnen | B C (+ A) | **regel** | Regel R1 unten |
| **G10 Zusammenbau prüfen / anordnen** – Überschneidung, Spiel, Eingriff, Druck-Layout | A C | baustein | `interference(a, b)`, `arrange(bodies, gap)` |
| **G11 `bottom_chamfer` zu pauschal** – frisst dünne Ringwände und Gitterstege (vorher: Zahnräder) | B (+ Lauf vom 7.11.) | baustein | Fase automatisch auf ≤ ¼ der Wand an z = 0 begrenzen → **die Zahnrad-Regel in `AGENTS.md` entfällt** |
| **G12 Toleranzen fehlen** – Print-in-place, Gewinde, Lagersitz | A C | config + kalibrieren | `pip`-Spiel in `printer.yaml`, Kalibrierteil um Lagersitz/Gelenk erweitern |
| **G13 Grenzwerte widersprüchlich** – `min_inlay` 0,4 vs. `text_min_*` 0,6 (unbenutzt) vs. AGENTS 0,6/0,8 | D | pruefung | eine Zahl je Grösse, Strichbreite und Tiefe getrennt prüfen |
| **G14 build123d-Fallen** – Polygon im Uhrzeigersinn extrudiert nach −Z (3 ×), `Vector.to_tuple`, `a & b - c`, `face.center()` | A B C | prüfung + Kurzreferenz | Prüfung: „Teil ragt unter z = 0 / Körper übereinander → Polygon-Umlaufsinn?“; Kurzreferenz-Block „Fallen“ |
| **G15 Umgebung** – cp1252-Konsole bricht Testskripte (3 ×), lange JSON-Ausgabe | A C D | ablauf | `PYTHONUTF8=1` für Bash in `.claude/settings.json`; `check --kurz` |
| **G16 Fach-Bausteine** (je 1 Strecke) | – | baustein | siehe Liste unten, nach Nutzen priorisieren – **keine Regeln** |

### Neue allgemeine Regeln (nur diese zwei)
- **R1 Drucklage nach Funktion:** Last (Biegung, Zug) in der Schichtebene; Taschen, Fallen und Senkungen öffnen
  nach oben; waagrechte Löcher als Tropfen. Erst danach „grösste ebene Fläche unten“.
- **R2 Projekte abgrenzen:** Was zusammen gedruckt wird, ist ein Projekt (`koerper: n`). Ein Gegenstück mit eigenem
  Material, eigener Menge oder eigenem Druckzeitpunkt ist ein eigenes Projekt mit `basis: <projekt>` und holt die
  Masse über `load_project`. Eine Änderung ist eine neue Version im selben Projekt, eine Variante ein neues
  Projekt mit `basis:`.

### Regeln, die wegfallen oder schrumpfen
- „Zahnräder: `gear_solid(stufe)` statt `bottom_chamfer`“ → entfällt mit G11.
- „`bottom_chamfer` fast alle Kanten … keine eigene Fase“ → bleibt als ein Satz; der Rest steckt im Baustein.
- „Mehrere getrennte Teile: `koerper`“ → geht in R2 auf.

### Fach-Bausteine (G16), Vorschlag zur Reihenfolge
1. Häufig und allgemein: `teardrop_cut` (G7), Schrauben-Tabellen bis M12 + Fehlermeldung mit bekannten Grössen,
   `bearing("608")` / `bearing_seat_cut`, `d_welle_cut`/`made_cut` nach `lib.parts`.
2. Mittel: `thread_pair` (Gewinde, netzsicher, ganze Gänge), `inlay_text`/`text_fit`, `hex_grid_cut`, `profile_round`.
3. Selten: `rack_face`, `belt_pulley`, `pip_hinge`, `dovetail_pair`, `snap_clip_face`, `enclosure`/`standoff`,
   `scale_cut`, `grip_flutes`.

## Was gut lief
- `report.zones` mit Feature-Namen half sofort (A2 `senkloch_m4`, D2 `striche`).
- `version_list` mit `params` ist für Änderungen sehr nützlich (D4).
- `koerper: n`, `offset(kind=INTERSECTION)` für Spiel, `bodies`-Überschneidungsprüfung, `countersink_cut`, `cut_feature`.
- Die Merkliste für Rückfragen griff; Ergänzungen: Stecktiefe/Abflachung/Ausrichtung (Welle), Lochbild +
  Buchsen-Typ/Höhe (Gehäuse), Schieberichtung (Steckverbindung).

## Vorgeschlagene Reihenfolge der Umsetzung
1. **Infrastruktur (G5):** Prüfung/Render im Unterprozess, Code-Stand in `get_config`, Zonensuche begrenzen – sonst ist
   kein weiterer Parcours parallel sinnvoll.
2. **Prüfung (G3, G4, G2, G14, G13):** behebt die meisten Fehlläufe, ohne neue Regeln.
3. **Messen (G1) und Tropfen (G7)** in `lib/`.
4. **Regeln R1, R2** und Streichungen in `AGENTS.md`; `bottom_chamfer` adaptiv (G11).
5. **Render (G6)**, Umgebung (G15), dann Fach-Bausteine (G16) nach Liste.
6. Parcours erneut fahren und Fehlläufe/Testskripte vergleichen.
