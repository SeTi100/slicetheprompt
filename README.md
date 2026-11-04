# slicetheprompt – Prompt-to-Print für den Creality Hi Combo

Du beschreibst ein Teil in Claude Code, Claude schreibt dafür CAD-Code (build123d), prüft die
Druckbarkeit, rendert eine Vorschau, slicet mit deinem Creality Print und druckt nach deinem
„drucken“ direkt auf dem Hi (192.168.0.27, Moonraker).

```
Prompt → spec.yaml + model.py → build123d → STEP/STL → Prüfung + Render
       → Creality-Print-CLI → G-Code + Kennzahlen → „drucken“ → Upload + Start über Moonraker
```

## Einrichtung (einmalig, ca. 10 Minuten)

1. **Claude Code installieren** (falls noch nicht vorhanden), in PowerShell:
   ```powershell
   irm https://claude.ai/install.ps1 | iex
   ```
   Danach ein neues Terminal öffnen und `claude --version` prüfen. Empfohlen: Git for Windows.
2. **Projekt einrichten**, in PowerShell im Ordner `slicetheprompt`:
   ```powershell
   cd $HOME\Coding_Projects\slicetheprompt
   powershell -ExecutionPolicy Bypass -File .\setup.ps1
   ```
   Das Skript installiert `uv` und Python 3.12 mit allen Paketen, findet Creality Print, sucht die
   Profile für „Creality Hi 0.4“ (PLA und PETG, 0,12/0,20/0,28 mm), slicet zur Probe einen
   20-mm-Würfel, testet die Verbindung zum Drucker, baut die Beispiel-Box und schreibt `.mcp.json`.
   - Creality Print wird nicht gefunden? `.\setup.ps1 -Exe "C:\…\CrealityPrint.exe"`
   - Test-Slice liefert keinen G-Code? **Creality Print schliessen** und erneut ausführen
     (das offene Programm kann den Kommandozeilen-Aufruf abfangen).
3. **Claude Code im Ordner starten:** `claude` (oder in der Claude-Desktop-App den Code-Bereich mit
   diesem Ordner öffnen). Beim ersten Start den Ordner als vertrauenswürdig bestätigen und den
   MCP-Server „printlab“ zulassen. Mit `/mcp` prüfen, dass printlab verbunden ist.

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

printlab liest das CFS über Moonraker: Platz (T1A–T4D), Material, Farbe, Restmenge und RFID-Code.
Über den Code wird das passende Creality-Print-Profil gewählt (z. B. 101001 → *Hyper PLA*).

- **Spule wählen** in `spec.yaml` oder beim Drucken: `filament: T1C`, `filament: schwarz` oder
  `filament: "#FF0000"`. Ohne Angabe nimmt printlab die Spule des Materials mit der grössten Restmenge.
- **Exaktes Profil** (z. B. ein Matt-PLA): `filament_profile: "CR-PLA Matte @Creality Hi 0.4 nozzle"`;
  alle Profile: `uv run printlab filaments --type PLA`.
- **Mehrfarbig:** in `model.py` `bodies = {"platte": …, "schrift": …}` statt `part` und in `spec.yaml`
  `filament: {platte: weiss, schrift: schwarz}`. printlab baut daraus eine 3MF mit einem Filament je
  Körper, slicet sie und prüft im G-Code, dass genau diese Spulen verwendet werden.
- Beim Druckstart wird erneut geprüft, ob dieselben Spulen noch auf denselben Plätzen liegen.
- Anzeigen: `uv run printlab cfs`, Zuordnung ohne Slicen: `uv run printlab plan <projekt>`.

## Ordner

| Pfad | Inhalt |
| --- | --- |
| `CLAUDE.md` | Arbeitsregeln für Claude |
| `config/printer.yaml` | Drucker-IP, Bauraum, Geometrie-Regeln, Toleranzen, Temperaturgrenzen |
| `config/calibration.yaml` | gemessene Toleranzen (nach `/kalibrieren`) |
| `config/slicer.yaml`, `config/slicer/` | gewählte Creality-Print-Profile (vom Setup erzeugt) |
| `lib/parts.py` | CAD-Bausteine mit Toleranzen: `screw_hole_cut`, `fit`, `nut_trap_cut`, … |
| `printlab/` | Prüfung, Render, Slicen, CFS, Drucker (MCP-Server und CLI) |
| `claude-setup/` | Vorlage für `.claude/` (Befehle, Freigaberegeln); `setup.ps1` kopiert sie |
| `projects/<datum>-<name>/` | je Teil: `spec.yaml`, `model.py`, `out/` mit STEP, STL, PNG, G-Code |

## Ohne Claude (Kommandozeile)

```powershell
uv run printlab new halter --material petg
uv run printlab run <projekt>            # bauen, prüfen, rendern
uv run printlab slice <projekt>          # gibt einen Freigabe-Token aus
uv run printlab print <token>            # hochladen und starten
uv run printlab status
```

## Git

Das Projekt ist ein Git-Repository (GitHub: `SeTi100/slicetheprompt`, privat). Nicht versioniert werden
`.venv`, `.mcp.json`, `.claude/`, die erzeugten Slicer-Profile und alle `out/`-Ordner.
Nach Änderungen an `claude-setup/` erneut `setup.ps1` ausführen.

## Profile ändern

`config/slicer.yaml` zeigt die gewählten Profile und Alternativen. Namen dort ändern und
`uv run printlab setup --keep-selection` ausführen.

## Grenzen

- Masshaltigkeit hängt vom Drucker ab: erst `/kalibrieren`, dann sind Passungen verlässlich.
- Mehrfarbdruck setzt voraus, dass Creality Print die Filamentzuordnung aus der 3MF übernimmt;
  das Setup testet das mit einem zweifarbigen Probe-Slice.
- Die Zuordnung Werkzeug → Platz (T0 = T1A …) ist aus der CFS-Meldung abgeleitet. Vor dem ersten
  mehrfarbigen Druck ein kleines Testteil drucken.
- Organische Formen (Figuren) sind bewusst nicht vorgesehen.
