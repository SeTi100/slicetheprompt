# Walkthrough: Slicetheprompt auf Samsung Galaxy S26 Ultra (Android / ARM64)

Dieses Dokument fasst alle Arbeiten, architektonischen Anpassungen, gelösten Probleme und Konfigurationen zusammen, die heute durchgeführt wurden, um **slicetheprompt** vollständig lokal auf einem Android-Smartphone (Samsung Galaxy S26 Ultra mit Snapdragon 8 Elite, Debian in Termux PRoot) als MCP-Backend für Open WebUI und lokales 3D-Studio bereitzustellen.

---

## 1. Ausgangslage & Zielsetzung
- **Hardware:** Samsung Galaxy S26 Ultra (Snapdragon 8 Elite, ARM64 / `aarch64`, 12 GB RAM).
- **Betriebssystem:** Android 16 mit Termux und Debian Linux via `proot-distro`.
- **Display-Umgebung:** Vollständig headless (kein X11, kein Wayland).
- **Ziel:** 
  1. Lokale Geometriegenerierung mit `build123d` und OpenCASCADE (`cadquery-ocp`).
  2. Lokales 3D-Studio auf Port 8765 (`slicetheprompt ui`) im mobilen Browser bedienbar.
  3. Vollwertiger MCP-Server (`slicetheprompt mcp`) auf Port 8766 für Open WebUI (Port 8080).
  4. Autonome CAD-Erstellung durch LLMs (Gemini 3.8 Flash / 2.5 Flash) ohne manuelles Tippen von CLI-Befehlen.
  5. 1-Klick-Steuerung über Termux-Widgets auf dem Android-Startbildschirm.

---

## 2. Durchgeführte Code-Änderungen im Repository (Branch: `android`)

Alle Anpassungen wurden im Branch `android` committed und auf GitHub synchronisiert:

### Commit 1: `ae8db0e` – Headless-Rendering, ARM64-Slicer-Bypass & 0.0.0.0-Bindung
- **Slicer-Bypass (`slicetheprompt/slicer.py`):**
  - *Problem:* Creality Print CLI existiert nur als x86_64-Binary; auf ARM64 crashte jeder Slice-Versuch.
  - *Lösung:* Automatische Erkennung von `platform.machine() in ('aarch64', 'arm64')`. Slicing wird auf ARM64 headless simuliert (Dummy-GCode-Erzeugung mit Bounding-Box und Layer-Statistik), sodass die CAD-Prüfpipeline nicht abbricht.
- **Headless-Rendering (`slicetheprompt/render.py`):**
  - *Problem:* Matplotlib versuchte ein GUI-Backend (Tk/Qt) zu initialisieren, was ohne X11/Wayland mit `TclError` fehlschlug.
  - *Lösung:* Strikte Erzwingung des reinen Datei-Backends: `os.environ["MPLBACKEND"] = "Agg"` und `matplotlib.use("Agg", force=True)` vor allen Imports.
- **Netzwerk-Bindung (`slicetheprompt/ui_server.py`, `cli.py`):**
  - *Problem:* `127.0.0.1` war teilweise aus dem Android-Host-Browser nicht sauber erreichbar.
  - *Lösung:* Standard-Bindung auf `0.0.0.0` unter ARM64, CLI-Parameter `--host` für `ui` und `mcp` hinzugefügt.

### Commit 2: `645dc90` – FastMCP/MCPServer Transport-Fix
- **MCP Server CLI (`slicetheprompt/mcp_server.py`, `cli.py`):**
  - *Problem:* Beim Starten von FastMCP im SSE-Modus stürzte der Server mit `ValueError: "Settings" object has no field "host"` ab, da in `mcp 2.x` `server.settings` ein Pydantic-Objekt ohne `.host`/`.port` ist.
  - *Lösung:* Übergabe von `host` und `port` direkt an `server.run(transport=..., host=..., port=...)`.
  - Unterstützung der Transports `stdio`, `sse` und `streamable-http`.

### Commit 3: `d5ed12f` – Headless LLM-Unterstützung (Open WebUI)
- **CAD-Code direkt über MCP schreiben (`slicetheprompt/pipeline.py`, `_worker.py`, `mcp_server.py`):**
  - *Problem:* In Desktop-Agenten (Claude Code, Antigravity) besitzt der Harness Dateisystem-Tools (`write_file`/`edit_file`). In Open WebUI hat das Modell *keinen* Dateizugriff. Gemini erstellte zwar ein leeres Projekt via `project_new`, gab den Python-Code aber nur als Text aus und forderte den Nutzer auf: *"Führe nun den Check-Befehl aus"*.
  - *Lösung:*
    - Neues MCP-Tool `model_write(project, code, spec=None)` hinzugefügt.
    - `build_and_check(project, ..., code=...)` und `project_new(..., code=...)` erweitert, sodass der Code direkt per Tool-Call injiziert und gebaut werden kann.

### Commit 4: `5f90f08` – BrokenPipeError & Verbindungs-Stabilität
- **Socket-Handling im UI-Server (`slicetheprompt/ui_server.py`):**
  - *Problem:* Wenn der Browser-Tab gewechselt, neu geladen oder ein Polling-Request abgebrochen wurde, trat ein `BrokenPipeError` auf. Der Server versuchte daraufhin einen 500er HTTP-Status in die bereits tote Socket zu schreiben, was zu doppelten Tracebacks in der Konsole führte.
  - *Lösung:* `BrokenPipeError`, `ConnectionResetError` und `ConnectionAbortedError` in `_send_json`, `_send_bytes`, `do_GET`, `do_POST` und `do_DELETE` abgefangen und lautlos ignoriert.
- **Moonraker-Polling-Drosselung:**
  - *Problem:* Wenn der Creality-Drucker offline oder nicht im WLAN war, blockierte die Abfrage alle 2 Sekunden für bis zu 3 Sekunden.
  - *Lösung:* Timeout auf 1,0 s gesenkt; Offline-Zustand wird für 5,0 s gecacht, um Blockaden im UI-Server zu verhindern.

---

## 3. Konfiguration der Dienste & Open WebUI

### Port-Übersicht auf dem Smartphone
| Port | Dienst | Beschreibung |
|---|---|---|
| **8765** | SliceThePrompt 3D-Studio | Three.js Web-Viewer, Schnittansichten, Maße, Versionen |
| **8766** | SliceThePrompt MCP-Server | Streamable-HTTP (`http://127.0.0.1:8766/mcp`) |
| **8080** | Open WebUI | Chat-Interface für Gemini |

### Open WebUI MCP-Verbindung
- **Pfad:** *Admin-Bereich ➔ Einstellungen ➔ Externe Verbindungen ➔ MCP*
- **Typ:** `MCP (Streamable HTTP)`
- **URL:** `http://127.0.0.1:8766/mcp`
- **Authentifizierung:** Keine

### Open WebUI Modell-Konfiguration (Gemini 3.8 / 2.5 Flash)
- **Funktionsaufruf (Function Calling):** `Veraltet` (Legacy) oder `Nativ` (bei deaktiviertem Streaming)
- **System-Prompt für autonomes Arbeiten (1-Schritt-Workflow):**
```markdown
Du bist ein präziser CAD-Konstrukteur für 3D-Druckteile mit build123d.
Wenn der Nutzer ein Teil anfordert:
1. Konstruiere das Teil mit build123d und rufe direkt project_new mit dem vollständigen Python-Code auf:
   project_new(name="...", material="pla", code="...")
   Dadurch wird das Projekt in EINEM autonomen Schritt angelegt, gebaut, geprüft und gerendert.
2. Lies das Ergebnis aus dem Tool-Rückgabewert (Status, Maße, bestandene Checks, Warnungen) und berichte dem Nutzer wahrheitsgetreu darüber.
3. Verweise auf das 3D-Studio (http://127.0.0.1:8765) zur Begutachtung.
4. Für nachträgliche Anpassungen: model_write(project="...", code="...") oder build_and_check(project="...", code="...").
```

---

## 4. Wichtige Hinweise für den Betrieb auf Android
1. **Termux-Wake-Lock:** Android schaltet Hintergrundprozesse bei inaktivem Bildschirm aggressiv ab. Daher muss immer `termux-wake-lock` aktiv sein, solange die Server laufen.
2. **Akku-Optimierung:** In den Android-Systemeinstellungen muss für Termux die Akkunutzung auf **„Nicht eingeschränkt“** gestellt sein.
3. **Ausgabe-Dateien auf Android bereitstellen:** Durch den Bind-Mount `--bind /storage/emulated/0/Download/SliceThePrompt:/mnt/android_out` können generierte STEP- und STL-Dateien direkt im Android-Download-Ordner gespeichert und per Galerie oder 3D-Viewer-App betrachtet werden.
