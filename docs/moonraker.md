# Moonraker: was der Creality Hi lesbar liefert

Stand 2026-11-10, live am Hi geprüft (Moonraker-API 1.0.5, Version meldet „?“, Klipper „ready“,
ARMv7 mit 2 Kernen, 512 MB). `Moonraker.details()` fragt die Objekte dynamisch ab
(`/printer/objects/list`). Was eine andere Firmware nicht kennt, bleibt einfach leer.

```bash
uv run slicetheprompt details
```

### Creality-Eigenheiten (live gesehen)
- **Kein `fan`-Objekt.** Die Lüfter laufen als `output_pin fan0`, `fanp0`, `board_fan` (Wert 0–1), dazu
  `heater_fan hotend_fan` und `fan_feedback` (fan0…fan4_speed). `printer_status.fan_pct` bleibt am Hi leer.
- **Sensoren:** nur `temperature_sensor mcu_temp` (Platine). Die CFS-Einheit meldet in `box.T1`
  Temperatur und Feuchte (`temperature`, `dry_and_humidity`). Eine CPU-Temperatur gibt es nicht.
- **5 Filamentsensoren** (`filament_switch_sensor filament_sensor`, `_2` … `_5`, der erste ist deaktiviert).
- **`output_pin LED`** = Bauraumlicht an/aus.
- **Wartungszähler** in `gcode_macro MAINTENANCE_ITEM`: je Punkt `cur_value`, `threshold` und `timeout`
  (= fällig), z. B. Führungen schmieren, Riemen, Düse, Schneidmesser, PTFE-Schläuche, Luftfilter,
  Trockenmittel CFS. Die Werte sind meist Druckzeit in s (912 600 s ≈ 253 h = Druckzeit gesamt).
  Bei Düse und Messer ist die Einheit unklar (Düse 117 von 20 000, Messer 127 Schnitte). Das Studio
  zeigt deshalb nur Prozent.
- **Keine Kamera** in `/server/webcams/list`. Port 4408 liefert die Creality-Weboberfläche (Fluidd).
- **Druckhistorie** mit Dateinamen `stp_<datum>-<zeit>_<projekt>…` (Präfix von slicetheprompt);
  bisher 73 Drucke, 252,6 h, 2 576 m Filament.
- `print_stats.info.current_layer/total_layer` ist im Leerlauf `null`, beim Druck gefüllt (Statusleiste).
- Weitere Creality-Objekte ohne Auswertung: `prtouch_v3`, `steer`, `motor_control`, `z_tilt`,
  `axis_twist_compensation`, `bed_mesh`, sowie viele `gcode_macro` (z. B. `PRINTER_PARAM`, `product_param`).

## Schon genutzt

| Quelle | Inhalt | Wo |
|---|---|---|
| `print_stats` | Zustand, Datei, Druckdauer, **Schicht x/y** (`info.current_layer/total_layer`), verbrauchtes Filament | Statusleiste, `printer_status` |
| `extruder`, `heater_bed` | Ist-/Soll-Temperatur | Statusleiste (Ist/Soll) |
| `display_status`, `virtual_sdcard` | Fortschritt → Restzeit | Statusleiste |
| `fan`, `gcode_move` | Bauteillüfter %, Speed-/Flow-Faktor | `printer_status` |
| `box`, `filament_rack` (Creality) | CFS-Spulen, Farbe, Rest, Feuchte/Temperatur je Einheit | Statusleiste, `cfs_status` |

## Neu im Studio (Tab „Kosten“ → Drucker-Details) und als `printer_details`

| Endpunkt / Objekt | Inhalt |
|---|---|
| `temperature_sensor *`, `temperature_fan *` | Kammer, Hauptplatine, MCU, CFS usw. (je nach Firmware) |
| `heater_fan *`, `output_pin *fan*` | Hotend-, Bauteil-, Platinenlüfter in % |
| `gcode_macro MAINTENANCE_ITEM` | Wartungszähler in % (fällig = rot) |
| `box` | Temperatur und Feuchte je CFS-Einheit |
| `output_pin LED` | Licht an/aus |
| `filament_switch_sensor *`, `filament_motion_sensor *` | Filament erkannt ja/nein, Sensor an/aus |
| `toolhead` | referenzierte Achsen, Position, max. Geschwindigkeit/Beschleunigung |
| `motion_report` | Live-Geschwindigkeit (mm/s) und Volumenfluss (mm³/s) |
| `exclude_object` | Objekte im laufenden Druck, ausgelassene Objekte |
| `system_stats` | CPU-Temperatur, Last, freier Speicher |
| `/server/history/totals` | Anzahl Drucke, Druckstunden, Filament gesamt, längster Druck |
| `/server/history/list` | letzte 5 Drucke mit Status, Dauer, Filament |
| `/server/webcams/list` | Kamera-Snapshot (am Hi derzeit leer; erscheint automatisch, falls eine Kamera dazukommt) |
| `/server/info`, `/machine/system_info` | Moonraker-Version, Klipper-Zustand, CPU, Betriebssystem |

## Weitere Möglichkeiten (noch nicht eingebaut)

- **Thumbnails und Metadaten** (`/server/files/metadata?filename=…`): geschätzte Zeit, Gramm, Slicer und
  Vorschaubild der Dateien auf dem Drucker, z. B. für eine Liste „Dateien auf dem Hi“.
- **Live-Updates per WebSocket** (`/websocket`, `printer.objects.subscribe`) statt Abfrage alle 4 s:
  flüssigere Temperaturkurven, weniger Last.
- **Temperaturverlauf** (`/server/temperature_store`): letzte ~20 min aller Heizungen/Sensoren → Diagramm.
- **G-Code-Konsole lesen** (`/server/gcode_store`): letzte Meldungen und Fehler von Klipper.
- **Bed Mesh** (`bed_mesh`-Objekt): Höhenkarte des Betts als 3D-Fläche im Studio.
- **Gerätezähler** (`/server/history/totals`, Creality-Objekte), z. B. für Wartungshinweise wie
  „Düse nach X Druckstunden prüfen“.
- **Job-Warteschlange** (`/server/job_queue/status`), falls die Creality-Firmware sie aktiviert hat.
- **Benachrichtigungen** (`notify_status_update` über den WebSocket): Studio-Toast bei „Druck fertig“.

Alles davon ist rein lesend. Schreibende Aufrufe (Start, Pause, Abbruch, G-Code senden) bleiben wie
bisher dem Chat vorbehalten (AGENTS.md, Regel 8).
