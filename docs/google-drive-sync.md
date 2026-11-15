# Google-Drive-Synchronisation mit rclone (Build-Hook & PRoot-Anleitung)

`slicetheprompt` kann fertige Projekte nach jedem Bau automatisch und **ohne Wartezeit (non-blocking)** mit `rclone` nach Google Drive hochladen:

```text
projects/<projekt>  ──(rclone copy im Hintergrund)──▶  gdrive:SliceThePrompt/projects/<projekt>
```

So liegen `model.py`, `spec.yaml`, `out/render.png`, `out/model.step` und `out/print.stl` sofort nach jedem Build in deinem Google Drive – ohne Git-Konflikte und ohne dass der Build auf das Netzwerk warten muss.

---

## 1. Einmalige Autorisierung: PC → Debian-PRoot (Handy)

Da Debian-PRoot (Termux) auf dem Handy keinen Desktop-Browser für den Google-OAuth-Login öffnen kann, erzeugst du das OAuth-Token **einmalig am PC** und überträgst es in die `rclone.conf` im Debian-PRoot.

### Schritt 1: `rclone` auf beiden Geräten installieren

- **Am PC (Windows):**
  ```powershell
  winget install Rclone.Rclone
  ```
  *(Alternativ macOS: `brew install rclone`, Linux: `sudo apt install rclone`.)*
  Danach ein neues Terminal öffnen, damit `rclone` im `PATH` liegt.

- **Im Debian-PRoot (Handy):**
  ```bash
  sudo apt update && sudo apt install -y rclone
  ```
  *(Für eine aktuellere Version mit `bisync`-Verbesserungen alternativ: `curl https://rclone.org/install.sh | sudo bash`.)*

### Schritt 2: OAuth-Token am PC erzeugen

Am PC im Terminal ausführen:

```bash
rclone authorize "drive"
```

1. Es öffnet sich automatisch der Browser mit der Google-Anmeldung.
2. Mit dem gewünschten Google-Konto anmelden und den Zugriff bestätigen (`Success!`).
3. Im Terminal erscheint ein JSON-Block zwischen `Paste the following into your remote machine --->` und `<---End paste`:
   ```json
   {"access_token":"...","token_type":"Bearer","refresh_token":"...","expiry":"..."}
   ```
4. Diese **komplette einzeilige JSON-Zeile** (inklusive `{` und `}`) kopieren (z. B. per Messenger/Notiz an dich selbst oder SSH ans Handy schicken).

> **Hinweis zu eigenen OAuth-Credentials (optional):**
> Die eingebaute Standard-Client-ID von `rclone` teilt sich das globale Google-API-Kontingent mit vielen Nutzern. Für normalen Projekt-Sync reicht sie völlig aus; falls es zu Rate-Limits kommt, kannst du in der Google Cloud Console eine eigene OAuth-Desktop-Client-ID anlegen (`rclone authorize "drive" "<CLIENT_ID>" "<CLIENT_SECRET>"`) und diese bei Schritt 3 in `client_id` / `client_secret` eintragen.

### Schritt 3: Remote `gdrive` im Debian-PRoot einrichten

Im Debian-PRoot `rclone config` starten:

```bash
rclone config
```

Folge den Eingaben:

| Abfrage | Eingabe |
| :--- | :--- |
| `n/s/q>` | `n` *(New remote)* |
| `name>` | `gdrive` *(wichtig: exakt dieser Standardname)* |
| `Storage>` | `drive` *(Google Drive)* |
| `client_id>` | *Enter (leer lassen)* |
| `client_secret>` | *Enter (leer lassen)* |
| `scope>` | `1` *(Full access all files – `drive`)* |
| `service_account_file>` | *Enter (leer lassen)* |
| `Edit advanced config?` | `n` |
| `Continue using the shared client_id anyway?` | **`y`** *(falls diese Abfrage bei neuerem rclone kommt; sonst fragt es nach einer eigenen `client_id`)* |
| `Use web browser to automatically authenticate rclone with remote?` | **`n`** *(wichtig im PRoot!)* |
| `config_token>` | **Hier die kopierte JSON-Zeile vom PC einfügen** + Enter |
| `Configure this as a Shared Drive (Team Drive)?` | `n` |
| `Keep this "gdrive" remote?` | `y` |
| `e/n/d/r/c/s/q>` | `q` |

Alternativ kannst du direkt `~/.config/rclone/rclone.conf` anlegen (Rechte `chmod 600 ~/.config/rclone/rclone.conf`):

```ini
[gdrive]
type = drive
scope = drive
token = {"access_token":"...","token_type":"Bearer","refresh_token":"...","expiry":"..."}
```

### Schritt 4: Verbindung & `slicetheprompt` testen

```bash
# Prüft, ob Google Drive erreichbar ist:
rclone lsd gdrive:

# Prüft die Integration in slicetheprompt:
uv run slicetheprompt sync --status
```

Erwartete Ausgabe bei `sync --status`:
- `rclone_installed: true`
- `remote_configured: true`
- `active: true`

---

## 2. Automatischer Upload nach jedem Build (Standard)

Sobald `rclone` installiert und der Remote `gdrive:` eingerichtet ist, ist der Build-Hook **automatisch aktiv** – es ist keine weitere Konfiguration nötig.

Am Ende von:
- `project_new` (über MCP oder CLI `new`)
- `build_and_check` (über MCP oder CLI `run`)
- `stencil` (über MCP oder CLI `schablone`)
- `build_assembly` (über MCP oder CLI `baugruppe`)

startet `slicetheprompt` im Hintergrund (`non-blocking`):

```bash
rclone copy projects/<projekt> gdrive:SliceThePrompt/projects/<projekt>
```

- Die Ausgaben landen im Log `.slicetheprompt/sync.log`.
- Schlägt der Sync fehl (z. B. offline unterwegs), läuft der CAD-Build trotzdem normal durch.
- Manueller Upload für ein einzelnes Projekt oder alle Projekte:
  ```bash
  uv run slicetheprompt sync                   # alle Projekte unter projects/
  uv run slicetheprompt sync mein-projekt      # nur ein Projekt
  uv run slicetheprompt sync --dry-run         # erst simulieren
  ```

### Optionale Konfiguration (`config/sync.yaml`)

Standardmässig braucht man keine Konfigurationsdatei. Falls du den Remote-Namen, den Zielordner oder Ausschlüsse anpassen (oder den Auto-Sync abschalten) möchtest, lege `config/sync.yaml` an:

```yaml
enabled: auto                 # auto | true | false
remote: gdrive                # rclone-Remote-Name
remote_path: SliceThePrompt/projects
exclude:                      # optionale Ausschlüsse (leer = alles kopieren)
  - "history/**"
  # - "*.gcode"
```

Kurzfristig per Umgebungsvariable abschalten: `SLICETHEPROMPT_NO_SYNC=1`.

---

## 3. Optional: 2-Wege-Sync (Handy ↔ PC)

Wenn du Projekte am PC direkt im Explorer/Editor bearbeiten möchtest (z. B. über **Google Drive for Desktop** im Ordner `Meine Ablage/SliceThePrompt/projects` oder ebenfalls mit `rclone`), kannst du zusätzlich `rclone bisync` nutzen.

> **Sicherheitshinweis:** Für den Anfang ist der automatische **1-Wege-Upload (`copy`)** sicherer, weil gleichzeitige Bearbeitungen auf Handy und PC sich nicht gegenseitig überschreiben können. Nutze `bisync` erst, wenn du abwechselnd auf beiden Geräten editierst.

### Einmalige Initialisierung von `bisync`

Vor dem allerersten 2-Wege-Sync muss einmal `--resync` laufen, damit `rclone` den Ausgangszustand kennt:

```bash
# Erst mit --dry-run prüfen:
uv run slicetheprompt sync --bisync --resync --dry-run

# Dann initial abgleichen:
uv run slicetheprompt sync --bisync --resync
```

### Laufender 2-Wege-Sync (z. B. in `start_all.sh` oder per Cron)

Danach reicht jeweils ohne `--resync` (bei gleichzeitigen Änderungen auf Handy & PC gewinnt standardmässig die neuere Datei via `--conflict-resolve newer`, die ältere bleibt als `.conflict` erhalten):

```bash
uv run slicetheprompt sync --bisync
```

Oder als Dauerläufer im Hintergrund (synchronisiert alle 120 s und führt beim allerersten Start automatisch `--resync` aus):

```bash
# In start_all.sh (beim Start im Hintergrund):
uv run slicetheprompt sync --watch --interval 120 >> .slicetheprompt/sync.log 2>&1 &
```

Alternativ alle 5 Minuten per Cron im Debian-PRoot (`crontab -e`):

```cron
*/5 * * * * cd ~/Coding_Projects/slicetheprompt && uv run slicetheprompt sync --bisync >> .slicetheprompt/sync.log 2>&1
```

---

## 4. Fehlerbehebung

- **Token abgelaufen / `invalid_grant`:** Auf dem PC erneut `rclone authorize "drive"` ausführen und `token = ...` in `~/.config/rclone/rclone.conf` aktualisieren.
- **Sync startet nicht nach dem Build:** `uv run slicetheprompt sync --status` ausführen und `.slicetheprompt/sync.log` prüfen.
- **`bisync` meldet „cannot find prior Path1/Path2 listings“:** Einmal `uv run slicetheprompt sync --bisync --resync` ausführen.
