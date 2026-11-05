# AGENT.md – Technischer Implementierungsplan: `SliceThePrompt` (3D-Inspektions-, Markierungs- & Versionierungs-GUI)

> **Zweck dieser Datei:** Dies ist die verbindliche, ausführbare Spezifikation für nachgelagerte Coding-Agents (in Antigravity Desktop, `/teamwork-preview`, `/boost` oder Claude Code), um **`SliceThePrompt`** vollständig (Stufe 1 + 2 + 3) zu implementieren.
> - **Laufende CAD-/Druckregeln für Nutzeraufträge** stehen weiterhin in [`AGENTS.md`](./AGENTS.md) und [`CLAUDE.md`](./CLAUDE.md).
> - **Freigegebene Entscheidungen des Nutzers:**
>   1. **Bereitstellung:** Lokale Web-App über `uv run slicetheprompt ui` (`http://127.0.0.1:8765`), kompatibel mit Antigravity Desktop **und** Claude Code.
>   2. **Umfang:** Vollständiger Ausbau (**alle Stufen**: 3D-Viewer, Versions-Snapshots + Geisterbild-Diff, automatische 3D-Fehler-Overlays & Feature-Tagging, interaktive 3D-Bereichsauswahl mit MCP-Rückkanal, AST-Parameter-Editor, 2-Punkt-Messwerkzeug und Moonraker-/CFS-Statusleiste).

---

## 1. Architektur-Überblick & Grundprinzipien

1. **Keine eigene KI-/API-Schicht im GUI:** Der Dialog bleibt in Antigravity Desktop bzw. Claude Code. Das GUI ist das **visuelle 3D-Auge und Zeigewerkzeug**, das über Projektdateien und den bestehenden MCP-Server ([`slicetheprompt/mcp_server.py`](./slicetheprompt/mcp_server.py)) mit dem Agenten gekoppelt ist.
2. **Null fragile Zusatz-Abhängigkeiten im Backend:** Der lokale UI-Server (`slicetheprompt/ui_server.py`) nutzt Pythons Standardbibliothek (`http.server.ThreadingHTTPServer`, `urllib.parse`, Server-Sent Events `text/event-stream`), damit `uv run slicetheprompt ui` sofort im bestehenden `.venv` startet, ohne neue C-Pakete kompilieren zu müssen.
3. **Echte 3D-Darstellung im Browser:** Statisches Frontend unter `slicetheprompt/ui/` auf Basis von **Three.js** (`STLLoader`, `OrbitControls`, `TransformControls`/Raycasting, Clipping Planes). Three.js-Module (`three.module.min.js`, `OrbitControls.js`, `STLLoader.js`) werden beim ersten Start oder direkt lokal unter `slicetheprompt/ui/vendor/` abgelegt bzw. per Importmap mit lokalem Fallback geladen, sodass das GUI auch offline zuverlässig funktioniert.
4. **Strikte Sicherheit:** Das GUI darf **niemals** einen Druck starten (`print_gcode` / `upload_and_print` wird im UI-Server **nicht** exponiert). Drucker- und CFS-Abfragen im GUI sind rein **lesend** (`Moonraker.status()`, `Moonraker.cfs()`).

### Koordinaten-Vertrag (`model` ↔ `print`)

In [`slicetheprompt/pipeline.py`](./slicetheprompt/pipeline.py) erzeugt `prepare()` aus `out/model.stl` (Modellkoordinaten aus `model.py`) die Drucklage `out/print.stl` auf dem `260 × 260 mm`-Bett mittels:
- Rotationsmatrix $R \in \mathbb{R}^{3 \times 3}$ aus `meshcheck.ORIENTATIONS[orientation]` (`p_rot = p_model @ R.T`)
- Verschiebungsvektor $s = [s_x, s_y, s_z]$ aus `place_on_bed` (`p_print = p_rot + s`)

**Pflicht-Erweiterung in `out/print.json`:**
`prepare()` speichert zusätzlich `"rotation_matrix": R.tolist()` und `"shift": [float(v) for v in shift]`.
Damit gilt für jeden im 3D-Viewer auf `print.stl` angeklickten Punkt $p_{\text{print}}$ exakt:
$$p_{\text{model}} = (p_{\text{print}} - s) \cdot R$$
*(da $(p_{\text{model}} R^T)^T = R p_{\text{model}}$ und $R^{-1} = R^T$).*
Jede Markierung im GUI speichert **immer beide Koordinatensysteme** (`model_xyz` für direkte Verwendung in `model.py` und `print_xyz` für die Druckbettansicht)!

---

## 2. Arbeitspakete & Modulplan (für Einzel- oder Parallel-Agents)

Die Umsetzung ist in **5 sauber getrennte Arbeitspakete (WP-1 bis WP-5)** gegliedert:

| Paket | Neue / Geänderte Dateien | Abhängigkeit | Kurzbeschreibung |
| --- | --- | --- | --- |
| **WP-1** | `slicetheprompt/history.py` *(neu)*, `slicetheprompt/params.py` *(neu)*, `slicetheprompt/pipeline.py`, `.gitignore` | keine | Automatische Versions-Snapshots (`v001`…), Rollback, AST-Parameter-Extraktion & -Update |
| **WP-2** | `lib/parts.py`, `slicetheprompt/_cad_runner.py`, `slicetheprompt/meshcheck.py`, `slicetheprompt/pipeline.py` | keine | 3D-Fehler-Diagnostik (`diagnostics_3d.json`), Feature-Tagging (`tag`, `sub_tag`) & Erkennung „leerer Schnitte“ (`ghost_*.stl`) |
| **WP-3** | `slicetheprompt/ui_server.py` *(neu)*, `slicetheprompt/cli.py` | WP-1, WP-2 | Lokaler HTTP/SSE-Server (`uv run slicetheprompt ui`), REST-API für Projekte, Versionen, Parameter, Feedback & Moonraker-Readonly |
| **WP-4** | `slicetheprompt/ui/index.html`, `slicetheprompt/ui/app.js`, `slicetheprompt/ui/styles.css`, `slicetheprompt/ui/vendor/*` *(alle neu)* | WP-3 | Interaktives Three.js-Studio: 3D-Viewer, Geisterbild-Diff, Fehler-Overlays, Pin/Box/Vektor-Markierung + Screenshot, Messband, Parameter-Panel |
| **WP-5** | `slicetheprompt/mcp_server.py`, `.agents/skills/teil/SKILL.md`, `.claude/commands/teil.md`, `AGENTS.md`, `CLAUDE.md`, `tests/test_ui_and_history.py` *(neu)* | WP-1..4 | MCP-Tools (`get_ui_feedback`, `clear_ui_feedback`, `version_list`, `version_restore`), Agent-Workflow-Integration & vollständige Test-Suite |

> **Hinweis bei Änderungen an `pipeline.py`:** WP-1 und WP-2 berühren unterschiedliche Stellen in `pipeline.py` (`prepare`/`check` vs. `run_all`-Snapshot-Hook). Bei paralleler Bearbeitung erst WP-2 in `prepare`/`check` integrieren, danach den Snapshot-Aufruf am Ende von `run_all` einhängen.

---

## 3. Detaillierte Spezifikation je Arbeitspaket

### WP-1: Versionshistorie & AST-Parameter-Editor

#### 1.1 `slicetheprompt/history.py` *(neu)*
Legt unter `projects/<projekt>/history/v001/`, `v002/`, … unveränderliche Snapshots jedes erfolgreichen Laufs an.

- **Ordnerinhalt je Version `vNNN` (3-stellig, ab `v001`):**
  - `meta.json`:
    ```json
    {
      "version": "v002",
      "number": 2,
      "timestamp": "2026-11-04T11:15:00",
      "reason": "build_and_check",
      "passed": true,
      "size_mm": [60.0, 25.0, 4.0],
      "volume_mm3": 5820.4,
      "errors_count": 0,
      "warnings_count": 1,
      "bodies": ["platte", "schrift"],
      "params": {"L": 60.0, "B": 25.0, "H": 4.0}
    }
    ```
  - Kopien von: `model.py`, `spec.yaml`, `out/model.stl`, `out/print.stl`, alle `out/print_*.stl`, `out/print.json`, `out/report.json`, `out/diagnostics_3d.json` (falls vorhanden), `out/render.png`.
- **Funktionen in `slicetheprompt/history.py`:**
  - `save_snapshot(project: str, reason: str = "build_and_check") -> dict`: Prüft per SHA-256 von `model.py` + `spec.yaml` + `out/print.stl`, ob der Stand identisch zur letzten Version ist; falls identisch, keinen doppelten Snapshot anlegen, sondern die letzte `meta.json` zurückgeben. Sonst nächsten `vNNN`-Ordner schreiben. Maximal 50 Versionen je Projekt behalten (älteste bei Bedarf aufräumen oder konfigurierbar lassen).
  - `list_versions(project: str) -> list[dict]`: Liest alle `history/v*/meta.json` chronologisch sortiert.
  - `restore_version(project: str, version: str) -> dict`: Sichert – falls der aktuelle `model.py`-Stand noch keinen Snapshot hat – diesen zuerst als Snapshot (`reason="auto_backup_before_restore"`), kopiert dann `model.py` und `spec.yaml` aus `history/<version>/` ins Projektverzeichnis und führt `pipeline.run_all(project)` aus (legt dabei regulär die wiederhergestellte Fassung als aktuellen Arbeitsstand an).
  - `diff_code(project: str, v_old: str, v_new: str) -> str`: Liefert `unified_diff` zwischen `v_old/model.py` und `v_new/model.py`.
- **`.gitignore` ergänzen:** Zeile `projects/*/history/` hinzufügen (Snapshots enthalten STLs/PNGs und gehören wie `out/` nicht ins Git-Repo).
- **Hook in `slicetheprompt/pipeline.py`:** Am Ende von `run_all(project, orientation)` automatisch `save_snapshot(project, reason="build_and_check")` aufrufen und `"version": snap["version"]` im Rückgabe-Dict ergänzen.

#### 1.2 `slicetheprompt/params.py` *(neu)*
Liest und aktualisiert numerische Top-Level-Parameter am Anfang von `model.py` per Python-`ast` (ohne Regex-Gebastel!).

- **Unterstützte Muster in `model.py`:**
  1. Einzelzuweisung: `L = 60.0` oder `LOCH_X: float = -15`
  2. Tupel-Entpackung von Zahlen: `L, B, H = 60.0, 25.0, 4.0`
  3. Negative Zahlen (`ast.UnaryOp(op=ast.USub, operand=ast.Constant(...))`) müssen sauber als `-15.0` erkannt und zurückgeschrieben werden!
  4. Inline-Kommentar in derselben Zeile (`SCHRIFT_H = 10.0  # Schriftgrösse`) als `"comment": "Schriftgrösse"` extrahieren, damit das GUI ihn als Tooltip/Label anzeigt.
- **Funktionen:**
  - `extract_params(model_path: Path) -> list[dict]`: Gibt `[{"name": "L", "value": 60.0, "is_int": False, "line": 13, "comment": ""}]` zurück. Ignoriert Variablen ab `part = ...`, `bodies = ...` oder `checks = ...`.
  - `update_params(model_path: Path, updates: dict[str, float | int]) -> list[dict]`: Ersetzt gezielt nur die geänderten Zahlenliterale anhand der AST-Spaltenpositionen (`lineno`, `col_offset`, `end_lineno`, `end_col_offset`), sodass alle Kommentare, Formatierungen und übrige Codezeilen zu 100 % erhalten bleiben!

---

### WP-2: 3D-Fehler-Diagnostik & Semantisches Feature-Tagging

Dieses Paket löst das Kernproblem: *„Wo genau am Teil liegt ein geometrischer oder räumlicher Fehler?“*

#### 2.1 Semantisches Feature-Tagging in `lib/parts.py` und `slicetheprompt/_cad_runner.py`
In `lib/parts.py` eine globale Liste `FEATURES: list[dict] = []` einführen (analog zu `WARNINGS`):

- **`tag(name: str, shape, role: str = "feature")`**:
  Berechnet `bb = shape.bounding_box()` und Volumen, hängt einen Eintrag an `FEATURES` an und gibt `shape` unverändert zurück (lässt sich also flüssig inline schreiben: `wand = tag("mittelwand", Pos(0, 0, 4) * Box(4, B, 20, align=C))`).
- **`cut_feature(part, name: str, cutter)`** *(Kurzalias `sub_tag`)*:
  Misst `vol_before = float(part.volume)`, führt `res = part - cutter` aus, misst `removed = vol_before - float(res.volume)`.
  - Speichert in `FEATURES`: `{"name": name, "role": "cut", "bbox_min": [...], "bbox_max": [...], "center": [...], "cutter_volume_mm3": ..., "removed_volume_mm3": round(removed, 4), "_shape": cutter (nur im Speicher)}`.
  - **Automatische Fehlererkennung „Leerer / verfehlter Schnitt“ (`empty_cut`):**
    Wenn `removed < 0.01` (das Loch / die Tasche hat den Körper gar nicht oder kaum getroffen, weil die Koordinaten im Raum danebenliegen!):
    - Fügt automatisch einen Hinweis zu `WARNINGS` hinzu: `f"Schnitt '{name}' hat kein Material abgetragen (liegt ausserhalb des Körpers!)"`.
    - Markiert das Feature mit `"empty_cut": True`.
  - **Automatische Teil-Abtrag-Warnung (`partial_cut`, Info):** Optional melden, falls `removed < 0.15 * cutter_volume` (z. B. Bohrung schneidet nur den äußersten Plattenrand an).
- **Auswertung in `slicetheprompt/_cad_runner.py`:**
  - Liest `getattr(lp, "FEATURES", [])` aus.
  - Exportiert für jedes Feature mit `empty_cut == True` dessen `_shape` als eigenes Hilfsnetz `out/ghost_<slug>.stl` (damit das GUI ein neben der Platte im Nichts schwebendes Bohrloch **rot-transparent im Raum** anzeigen kann!).
  - Schreibt die serialisierbaren Metadaten (ohne `_shape`) als `"features": [...]` in `out/build.json` und fügt bei `empty_cut == True` einen fehlgeschlagenen Check (`ok: False`) in `result["checks"]` ein, damit `build_and_check` den Agenten sofort zwingt, die falsche Lochposition zu korrigieren!

#### 2.2 Dreiecksgenaue 3D-Problemzonen in `slicetheprompt/meshcheck.py` & `out/diagnostics_3d.json`
In `slicetheprompt/meshcheck.py` eine neue Funktion `diagnostics_3d(print_tris: np.ndarray, cfg: dict, build_data: dict | None = None, print_info: dict | None = None) -> dict` ergänzen, die während `pipeline.check(project)` ausgeführt wird und `out/diagnostics_3d.json` (in **Druckbettkoordinaten** + mit Transformationsdaten zu Modellkoordinaten) schreibt:

1. **`thin_walls`**:
   - Nutzt die bereits in `wall_thickness()` berechneten Strahl-Distanzen je Dreieck.
   - Sammelt alle Dreiecks-Indizes mit lokaler Wandstärke `< rules["min_wall"]` (`0.84 mm`), gruppiert benachbarte Dreiecke über `face_adjacency_components` (ab Fläche `≥ 0.5 mm²`) und speichert je Problemstelle:
     `{"id": "thin_1", "kind": "thin_wall", "severity": "error", "label": "Wand zu dünn (0.62 mm < 0.84 mm)", "bbox_print": [xmin,ymin,zmin,xmax,ymax,zmax], "bbox_model": [...], "center_print": [...], "center_model": [...], "tri_indices": [...]}`.
2. **`overhangs` & `bad_bridges`**:
   - Nutzt `overhang_stats()`-Logik: Dreiecksgruppen mit `n_z < -sin(max_overhang_deg + 1°)` (abzüglich Bodenauflage, auf anderen Körpern gestützter Flächen und zulässiger Brücken `≤ max_bridge`).
   - Zu lange Brücken (`span_mm > max_bridge`) als `"kind": "bad_bridge"`, sonstige ungestützte Überhänge als `"kind": "overhang"` (`severity: "warn"`), jeweils mit `tri_indices`, `bbox_print`, `bbox_model` und `center_print`.
3. **`open_edges`**:
   - Falls `open_edges > 0` oder `nonmanifold_edges > 0`: bis zu 500 betroffene Kanten als 3D-Strecken `[[x1,y1,z1], [x2,y2,z2]]` exportieren (`"kind": "open_edge"`, `"severity": "error"`).
4. **`floating_parts`**:
   - Falls `components(faces)` mehrere unverbundene Teilnetze findet und eines davon `z_min > z_bed + 0.05` hat („schwebendes Teil im Raum!“): als `"kind": "floating_part"`, `"severity": "error"` mit `tri_indices` und `bbox_print` ausgeben!
5. **`features` (transformiert auf `print` + original `model`)**:
   - Übernimmt `build_data.get("features", [])`, transformiert deren Bounding-Boxen mittels `R` und `shift` aus `print.json` zusätzlich nach `bbox_print` und verweist bei `empty_cut` auf `ghost_<slug>_print.stl` (in `pipeline.prepare` werden etwaige `ghost_*.stl` genau wie `body_*.stl` mit `R` + `shift` zu `ghost_*_print.stl` gedreht!).

---

### WP-3: Lokaler Backend-Server (`slicetheprompt/ui_server.py` & CLI)

#### 3.1 CLI-Einstieg in `slicetheprompt/cli.py`
Neues Subcommand `ui`:
```powershell
uv run slicetheprompt ui [--project <name>] [--port 8765] [--no-browser]
```
Startet den Server auf `127.0.0.1:8765` (falls Port belegt, nächsten freien Port `8766..8775` wählen und klar ausgeben) und öffnet standardmäßig `webbrowser.open(url)`.

#### 3.2 Endpunkte in `slicetheprompt/ui_server.py`
Statische Auslieferung von `slicetheprompt/ui/*` unter `/` sowie folgende JSON-Endpunkte:

- `GET /api/projects` → Liste aller Projekte aus `pipeline.list_projects()` inkl. letztem Änderungszeitpunkt (`mtime`), aktivem Prüfstatus (`passed`), Versionsanzahl und ob ein offenes `ui_feedback.json` existiert.
- `GET /api/projects/<project>/state` → Komplettpaket des Projekts:
  - `spec` (`spec.yaml`), `model_code` (`model.py`), `params` (`extract_params`),
  - `build` (`build.json`), `print_info` (`print.json` inkl. `rotation_matrix` & `shift`), `report` (`report.json`),
  - `diagnostics` (`diagnostics_3d.json`), `feedback` (`out/ui_feedback.json` falls vorhanden),
  - `versions` (`list_versions`), `colors` (`_body_colors` bzw. Spulenfarben aus letztem Slice/Plan),
  - Asset-URLs für alle STLs (`print.stl`, `model.stl`, `print_<body>.stl`, `ghost_*_print.stl`, sowie historische STLs unter `/api/projects/<project>/files/...`).
- `GET /api/projects/<project>/files/<relpath>` → Sichere Datei-Auslieferung innerhalb des Projektordners (`out/*` und `history/v*/*`, Path-Traversal strikt per `Path.resolve().is_relative_to(proj_dir)` blockieren!).
- `GET /api/projects/<project>/diff?old=v001&new=v002` → Code-Diff + Maß-/Volumen-Delta.
- `POST /api/projects/<project>/params` → Body `{"params": {"L": 65.0, ...}, "orientation": "wie modelliert"}`: ruft `update_params()` und anschließend `pipeline.run_all()` auf (erzeugt neuen Snapshot `reason="ui_param_edit"`), gibt neuen `state` zurück.
- `POST /api/projects/<project>/rebuild` → Body `{"orientation": "..."}`: führt `pipeline.run_all(project, orientation)` aus (`reason="ui_rebuild"`).
- `POST /api/projects/<project>/restore` → Body `{"version": "v002"}`: ruft `history.restore_version(project, version)` auf.
- `POST /api/projects/<project>/feedback` → Speichert die 3D-Markierung des Nutzers:
  - Schreibt `out/ui_feedback.json` (Schema siehe Abschnitt 4) und dekodiert das mitgelieferte `screenshot_data_url` (`data:image/png;base64,...`) nach `out/ui_feedback.png`.
- `DELETE /api/projects/<project>/feedback` → Löscht erledigtes `out/ui_feedback.json` + `out/ui_feedback.png`.
- `GET /api/printer` → Rein lesender Status (`Moonraker().status()` + `Moonraker().cfs()`, mit `2 s` In-Memory-Cache und sauberem Fallback `{"connected": False}`, falls der Drucker ausgeschaltet ist).
- `GET /api/events` → **Server-Sent Events (SSE)**: Überwacht im Hintergrund alle `1.0 s` die `mtime` von `projects/*/out/report.json`, `build.json` und `ui_feedback.json`. Sobald Antigravity oder Claude im Hintergrund `build_and_check` oder `slice` ausführt, pusht `/api/events` sofort `event: project_updated` an den Browser → **automatischer Live-Reload des 3D-Modells ohne F5!**

---

### WP-4: Interaktives Three.js-Frontend (`slicetheprompt/ui/`)

Dunkles, klares Engineering-Layout (Tailwind oder schlankes modernes CSS Grid in `styles.css`, keine Build-Toolchain wie Webpack/Node nötig – direkt ausführbare ES-Module!).

#### 4.1 4-Zonen-Layout
1. **Linke Seitenleiste (260 px): Projekte & Versions-Timeline**
   - Projekt-Dropdown + Status-Badge (`OK` grün / `Fehler` rot / `Warnung` gelb).
   - **Versionsliste (`v005`, `v004`, …):** Zeigt Nummer, Uhrzeit, Grund (`build`, `param_edit`, `restore`), Maße `X×Y×Z` und Status.
   - Je Version 3 Aktionen:
     - **Ansehen** (lädt die Version in den Viewer),
     - **Geisterbild (`Ghost`)** (blendet diese ältere Version halbtransparent kupfer/grau über die aktuelle Version ein, um Geometrieänderungen direkt im 3D-Raum zu sehen!),
     - **Wiederherstellen (`Rollback`)** (setzt diese Version wieder als aktiven Arbeitsstand).
   - Button **„Code-Diff anzeigen“** (Modal mit `model.py`-Diff zwischen zwei Versionen).

2. **Zentraler 3D-Viewport (Three.js)**
   - **Szene & Orientierung:**
     - Z-up-Koordinatensystem (passend zu CAD & Klipper!).
     - Darstellungsmodus umschaltbar: **`Drucklage (260×260 Bett)`** (Standard) ↔ **`Modellkoordinaten (Ursprung 0,0,0)`**.
     - Sichtbares Druckbett (`260 × 260 mm` mit markiertem Nutzbereich `250 × 250 mm`, `10 mm`-Raster und klar beschrifteten Achsen `+X` rot / `+Y` grün / `+Z` blau inkl. Koordinatenursprung, damit Nutzer und Agent dieselben Achsen sehen).
     - Kamera-Schnellansichten: `Iso`, `Vorne (-Y)`, `Hinten (+Y)`, `Links (-X)`, `Rechts (+X)`, `Oben (+Z)`, `Unten (-Z)`.
   - **Körper, Features & Overlays:**
     - Lädt bei Mehrfarb-Projekten jeden `print_<body>.stl` in seiner echten Spulen-/Vorschaufarbe; Kantenlinien (`EdgesGeometry`, `15°`-Schwelle) dezent einzeichnen, damit Bohrungen, Fasen und Stufen gestochen scharf erkennbar sind!
     - **Fehler-Overlay-Schalter:** Färbt betroffene Dreiecke aus `diagnostics_3d.json` direkt auf dem Modell ein (**Rot** = zu dünne Wand / schwebendes Teil / offene Kante, **Orange** = kritischer Überhang / zu lange Brücke) und rendert etwaige `ghost_*_print.stl` (verfehlte Schnitte!) pulsierend rot-transparent.
     - **Feature-Rahmen-Schalter:** Zeigt auf Wunsch die Bounding-Boxen + Namen aller per `tag()` benannten Elemente (`platte`, `mittelwand`, `loch_1`) im 3D-Raum.
     - **Schnittebene (Clipping Plane):** Schieberegler für `X`-, `Y`- oder `Z`-Schnitt, um in verdeckte Innenräume, Senklöcher und Mutternfallen hineinzuschauen.

3. **Obere Werkzeugleiste im Viewport (Interaktions-Modi)**
   - `Drehen` (Standard Orbit)
   - `Pin setzen` (**Flächen-/Feature-Markierung**: Klick auf beliebige Modellstelle setzt nummerierten 3D-Pin + Normalenpfeil, erkennt automatisch getroffenen Körper & überlappendes `tag`-Feature)
   - `Bereichsbox` (**3D-Volumenauswahl**: 1. Klick auf Startecke/Fläche + Ziehen/2. Klick + Höhenregler erzeugt halbtransparente Auswahlbox `[xmin..xmax, ymin..ymax, zmin..zmax]` zum Markieren ganzer Wandabschnitte oder Lochgruppen)
   - `Verschieben (Von → Nach)` (**Richtungs-/Positionskorrektur**: 1. Klick auf falsche Position am Bauteil, 2. Klick auf gewünschte Zielposition auf einer Fläche oder dem Bettraster → zeichnet 3D-Pfeil und berechnet exakt `delta_model_mm = [dx, dy, dz]`)
   - `Messen` (**2-Punkt-Messband**: Klick Punkt A → Punkt B zeigt euklidische Distanz `d` sowie `|ΔX|`, `|ΔY|`, `|ΔZ|` in `mm`, rastet optional an Ecken/Kanten ein)

4. **Rechte Seitenleiste (340 px): 3 Tabs**
   - **Tab 1 – Diagnose & Markierung (Standard):**
     - Liste aller Fehler & Warnungen aus `report.json` + `diagnostics_3d.json`. **Klick auf einen Eintrag schwenkt die Kamera direkt auf die Problemstelle und lässt sie kurz aufleuchten!**
     - Liste der vom Nutzer gesetzten **Markierungen (Pins / Boxen / Von→Nach-Pfeile)** mit freiem Kommentarfeld je Markierung (z. B. *„Mittelwand steht um 90° falsch, muss parallel zur X-Achse mittig sitzen“* oder *„Diese 2 Löcher 8 mm weiter nach +Y“*).
     - Primärbutton **„Markierung für Agent speichern“**:
       1. Rendert den aktuellen 3D-Viewport inkl. eingeblendeter Pins/Boxen/Labels als PNG (`canvas.toDataURL("image/png")`),
       2. sendet Koordinaten + Kommentare + Screenshot an `POST /api/projects/<project>/feedback`,
       3. kopiert einen kurzen Prompt-Schnipsel in die Zwischenablage (z. B. `Bitte setze mein markiertes 3D-Feedback in Projekt <name> um (get_ui_feedback).`).
   - **Tab 2 – Parameter (`model.py`):**
     - Eingabefelder für alle erkannten Top-Level-Parameter (`L`, `B`, `H`, …) + Dropdown für Druckorientierung (`wie modelliert`, `auto`, `X +90°`, …) + Button **„Sofort neu bauen (ohne KI)“**.
   - **Tab 3 – Spezifikation & Checks:**
     - Übersichtliche Tabelle aller `checks` (`Ist` vs. `Soll ± Toleranz`, grün/rot) sowie `masse` und `annahmen` aus `spec.yaml`.

5. **Untere Statusleiste (kompakt, 32 px):**
   - Live-Druckerstatus (`standby` / `printing 43 %`, Düse & Bett °C) + geladene CFS-Spulen `T1A`–`T1D` als kleine Farbpunkte.

---

## 4. Datenformat `out/ui_feedback.json` & MCP-Integration (WP-5)

### 4.1 Schema von `out/ui_feedback.json`
```json
{
  "project": "2026-11-04-wandhalter",
  "based_on_version": "v002",
  "created_at": "2026-11-04T11:30:00",
  "view_mode": "print",
  "general_note": "Mittelwand falsch ausgerichtet und oberes Loch zu nah am Rand.",
  "screenshot": "C:/.../projects/2026-11-04-wandhalter/out/ui_feedback.png",
  "annotations": [
    {
      "id": 1,
      "type": "pin",
      "body": "_teil",
      "feature": "mittelwand",
      "model_xyz": [0.0, 12.5, 10.0],
      "model_normal": [1.0, 0.0, 0.0],
      "print_xyz": [130.0, 142.5, 10.0],
      "note": "Diese Mittelwand um 90° drehen (soll entlang X laufen, Dicke 3 mm in Y)."
    },
    {
      "id": 2,
      "type": "move",
      "feature": "loch_oben",
      "from_model_xyz": [22.0, 8.0, 4.0],
      "to_model_xyz": [15.0, 8.0, 4.0],
      "delta_model_mm": [-7.0, 0.0, 0.0],
      "note": "Loch hierhin verschieben (7 mm weiter nach innen)."
    },
    {
      "id": 3,
      "type": "box",
      "bbox_model": [-30.0, -15.0, 0.0, -10.0, 15.0, 6.0],
      "features_inside": ["loch_links_1", "loch_links_2"],
      "note": "Beide Löcher in diesem Bereich als M4-Senkloch statt M3 ausführen."
    }
  ]
}
```

### 4.2 Neue MCP-Tools in `slicetheprompt/mcp_server.py` (und CLI-Pendants in `slicetheprompt/cli.py`)
1. **`get_ui_feedback(project: str = "") -> dict`**:
   - Liest `out/ui_feedback.json` des angegebenen (oder zuletzt geänderten) Projekts.
   - Gibt das vollständige JSON inkl. absolutem Pfad `screenshot` (`out/ui_feedback.png`) zurück, mit dem Hinweis an den Agenten, `ui_feedback.png` zusätzlich per `read_file`/`Read` anzusehen und nach erfolgreicher Umsetzung `clear_ui_feedback(project)` aufzurufen.
2. **`clear_ui_feedback(project: str) -> dict`**:
   - Archiviert oder entfernt `out/ui_feedback.json` + `out/ui_feedback.png`, nachdem die Änderung erfolgreich gebaut und geprüft wurde.
3. **`version_list(project: str) -> dict`** & **`version_restore(project: str, version: str) -> dict`**:
   - Erlaubt auch dem Agenten im Chat auf Zuruf (*„Geh zurück auf Version 2“*), sauber über dieselbe Snapshot-Logik zurückzuspringen.

### 4.3 Anpassung der Skills & Arbeitsregeln
- In [`.agents/skills/teil/SKILL.md`](./.agents/skills/teil/SKILL.md) und [`.claude/commands/teil.md`](./.claude/commands/teil.md) (und Kopie nach `.claude/commands/teil.md`):
  - Bei Änderungen an einem bestehenden Projekt zuerst prüfen, ob `out/ui_feedback.json` vorliegt (`get_ui_feedback`). Falls ja: die exakten `model_xyz` / `delta_model_mm` / `bbox_model`-Angaben und `ui_feedback.png` nutzen, nach erfolgreichem Rebuild `clear_ui_feedback` aufrufen.
  - Empfehlung aufnehmen, bei mehrteiligen Geometrien (Platte + Wände + Bohrungen) wichtige Elemente mit `tag("name", ...)` bzw. `cut_feature(part, "loch_1", ...)` aus `lib.parts` zu benennen, damit `SliceThePrompt` sie einzeln auswählbar macht und verfehlte Schnitte automatisch erkennt.
- Kurzen Einzeiler-Verweis auf `uv run slicetheprompt ui` und `get_ui_feedback` in [`AGENTS.md`](./AGENTS.md), [`CLAUDE.md`](./CLAUDE.md) und [`README.md`](./README.md) ergänzen.

---

## 5. Qualitäts- & Abnahmekriterien (Definition of Done)

Bevor die Implementierung als abgeschlossen gemeldet wird, **müssen** alle folgenden Prüfungen fehlerfrei durchlaufen:

1. **Bestehende Selbsttests unverändert grün:**
   ```powershell
   uv run python tests/test_meshcheck.py
   uv run python tests/test_cfs_3mf.py
   ```
2. **Neue automatisierte Test-Suite `tests/test_ui_and_history.py` schreiben und ausführen:**
   ```powershell
   uv run python tests/test_ui_and_history.py
   ```
   Pflicht-Testfälle in `tests/test_ui_and_history.py`:
   - `test_snapshots_and_restore`: Projekt bauen → `v001` angelegt → `model.py` ändern & bauen → `v002` angelegt → `restore_version(..., "v001")` stellt `v001`-Geometrie wieder her; identischer Doppel-Build erzeugt kein Duplikat.
   - `test_ast_params`: Extrahiert und ändert Einzel- (`L = 40.0`), Negativ- (`X = -12.5`) und Tupel-Parameter (`L, B, H = 60.0, 25.0, 4.0`), ohne Kommentare oder restlichen Code zu verändern.
   - `test_empty_cut_detection`: Ein `cut_feature(Box(20, 20, 5), "loch_daneben", Pos(50, 0, 0) * hole_cut(4, 5))` wird als `empty_cut` erkannt, erzeugt `ghost_loch_daneben.stl` und lässt `check()` fehlschlagen (`passed == False`).
   - `test_diagnostics_3d_and_coord_transform`: Dünne Wand + Überhang erscheinen mit gültigen `tri_indices` in `diagnostics_3d.json`; Rücktransformation `p_model = (p_print - shift) @ R` stimmt für alle 6 `ORIENTATIONS` auf `< 1e-6 mm`.
   - `test_ui_http_endpoints_and_feedback`: Startet den `ThreadingHTTPServer` auf Port `0` (ephemerer Testport), prüft `GET /`, `GET /api/projects`, `GET /api/projects/<p>/state`, `POST /api/projects/<p>/feedback` (inkl. PNG-Dekodierung), `get_ui_feedback` via MCP-Funktion und Path-Traversal-Abwehr (`403`/`404` bei `../`).
3. **End-to-End-Probelauf an den realen Beispielprojekten:**
   - `uv run slicetheprompt run beispiel-box-m3` und `uv run slicetheprompt run beispiel-schild-zweifarbig` erzeugen jeweils `history/v001/` und `out/diagnostics_3d.json`.
   - `uv run slicetheprompt ui --no-browser` startet sauber und beantwortet `http://127.0.0.1:8765/api/projects` mit HTTP `200`.
4. **Hinweis an den Nutzer nach Abschluss:**
   Erinnern, den MCP-Server `slicetheprompt` in Antigravity Desktop (**Additional Options (...) > MCP Servers**) bzw. Claude Code (`/mcp`) neu zu laden, damit die neuen Tools `get_ui_feedback`, `clear_ui_feedback`, `version_list` und `version_restore` aktiv sind.
