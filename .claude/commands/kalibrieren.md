---
description: Toleranzen für PLA oder PETG am eigenen Drucker messen und übernehmen
argument-hint: <pla|petg>
---
Kalibriere die Toleranzen für Material: $ARGUMENTS

1. `calibration_part` für das Material, dann `build_and_check` und das Render ansehen.
2. Wie bei /drucken slicen und nach ausdrücklichem „drucken“ starten.
3. Dem Nutzer erklären, was er nach dem Abkühlen mit dem Messschieber misst (je 2× messen, Mittelwert):
   - Block: Breite X, Tiefe Y, Höhe Z (Soll 20 / 20 / 10 mm)
   - Löcher Ø5, Ø8, Ø10 in halber Plattenhöhe, an der engsten Stelle
   - Stift Ø8 in halber Höhe
4. Sobald die Werte da sind: `apply_calibration` und die neuen Werte (hole_add, pin_sub, Genauigkeit)
   in einem Satz erklären. Hinweis: gilt ab dem nächsten Build automatisch.
