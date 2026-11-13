// Stark vereinfachter Usage-Tab für Android (exakte API-Token-Logs aus token_usage.jsonl)

let _usageData = null;
let _activePeriod = "today";

function esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtTokens(n) {
  const v = Number(n) || 0;
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 10_000) return `${(v / 1_000).toFixed(1)}k`;
  return v.toLocaleString("de-DE");
}

function fmtUsd(v) {
  const n = Number(v) || 0;
  if (n >= 1) return `$${n.toFixed(2)}`;
  if (n >= 0.01) return `$${n.toFixed(3)}`;
  return `$${n.toFixed(4)}`;
}

function fmtDayLabel(isoDate) {
  const parts = String(isoDate || "").split("-");
  if (parts.length !== 3) return esc(isoDate);
  return `${parts[2]}.${parts[1]}.`;
}

function fmtTs(isoTs) {
  if (!isoTs) return "–";
  const d = new Date(isoTs);
  if (Number.isNaN(d.getTime())) return esc(isoTs);
  return d.toLocaleString("de-DE", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function renderUsage() {
  const box = document.getElementById("usage-content");
  if (!box) return;
  if (!_usageData) {
    box.innerHTML = `<span class="status-muted">Noch keine Daten geladen.</span>`;
    return;
  }
  if (!_usageData.exists || _usageData.total_entries === 0) {
    box.innerHTML = `
      <div class="spec-details">
        <div>Noch keine API-Token-Logs vorhanden.</div>
        <span class="status-muted">Erwartete Datei: <code>${esc(_usageData.log_path || "out/token_usage.jsonl")}</code></span>
        <span class="status-muted">Die OpenWebUI ReAct-Pipe schreibt dort nach jeder Antwort exakte Token-Werte hinein.</span>
      </div>`;
    return;
  }

  const period = (_usageData.periods && _usageData.periods[_activePeriod]) || null;
  if (!period) {
    box.innerHTML = `<span class="status-muted">Keine Daten für diesen Zeitraum.</span>`;
    return;
  }

  const t = period.totals || {};
  const thoughtText = t.thought_tokens > 0 ? ` · Thought ${fmtTokens(t.thought_tokens)}` : "";
  const errText = t.error_requests > 0 ? ` · <span class="usage-err">${t.error_requests} Fehler</span>` : "";

  const kpiHtml = `
    <div class="usage-kpi-grid">
      <div class="usage-kpi-card">
        <div class="usage-kpi-label">Tokens gesamt</div>
        <div class="usage-kpi-val">${fmtTokens(t.total_tokens)}</div>
        <div class="usage-kpi-sub">In ${fmtTokens(t.input_tokens)} · Out ${fmtTokens(t.output_tokens)}${thoughtText}</div>
      </div>
      <div class="usage-kpi-card">
        <div class="usage-kpi-label">Kosten (API)</div>
        <div class="usage-kpi-val">${fmtUsd(t.cost_usd)}</div>
        <div class="usage-kpi-sub">${t.total_steps || 0} ReAct-Schritte</div>
      </div>
      <div class="usage-kpi-card">
        <div class="usage-kpi-label">Anfragen</div>
        <div class="usage-kpi-val">${t.requests || 0}</div>
        <div class="usage-kpi-sub">${t.ok_requests || 0} ok${errText}</div>
      </div>
      <div class="usage-kpi-card">
        <div class="usage-kpi-label">Ø je Anfrage</div>
        <div class="usage-kpi-val">${fmtTokens(t.avg_tokens)}</div>
        <div class="usage-kpi-sub">${Number(t.avg_duration_s || 0).toFixed(1)} s · ${Number(t.avg_steps || 0).toFixed(1)} Schritte</div>
      </div>
    </div>`;

  const daily = period.daily || [];
  let dailyHtml = "";
  if (daily.length > 0) {
    const maxTok = Math.max(1, ...daily.map((d) => d.total_tokens || 0));
    const bars = daily
      .map((d) => {
        const pct = Math.max(4, Math.round(((d.total_tokens || 0) / maxTok) * 100));
        const title = `${d.date}: ${fmtTokens(d.total_tokens)} Tokens (${d.requests} Anfragen, ${fmtUsd(d.cost_usd)})`;
        return `
          <div class="usage-bar-col" title="${esc(title)}">
            <div class="usage-bar-val">${fmtTokens(d.total_tokens)}</div>
            <div class="usage-bar-track"><div class="usage-bar-fill" style="height:${pct}%"></div></div>
            <div class="usage-bar-lbl">${fmtDayLabel(d.date)}</div>
          </div>`;
      })
      .join("");
    dailyHtml = `
      <div class="usage-block">
        <div class="usage-block-title">Tagesverlauf</div>
        <div class="usage-bars">${bars}</div>
      </div>`;
  }

  const byModel = period.by_model || [];
  const modelHtml = byModel.length
    ? `
      <div class="usage-block">
        <div class="usage-block-title">Nach Modell</div>
        <table class="checks-table usage-table">
          <thead><tr><th>Modell</th><th>Anfr.</th><th>Tokens</th><th>Kosten</th></tr></thead>
          <tbody>
            ${byModel
              .map(
                (m) => `
              <tr>
                <td class="mono">${esc(m.model)}</td>
                <td class="mono">${m.requests}</td>
                <td class="mono">${fmtTokens(m.total_tokens)}</td>
                <td class="mono">${fmtUsd(m.cost_usd)}</td>
              </tr>`
              )
              .join("")}
          </tbody>
        </table>
      </div>`
    : "";

  const recent = period.recent || [];
  const recentHtml = recent.length
    ? `
      <div class="usage-block">
        <div class="usage-block-title">Letzte Anfragen (${recent.length})</div>
        <div class="usage-recent-list">
          ${recent
            .map((r) => {
              const ok = r.status === "ok";
              const badgeCls = ok ? "badge-ok" : "badge-err";
              const thoughtPart = r.thought_tokens > 0 ? ` · T ${fmtTokens(r.thought_tokens)}` : "";
              return `
                <div class="usage-recent-item">
                  <div class="usage-recent-top">
                    <span class="mono">${fmtTs(r.ts)}</span>
                    <span class="badge ${badgeCls}">${esc(r.status)}</span>
                  </div>
                  <div class="usage-recent-mid">
                    <span class="mono">${esc(r.model)}</span>
                    <span>${r.steps} Schr. · ${Number(r.duration_s || 0).toFixed(1)} s</span>
                  </div>
                  <div class="usage-recent-bot mono">
                    <span>${fmtTokens(r.total_tokens)} Tok (In ${fmtTokens(r.input_tokens)} · Out ${fmtTokens(r.output_tokens)}${thoughtPart})</span>
                    <span>${fmtUsd(r.cost_usd)}</span>
                  </div>
                </div>`;
            })
            .join("")}
        </div>
      </div>`
    : `<div class="status-muted">Keine Einträge in diesem Zeitraum.</div>`;

  box.innerHTML = `${kpiHtml}${dailyHtml}${modelHtml}${recentHtml}`;
}

export async function loadUsageTab() {
  const box = document.getElementById("usage-content");
  if (!box) return;
  try {
    const res = await fetch("/api/usage");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _usageData = await res.json();
    renderUsage();
  } catch (e) {
    box.innerHTML = `<span class="status-muted">Token-Logs konnten nicht geladen werden: ${esc(e.message)}</span>`;
  }
}

export function initUsageTab() {
  const reloadBtn = document.getElementById("btn-usage-reload");
  if (reloadBtn) {
    reloadBtn.addEventListener("click", () => loadUsageTab());
  }
  const periodsEl = document.getElementById("usage-periods");
  if (periodsEl) {
    periodsEl.addEventListener("click", (ev) => {
      const btn = ev.target.closest(".seg-btn[data-period]");
      if (!btn) return;
      _activePeriod = btn.dataset.period || "today";
      periodsEl.querySelectorAll(".seg-btn").forEach((b) => {
        b.classList.toggle("active", b === btn);
      });
      if (_usageData) renderUsage();
      else loadUsageTab();
    });
  }
}
