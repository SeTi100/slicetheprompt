---
name: drucken
description: >-
  Wählt passende CFS-Spulen, slicet ein Projekt mit Creality Print CLI, zeigt Druckzeit, Filamentverbrauch
  und Vorschau in Spulenfarben und startet den Druck auf dem Creality Hi nach ausdrücklicher Bestätigung
  des Nutzers. Verwenden, wenn der Nutzer ein Projekt slicen oder drucken möchte (oder /drucken schreibt).
---

# Projekt slicen und sicher auf dem Creality Hi drucken

Arbeite strikt nach `AGENTS.md` (insbesondere Regel 8 – niemals ohne ausdrückliches „drucken“ starten):

1. **Prüfstatus kontrollieren:** Ist `out/report.json` nicht vorhanden, nicht bestanden (`passed: false`) oder älter als `model.py`/`spec.yaml`, führe zuerst `build_and_check` (`uv run slicetheprompt run <projekt>`) aus.
2. **Drucker & CFS prüfen:** `printer_status` (`uv run slicetheprompt status`) und `cfs_status` (`uv run slicetheprompt cfs`) abrufen. Ist der Drucker beschäftigt (`printing`/`paused`), abbrechen und melden.
3. **Spulen planen:** `filament_plan` (`uv run slicetheprompt plan <projekt> [--filament ...]`) prüfen. Passt eine gewünschte Farbe nicht zu den geladenen Spulen oder hat eine Spule < 15 % Restmenge, weise den Nutzer offen darauf hin und nenne Alternativen.
4. **Slicen:** `slice` (`uv run slicetheprompt slice <projekt> [...]`) ausführen. Ist `blocked` nicht leer, Gründe nennen und **nicht** drucken.
5. **Vorschau & Kennzahlen zeigen:**
   - `out/render.png` (jetzt in den echten Spulenfarben gerendert) mit `read_file` prüfen.
   - Dem Nutzer übersichtlich zeigen: je Körper **Spule → Werkzeug → Farbe → Profil**, Qualität/Schichthöhe, **Druckzeit**, **Filament in g** (gesamt und je Spule), **Kosten** (`kosten.total` in CHF, je Spule; steht dort „Standardpreis“, ist noch kein Rollenpreis hinterlegt → `filament_price` anbieten), Farbwechsel, Düse/Bett °C, Hinweise sowie den Freigabe-Token.
   - Ausdrücklich fragen: **„Ist das Druckbett frei? Antworte mit `drucken`, dann lade ich genau diesen G-Code hoch und starte den Druck.“**
6. **Warten auf Bestätigung:** Erst nachdem der Nutzer in seiner Antwort ausdrücklich „drucken“ (oder eine eindeutige Druckfreigabe für diesen Slice) geschrieben hat, `upload_and_print(approval_token)` (`uv run slicetheprompt print <token>`) aufrufen.
7. **Start kontrollieren:** Danach `printer_status` (`uv run slicetheprompt status`) abrufen und Zustand, Zieltemperaturen und Datei bestätigen.
