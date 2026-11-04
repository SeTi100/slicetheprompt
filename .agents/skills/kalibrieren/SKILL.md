---
name: kalibrieren
description: >-
  Erzeugt und druckt das Toleranz-Kalibrierteil (Block 20×20×10 mm, Löcher Ø5/8/10 mm, Stift Ø8 mm)
  für PLA oder PETG und übernimmt gemessene Messschieber-Werte in config/calibration.yaml. Verwenden,
  wenn der Nutzer den Drucker, die Masshaltigkeit oder Toleranzen kalibrieren oder einen Kalibrierwürfel
  bzw. ein Kalibrierteil drucken möchte (oder /kalibrieren schreibt).
---

# Toleranzen für PLA oder PETG kalibrieren

1. **Kalibrierprojekt anlegen & bauen (falls noch nicht vorhanden):**
   - `calibration_part(material)` (`uv run printlab calib <pla|petg>`), Standard `pla`.
   - `build_and_check` (`uv run printlab run <projekt>`) ausführen und `out/render.png` mit `read_file` prüfen.
2. **Slicen & Druckfreigabe einholen (wie im Skill `drucken`):**
   - `printer_status` und `cfs_status` prüfen.
   - `slice` (`uv run printlab slice <projekt>`) ausführen, Kennzahlen (Spule, Druckzeit, Gramm, Temperaturen) zeigen und fragen, ob das Druckbett frei ist.
   - **Erst nach ausdrücklichem „drucken“** des Nutzers `upload_and_print(token)` (`uv run printlab print <token>`) starten.
3. **Messanleitung mitgeben:** Erkläre kurz, was nach dem vollständigen Abkühlen mit dem Messschieber gemessen wird (je 2× messen, Mittelwert):
   - `block_x`, `block_y`, `block_z`: Breite X, Tiefe Y, Höhe Z des Blocks (Soll: `20.0` / `20.0` / `10.0` mm)
   - `loch5`, `loch8`, `loch10`: Innendurchmesser der drei Löcher auf halber Plattenhöhe an der engsten Stelle (Soll: `5.0` / `8.0` / `10.0` mm)
   - `stift8`: Aussendurchmesser des Stifts auf halber Höhe (Soll: `8.0` mm)
4. **Messwerte übernehmen:** Sobald der Nutzer die 7 Messwerte nennt, `apply_calibration` (`uv run printlab applycal <pla|petg> block_x=... ...`) aufrufen und die berechneten Korrekturwerte (`hole_add`, `pin_sub`, `outer_offset`, `accuracy` in `config/calibration.yaml`) kurz erläutern. Sie gelten ab dem nächsten Build automatisch.
