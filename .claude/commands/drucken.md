---
description: Aktuelles Projekt slicen, Kennzahlen zeigen und nach Bestätigung auf dem Hi drucken
argument-hint: [projekt] [pla|petg] [fein|standard|grob] [Spule/Farbe, z. B. T1C oder schwarz]
---
Drucke ein Projekt auf dem Creality Hi nach AGENTS.md, Regel 8.

Argumente: $ARGUMENTS  (ohne Projekt: das zuletzt bearbeitete; ohne Material/Qualität: aus spec.yaml)

1. Ist `out/report.json` nicht bestanden oder veraltet → zuerst `build_and_check`.
2. `printer_status`: Drucker erreichbar und nicht beschäftigt?
3. `filament_plan` (mit Spulen-/Farbwunsch aus den Argumenten, sonst spec.yaml). Passt eine Farbe
   nicht oder ist eine Spule fast leer: dem Nutzer sagen und Alternative vorschlagen.
4. `slice` mit denselben Angaben. Ist `blocked` nicht leer: Gründe nennen, nicht drucken.
5. Dem Nutzer zeigen: je Körper Spule → Farbe → Profil, Qualität/Schichthöhe, Druckzeit,
   Filament in g (je Spule), Farbwechsel, Düse/Bett °C, Hinweise, Bild (`render.png` in Spulenfarben
   mit Read ansehen). Fragen: „Bett frei? Mit ‚drucken‘ starte ich genau diese Datei.“
6. **Warten.** Erst wenn der Nutzer ausdrücklich „drucken“ (oder eindeutig gleichbedeutend) antwortet:
   `upload_and_print` mit dem Token aus Schritt 4.
7. `printer_status` zeigen (Zustand, Temperaturen, Restzeit).
