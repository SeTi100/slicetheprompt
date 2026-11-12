# Skript-Sammlung für Termux & Debian (1-Klick-Bedienung)

Diese Datei enthält alle Skripte, um SliceThePrompt, den MCP-Server und Open WebUI auf dem Samsung Galaxy S26 Ultra vollautomatisch zu steuern.

---

## 1. Automatisiertes Update-Skript nach Git-Änderungen (`update_and_restart.sh`)

Führe dieses Skript in Debian aus, wenn am Repository etwas geändert oder gepusht wurde. Es zieht den neuesten Code, aktualisiert das Arbeitsverzeichnis und startet die Server sauber neu:

```bash
cat << 'EOF' > /root/update_and_restart.sh
#!/bin/bash
set -e
echo "=== 1. Git Pull (Branch: android) ==="
cd /root/slicetheprompt
git fetch origin android
git reset --hard origin/android

echo "=== 2. Dienste neu starten ==="
/root/start_all.sh

echo "=== Update & Neustart erfolgreich abgeschlossen ==="
EOF

chmod +x /root/update_and_restart.sh
```

---

## 2. Master-Startskript in Debian (`/root/start_all.sh`)

Startet 3D-Studio, MCP-Server und Open WebUI im Hintergrund und leitet Ausgaben in Logdateien unter `/tmp/` um:

```bash
cat << 'EOF' > /root/start_all.sh
#!/bin/bash

echo "[1/4] Stoppe alte Instanzen..."
pkill -f "slicetheprompt" 2>/dev/null || true
pkill -f "open-webui" 2>/dev/null || true
sleep 1

echo "[2/4] Starte SliceThePrompt 3D-Studio (Port 8765)..."
cd /root/slicetheprompt
nohup uv run slicetheprompt ui > /tmp/slicetheprompt_ui.log 2>&1 &

echo "[3/4] Starte SliceThePrompt MCP Streamable HTTP (Port 8766)..."
nohup uv run slicetheprompt mcp --transport streamable-http --host 127.0.0.1 --port 8766 > /tmp/slicetheprompt_mcp.log 2>&1 &

echo "[4/4] Starte Open WebUI (Port 8080)..."
nohup open-webui serve > /tmp/openwebui.log 2>&1 &

sleep 2
echo "============================================="
echo " Alle Dienste erfolgreich im Hintergrund aktiv!"
echo " 3D-Studio:   http://127.0.0.1:8765"
echo " MCP-Server:  http://127.0.0.1:8766/mcp"
echo " Open WebUI:  http://127.0.0.1:8080"
echo " Logs unter:  /tmp/*.log"
echo "============================================="
EOF

chmod +x /root/start_all.sh
```

---

## 3. Master-Stopskript in Debian (`/root/stop_all.sh`)

Beendet alle aktiven Hintergrunddienste sauber:

```bash
cat << 'EOF' > /root/stop_all.sh
#!/bin/bash
echo "Stoppe SliceThePrompt und Open WebUI..."
pkill -f "slicetheprompt" 2>/dev/null || true
pkill -f "open-webui" 2>/dev/null || true
sleep 1
echo "Alle Dienste beendet."
EOF

chmod +x /root/stop_all.sh
```

---

## 4. Status- & Diagnose-Skript in Debian (`/root/status_all.sh`)

Zeigt auf einen Blick, ob alle Ports offen sind und gibt die letzten Zeilen der Logs aus:

```bash
cat << 'EOF' > /root/status_all.sh
#!/bin/bash
echo "=== Aktive Dienste & Ports ==="
ss -tulpn | grep -E '8765|8766|8080' || echo "Keine Ports aktiv!"

echo ""
echo "=== Letzte Zeilen: UI Server Log ==="
tail -n 5 /tmp/slicetheprompt_ui.log 2>/dev/null || echo "Kein Log vorhanden."

echo ""
echo "=== Letzte Zeilen: MCP Server Log ==="
tail -n 5 /tmp/slicetheprompt_mcp.log 2>/dev/null || echo "Kein Log vorhanden."

echo ""
echo "=== Letzte Zeilen: Open WebUI Log ==="
tail -n 5 /tmp/openwebui.log 2>/dev/null || echo "Kein Log vorhanden."
EOF

chmod +x /root/status_all.sh
```

---

## 5. Termux-Widget-Skripte (Außerhalb von Debian im Android-Host)

Diese Skripte werden direkt in Termux (`~ $`) ausgeführt, um 1-Klick-Widgets auf dem Homescreen anzulegen.

### A) Widget: Starten (`~/.shortcuts/Start_Harness.sh`)

```bash
mkdir -p ~/.shortcuts
cat << 'EOF' > ~/.shortcuts/Start_Harness.sh
#!/bin/bash
# 1. Verhindert Standby bei ausgeschaltetem Display
termux-wake-lock

# 2. Stellt sicher, dass der Android-Download-Ordner existiert
mkdir -p /storage/emulated/0/Download/SliceThePrompt

# 3. Startet Debian mit Bind-Mount und führt das Startskript aus
proot-distro login debian \
  --bind /storage/emulated/0/Download/SliceThePrompt:/mnt/android_out \
  --shared-tmp \
  -- /root/start_all.sh
EOF

chmod +x ~/.shortcuts/Start_Harness.sh
```

### B) Widget: Stoppen (`~/.shortcuts/Stop_Harness.sh`)

```bash
cat << 'EOF' > ~/.shortcuts/Stop_Harness.sh
#!/bin/bash
proot-distro login debian -- /root/stop_all.sh
termux-wake-unlock
echo "System gestoppt und Wakelock freigegeben."
EOF

chmod +x ~/.shortcuts/Stop_Harness.sh
```

### C) Widget: Update & Neustart (`~/.shortcuts/Update_Harness.sh`)

Aktualisiert den Code per Knopfdruck vom GitHub-Branch:

```bash
cat << 'EOF' > ~/.shortcuts/Update_Harness.sh
#!/bin/bash
proot-distro login debian -- /root/update_and_restart.sh
EOF

chmod +x ~/.shortcuts/Update_Harness.sh
```
