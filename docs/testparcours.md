# Testparcours – Regeln aus Erfahrung statt aus Einzelfällen

Ziel: 16 Testteile quer durch typische Anwendungen bauen lassen und **jede Reibung gleich protokollieren**.
Daraus werden danach nur die Lehren zu Regeln, die in mehreren Bereichen auftauchen. Alles andere
gehört in Bausteine oder in die Prüfung (siehe „Auswertung“).

## Grundsatz: wohin gehört eine Lehre?

| Art | Wann | Wohin | Beispiel |
| --- | --- | --- | --- |
| `baustein` | Wissen über eine Teilart (Zahnrad, Gewinde, Scharnier) | Funktion in `lib/` mit Docstring – erscheint automatisch in `get_config → bausteine` | „Modul 1: Fase geht nicht“ → steckt in `gear_solid` |
| `pruefung` | Fehler, den die Geometrie verrät | `meshcheck`/`zones` mit Hinweis | „Fase an z = 0 doppelt“ → Zonen-Hinweis |
| `regel` | Prinzip, das in **≥ 2 Bereichen** gilt | `AGENTS.md` (kurz, allgemein) | „Anbauten überlappen lassen, nicht nur berühren“ |
| `ablauf` | Reihenfolge, Rückfrage, Zusammenfassung | Skill `teil` | Merkliste für Rückfragen |
| `nutzer` | Information, die nur der Nutzer hat | Merkliste/Rückfrage | „Ø gemessen oder Nennmass?“ |

`AGENTS.md` bleibt damit kurz. Spezialwissen wächst in `lib/` und in der Prüfung, wo es der Agent nur
dann sieht, wenn er es braucht.

## Parallelbetrieb – geprüft

Geht im gleichen Checkout mit **4 Sessions gleichzeitig** (12 Kerne; jeder Build läuft als eigener
Prozess je Projekt, jede Session startet ihren eigenen MCP-Server).

| Thema | Befund | Folge für den Parcours |
| --- | --- | --- |
| Worktrees | `projects/` und `config/slicer.yaml` sind gitignored, `PROJECTS_DIR` hängt am Checkout → ein Worktree sieht keine Projekte und hat keinen Slicer | **kein Worktree**, alle im Haupt-Checkout |
| Projektnamen | `project_dir` erkennt Teilnamen (`*name*`); bei parallel entstehenden Projekten kann das mehrdeutig werden | immer den **vollen Ordnernamen** aus `project_new` verwenden |
| Builds | eigener Unterprozess, Ausgaben nur in `projects/<projekt>/out/`, Versionen in `…/history/` | unkritisch |
| Slicen | `.slicetheprompt/approvals.json` wird ohne Sperre gelesen/geschrieben; Creality-Print-CLI mehrfach parallel ungetestet | **nicht slicen, nicht drucken** |
| Gemeinsamer Code | `lib/`, `slicetheprompt/`, Regeln und Skills gelten für alle Sessions | Testläufe **ändern nichts ausserhalb ihrer Projekte**; fehlende Bausteine im eigenen `model.py` schreiben und protokollieren |
| Rückfragen | eine autonome Session würde auf Antwort warten | Rückfrage ins Protokoll, Annahme treffen, weiterbauen |
| Git | Projekte sind lokal (gitignored) | keine Commits aus den Testläufen |

## Auftrag an jede Strecke (Prompt für die Session)

> Du fährst Strecke **‹X›** des Testparcours aus `docs/testparcours.md`. Baue die vier Teile der Strecke
> nacheinander mit dem Ablauf `/teil`, jeweils mit dem Text unter „Prompt“ als Auftrag. Testmodus:
> - Projektnamen genau wie angegeben (`tp-…`), danach überall den vollen Ordnernamen aus `project_new` verwenden.
> - Nicht slicen, nicht drucken, nichts committen. Nichts ausserhalb deiner `projects/…-tp-…`-Ordner ändern
>   (kein `lib/`, `slicetheprompt/`, `AGENTS.md`, Skills, `config/`).
> - Fehlt ein kritisches Mass: die Rückfrage, die du stellen würdest, ins Protokoll schreiben, einen
>   plausiblen Wert annehmen und weiterbauen.
> - Höchstens 5 Build-Runden je Teil; dann Stand protokollieren und zum nächsten Teil.
> - Nach jedem Teil `protokoll.md` im Projektordner nach der Vorlage unten schreiben – ehrlich, auch
>   Kleinigkeiten (falsche API-Annahme, unklare Meldung, unnötiges Lesen einer Datei).
> Am Ende eine Zeile je Teil: bestanden?, Runden, wichtigste Reibung.

## Protokoll-Vorlage (`projects/<projekt>/protokoll.md`)

```markdown
# <projekt>
- Ergebnis: bestanden ja/nein · Runden (build_and_check): n, davon fehlgeschlagen: n · Render geprüft: ja/nein
- Rückfragen, die ich gestellt hätte: …
- Annahmen: …
- Gelesene Dateien ausser spec.yaml/model.py/render.png: … (warum?)
- Reibung (je Punkt eine Zeile):
  - was: … | ursache: … | kosten: n Runden / Testskripte | vorschlag: … | art: baustein|pruefung|regel|ablauf|nutzer
- Fehlender Baustein: … (Signatur, die ich mir gewünscht hätte)
- Irreführende Meldung oder Regel: …
```

## Strecken

Jede Strecke mischt einfache und schwere Teile. Einige Prompts sind absichtlich lückenhaft (⚠), um das
Rückfrageverhalten zu prüfen.

### Strecke A – Passungen und Verbindungen

| Projekt | Prompt | Prüft |
| --- | --- | --- |
| `tp-a1-schraubdose` | Runde Dose Ø 50 × 40 mm mit Schraubdeckel, Gewinde 2 mm Steigung, PLA. | fehlender Baustein (Gewinde), zwei Teile, Spiel in Z |
| `tp-a2-clip` | Clip für ein Rohr Ø 16 mm, soll aufschnappen und halten, an die Wand schraubbar (M4). | Material (PETG), Biegelast, dünne Federzunge, Senkung |
| `tp-a3-schwalbenschwanz` | Zwei Platten 60 × 40 × 6 mm, die sich per Schwalbenschwanz zusammenschieben lassen, leicht gängig. | Gleitpassung, Überhang am Schwalbenschwanz, `koerper: 2` oder 2 Projekte |
| `tp-a4-platinengehaeuse` | ⚠ Gehäuse für eine Platine mit 4 Befestigungslöchern, USB-Buchse an der Schmalseite, Deckel zum Schrauben. | Rückfrage (Masse fehlen), Abstandsbolzen, Mutterfallen, Öffnung in der Wand |

### Strecke B – Geometrie und Druckbarkeit

| Projekt | Prompt | Prüft |
| --- | --- | --- |
| `tp-b1-kabelhalter` | Kabelhalter für den Schreibtischrand (Platte 25 mm), 3 Kabel Ø 6 mm, alles schön gerundet. | Rundungen (`fillet` scheitert), Klemmpassung, Drucklage |
| `tp-b2-wandhaken` | Wandhaken für eine Jacke, 2 Schraublöcher, Haken 40 mm ausladend, hält 5 kg. | Überhang vs. Drucklage `auto`, Last → Material/Wandstärke |
| `tp-b3-trichter` | Trichter, oben Ø 80, unten Auslauf Ø 10 × 30 mm, Wand 1,2 mm. | Hohlkörper/`offset`, Kegel-Überhang, dünne Wand an der Grenze |
| `tp-b4-lueftungsgitter` | Lüftungsgitter 120 × 120 mm mit Wabenmuster, 4 Löcher für Lüfterschrauben (Lochabstand 105 mm). | viele Features (Bauzeit), Brücken, Mindeststege |

### Strecke C – Bewegung und Mechanik

| Projekt | Prompt | Prüft |
| --- | --- | --- |
| `tp-c1-scharnier` | Scharnier, das am Stück gedruckt wird und sich danach bewegen lässt, Flügel 30 × 20 mm. | Print-in-place-Spiel, Brücke/Überhang am Gelenk, mehrere Körper |
| `tp-c2-zahnstange` | Zahnstange 100 mm lang mit passendem Ritzel 15 Zähne, Modul 1,5, Ritzel auf 5-mm-Welle. | Zahnrad-Bausteine ausserhalb des Hohlrads, gerade Verzahnung fehlt? |
| `tp-c3-riemenscheibe` | GT2-Riemenscheibe 20 Zähne für 6-mm-Riemen, Bohrung 5 mm mit Madenschraube. | fehlender Baustein (Riemenprofil), Bordscheiben, Madenschraube |
| `tp-c4-kurbel` | ⚠ Handkurbel für eine Spindel, mit Kugellager im Griff. | Rückfrage (Spindelmass, Lagertyp), Presspassung 608 (22 mm), Hebelform |

### Strecke D – Oberfläche, Schrift, Mehrfarbe, Änderungen

| Projekt | Prompt | Prüft |
| --- | --- | --- |
| `tp-d1-namensschild` | Türschild 100 × 40 mm „Werkstatt“, weisse Schrift auf schwarz, zweifarbig. | `bodies`, Schrift-Mindeststärke, Farbwechsel-Schichten |
| `tp-d2-lineal` | Lineal 200 mm mit Millimeterstrichen und Zahlen alle 10 mm graviert. | lange flache Teile, Gravurtiefe, viele kleine Schnitte (Bauzeit) |
| `tp-d3-drehknopf` | ⚠ Ersatz-Drehknopf für den Herd, Achse mit Abflachung. | Rückfrage (Ø/Abflachung), `d_welle_cut`, Griffrillen |
| `tp-d4-aenderung` | Nimm `tp-d1-namensschild`: Schrift 20 % grösser, Ecken mit 5 mm runden, zwei Löcher zum Aufhängen. (Erst nach D1.) | Änderungsrunde, Versionen, Parameter wiederverwenden |

## Auswertung (nach dem Parcours)

1. Alle `projects/*-tp-*/protokoll.md` einsammeln.
2. Reibungen gruppieren; je Gruppe zählen: wie viele Teile, wie viele **Strecken** (Bereiche), Kosten in Runden.
3. Zuordnen nach der Tabelle oben:
   - `regel` nur, wenn ≥ 2 Strecken betroffen sind – sonst `baustein` oder `pruefung`.
   - Bestehende Spezialregeln in `AGENTS.md` (z. B. Zahnräder) dabei mit prüfen und in Bausteine/Prüfung verschieben.
4. Ergebnis: Liste „Baustein X anlegen“, „Hinweis Y in Prüfung“, höchstens wenige neue allgemeine Regeln –
   jeweils mit Beleg (welche Teile, wie viele Runden gespart).
5. Danach denselben Parcours (oder eine Auswahl) erneut fahren und Runden vergleichen.
