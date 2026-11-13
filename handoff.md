# Handoff: Aktueller System- & Skript-Stand (Samsung Galaxy S26 Ultra)

Dieses Dokument hält den **exakten, geprüften und funktionierenden Stand** aller Skripte, Pfade und Konfigurationen für die lokale Bereitstellung von **slicetheprompt** und **Open WebUI** auf dem Samsung Galaxy S26 Ultra fest.

---

## 1. System-Übersicht & Ports

- **Hardware:** Samsung Galaxy S26 Ultra (Snapdragon 8 Elite, ARM64 / `aarch64`, 12 GB RAM)
- **Host-OS:** Android 16 (Termux)
- **Container-OS:** Debian Linux via `proot-distro` (komplett headless)
- **Storage-Mount:** `/storage/emulated/0/Download/SliceThePrompt` ➔ `/mnt/android_out` in Debian
- **Git-Repository:** `/root/slicetheprompt/` auf Branch `android` (Remote: `origin/android`)

| Port | Dienst | URL / Endpunkt | Funktion |
|---|---|---|---|
| **8765** | SliceThePrompt 3D-Studio | `http://127.0.0.1:8765` | Three.js Web-CAD-Viewer, Maße, Schnittansichten |
| **8766** | SliceThePrompt MCP-Server | `http://127.0.0.1:8766/mcp` | Streamable HTTP MCP-Server für LLMs |
| **8080** | Open WebUI | `http://127.0.0.1:8080` | Lokales Chat-Interface für Gemini |

---

## 2. Datenbank & Persistenz (Open WebUI)

- **Echte SQLite-Datenbank (mit allen Accounts, Chats, Tools & Keys):**  
  `/root/.local/share/uv/tools/open-webui/lib/python3.12/site-packages/open_webui/data/webui.db`
- **Sicherheits-Backup:**  
  `/root/webui_original_backup.db`
- **Umgebungsvariable in allen Startskripten:**  
  `export DATA_DIR="/root/.local/share/uv/tools/open-webui/lib/python3.12/site-packages/open_webui/data"`

---

## 3. Skripte im Debian-Container (`/root/`)

### A) Master-Starter: `/root/start_all.sh`
Startet 3D-Studio, MCP-Server und Open WebUI im Hintergrund, nutzt die Original-Datenbank und hält die PRoot-Session über `wait` dauerhaft aktiv:

```bash
#!/bin/bash
export PATH="/root/.local/bin:/root/.cargo/bin:$PATH"
export HOME="/root"
export DATA_DIR="/root/.local/share/uv/tools/open-webui/lib/python3.12/site-packages/open_webui/data"

echo "[1/4] Stoppe alte Instanzen..."
pkill -f '[s]licetheprompt' 2>/dev/null || true
pkill -f '[o]pen-webui' 2>/dev/null || true
sleep 1

# Sicherheitskopie der Datenbank (überschreibt bestehendes Backup nicht)
cp -n "$DATA_DIR/webui.db" /root/webui_original_backup.db 2>/dev/null || true

echo "[2/4] Starte SliceThePrompt 3D-Studio (Port 8765)..."
cd /root/slicetheprompt
nohup uv run slicetheprompt ui > /tmp/slicetheprompt_ui.log 2>&1 &

echo "[3/4] Starte SliceThePrompt MCP Streamable HTTP (Port 8766)..."
nohup uv run slicetheprompt mcp --transport streamable-http --host 127.0.0.1 --port 8766 > /tmp/slicetheprompt_mcp.log 2>&1 &

echo "[4/4] Starte Open WebUI (Port 8080)..."
nohup open-webui serve > /tmp/openwebui.log 2>&1 &

echo "============================================="
echo " Alle Dienste aktiv! Halte PRoot-Session offen..."
echo " 3D-Studio:   http://127.0.0.1:8765"
echo " MCP-Server:  http://127.0.0.1:8766/mcp"
echo " Open WebUI:  http://127.0.0.1:8080"
echo "============================================="

# Hält PRoot am Leben, solange die Dienste laufen
wait
```

### B) Master-Stopper: `/root/stop_all.sh`
```bash
#!/bin/bash
echo "Stoppe SliceThePrompt und Open WebUI..."
pkill -f '[s]licetheprompt' 2>/dev/null || true
pkill -f '[o]pen-webui' 2>/dev/null || true
sleep 1
echo "Alle Dienste beendet."
```

### C) Git-Update & Neustart: `/root/update_and_restart.sh`
Holt die neuesten Commits sauber per Hard-Reset (keine Merge-Konflikte) und ersetzt den laufenden Prozess per `exec` durch den neuen Server-Start:

```bash
#!/bin/bash
export PATH="/root/.local/bin:/root/.cargo/bin:$PATH"
export HOME="/root"

echo "=== 1. Git Update von Branch 'android' ==="
cd /root/slicetheprompt
git fetch origin android
git reset --hard origin/android
echo "Aktueller Git-Stand:"
git log -1 --oneline

# Open WebUI Pipe-JSON für 1-Klick-Import in den Android-Download-Ordner kopieren
if [ -d /mnt/android_out ] && [ -f /root/slicetheprompt/scripts/openwebui_gemini_react_pipe.json ]; then
  cp -f /root/slicetheprompt/scripts/openwebui_gemini_react_pipe.json /mnt/android_out/slicetheprompt_react_pipe.json
fi

# Open WebUI Pipe direkt in webui.db synchronisieren (behält Valves/API-Key)
if [ -f /root/slicetheprompt/scripts/sync_openwebui_pipe.py ] && [ -x /root/.local/share/uv/tools/open-webui/bin/python ]; then
  DATA_DIR="/root/.local/share/uv/tools/open-webui/lib/python3.12/site-packages/open_webui/data" \
  WEBUI_SECRET_KEY=temp \
  /root/.local/share/uv/tools/open-webui/bin/python /root/slicetheprompt/scripts/sync_openwebui_pipe.py || true
fi

echo "=== 2. Dienste mit neuem Code neu starten ==="
exec /root/start_all.sh
```

---

## 4. Android Host-Skripte (Termux `~/.shortcuts/tasks/`)

Alle Skripte liegen im Unterordner `tasks/`, damit Termux:Widget sie **vollständig stumm im Hintergrund ohne aufpoppendes Konsolenfenster** ausführt.

### A) Widget: `~/.shortcuts/tasks/Start_Harness.sh`
```bash
#!/bin/bash
termux-wake-lock
mkdir -p /storage/emulated/0/Download/SliceThePrompt
proot-distro login debian \
  --bind /storage/emulated/0/Download/SliceThePrompt:/mnt/android_out \
  --shared-tmp \
  -- /root/start_all.sh
```

### B) Widget: `~/.shortcuts/tasks/Stop_Harness.sh`
```bash
#!/bin/bash
proot-distro login debian -- /root/stop_all.sh
termux-wake-unlock
echo "System gestoppt & Akku-Wakelock freigegeben."
```

### C) Widget: `~/.shortcuts/tasks/Update_Harness.sh`
```bash
#!/bin/bash
termux-wake-lock
proot-distro login debian \
  --bind /storage/emulated/0/Download/SliceThePrompt:/mnt/android_out \
  --shared-tmp \
  -- /root/update_and_restart.sh
```

---

## 5. Open WebUI MCP- & Modell-Konfiguration

### A) Externe MCP-Verbindung
1. **Admin-Bereich ➔ Einstellungen ➔ Externe Verbindungen:**
   - Typ: `MCP (Streamable HTTP)`
   - URL: `http://127.0.0.1:8766/mcp`
   - Klick auf das Prüfsymbol (Häkchen/Verify) ➔ **Speichern**

### B) Modell-Einstellungen (Workspace ➔ Modelle ➔ '3D designer')
*Wichtig: Einstellungen immer direkt im Modell-Preset speichern, nicht im Chat-Menü 'Steuerung' (dort steht 'Standard' für die Übernahme der Modellwerte).*
- **Funktionsaufruf (Function Calling):** Zwingend auf **`Veraltet` (Legacy)**!
  - *Hintergrund:* Da Gemini über die Google OpenAI-Kompatibilitätsschicht angebunden ist, bricht `Nativ` komplett ab (Modell antwortet nur mit eigenem Namen oder bleibt stumm). Im Modus `Veraltet` injiziert Open WebUI die Tools per Prompt-Verfahren – das funktioniert verlässlich.
  - *Einschränkung von Legacy:* Es wird genau **ein Tool-Aufruf pro Chat-Turn** ausgeführt. Daher muss der 1-Schritt-Workflow genutzt werden.
- **Streaming:** Deaktiviert.
- **Valves:** Der Reiter „Valves“ bei Werkzeugen ist nur für interne Python-Plugins gedacht – dass dieser bei externen MCP-Servern leer ist, ist normal.
- **Werkzeuge:** Haken bei `slicetheprompt` setzen.
- **System-Prompt (1-Schritt-Workflow inkl. build123d-Vertrag):**
  ```markdown
  Du bist ein präziser CAD-Konstrukteur für 3D-Druckteile mit build123d.
  Wenn der Nutzer ein Teil anfordert:
  1. Schreibe sauberen build123d-Code nach folgendem Schema (MUSS `part` oder `bodies` definieren, z=0 auf Druckbett):
     ```python
     from build123d import *
     from lib.parts import *
     set_material("pla")
     C = (Align.CENTER, Align.CENTER, Align.MIN)
     part = Box(20, 20, 20, align=C)
     part = bottom_chamfer(part)
     bb = part.bounding_box()
     checks = {"Breite": (bb.size.X, 20.0, 0.05)}
     ```
  2. Rufe direkt `project_new(name="...", material="pla", code="...")` auf. Dadurch wird das Projekt in EINEM Schritt gebaut, geprüft und gerendert.
  3. Lies das Ergebnis aus dem Tool-Rückgabewert (Status, Maße, bestandene Checks, Fehler/Warnungen) und berichte dem Nutzer wahrheitsgetreu.
  4. Verweise auf das 3D-Studio (http://127.0.0.1:8765).
  5. Für nachträgliche Änderungen: `build_and_check(project="...", code="...")`.
  ```
