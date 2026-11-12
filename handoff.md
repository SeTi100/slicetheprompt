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

1. **MCP-Integration (Admin ➔ Einstellungen ➔ Externe Verbindungen):**
   - Typ: `MCP (Streamable HTTP)`
   - URL: `http://127.0.0.1:8766/mcp`
   - Authentifizierung: Keine
2. **Modell-Einstellungen (z. B. Gemini 3.8 Flash):**
   - Funktionsaufruf (Function Calling): `Veraltet` (Legacy) oder `Nativ` (sofern Streaming aus ist)
   - Streaming: Deaktiviert bei Tool-Nutzung
   - **System-Prompt (für verlässliches 1-Schritt-Bauen ohne Tool-Ketten-Abbruch):**
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
