# SliceThePrompt – Prompt-to-Print für den Creality Hi Combo

Du beschreibst ein Teil in einem KI-Agenten deiner Wahl (Claude Code, Codex, Antigravity, Claude Desktop).
Der Agent schreibt dafür CAD-Code (build123d), prüft die Druckbarkeit, rendert eine Vorschau, slicet mit
deinem Creality Print und druckt nach deinem „drucken“ direkt auf dem Hi (192.168.0.27, Moonraker).

```
Prompt → spec.yaml + model.py → build123d → STEP/STL → Prüfung + Render
       → Creality-Print-CLI → G-Code + Kennzahlen → „drucken“ → Upload + Start über Moonraker
```

Die ganze Druck-Logik steckt im MCP-Server `slicetheprompt` (Python). Jeder Agent, der MCP spricht,
kann ihn nutzen. Die Anbieter unterscheiden sich nur darin, wo der Server eingetragen wird und wie
Regeln und Befehle heissen.

## Unterstützte Agenten

| Agent | Regeln | Befehle / Skills | MCP-Eintrag | Getestet |
| --- | --- | --- | --- | --- |
| **Claude Code** (CLI, Desktop-App „Code“, IDE) | `CLAUDE.md` | `.claude/commands/` → `/teil`, `/drucken` … | `.mcp.json` im Projekt (versioniert) | ja |
| **Codex** (CLI, App, IDE) | `AGENTS.md` | `.agents/skills/` → `$teil`, `$drucken` … | `~/.codex/config.toml` | Konfiguration ja, Betrieb noch offen |
| **Antigravity** | `AGENTS.md` | `.agents/skills/` | `~/.gemini/config/mcp_config.json` | ja |
| **Claude Desktop** (Chat) | Anweisungen des Servers | – | `claude_desktop_config.json` | ja |

## Einrichtung (einmalig, ca. 10 Minuten)

Voraussetzungen: Windows 11, Creality Print 7.x mit eingerichtetem Creality Hi, Drucker im selben
Netz (Standard-IP `192.168.0.27`, sonst in `config/printer.yaml` ändern), Git und mindestens einer
der Agenten oben.

```powershell
git clone https://github.com/SeTi100/slicetheprompt
cd slicetheprompt
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

Das Setup installiert `uv` (eigenständig, nicht nur aus conda, damit Desktop-Apps es finden),
Python 3.12 und alle Pakete, installiert Creality Print per winget, falls es fehlt, löst die Profile
für „Creality Hi 0.4“ auf (PLA/PETG, 0,12/0,20/0,28 mm), testet Drucker und CFS, slicet zur Probe
ein- und zweifarbig und baut die Beispiele. Sind Codex oder Antigravity schon installiert, trägt es
den MCP-Server dort gleich ein. Danach den Server für deinen Agenten prüfen (nächster Abschnitt).

- Creality Print wird nicht gefunden? `.\setup.ps1 -Exe "C:\…\CrealityPrint.exe"`
- Test-Slice liefert keinen G-Code? **Creality Print schliessen** und erneut ausführen.
- Zustand prüfen: `uv run slicetheprompt doctor` (oder im Chat: „doctor“).

## MCP-Server einrichten

Für jeden Agenten gibt es einen Befehl. Er schreibt den Eintrag, legt vorher eine Sicherung an
(`*.bak-<datum>`) und entfernt einen alten Eintrag `printlab` (früherer Name):

```powershell
uv run slicetheprompt install claude-code      # nichts zu tun, zeigt nur den Hinweis
uv run slicetheprompt install codex            # schreibt ~/.codex/config.toml
uv run slicetheprompt install antigravity      # schreibt ~/.gemini/config/mcp_config.json
uv run slicetheprompt install claude-desktop   # App vorher ganz beenden!
```

Mit `--config <pfad>` lässt sich eine andere Konfigurationsdatei angeben. Manche Aufrufe (erstes
Slicen, grosse Modelle) dauern mehrere Minuten; die Einträge setzen darum längere Timeouts als die
Standardwerte der Agenten.

### Claude Code

Nichts eintragen: Claude Code liest die versionierte [`.mcp.json`](.mcp.json) im Projektordner und
startet den Server über `uv`. [`.claude/settings.json`](.claude/settings.json) gibt ihn frei und legt
fest, welche Werkzeuge nachfragen (Druck starten, abbrechen, Kalibrierung übernehmen).

1. Im Projektordner `claude` starten (oder den Ordner in der Desktop-App öffnen).
2. **Den Ordner als vertrauenswürdig bestätigen.** Sonst ignoriert Claude Code die Freigabe in
   `.claude/settings.json`, und der Server bleibt auf „Pending approval“.
3. `/mcp` zeigt `slicetheprompt · connected`. Falls nicht: dort auswählen → *Reconnect*.

Typische Probleme:

- **Eintrag wird nicht übernommen.** `claude mcp add …` schreibt standardmässig in den *lokalen*
  Bereich (`~/.claude.json`, nur für genau den Ordnerpfad, in dem der Befehl lief), nicht in die
  `.mcp.json`. Für dieses Projekt `claude mcp add` nicht verwenden: ein zweiter Eintrag mit gleichem
  Namen verdeckt die `.mcp.json`. Prüfen mit `claude mcp list`, entfernen mit
  `claude mcp remove slicetheprompt -s local` (bzw. `-s user`).
- **Früher abgelehnt?** `claude mcp reset-project-choices`, dann Claude Code neu starten.
- **„failed to connect“**: Meist liegt `uv` nur in einer conda-Umgebung. `.\setup.ps1` erneut
  ausführen (installiert ein eigenes `uv`), dann Claude Code neu starten.
- **Ältere Klone** (vor dem 4.11.2026 18:30 eingerichtet): `.mcp.json` und `.claude\` waren damals
  erzeugte Dateien. Einmalig `Remove-Item .mcp.json; Remove-Item -Recurse .claude; git pull`.

### Codex (CLI, App, IDE)

Codex liest `AGENTS.md` und die Skills in `.agents/skills/` von selbst. Den MCP-Server trägt
`uv run slicetheprompt install codex` in `~/.codex/config.toml` ein; CLI, App und IDE-Erweiterung
teilen diese Datei. Danach Codex neu starten und mit `/mcp` (CLI) oder `codex mcp list` prüfen.

Von Hand (Pfade anpassen):

```toml
[mcp_servers.slicetheprompt]
command = 'C:\Users\<du>\…\slicetheprompt\.venv\Scripts\python.exe'
args = ["-m", "slicetheprompt.mcp_server"]
startup_timeout_sec = 120     # Standard 10 s – zu kurz für den ersten Start
tool_timeout_sec = 900        # Standard 60 s – zu kurz fürs Slicen
env_vars = ["APPDATA", "LOCALAPPDATA", "USERPROFILE", "PROGRAMFILES", "TEMP"]

[mcp_servers.slicetheprompt.env]
PYTHONPATH = 'C:\Users\<du>\…\slicetheprompt'
PYTHONIOENCODING = "utf-8"
PYTHONUTF8 = "1"

[mcp_servers.slicetheprompt.tools.upload_and_print]
approval_mode = "prompt"      # vor jedem Druckstart nachfragen (ebenso print_control, apply_calibration)
```

Zu beachten:

- Codex startet MCP-Server unter Windows mit einer **gekürzten Umgebung**. `env_vars` reicht die
  Variablen durch, die `uv` und die Slicer-Suche brauchen.
- Befehle sind in Codex Skills: `$teil Wandhalter …`, `$drucken`, `$status`, `$kalibrieren pla`.
  Oder einfach frei beschreiben, Codex wählt den Skill selbst.
- Den Projektordner beim ersten Start als vertrauenswürdig markieren, damit Codex in `projects/`
  schreiben und `uv run …` ausführen darf.
- Codex muss die Vorschau `out/render.png` ansehen. Sieht es das Bild nicht, den Pfad im Chat nennen
  oder das Bild anhängen.

### Antigravity

`uv run slicetheprompt install antigravity` schreibt den Eintrag in
`~/.gemini/config/mcp_config.json`, mit `timeoutSeconds: 900` (Standard: 180 s je Aufruf). Danach
in Antigravity **Customizations → MCP Servers → Refresh**; dort zeigt *Copy Config File Path* den
Pfad der Datei. Regeln (`AGENTS.md`) und Skills (`.agents/skills/`) liest Antigravity aus dem Workspace.

Von Hand:

```json
{
  "mcpServers": {
    "slicetheprompt": {
      "command": "C:\\Users\\<du>\\…\\slicetheprompt\\.venv\\Scripts\\python.exe",
      "args": ["-m", "slicetheprompt.mcp_server"],
      "env": {
        "PYTHONPATH": "C:\\Users\\<du>\\…\\slicetheprompt",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1"
      },
      "timeoutSeconds": 900
    }
  }
}
```

### Claude Desktop (Chat, ohne „Code“)

App **ganz** beenden (Infobereich → Beenden; sie überschreibt ihre Konfiguration beim Schliessen),
dann `uv run slicetheprompt install claude-desktop` und die App starten. Der Befehl findet auch die
Store-Version (MSIX) mit ihrem umgeleiteten Konfigurationsordner. Einstellungen → Entwickler zeigt,
ob `slicetheprompt` läuft.

### Nach Updates oder Umbenennungen

Ein noch laufender alter Server sperrt seine Dateien in `.venv` (`uv sync` meldet „Der Prozess kann
nicht auf die Datei zugreifen“, os error 32). Alle Agenten schliessen, die ihn gestartet haben, oder
in PowerShell:

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*slicetheprompt*mcp*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
uv sync
```

### Alternativen zum manuellen Eintrag

| Weg | Für wen | Bewertung hier |
| --- | --- | --- |
| **Projektdatei**: `.mcp.json` (Claude Code), `.codex/config.toml` (Codex, nur vertrauenswürdige Projekte), `.agents/mcp_config.json` (Antigravity) | alle drei | Claude Code: genutzt. Codex/Antigravity: möglich, aber in welchem Ordner der Server startet, ist dort nicht festgelegt – darum der Installer mit absoluten Pfaden. |
| **MCPB** (früher **DXT**, `.mcpb`-Datei per Doppelklick) | nur Claude Desktop | Lohnt sich nicht: Das Bündel müsste Python, OpenCascade und den Projektordner enthalten, und die Teile liegen ohnehin im Git-Klon. |
| **Plugin / Marketplace**: Claude Code `/plugin`, Codex `codex plugin marketplace add`, Antigravity-Plugins mit eigener `mcp_config.json` | je Anbieter | Gut für Werkzeuge ohne eigene Daten. SliceThePrompt braucht den Klon mit `projects/` und `config/`; ein Plugin würde nur den Eintrag ersparen, den der Installer schon schreibt. |
| **`claude mcp add` / `codex mcp add`** | Claude Code, Codex | Geht, setzt aber keine Timeouts und keine Rückfrage je Werkzeug. Bei Claude Code entsteht leicht ein verdeckender zweiter Eintrag. |

## Benutzung

Die Beispiele zeigen die Schreibweise von Claude Code. In Codex `$teil` statt `/teil`, in Antigravity
und Claude Desktop einfach den Wunsch beschreiben („Konstruiere einen Wandhalter …“).

| Eingabe | Was passiert |
| --- | --- |
| `/teil Wandhalter für Ladegerät 68 × 32 × 15 mm, 2 Löcher für 4-mm-Holzschrauben, PETG` | Spezifikation, CAD-Modell, Prüfung, Render – endet vor dem Slicen |
| `/drucken` | Spulen wählen, slicen, Druckzeit/Filament zeigen, auf „drucken“ warten, hochladen, starten |
| `/drucken schwarz` oder `/drucken T1C` | mit genau dieser Spule (Farbe oder CFS-Platz) |
| `/status` | Fortschritt, Temperaturen, Restzeit |
| `/kalibrieren pla` | Toleranz-Testteil drucken, messen, Werte übernehmen |
| `/schablone halter` oder `/stencil halter` | dünne Scheibe (2 mm) aus dem Teil als schneller Passungs-Testdruck; z. B. `/schablone halter bei z 0 und 12` |
| freier Text | z. B. „Mach den Halter 5 mm höher und druck ihn nochmal“ |
| `/teil Namensschild 60 × 25 mm, weiss mit schwarzer Schrift „TIM“` | zweifarbig über das CFS |

Vor jedem Druckstart fragt der Agent zusätzlich per Bestätigungsdialog (Claude Code:
`.claude/settings.json`, Codex: `approval_mode = "prompt"`). Der Druck startet nur mit dem Token
genau des gezeigten G-Codes.

## Filament, CFS und Mehrfarbdruck

slicetheprompt liest das CFS über Moonraker: Platz (T1A–T4D), Material, Farbe, Restmenge und RFID-Code.
Über den Code wird das passende Creality-Print-Profil gewählt (z. B. 101001 → *Hyper PLA*).

- **Spule wählen** in `spec.yaml` oder beim Drucken: `filament: T1C`, `filament: schwarz` oder
  `filament: "#FF0000"`. Ohne Angabe nimmt slicetheprompt die Spule des Materials mit der grössten Restmenge.
- **Exaktes Profil** (z. B. ein Matt-PLA): `filament_profile: "CR-PLA Matte @Creality Hi 0.4 nozzle"`;
  alle Profile: `uv run slicetheprompt filaments --type PLA`.
- **Mehrfarbig:** in `model.py` `bodies = {"platte": …, "schrift": …}` statt `part` und in `spec.yaml`
  `filament: {platte: weiss, schrift: schwarz}`. slicetheprompt baut daraus eine 3MF mit einem Filament je
  Körper, slicet sie und prüft im G-Code, dass genau diese Spulen verwendet werden.
- Beim Druckstart wird erneut geprüft, ob dieselben Spulen noch auf denselben Plätzen liegen.
- Anzeigen: `uv run slicetheprompt cfs`, Zuordnung ohne Slicen: `uv run slicetheprompt plan <projekt>`.

## Ordner

| Pfad | Inhalt |
| --- | --- |
| `CLAUDE.md` | Arbeitsregeln für Claude Code |
| `AGENTS.md` | dieselben Regeln für Codex, Antigravity und andere Agenten |
| `config/printer.yaml` | Drucker-IP, Bauraum, Geometrie-Regeln, Toleranzen, Temperaturgrenzen |
| `config/calibration.yaml` | gemessene Toleranzen (nach `/kalibrieren`) |
| `config/slicer.yaml`, `config/slicer/` | gewählte Creality-Print-Profile (vom Setup erzeugt) |
| `lib/parts.py` | CAD-Bausteine mit Toleranzen: `screw_hole_cut`, `fit`, `nut_trap_cut`, … |
| `slicetheprompt/` | Prüfung, Render, Slicen, CFS, Drucker (MCP-Server und CLI) |
| `.claude/` | Befehle `/teil`, `/drucken`, `/status`, `/kalibrieren`, `/schablone` (`/stencil`) und Freigaberegeln für Claude Code |
| `.agents/skills/` | dieselben Abläufe als Skills für Codex und Antigravity |
| `.mcp.json` | startet slicetheprompt für Claude Code über `uv` (ohne absolute Pfade) |
| `projects/<datum>-<name>/` | je Teil: `spec.yaml`, `model.py`, `out/` mit STEP, STL, PNG, G-Code |

## Ohne KI-Agent (Kommandozeile)

```powershell
uv run slicetheprompt ui                       # SliceThePrompt (3D-Viewer, Fehler-Overlays, Versionen & Markierung)
uv run slicetheprompt new halter --material petg
uv run slicetheprompt run <projekt>            # bauen, prüfen, rendern
uv run slicetheprompt schablone <projekt> --z 0 12 --hoehe 2   # Passungs-Testdruck (Alias: stencil)
uv run slicetheprompt slice <projekt>          # gibt einen Freigabe-Token aus
uv run slicetheprompt print <token>            # hochladen und starten
uv run slicetheprompt status
```

## Git

Das Projekt ist ein Git-Repository (GitHub: `SeTi100/slicetheprompt`, öffentlich). Nicht versioniert werden
`.venv`, die rechnerspezifische Slicer-Konfiguration (`config/slicer.yaml`, `config/slicer/`),
`.claude/settings.local.json` und alle `out/`-Ordner. **Eigene Teile bleiben lokal:** `projects/` ist
ignoriert, nur die Beispiele (`*-beispiel-*`, `*-kalibrierung-*`, `*-wuerfel-5mm`) sind im Repo. Für eigene Commits die GitHub-noreply-Adresse
verwenden: `git config user.email "<ID>+<name>@users.noreply.github.com"`.

## Profile ändern

`config/slicer.yaml` zeigt die gewählten Profile und Alternativen. Namen dort ändern und
`uv run slicetheprompt setup --keep-selection` ausführen.

## Grenzen

- Masshaltigkeit hängt vom Drucker ab: erst `/kalibrieren`, dann sind Passungen verlässlich.
- Mehrfarbdruck setzt voraus, dass Creality Print die Filamentzuordnung aus der 3MF übernimmt;
  das Setup testet das mit einem zweifarbigen Probe-Slice.
- Die Zuordnung Werkzeug → Platz (T0 = T1A …) ist aus der CFS-Meldung abgeleitet. Vor dem ersten
  mehrfarbigen Druck ein kleines Testteil drucken.
- Organische Formen (Figuren) sind bewusst nicht vorgesehen.
