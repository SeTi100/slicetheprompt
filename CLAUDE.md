# slicetheprompt – Prompt-to-Print für den Creality Hi Combo

Die Arbeitsregeln gelten für alle Agenten und stehen nur in `AGENTS.md`:

@AGENTS.md

## Nur für Claude Code
- MCP-Server über `.mcp.json` im Projekt. Nach Änderungen an `slicetheprompt/`: `/mcp` → slicetheprompt → reconnect.
- Abläufe als Slash-Befehle in `.claude/commands/`: `/teil`, `/drucken`, `/kalibrieren`, `/status`,
  `/schablone` (`/stencil`), `/baugruppe`. Gleicher Inhalt wie `.agents/skills/` – Änderungen in beiden nachziehen.
- Render-PNGs und `out/ui_feedback.png` mit dem Read-Tool ansehen.
