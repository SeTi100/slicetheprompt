# slicetheprompt – Prompt-to-Print für den Creality Hi Combo

Du beschreibst ein Teil in Claude Code, Claude schreibt dafür CAD-Code (build123d), prüft die
Druckbarkeit, rendert eine Vorschau, slicet mit deinem Creality Print und druckt nach deinem
„drucken“ direkt auf dem Hi (192.168.0.27, Moonraker).

```
Prompt → spec.yaml + model.py → build123d → STEP/STL → Prüfung + Render
       → Creality-Print-CLI → G-Code + Kennzahlen → „drucken“ → Upload + Start über Moonraker
```

## Einrichtung (einmalig, ca. 10 Minuten)

Voraussetzungen: Windows 11, Creality Print 7.x mit eingerichtetem Creality Hi, Drucker im selben
Netz (Standard-IP `192.168.0.27`, sonst in `config/printer.yaml` ändern), Git und Claude Code
(`irm https://claude.ai/install.ps1 | iex`).

```powershell
git clone https://github.com/SeTi100/slicetheprompt
cd slicetheprompt
powershell -ExecutionPolicy Bypass -File .\setup.ps1
claude
```

Das Setup installiert `uv` (eigenständig, nicht nur aus conda, damit die Claude-App es findet),
Python 3.12 und alle Pakete, installiert Creality Print per winget, falls es fehlt, löst die Profile
für „Creality Hi 0.4“ auf (PLA/PETG, 0,12/0,20/0,28 mm), testet Drucker und CFS, slicet zur Probe
ein- und zweifarbig und baut die Beispiele. Danach `claude` im Ordner starten, den Ordner als
vertrauenswürdig bestätigen und den MCP-Server „slicetheprompt“ zulassen (`/mcp` zeigt den Status).

- Ohne Setup geht es auch: Claude Code startet slicetheprompt über die `.mcp.json` mit `uv`, und der
  Slicer richtet sich beim ersten Slicen selbst ein. Der erste Start dauert dann einige Minuten.
- **Claude-Desktop-App** statt Claude Code: App ganz beenden, `uv run slicetheprompt install claude-desktop`,
  App starten. Der Befehl trägt slicetheprompt mit absolutem Pfad ein und legt eine Sicherung an.
- slicetheprompt in Claude Code „failed to connect“? Meist liegt `uv` nur in einer conda-Umgebung:
  `.\setup.ps1` erneut ausführen (installiert ein eigenes `uv`), dann die App neu starten.
- Creality Print wird nicht gefunden? `.\setup.ps1 -Exe "C:\…\CrealityPrint.exe"`
- Test-Slice liefert keinen G-Code? **Creality Print schliessen** und erneut ausführen.
- Zustand prüfen: `uv run slicetheprompt doctor` (oder im Chat: „doctor“).

**Ältere Klone aktualisieren** (vor dem 4.11.2026 18:30 eingerichtet): `.mcp.json` und `.claude\` waren
damals erzeugte Dateien und sind jetzt versioniert. Einmalig:
```powershell
Remove-Item .mcp.json; Remove-Item -Recurse .claude
git pull
```

## Benutzung

| Eingabe | Was passiert |
| --- | --- |
| `/teil Wandhalter für Ladegerät 68 × 32 × 15 mm, 2 Löcher für 4-mm-Holzschrauben, PETG` | Spezifikation, CAD-Modell, Prüfung, Render – endet vor dem Slicen |
| `/drucken` | Spulen wählen, slicen, Druckzeit/Filament zeigen, auf „drucken“ warten, hochladen, starten |
| `/drucken schwarz` oder `/drucken T1C` | mit genau dieser Spule (Farbe oder CFS-Platz) |
| `/status` | Fortschritt, Temperaturen, Restzeit |
| `/kalibrieren pla` | Toleranz-Testteil drucken, messen, Werte übernehmen |
| freier Text | z. B. „Mach den Halter 5 mm höher und druck ihn nochmal“ |
| `/teil Namensschild 60 × 25 mm, weiss mit schwarzer Schrift „TIM“` | zweifarbig über das CFS |

Vor jedem Druckstart fragt Claude Code zusätzlich per Bestätigungsdialog (Regel in
`.claude/settings.json`); der Druck startet nur mit dem Token genau des gezeigten G-Codes.

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
| `CLAUDE.md` | Arbeitsregeln für Claude |
| `config/printer.yaml` | Drucker-IP, Bauraum, Geometrie-Regeln, Toleranzen, Temperaturgrenzen |
| `config/calibration.yaml` | gemessene Toleranzen (nach `/kalibrieren`) |
| `config/slicer.yaml`, `config/slicer/` | gewählte Creality-Print-Profile (vom Setup erzeugt) |
| `lib/parts.py` | CAD-Bausteine mit Toleranzen: `screw_hole_cut`, `fit`, `nut_trap_cut`, … |
| `slicetheprompt/` | Prüfung, Render, Slicen, CFS, Drucker (MCP-Server und CLI) |
| `.claude/` | Befehle `/teil`, `/drucken`, `/status`, `/kalibrieren` und Freigaberegeln für Claude Code |
| `.mcp.json` | startet slicetheprompt für Claude Code über `uv` (ohne absolute Pfade) |
| `projects/<datum>-<name>/` | je Teil: `spec.yaml`, `model.py`, `out/` mit STEP, STL, PNG, G-Code |

## Ohne Claude (Kommandozeile)

```powershell
uv run slicetheprompt ui                       # SliceThePrompt (3D-Viewer, Fehler-Overlays, Versionen & Markierung)
uv run slicetheprompt new halter --material petg
uv run slicetheprompt run <projekt>            # bauen, prüfen, rendern
uv run slicetheprompt slice <projekt>          # gibt einen Freigabe-Token aus
uv run slicetheprompt print <token>            # hochladen und starten
uv run slicetheprompt status
```

## Git

Das Projekt ist ein Git-Repository (GitHub: `SeTi100/slicetheprompt`, öffentlich). Nicht versioniert werden
`.venv`, die rechnerspezifische Slicer-Konfiguration (`config/slicer.yaml`, `config/slicer/`),
`.claude/settings.local.json` und alle `out/`-Ordner. Für eigene Commits die GitHub-noreply-Adresse
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
