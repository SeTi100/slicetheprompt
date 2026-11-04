---
name: status
description: >-
  Zeigt den aktuellen Zustand des Creality Hi Combo (Druckstatus, Fortschritt, Restzeit, Düsen- und
  Betttemperatur) sowie optional die geladenen CFS-Spulen über Moonraker an. Verwenden, wenn der Nutzer
  nach dem Druckerstatus, Fortschritt, Temperaturen oder geladenem Filament fragt (oder /status schreibt).
---

# Drucker- und CFS-Status anzeigen

1. Rufe `printer_status` (`uv run printlab status`) und bei Fragen zum Filament zusätzlich `cfs_status` (`uv run printlab cfs`) auf.
2. Fasse das Ergebnis in 2–4 knappen Zeilen zusammen:
   - **Zustand** (`standby`, `printing`, `paused`, `complete`, `error`), geladene Datei, **Fortschritt in %**, verstrichene und verbleibende Zeit.
   - **Düse** und **Bett** (Ist / Soll in °C).
   - Bei einer Fehlermeldung des Druckers diese wörtlich nennen.
3. Ist der Drucker nicht erreichbar: darauf hinweisen zu prüfen, ob er eingeschaltet und im WLAN ist (IP in `config/printer.yaml`).
