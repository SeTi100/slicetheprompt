import { renderIsoStl } from "/usage_iso.js";

/**
 * Frontend-Modul für das Antigravity Usage-, Quota- & Chat-Timeline-Dashboard.
 * Integriert in SliceThePrompt Studio:
 *   - Statusbar Quota-Pill (#usage-status-pill)
 *   - Rechte Sidebar Tab "Kosten": Kompakte KI-Entwicklungskosten + 3D-Druck-Vergleich
 *   - Vollbild-Overlay / Route `/usage` (#usage-modal):
 *       * Account-weite 3-Pool-Gauges (Gemini Models 5h+Wk, Claude & GPT 5h+Wk, Argon Wk)
 *       * Fading Model-Volume SVG-Plot (Transparenz sinkt mit Rest-Limit, Volumen = effektive Nutzung)
 *       * Multi-Wert-Kalibrierung & Zukunfts-Modelle + Eco-Toggle (Energie Wh / Wasser ml)
 *       * Workspace -> Teile & Chats Liste + Was-wäre-wenn Modell-Simulator
 *       * Phone-Messaging-Chat-Timeline mit High-Res Studio-Iso-CAD-Renders & stationärer Scroll-Summenlinie
 */

const state = {
  overview: null,
  overviewError: null,
  detail: null,
  selectedKind: "project", // "project" | "conversation"
  selectedId: null,
  calibrationOpen: false,
  lightboxImg: null,
  onOpenVersion: null,
  listenersBound: false,
  chatResizeObserver: null,
  screenshot: {
    loading: false,
    error: null,
    dataUrl: null,
    result: null,
    bySlot: {},
    editedSlots: {},
  },
};

function esc(v) {
  return String(v ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function fmtMoney(costObj, settings) {
  if (!costObj) return "CHF 0.00";
  const cur = (settings && settings.currency) || costObj.currency || "CHF";
  const val = cur === "USD" ? Number(costObj.cost_usd || 0) : Number(costObj.cost_chf || 0);
  const dec = val < 0.1 && val > 0 ? 3 : 2;
  return `${cur} ${val.toFixed(dec)}`;
}

function fmtPct(val, suffix = "") {
  if (val === null || val === undefined || Number.isNaN(Number(val))) return "–";
  const n = Number(val);
  const dec = n > 0 && n < 0.1 ? 2 : 1;
  return `${n.toFixed(dec)}%${suffix ? " " + suffix : ""}`;
}

function fmtTokens(n) {
  const num = Number(n || 0);
  if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(2)}M`;
  if (num >= 1_000) return `${(num / 1_000).toFixed(1)}k`;
  return `${Math.round(num)}`;
}

function fmtEco(costObj, showEco) {
  if (!showEco || !costObj) return "";
  const wh = Number(costObj.energy_wh || 0);
  const ml = Number(costObj.water_ml || 0);
  const whTxt = wh >= 1000 ? `${(wh / 1000).toFixed(2)} kWh` : `${wh.toFixed(wh < 1 ? 2 : 1)} Wh`;
  const mlTxt = ml >= 1000 ? `${(ml / 1000).toFixed(2)} L` : `${ml.toFixed(ml < 1 ? 2 : 1)} ml`;
  return ` · ⚡ ${whTxt} · 💧 ${mlTxt}`;
}

function poolColorClass(pctLeft) {
  if (pctLeft === null || pctLeft === undefined) return "ok";
  if (pctLeft <= 10) return "crit";
  if (pctLeft <= 30) return "warn";
  return "ok";
}

function ringColorLabel(color) {
  if (color === "green") return "Grün";
  if (color === "amber") return "Orange";
  if (color === "red") return "Rot";
  if (color === "empty") return "Leer";
  return color || "–";
}

function slotKey(poolId, horizon) {
  return `${poolId}:${horizon}`;
}

function resetScreenshotState() {
  state.screenshot = {
    loading: false,
    error: null,
    dataUrl: null,
    result: null,
    bySlot: {},
    editedSlots: {},
  };
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("Datei konnte nicht gelesen werden"));
    reader.readAsDataURL(file);
  });
}

async function processScreenshotFile(file) {
  if (!file) return;
  try {
    const dataUrl = await readFileAsDataUrl(file);
    await parseScreenshotDataUrl(dataUrl);
  } catch (err) {
    state.calibrationOpen = true;
    state.screenshot.loading = false;
    state.screenshot.error = err?.message || "Bild konnte nicht gelesen werden";
    renderCalibrationDrawer();
  }
}

async function parseScreenshotDataUrl(dataUrl) {
  if (!dataUrl) return;
  state.calibrationOpen = true;
  state.screenshot = {
    loading: true,
    error: null,
    dataUrl,
    result: null,
    bySlot: {},
    editedSlots: {},
  };
  renderCalibrationDrawer();

  try {
    const res = await apiPost("/api/usage/parse-screenshot", { image_base64: dataUrl });
    const bySlot = {};
    for (const sn of res.snapshots || []) {
      bySlot[slotKey(sn.pool_id, sn.horizon)] = sn;
    }
    state.screenshot = {
      loading: false,
      error: null,
      dataUrl,
      result: res,
      bySlot,
      editedSlots: {},
    };
  } catch (err) {
    state.screenshot.loading = false;
    state.screenshot.error = err?.message || "Screenshot-Analyse fehlgeschlagen";
  }
  renderCalibrationDrawer();
}

function fmtDateTimeShort(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleString("de-CH", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return String(iso);
  }
}

async function apiGet(url) {
  const r = await fetch(url, { cache: "no-store" });
  const data = await r.json();
  if (!r.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${r.status}`);
  }
  return data;
}

async function apiPost(url, payload) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await r.json();
  if (!r.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${r.status}`);
  }
  return data;
}

// ============================================================================
// Statusbar Pill & Kosten-Sidebar-Karte
// ============================================================================

export function renderStatusPill(overview) {
  const el = document.getElementById("usage-status-pill");
  if (!el || !overview || !overview.pools) return;
  const gemini = overview.pools.gemini;
  const claude = overview.pools.claude_gpt;
  const argon = overview.pools.argon;

  const bits = [];
  if (gemini && gemini.weekly) {
    const h5t = gemini.h5 ? `/${Math.round(gemini.h5.pct_left)}% 5h` : "";
    bits.push(`<span class="up-chip ${poolColorClass(gemini.weekly.pct_left)}">Gem ${Math.round(gemini.weekly.pct_left)}% Wk${h5t}</span>`);
  }
  if (claude && claude.weekly) {
    bits.push(`<span class="up-chip ${poolColorClass(claude.weekly.pct_left)}">Cl/GPT ${Math.round(claude.weekly.pct_left)}% Wk</span>`);
  }
  if (argon && argon.weekly) {
    bits.push(`<span class="up-chip ${poolColorClass(argon.weekly.pct_left)}">Argon ${Math.round(argon.weekly.pct_left)}% Wk</span>`);
  }
  el.innerHTML = `<span class="up-label">⚡ Quota:</span> ${bits.join("")}`;
}

export async function refreshUsageOverview({ selectCurrentProject = null } = {}) {
  try {
    const ov = await apiGet("/api/usage/overview?days=14");
    state.overview = ov;
    state.overviewError = null;
    renderStatusPill(ov);
    if (selectCurrentProject) {
      await refreshSidebarPartCard(selectCurrentProject);
    }
    const modal = document.getElementById("usage-modal");
    if (modal && !modal.classList.contains("hidden")) {
      renderDashboardHeader();
      renderWorkspaceTree();
    }
    return ov;
  } catch (err) {
    state.overviewError = err?.message || "Usage-API nicht erreichbar";
    console.warn("Usage overview Fehler:", err);
    if (selectCurrentProject) {
      await refreshSidebarPartCard(selectCurrentProject);
    }
    const modal = document.getElementById("usage-modal");
    if (modal && !modal.classList.contains("hidden")) {
      renderDashboardHeader();
    }
    return null;
  }
}

function _buildAltMoney(costObj, cfg) {
  const cur = (cfg && cfg.currency) || costObj?.currency || "CHF";
  if (cur === "USD") {
    return `CHF ${Number(costObj?.cost_chf || 0).toFixed(2)}`;
  }
  const usd = Number(costObj?.cost_usd || 0);
  return `$${usd.toFixed(usd < 0.1 && usd > 0 ? 3 : 2)}`;
}

export async function refreshSidebarPartCard(projectName) {
  const box = document.getElementById("ai-part-cost-box");
  if (!box) return;
  if (!projectName) {
    box.innerHTML = `<span class="status-muted">Projekt wählen …</span>`;
    return;
  }
  try {
    const det = await apiGet(`/api/usage/projects/${encodeURIComponent(projectName)}`);
    if (state.selectedKind === "project" && state.selectedId === projectName) {
      state.detail = det;
    }
    const s = det.summary || {};
    const cfg = det.settings || state.overview?.settings || {};
    const cur = cfg.currency || "CHF";
    const turns = det.turns || [];
    const pc = det.print_cost;

    let promptSumChf = 0;
    let promptSumUsd = 0;
    let answerSumChf = 0;
    let answerSumUsd = 0;
    for (const t of turns) {
      promptSumChf += Number(t.prompt_cost?.cost_chf || 0);
      promptSumUsd += Number(t.prompt_cost?.cost_usd || 0);
      answerSumChf += Number(t.answer_cost?.cost_chf || 0);
      answerSumUsd += Number(t.answer_cost?.cost_usd || 0);
    }
    const pCostObj = { cost_chf: promptSumChf, cost_usd: promptSumUsd, currency: cur };
    const aCostObj = { cost_chf: answerSumChf, cost_usd: answerSumUsd, currency: cur };
    const totVal = cur === "USD" ? Number(s.cost_usd || 0) : Number(s.cost_chf || 0);
    const pShare = totVal > 0 ? Math.round(((cur === "USD" ? promptSumUsd : promptSumChf) / totVal) * 100) : 0;
    const aShare = totVal > 0 ? Math.max(0, 100 - pShare) : 0;

    // 3D-Druck Vergleich & Gesamtsumme
    let printRows = "";
    if (pc && pc.total_chf != null) {
      const pCur = pc.waehrung || "CHF";
      const aiForSum = Number(s.cost_chf || 0);
      const totalBoth = aiForSum + Number(pc.total_chf || 0);
      printRows = `
        <tr><td>3D-Druck (letzter Slice · ${Number(pc.gramm || 0).toFixed(1)} g)</td><td class="num">${esc(pCur)} ${Number(pc.total_chf).toFixed(2)}</td></tr>
        <tr class="cost-total-row"><td><strong>Gesamt (KI + 3D-Druck)</strong></td><td class="num"><strong>CHF ${totalBoth.toFixed(2)}</strong></td></tr>`;
    } else {
      printRows = `<tr><td colspan="2" class="status-muted">3D-Druckkosten nach Slicen verfügbar</td></tr>`;
    }

    // Variante 1: Alle Modell-Varianten (Was-wäre-wenn)
    const domModels = new Set(turns.map((t) => t.model_key));
    const simRows = (det.simulation || [])
      .map((m) => {
        const isUsed = domModels.has(m.model_key);
        const badge = isUsed ? ` <span class="cost-tag-used">aktiv</span>` : "";
        return `
          <tr class="${isUsed ? "cost-active-row" : ""}">
            <td>${esc(m.display_name)}${badge}<br><span class="status-muted">${esc(fmtPct(m.pct_5h, "5h"))} · ${esc(fmtPct(m.pct_weekly, "Wk"))}</span></td>
            <td class="num">${esc(fmtMoney(m, cfg))}<br><span class="status-muted">${esc(_buildAltMoney(m, cfg))}</span></td>
          </tr>`;
      })
      .join("");

    // Variante 2: Chat-Abschnitte (Turns, Prompt vs. Antwort, Versionen)
    const turnRows = turns
      .map((t) => {
        const verTxt = (t.versions || []).map((v) => v.version).join(", ");
        const subTxt = t.subagent_calls > 0 ? ` · +${t.subagent_calls} Sub` : "";
        return `
          <tr>
            <td>
              <strong>#${t.turn_number}</strong> ${esc(t.model_display)}${verTxt ? ` · <span class="cost-ver">${esc(verTxt)}</span>` : ""}${esc(subTxt)}<br>
              <span class="status-muted">${esc(t.prompt_short || "–")}</span>
            </td>
            <td class="num">
              <strong>${esc(fmtMoney(t.total_cost, cfg))}</strong><br>
              <span class="status-muted">P ${esc(fmtMoney(t.prompt_cost, cfg))} · A ${esc(fmtMoney(t.answer_cost, cfg))}</span>
            </td>
          </tr>`;
      })
      .join("");

    // Variante 3: Tokens, Öko-Bilanz & Account-Quotas
    const wh = Number(s.energy_wh || 0);
    const ml = Number(s.water_ml || 0);
    const whTxt = wh >= 1000 ? `${(wh / 1000).toFixed(2)} kWh` : `${wh.toFixed(wh < 1 ? 2 : 1)} Wh`;
    const mlTxt = ml >= 1000 ? `${(ml / 1000).toFixed(2)} L` : `${ml.toFixed(ml < 1 ? 2 : 1)} ml`;
    const cacheTotalIn = Number(s.fresh_in || 0) + Number(s.cache_read || 0);
    const cachePct = cacheTotalIn > 0 ? ((Number(s.cache_read || 0) / cacheTotalIn) * 100).toFixed(0) : "0";

    const poolRows = Object.values(state.overview?.pools || {})
      .map((p) => {
        const parts = [];
        if (p.weekly) parts.push(`${Number(p.weekly.pct_left).toFixed(0)}% Wk${p.weekly.resets_in_text ? " (" + p.weekly.resets_in_text + ")" : ""}`);
        if (p.h5) parts.push(`${Number(p.h5.pct_left).toFixed(0)}% 5h`);
        return `<tr><td>${esc(p.label)}</td><td class="num">${esc(parts.join(" · "))}</td></tr>`;
      })
      .join("");

    box.innerHTML = `
      <div>
        <span class="cost-total">${esc(fmtMoney(s, cfg))}</span>
        <span class="status-muted">· ${esc(_buildAltMoney(s, cfg))} · ${Number(s.cu || 0).toFixed(1)} CU · ${turns.length} Turns</span>
      </div>
      <table class="cost-table" style="margin-top:4px;">
        <tr><td>Prompt-Anteil (${pShare} %)</td><td class="num">${esc(fmtMoney(pCostObj, cfg))}</td></tr>
        <tr><td>Antwort-Anteil (${aShare} %, inkl. Thinking)</td><td class="num">${esc(fmtMoney(aCostObj, cfg))}</td></tr>
        <tr><td>5h-Limit · Weekly-Limit</td><td class="num">${esc(fmtPct(s.pct_5h, "5h"))} · ${esc(fmtPct(s.pct_weekly, "Wk"))}</td></tr>
        ${printRows}
      </table>

      <details class="code-details cost-collapse">
        <summary>Modell-Varianten vergleichen (${det.simulation?.length || 0})</summary>
        <table class="cost-table cost-subtable">${simRows || `<tr><td class="status-muted">Keine Simulation</td></tr>`}</table>
      </details>

      <details class="code-details cost-collapse">
        <summary>Abschnitte &amp; Iterationen (${turns.length} Turns)</summary>
        <table class="cost-table cost-subtable">${turnRows || `<tr><td class="status-muted">Keine Chat-Turns</td></tr>`}</table>
      </details>

      <details class="code-details cost-collapse">
        <summary>Tokens, Öko-Bilanz &amp; Quota-Pools</summary>
        <table class="cost-table cost-subtable">
          <tr><td>Fresh Input / Cache Read</td><td class="num">${esc(fmtTokens(s.fresh_in))} / ${esc(fmtTokens(s.cache_read))} (${cachePct}%)</td></tr>
          <tr><td>Output sichtbar / Thinking</td><td class="num">${esc(fmtTokens(s.visible_output))} / ${esc(fmtTokens(s.thinking_tokens))}</td></tr>
          <tr><td>Energieaufwand (geschätzt)</td><td class="num">${esc(whTxt)}</td></tr>
          <tr><td>Wasseräquivalent (geschätzt)</td><td class="num">${esc(mlTxt)}</td></tr>
          ${poolRows ? `<tr class="cost-divider-row"><td colspan="2" class="status-muted">Aktuelle Account-Pools</td></tr>${poolRows}` : ""}
        </table>
      </details>

      <button id="btn-sidebar-open-chat" class="btn-secondary full-width" style="margin-top:8px;">Chat-Timeline &amp; Kalibrierung öffnen</button>
    `;
    const btn = document.getElementById("btn-sidebar-open-chat");
    if (btn) {
      btn.addEventListener("click", () => openUsageModal(projectName, "project"));
    }
  } catch (err) {
    box.innerHTML = `<span class="status-muted">Keine KI-Chat-Daten für ${esc(projectName)}${err?.message ? " (" + esc(err.message) + ")" : ""}.</span>`;
  }
}

// ============================================================================
// Dashboard Modal: Top Pools + Fading Volume Plot + Kalibrierung
// ============================================================================

function renderRingSvg(pctLeft, color, size = 34) {
  const pct = Math.max(0, Math.min(100, Number(pctLeft ?? 100)));
  const r = 13;
  const c = 2 * Math.PI * r;
  const dash = (pct / 100) * c;
  return `
    <svg width="${size}" height="${size}" viewBox="0 0 34 34" class="pool-ring">
      <circle cx="17" cy="17" r="${r}" fill="none" stroke="rgba(148,163,184,0.18)" stroke-width="3.2" />
      <circle cx="17" cy="17" r="${r}" fill="none" stroke="${esc(color)}" stroke-width="3.2"
        stroke-dasharray="${dash.toFixed(1)} ${c.toFixed(1)}" stroke-linecap="round"
        transform="rotate(-90 17 17)" />
    </svg>`;
}

function renderHorizonLine(label, hData, color) {
  if (!hData) return "";
  const pct = Number(hData.pct_left ?? 100);
  const resetTxt = hData.resets_in_text ? `Resets in ${hData.resets_in_text}` : (hData.calibrated ? "Aktiv" : "Schätzung");
  const calTag = hData.calibrated ? `<span class="cal-dot cal-ok" title="Kalibriert">●</span>` : `<span class="cal-dot cal-est" title="Ungespannter Schätzwert">○</span>`;
  const partsTxt = hData.parts_remaining_est != null ? `≈ ${hData.parts_remaining_est} Teile` : "";
  return `
    <div class="pool-horizon-row">
      <div class="pool-h-left">
        <div class="pool-h-label">${esc(label)} ${calTag}</div>
        <div class="pool-h-sub">${esc(resetTxt)}${partsTxt ? " · " + esc(partsTxt) : ""}</div>
      </div>
      <div class="pool-h-right">
        <div class="pool-h-pct ${poolColorClass(pct)}">${pct.toFixed(0)}% left</div>
        ${renderRingSvg(pct, pct <= 10 ? "#ef4444" : pct <= 30 ? "#f59e0b" : color)}
      </div>
    </div>`;
}

function renderFadingPlotSvg(ov) {
  const plot = ov?.fading_plot;
  if (!plot) return `<div class="status-muted">Keine Plot-Daten</div>`;
  const modelSummary = plot.model_summary || [];
  const seriesByPool = plot.series_by_pool || {};
  const pools = ov.pools || {};

  // Zeitlich alle Requests zu einem übersichtlichen Balken-/Area-Band je Pool bündeln (max 36 Bins)
  const W = 540;
  const H = 175;
  const padL = 34;
  const padR = 10;
  const padT = 14;
  const padB = 24;
  const pw = W - padL - padR;
  const ph = H - padT - padB;

  const modelColors = {
    flash: "#22d3ee",
    hydrogen: "#38bdf8",
    pro: "#60a5fa",
    argon: "#f43f5e",
    claude_sonnet: "#a855f7",
    claude_opus: "#c084fc",
    gpt: "#10b981",
  };
  const getColor = (mkey, pid) => modelColors[mkey] || pools[pid]?.color || "#38bdf8";

  // Finde zeitliche Grenzen über alle Pools
  const allPts = [];
  for (const [pid, pts] of Object.entries(seriesByPool)) {
    for (const pt of pts) {
      allPts.push({ ...pt, pid, ms: Date.parse(pt.ts || "") });
    }
  }
  allPts.sort((a, b) => a.ms - b.ms);

  if (allPts.length === 0) {
    return `<div class="status-muted" style="padding:20px;">Noch keine Requests im gewählten Zeitfenster.</div>`;
  }

  const tMin = allPts[0].ms;
  const tMax = Math.max(tMin + 60_000, allPts[allPts.length - 1].ms);
  const maxEff = Math.max(1, ...allPts.map((p) => Number(p.effective_tokens_per_pct || 1)));
  const bins = 28;
  const binW = pw / bins;

  // Für jeden Bin: sammle Anteile pro Modell, Höhe proportional zu (tokens / maxBinTokens) & Volumen = effektive Ausbeute, Opacity = fade_alpha!
  const binned = Array.from({ length: bins }, () => ({ items: [], totalVol: 0, avgAlpha: 1.0 }));
  for (const pt of allPts) {
    const frac = Math.max(0, Math.min(0.999, (pt.ms - tMin) / (tMax - tMin)));
    const bIdx = Math.floor(frac * bins);
    const b = binned[bIdx];
    const existing = b.items.find((x) => x.model_key === pt.model_key);
    // Effektives Nutzungsvolumen gewichtet nach Tokens * Effizienz
    const effRatio = Math.sqrt(Math.max(0.15, Number(pt.effective_tokens_per_pct || 1) / maxEff));
    const vol = Number(pt.cu || 0) * effRatio;
    if (existing) {
      existing.vol += vol;
      existing.tokens += Number(pt.tokens || 0);
      existing.cu += Number(pt.cu || 0);
      existing.alpha = Math.min(existing.alpha, Number(pt.fade_alpha || 0.7));
    } else {
      b.items.push({
        model_key: pt.model_key,
        model_name: pt.model_name,
        pid: pt.pid,
        vol,
        tokens: Number(pt.tokens || 0),
        cu: Number(pt.cu || 0),
        alpha: Number(pt.fade_alpha || 0.7),
        rem_pct: Number(pt.rem_pct || 50),
      });
    }
    b.totalVol += vol;
  }
  const maxBinVol = Math.max(1, ...binned.map((b) => b.totalVol));

  let rectsSvg = "";
  for (let i = 0; i < bins; i++) {
    const b = binned[i];
    if (b.items.length === 0) continue;
    let yCur = padT + ph;
    const x = padL + i * binW + 1.5;
    const bw = Math.max(3, binW - 3);
    for (const it of b.items) {
      const h = Math.max(2, (it.vol / maxBinVol) * ph * 0.92);
      yCur -= h;
      const col = getColor(it.model_key, it.pid);
      rectsSvg += `<rect x="${x.toFixed(1)}" y="${yCur.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="2" fill="${col}" fill-opacity="${it.alpha.toFixed(2)}">
        <title>${esc(it.model_name)} · Restlimit ~${it.rem_pct.toFixed(0)}% · ${fmtTokens(it.tokens)} Tok (${it.cu.toFixed(1)} CU)</title>
      </rect>`;
    }
  }

  // Kurvenlinie des Restlimits pro Pool (sanft abfallend)
  let linesSvg = "";
  for (const [pid, pts] of Object.entries(seriesByPool)) {
    if (!pts || pts.length < 2) continue;
    const pCol = pools[pid]?.color || "#38bdf8";
    const step = Math.max(1, Math.floor(pts.length / 40));
    const coords = [];
    for (let i = 0; i < pts.length; i += step) {
      const pt = pts[i];
      const ms = Date.parse(pt.ts || "");
      const x = padL + ((ms - tMin) / (tMax - tMin)) * pw;
      const y = padT + ph * (1 - Number(pt.rem_pct || 0) / 100);
      coords.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    }
    const last = pts[pts.length - 1];
    const lx = padL + ((Date.parse(last.ts || "") - tMin) / (tMax - tMin)) * pw;
    const ly = padT + ph * (1 - Number(last.rem_pct || 0) / 100);
    coords.push(`${lx.toFixed(1)},${ly.toFixed(1)}`);
    linesSvg += `<polyline fill="none" stroke="${pCol}" stroke-width="1.6" stroke-dasharray="3 2" stroke-opacity="0.85" points="${coords.join(" ")}" />`;
  }

  const legend = modelSummary
    .slice(0, 5)
    .map((m) => {
      const col = getColor(m.model_key, m.pool_id);
      return `<span class="plot-leg-item"><i style="background:${col}"></i>${esc(m.model_name)}: <strong>${fmtTokens(m.tokens_per_pct)}</strong> Tok/1%</span>`;
    })
    .join("");

  return `
    <div class="fading-plot-wrap">
      <div class="fading-plot-head">
        <span>Fading Model-Volumen (${plot.days}d · Fläche = effektive Ausbeute · Opazität = Rest-%)</span>
      </div>
      <svg viewBox="0 0 ${W} ${H}" class="fading-svg" preserveAspectRatio="none">
        <line x1="${padL}" y1="${padT}" x2="${padL}" y2="${padT + ph}" stroke="#334155" stroke-width="1" />
        <line x1="${padL}" y1="${padT + ph}" x2="${W - padR}" y2="${padT + ph}" stroke="#334155" stroke-width="1" />
        <line x1="${padL}" y1="${padT + ph * 0.5}" x2="${W - padR}" y2="${padT + ph * 0.5}" stroke="#1e293b" stroke-dasharray="3 3" />
        <text x="4" y="${padT + 8}" fill="#64748b" font-size="9">100%</text>
        <text x="8" y="${padT + ph * 0.5 + 3}" fill="#64748b" font-size="9">50%</text>
        <text x="14" y="${padT + ph}" fill="#64748b" font-size="9">0%</text>
        <text x="${padL}" y="${H - 6}" fill="#64748b" font-size="9">${esc(fmtDateTimeShort(allPts[0].ts))}</text>
        <text x="${W - padR}" y="${H - 6}" fill="#64748b" font-size="9" text-anchor="end">Jetzt</text>
        ${rectsSvg}
        ${linesSvg}
      </svg>
      <div class="plot-legend">${legend}</div>
    </div>`;
}

function _renderCalSlotField(p, horizon, labelText, placeholderText, existingHorizonObj) {
  const key = slotKey(p.pool_id, horizon);
  const sn = state.screenshot.bySlot[key];
  const pctVal = sn && sn.pct_left != null
    ? Number(sn.pct_left).toFixed(1)
    : (existingHorizonObj ? Number(existingHorizonObj.pct_left).toFixed(1) : "");
  const rstVal = sn
    ? (sn.resets_in || "")
    : (existingHorizonObj?.resets_in_text || "");

  let badgeHtml = "";
  if (sn) {
    const colTxt = ringColorLabel(sn.ring_color);
    if (sn.mismatch && sn.ocr_pct != null && sn.ocr_pct > 100) {
      badgeHtml = `<div class="cal-ring-badge warn">⚠ OCR-Fehler (${esc(sn.ocr_pct)}%) → Ring-Fallback ${esc(sn.ring_pct)}% (${esc(colTxt)})</div>`;
    } else if (sn.mismatch) {
      badgeHtml = `<div class="cal-ring-badge warn">⚠ Abweichung: OCR ${esc(sn.ocr_pct)}% vs. Ring ${esc(sn.ring_pct)}% (${esc(colTxt)}) <button type="button" class="btn-xs cal-apply-ring" data-apply-pool="${esc(p.pool_id)}" data-apply-h="${esc(horizon)}" data-ring-val="${esc(sn.ring_pct)}">Ring übernehmen</button></div>`;
    } else if (sn.source === "ocr+ring") {
      badgeHtml = `<div class="cal-ring-badge ok">✓ OCR ${esc(sn.ocr_pct)}% · Ring ${esc(sn.ring_pct)}% (${esc(colTxt)})</div>`;
    } else if (sn.source === "ring_fallback") {
      badgeHtml = `<div class="cal-ring-badge info">◎ Ring-Fallback ${esc(sn.ring_pct)}% (${esc(colTxt)})</div>`;
    } else if (sn.source === "ocr_only") {
      badgeHtml = `<div class="cal-ring-badge muted">OCR ${esc(sn.ocr_pct)}% (kein Ring erkannt)</div>`;
    }
  }

  return `
    <div class="cal-field-wrap">
      <div class="cal-field-pair">
        <label>${esc(labelText)}</label>
        <input type="number" step="0.5" min="0" max="100" data-cal-pool="${esc(p.pool_id)}" data-cal-h="${esc(horizon)}" data-cal-field="pct" value="${esc(pctVal)}" />
        <input type="text" placeholder="${esc(placeholderText)}" data-cal-pool="${esc(p.pool_id)}" data-cal-h="${esc(horizon)}" data-cal-field="reset" value="${esc(rstVal)}" />
      </div>
      ${badgeHtml}
    </div>`;
}

function renderCalibrationDrawer() {
  const drawer = document.getElementById("usage-cal-drawer");
  if (!drawer) return;
  if (!state.calibrationOpen || !state.overview) {
    drawer.classList.add("hidden");
    return;
  }
  drawer.classList.remove("hidden");

  const ov = state.overview;
  const s = ov.settings || {};
  const pools = ov.pools || {};
  const ss = state.screenshot;
  const ssRes = ss.result;

  let ssStatusHtml = "";
  if (ss.loading) {
    ssStatusHtml = `<span class="cal-ss-status">⏳ Analysiere Screenshot (RapidOCR + 360°-Ring-Tracker) …</span>`;
  } else if (ss.error) {
    ssStatusHtml = `<span class="cal-ss-status err">✕ ${esc(ss.error)}</span>`;
  } else if (ssRes) {
    const engineTxt = ssRes.ocr_available ? "RapidOCR + Ring-Tracker" : "Nur Ring-Tracker (Fallback)";
    const snapCnt = (ssRes.snapshots || []).length;
    const ringCnt = (ssRes.rings || []).length;
    const warns = (ssRes.warnings || [])
      .map((w) => `<div class="cal-ss-warn">⚠ ${esc(w.message || w.code)}</div>`)
      .join("");
    ssStatusHtml = `
      <span class="cal-ss-status ok">✓ ${esc(engineTxt)} · ${ringCnt} Ringe · ${snapCnt} Werte erkannt</span>
      ${warns}`;
  } else {
    ssStatusHtml = `<span class="cal-ss-status muted">Tipp: Antigravity-Popover mit Win+Shift+S kopieren &amp; hier per Strg+V einfügen.</span>`;
  }

  const dropZoneHtml = `
    <div id="usage-cal-dropzone" class="cal-dropzone">
      <input type="file" id="cal-screenshot-file" accept="image/*" class="hidden" />
      <div class="cal-dz-left">
        ${ss.dataUrl ? `<img src="${ss.dataUrl}" alt="Usage Screenshot Vorschau" class="cal-ss-thumb" />` : `<div class="cal-ss-icon">📸</div>`}
      </div>
      <div class="cal-dz-body">
        <div class="cal-dz-title">
          <strong>Screenshot-Importer</strong>
          <span>Strg+V (Zwischenablage), Datei ablegen oder <button type="button" id="btn-cal-pick-file" class="btn-xs">Datei wählen</button></span>
          ${ss.dataUrl ? `<button type="button" id="btn-cal-clear-ss" class="btn-xs">✕ Verwerfen</button>` : ""}
        </div>
        <div class="cal-dz-meta">${ssStatusHtml}</div>
      </div>
    </div>`;

  const poolRows = Object.values(pools)
    .map((p) => {
      const wkInput = p.has_weekly
        ? _renderCalSlotField(p, "weekly", "Weekly Rest-%", "z. B. 1d 6h", p.weekly)
        : "";
      const h5Input = p.has_5h
        ? _renderCalSlotField(p, "5h", "5h Rest-%", "z. B. 3h 9m", p.h5)
        : `<div class="cal-field-pair"><span class="status-muted">Nur Weekly-Limit</span></div>`;
      return `
        <div class="cal-pool-block">
          <div class="cal-pool-title" style="border-left:3px solid ${esc(p.color)};padding-left:8px;">${esc(p.label)}</div>
          ${wkInput}
          ${h5Input}
        </div>`;
    })
    .join("");

  const unknownModels = (ssRes?.detected_models || []).filter((m) => !m.known);
  const poolOptionsHtml = (selId) =>
    Object.values(pools)
      .map((pl) => `<option value="${esc(pl.pool_id)}" ${pl.pool_id === selId ? "selected" : ""}>${esc(pl.label)}</option>`)
      .join("");

  const detectedModelsHtml = unknownModels.length > 0
    ? `<div class="cal-detected-box">
         <div class="cal-detected-head">🧩 Neue / unbekannte Modelle aus Screenshot (${unknownModels.length})</div>
         <div class="cal-detected-list">
           ${unknownModels
             .map((m, idx) => {
               const stCls = String(m.status || "Available").toLowerCase();
               const stBadge = m.badge || m.status || "";
               return `
                 <div class="cal-detected-row">
                   <label class="cal-det-check">
                     <input type="checkbox" data-det-model-idx="${idx}" />
                     <span class="cal-det-name">${esc(m.name)}</span>
                     ${m.tier ? `<span class="cal-det-tag">${esc(m.tier)}</span>` : ""}
                     ${stBadge ? `<span class="cal-det-tag ${esc(stCls)}">${esc(stBadge)}</span>` : ""}
                   </label>
                   <select data-det-pool-idx="${idx}" title="Ziel-Pool für custom_models">
                     ${poolOptionsHtml(m.suggested_pool_id || "gemini")}
                   </select>
                 </div>`;
             })
             .join("")}
         </div>
       </div>`
    : "";

  drawer.innerHTML = `
    ${dropZoneHtml}
    <div class="cal-grid">
      <div class="cal-col">
        <h4>📊 Echte App-Restwerte eingeben (Anker-Kalibrierung)</h4>
        <div class="cal-pools-list">${poolRows}</div>
      </div>
      <div class="cal-col">
        <h4>⚙️ Darstellung &amp; Zukunfts-Modelle</h4>
        <label class="cal-toggle">
          <input type="checkbox" id="cal-eco-toggle" ${s.show_eco_metrics ? "checked" : ""} />
          <span>⚡ Energieverbrauch (Wh) &amp; 💧 Wasseräquivalent (ml) je Abschnitt anzeigen</span>
        </label>
        <div class="cal-inline-row">
          <label>Währung:</label>
          <select id="cal-currency">
            <option value="CHF" ${s.currency === "CHF" ? "selected" : ""}>CHF</option>
            <option value="USD" ${s.currency === "USD" ? "selected" : ""}>USD</option>
          </select>
          <label>USD→CHF:</label>
          <input type="number" step="0.01" id="cal-fx" value="${Number(s.usd_to_chf || 0.88).toFixed(2)}" style="width:68px;" />
        </div>
        ${detectedModelsHtml}
        <details class="cal-future-box">
          <summary>+ Zukünftiges Modell / neuen Pool registrieren</summary>
          <div class="cal-future-fields">
            <input type="text" id="cal-new-pool-id" placeholder="Pool-ID (z. B. gemini_5)" />
            <input type="text" id="cal-new-pool-label" placeholder="Pool-Name (z. B. Gemini 5)" />
            <label><input type="checkbox" id="cal-new-pool-5h" /> 5h-Fenster aktiv</label>
            <hr />
            <input type="text" id="cal-new-model-slug" placeholder="Model Slug / Display-Match (z. B. gemini-5-pro)" />
            <input type="text" id="cal-new-model-name" placeholder="Anzeigename (z. B. Gemini 5 Pro)" />
            <input type="number" step="0.1" id="cal-new-model-in" placeholder="Input $/1M (z. B. 2.5)" />
            <input type="number" step="0.1" id="cal-new-model-out" placeholder="Output $/1M (z. B. 15.0)" />
          </div>
        </details>
        <div class="cal-actions">
          <button id="btn-cal-save" class="btn-primary">Kalibrierung &amp; Einstellungen speichern</button>
          <button id="btn-cal-cancel" class="btn-secondary">Schliessen</button>
        </div>
      </div>
    </div>`;

  const fileInput = document.getElementById("cal-screenshot-file");
  const pickBtn = document.getElementById("btn-cal-pick-file");
  const dzEl = document.getElementById("usage-cal-dropzone");
  const clearBtn = document.getElementById("btn-cal-clear-ss");

  if (pickBtn && fileInput) {
    pickBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      fileInput.click();
    });
  }
  if (dzEl && fileInput) {
    dzEl.addEventListener("click", (ev) => {
      if (ev.target.closest("button")) return;
      fileInput.click();
    });
  }
  if (fileInput) {
    fileInput.addEventListener("change", () => {
      const f = fileInput.files?.[0];
      if (f) processScreenshotFile(f);
    });
  }
  if (clearBtn) {
    clearBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      resetScreenshotState();
      renderCalibrationDrawer();
    });
  }

  drawer.querySelectorAll('input[data-cal-field="pct"]').forEach((inp) => {
    inp.addEventListener("input", () => {
      const k = slotKey(inp.dataset.calPool, inp.dataset.calH);
      state.screenshot.editedSlots[k] = true;
    });
  });

  drawer.querySelectorAll(".cal-apply-ring").forEach((btn) => {
    btn.addEventListener("click", () => {
      const pid = btn.dataset.applyPool;
      const h = btn.dataset.applyH;
      const val = btn.dataset.ringVal;
      const pctEl = drawer.querySelector(`input[data-cal-pool="${pid}"][data-cal-h="${h}"][data-cal-field="pct"]`);
      if (pctEl && val != null) {
        pctEl.value = Number(val).toFixed(1);
        const k = slotKey(pid, h);
        state.screenshot.editedSlots[k] = true;
        if (state.screenshot.bySlot[k]) {
          state.screenshot.bySlot[k].applied_ring = true;
        }
      }
    });
  });

  document.getElementById("btn-cal-cancel").addEventListener("click", () => {
    state.calibrationOpen = false;
    renderCalibrationDrawer();
  });

  document.getElementById("btn-cal-save").addEventListener("click", async () => {
    const snapshots = [];
    for (const p of Object.values(pools)) {
      for (const h of ["weekly", "5h"]) {
        const pctEl = drawer.querySelector(`input[data-cal-pool="${p.pool_id}"][data-cal-h="${h}"][data-cal-field="pct"]`);
        const rstEl = drawer.querySelector(`input[data-cal-pool="${p.pool_id}"][data-cal-h="${h}"][data-cal-field="reset"]`);
        if (pctEl && pctEl.value !== "") {
          const k = slotKey(p.pool_id, h);
          const sn = state.screenshot.bySlot[k];
          const snapObj = {
            pool_id: p.pool_id,
            horizon: h,
            pct_left: Number(pctEl.value),
            resets_in: rstEl ? rstEl.value.trim() : "",
          };
          if (sn) {
            if (sn.ring_pct != null) snapObj.ring_pct = sn.ring_pct;
            if (sn.ring_color) snapObj.ring_color = sn.ring_color;
            if (sn.ocr_pct != null) snapObj.ocr_pct = sn.ocr_pct;
            if (sn.applied_ring) {
              snapObj.source = "ring_fallback";
            } else if (state.screenshot.editedSlots[k]) {
              snapObj.source = "manual";
            } else {
              snapObj.source = sn.source || "ocr+ring";
            }
          }
          snapshots.push(snapObj);
        }
      }
    }
    const payload = {
      days: 14,
      snapshots,
      settings: {
        show_eco_metrics: !!document.getElementById("cal-eco-toggle")?.checked,
        currency: document.getElementById("cal-currency")?.value || "CHF",
        usd_to_chf: Number(document.getElementById("cal-fx")?.value || 0.88),
      },
    };
    const npId = document.getElementById("cal-new-pool-id")?.value.trim();
    const npLbl = document.getElementById("cal-new-pool-label")?.value.trim();
    if (npId && npLbl) {
      payload.settings.new_pool = {
        id: npId,
        label: npLbl,
        has_weekly: true,
        has_5h: !!document.getElementById("cal-new-pool-5h")?.checked,
      };
    }
    const customModelsToSave = {};
    unknownModels.forEach((m, idx) => {
      const cb = drawer.querySelector(`input[data-det-model-idx="${idx}"]`);
      if (cb && cb.checked) {
        const sel = drawer.querySelector(`select[data-det-pool-idx="${idx}"]`);
        customModelsToSave[m.slug] = {
          display_name: m.name,
          pool_id: sel ? sel.value : (m.suggested_pool_id || "gemini"),
          input_per_1m: Number(m.suggested_input_per_1m ?? 2.0),
          output_per_1m: Number(m.suggested_output_per_1m ?? 12.0),
        };
      }
    });
    const nmSlug = document.getElementById("cal-new-model-slug")?.value.trim();
    const nmName = document.getElementById("cal-new-model-name")?.value.trim();
    if (nmSlug && nmName) {
      customModelsToSave[nmSlug] = {
        display_name: nmName,
        pool_id: npId || "gemini",
        input_per_1m: Number(document.getElementById("cal-new-model-in")?.value || 2.0),
        output_per_1m: Number(document.getElementById("cal-new-model-out")?.value || 12.0),
      };
    }
    if (Object.keys(customModelsToSave).length > 0) {
      payload.settings.custom_models = customModelsToSave;
    }

    try {
      const res = await apiPost("/api/usage/calibrate", payload);
      state.overview = res.overview;
      state.overviewError = null;
      state.calibrationOpen = false;
      resetScreenshotState();
      renderStatusPill(res.overview);
      renderDashboardHeader();
      renderCalibrationDrawer();
      if (state.selectedId) {
        await loadUsageDetail(state.selectedKind, state.selectedId);
      }
      if (typeof state.getCurrentProject === "function" && state.getCurrentProject()) {
        await refreshSidebarPartCard(state.getCurrentProject());
      }
    } catch (err) {
      alert("Kalibrierung fehlgeschlagen: " + err.message);
    }
  });
}

function renderDashboardHeader() {
  const host = document.getElementById("usage-top-area");
  if (!host) return;
  if (!state.overview) {
    host.innerHTML = state.overviewError
      ? `<div class="usage-api-error"><strong>Usage-API nicht aktiv:</strong> ${esc(state.overviewError)} — Falls <code>slicetheprompt ui</code> bereits vorher lief, Server einmal neu starten.</div>`
      : `<div class="status-muted">Lade Konto-Quotas …</div>`;
    return;
  }
  const ov = state.overview;
  const s = ov.settings || {};

  const poolCards = Object.values(ov.pools || {})
    .map((p) => {
      const rt = p.ring_tracking;
      let rtHtml = "";
      if (rt && rt.timestamp) {
        const diffTxt = rt.diff_pct != null ? ` · Δ ${Math.abs(Number(rt.diff_pct)).toFixed(1)}%` : "";
        const ringInfo = rt.ring_pct != null
          ? `Ring ${Number(rt.ring_pct).toFixed(0)}% (${ringColorLabel(rt.ring_color)})`
          : (rt.source || "");
        rtHtml = `<div class="pool-ring-meta" title="Letzter Ring-Track / Kalibrierungsabgleich (${esc(rt.source || "")})">◎ ${esc(fmtDateTimeShort(rt.timestamp))} · ${esc(ringInfo)}${esc(diffTxt)}</div>`;
      }
      return `
      <div class="usage-pool-card">
        <div class="usage-pool-title">
          <span class="pool-dot" style="background:${esc(p.color)}"></span>
          <strong>${esc(p.label)}</strong>
        </div>
        ${renderHorizonLine("Weekly limit", p.weekly, p.color)}
        ${renderHorizonLine("5h limit", p.h5, p.color)}
        ${rtHtml}
      </div>`;
    })
    .join("");

  host.innerHTML = `
    <div class="usage-top-grid">
      <div class="usage-pools-grid">${poolCards}</div>
      <div class="usage-plot-card">
        ${renderFadingPlotSvg(ov)}
        <div class="usage-top-tools">
          <label class="eco-inline-toggle">
            <input type="checkbox" id="quick-eco-toggle" ${s.show_eco_metrics ? "checked" : ""} />
            <span>⚡ Energie & 💧 Wasser anzeigen</span>
          </label>
          <button id="btn-toggle-cal" class="btn-xs">🎛️ Rest-% kalibrieren & Modelle</button>
        </div>
      </div>
    </div>`;

  document.getElementById("btn-toggle-cal").addEventListener("click", () => {
    state.calibrationOpen = !state.calibrationOpen;
    renderCalibrationDrawer();
  });

  document.getElementById("quick-eco-toggle").addEventListener("change", async (e) => {
    try {
      const res = await apiPost("/api/usage/calibrate", {
        days: 14,
        settings: { show_eco_metrics: !!e.target.checked },
      });
      state.overview = res.overview;
      renderDashboardHeader();
      renderCalibrationDrawer();
      if (state.detail) {
        await loadUsageDetail(state.selectedKind, state.selectedId);
      }
    } catch (err) {
      console.error(err);
    }
  });
}

// ============================================================================
// Left Pane: Workspaces -> Parts & Chats
// ============================================================================

function renderWorkspaceTree() {
  const host = document.getElementById("usage-ws-tree");
  if (!host || !state.overview) return;
  const cfg = state.overview.settings || {};
  const wss = state.overview.workspaces || [];

  if (wss.length === 0) {
    host.innerHTML = `<div class="status-muted">Keine Workspaces gefunden.</div>`;
    return;
  }

  host.innerHTML = wss
    .map((ws) => {
      const projs = (ws.projects_list || [])
        .map((p) => {
          const active = state.selectedKind === "project" && state.selectedId === p.project_name ? "active" : "";
          const pcTxt = p.print_cost ? ` · 🖨️ ${p.print_cost.total_chf.toFixed(2)}` : "";
          return `
            <button class="ws-item ${active}" data-kind="project" data-id="${esc(p.project_name)}">
              <div class="ws-item-top">
                <span class="ws-item-name">🧩 ${esc(p.project_name)}</span>
                <span class="ws-item-cost">${esc(fmtMoney(p.cost, cfg))}</span>
              </div>
              <div class="ws-item-meta">
                <span>${p.versions_count} CAD-Ver. · ${p.turns_count} Turns${pcTxt}</span>
                <span>${esc(fmtPct(p.cost?.pct_weekly, "Wk"))}</span>
              </div>
            </button>`;
        })
        .join("");

      const convs = (ws.conversations || [])
        .slice(0, 10)
        .map((c) => {
          const active = state.selectedKind === "conversation" && state.selectedId === c.conversation_id ? "active" : "";
          return `
            <button class="ws-item ws-item-chat ${active}" data-kind="conversation" data-id="${esc(c.conversation_id)}">
              <div class="ws-item-top">
                <span class="ws-item-name">💬 ${esc(c.title)}</span>
                <span class="ws-item-cost">${esc(fmtMoney(c.cost, cfg))}</span>
              </div>
              <div class="ws-item-meta">
                <span>${c.turns_count} Turns · ${c.subagent_calls} Sub</span>
                <span>${esc(fmtPct(c.cost?.pct_weekly, "Wk"))}</span>
              </div>
            </button>`;
        })
        .join("");

      return `
        <details class="ws-group" open>
          <summary class="ws-group-head">
            <span>📁 ${esc(ws.workspace)}</span>
            <span class="ws-group-sum">${esc(cfg.currency || "CHF")} ${Number(ws.total_cost_chf || 0).toFixed(2)}</span>
          </summary>
          <div class="ws-items-list">
            ${projs}
            ${convs ? `<div class="ws-subhead">Chats in ${esc(ws.workspace)}</div>${convs}` : ""}
          </div>
        </details>`;
    })
    .join("");

  host.querySelectorAll(".ws-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      loadUsageDetail(btn.dataset.kind, btn.dataset.id);
    });
  });
}

// ============================================================================
// Right Pane: Phone-Messaging-Chat-Timeline + Stationäre Summenlinie
// ============================================================================

async function loadUsageDetail(kind, id) {
  if (!id) return;
  state.selectedKind = kind;
  state.selectedId = id;
  renderWorkspaceTree();

  const pane = document.getElementById("usage-chat-pane");
  if (!pane) return;
  pane.innerHTML = `<div class="status-muted" style="padding:24px;">Lade Chat-Timeline & Versionen …</div>`;

  try {
    const url =
      kind === "project"
        ? `/api/usage/projects/${encodeURIComponent(id)}`
        : `/api/usage/conversations/${encodeURIComponent(id)}`;
    const det = await apiGet(url);
    state.detail = det;
    renderChatDetail(det);
  } catch (err) {
    pane.innerHTML = `<div class="status-muted" style="padding:24px;color:var(--red);">Fehler: ${esc(err.message)}</div>`;
  }
}

function _buildIsoCardHtml(v, projectName) {
  const statusCls = v.passed ? "ok" : "err";
  const statusLbl = v.passed ? "✓ OK" : `✕ ${v.errors_count} Fehler`;
  const dims = Array.isArray(v.size_mm) && v.size_mm.length === 3
    ? `${Number(v.size_mm[0]).toFixed(1)}×${Number(v.size_mm[1]).toFixed(1)}×${Number(v.size_mm[2]).toFixed(1)} mm`
    : "";
  return `
    <div class="iso-ver-card" data-stl-url="${esc(v.stl_url)}" data-passed="${v.passed ? "1" : "0"}" data-version="${esc(v.version)}" data-project="${esc(projectName || "")}">
      <div class="iso-img-box">
        <div class="iso-placeholder">Rendere Iso …</div>
      </div>
      <div class="iso-ver-foot">
        <span class="iso-ver-badge ${statusCls}">${esc(v.version)} · ${esc(statusLbl)}</span>
        <span class="iso-ver-dims">${esc(dims)}</span>
        ${projectName ? `<button class="btn-xs iso-open-3d" title="In Studio 3D öffnen">3D ↗</button>` : ""}
      </div>
    </div>`;
}

function updateScannerAccumulator() {
  const scroller = document.getElementById("phone-chat-scroll");
  const readEl = document.getElementById("scanner-readout");
  const lineEl = document.getElementById("chat-scanner-line");
  if (!scroller || !readEl || !lineEl) return;

  const sbWidth = Math.max(0, scroller.offsetWidth - scroller.clientWidth);
  lineEl.style.right = `${sbWidth}px`;

  const lineRect = lineEl.getBoundingClientRect();
  const lineY = lineRect.top + lineRect.height / 2;
  const blocks = scroller.querySelectorAll(".chat-seg[data-seg='1']");

  let chf = 0;
  let usd = 0;
  let pct5h = 0;
  let pctWk = 0;
  let tokens = 0;
  let wh = 0;
  let ml = 0;
  let count = 0;

  blocks.forEach((b) => {
    const r = b.getBoundingClientRect();
    // Alles oberhalb bzw. bis zur stationären horizontalen Linie summieren
    const passed = r.top <= lineY;
    b.classList.toggle("passed-line", passed);
    if (passed) {
      count++;
      chf += Number(b.dataset.chf || 0);
      usd += Number(b.dataset.usd || 0);
      pct5h += Number(b.dataset.pct5h || 0);
      pctWk += Number(b.dataset.pctwk || 0);
      tokens += Number(b.dataset.tokens || 0);
      wh += Number(b.dataset.wh || 0);
      ml += Number(b.dataset.ml || 0);
    }
  });

  const cfg = state.detail?.settings || state.overview?.settings || {};
  const moneyStr = cfg.currency === "USD" ? `USD ${usd.toFixed(2)}` : `CHF ${chf.toFixed(2)}`;
  const ecoStr = cfg.show_eco_metrics
    ? ` · ⚡ ${wh.toFixed(wh < 1 ? 2 : 1)} Wh · 💧 ${ml.toFixed(ml < 1 ? 2 : 1)} ml`
    : "";

  readEl.textContent = `Kumuliert bis Linie (${count}/${blocks.length}): ${moneyStr} · ${pct5h.toFixed(1)}% 5h · ${pctWk.toFixed(2)}% Wk · ${fmtTokens(tokens)} Tok${ecoStr}`;
}

function renderChatDetail(det) {
  const pane = document.getElementById("usage-chat-pane");
  if (!pane) return;
  const cfg = det.settings || state.overview?.settings || {};
  const showEco = !!cfg.show_eco_metrics;
  const sum = det.summary || {};
  const title = det.project_name || det.title || det.conversation_id;
  const isProject = det.kind === "project";

  // AI vs 3D Print Pill
  let vsPrintHtml = "";
  if (det.print_cost && det.print_cost.total_chf != null) {
    const pc = det.print_cost;
    vsPrintHtml = `
      <span class="vs-print-pill">
        🤖 KI: <strong>${esc(fmtMoney(sum, cfg))}</strong> vs. 🖨️ 3D-Druck: <strong>${esc(pc.waehrung || "CHF")} ${Number(pc.total_chf).toFixed(2)}</strong> (${Number(pc.gramm || 0).toFixed(1)} g · ${esc(pc.zeit || "–")})
      </span>`;
  }

  // What-If Model Simulator (kompakt)
  const simRows = (det.simulation || [])
    .map((m) => `
      <div class="sim-chip">
        <span class="sim-name">${esc(m.display_name)}</span>
        <span class="sim-vals">${esc(fmtMoney(m, cfg))} · ${esc(fmtPct(m.pct_5h, "5h"))} · ${esc(fmtPct(m.pct_weekly, "Wk"))}</span>
      </div>`)
    .join("");

  let cumChf = 0;
  let cum5h = 0;
  let cumWk = 0;

  const turnsHtml = (det.turns || [])
    .map((t) => {
      const pc = t.prompt_cost || {};
      const ac = t.answer_cost || {};
      const tc = t.total_cost || {};
      cumChf += Number(tc.cost_chf || 0);
      cum5h += Number(tc.pct_5h || 0);
      cumWk += Number(tc.pct_weekly || 0);

      const vers = t.versions || [];
      const isoGrid = vers.length > 0
        ? `<div class="iso-cards-grid">${vers.map((v) => _buildIsoCardHtml(v, t.project_name || det.project_name)).join("")}</div>`
        : "";

      const subChip = t.subagent_calls > 0 ? `<span class="chat-chip sub">🤖 +${t.subagent_calls} Subagenten</span>` : "";
      const cacheBreakChip = t.cache_break ? `<span class="chat-chip warn">⚠ Cache-Reset (Modellwechsel)</span>` : "";

      const promptBubble = `
        <div class="chat-row right chat-seg" data-seg="1"
          data-chf="${pc.cost_chf || 0}" data-usd="${pc.cost_usd || 0}"
          data-pct5h="${pc.pct_5h || 0}" data-pctwk="${pc.pct_weekly || 0}"
          data-tokens="${pc.total_tokens || 0}" data-wh="${pc.energy_wh || 0}" data-ml="${pc.water_ml || 0}">
          <div class="bubble bubble-user">
            <div class="bubble-meta-top">
              <span>User · Turn #${t.turn_number}</span>
              <span>${esc(fmtDateTimeShort(t.timestamp))}</span>
            </div>
            <div class="prompt-mini" data-expanded="0" title="Klicken zum Aufklappen">
              <div class="prompt-short-txt">${esc(t.prompt_short)}</div>
              <div class="prompt-full-txt hidden">${esc(t.prompt_full)}</div>
            </div>
            <div class="bubble-cost-footer">
              <strong>Prompt:</strong> ${esc(fmtMoney(pc, cfg))} · ${esc(fmtPct(pc.pct_5h, "5h"))} · ${esc(fmtPct(pc.pct_weekly, "Wk"))} · ${esc(fmtTokens(pc.total_tokens))} Tok${esc(fmtEco(pc, showEco))}
            </div>
          </div>
        </div>`;

      const answerBubble = `
        <div class="chat-row left chat-seg" data-seg="1"
          data-chf="${ac.cost_chf || 0}" data-usd="${ac.cost_usd || 0}"
          data-pct5h="${ac.pct_5h || 0}" data-pctwk="${ac.pct_weekly || 0}"
          data-tokens="${ac.total_tokens || 0}" data-wh="${ac.energy_wh || 0}" data-ml="${ac.water_ml || 0}">
          <div class="bubble bubble-ai">
            <div class="bubble-meta-top">
              <span class="model-pill">${esc(t.model_display)}</span>
              <span class="chat-chips-inline">
                <span class="chat-chip">${t.planner_calls} Calls</span>
                <span class="chat-chip">Cache ${Number(t.cache_hit_pct || 0).toFixed(0)}%</span>
                <span class="chat-chip">Think ${Number(t.thinking_share_pct || 0).toFixed(0)}%</span>
                ${subChip}${cacheBreakChip}
              </span>
            </div>
            ${isoGrid}
            <div class="bubble-cost-footer">
              <strong>Antwort:</strong> ${esc(fmtMoney(ac, cfg))} · ${esc(fmtPct(ac.pct_5h, "5h"))} · ${esc(fmtPct(ac.pct_weekly, "Wk"))} · ${esc(fmtTokens(ac.total_tokens))} Tok${esc(fmtEco(ac, showEco))}
            </div>
          </div>
        </div>`;

      const sectionSummary = `
        <div class="chat-section-divider">
          <span>Abschnitt #${t.turn_number} (Prompt+Antwort): <strong>${esc(fmtMoney(tc, cfg))}</strong> · ${esc(fmtPct(tc.pct_5h, "5h"))} · ${esc(fmtPct(tc.pct_weekly, "Wk"))} · <em>Kumuliert: ${esc(cfg.currency || "CHF")} ${cumChf.toFixed(2)} (${cumWk.toFixed(2)}% Wk)</em></span>
        </div>`;

      return promptBubble + answerBubble + sectionSummary;
    })
    .join("");

  const unmatched = det.unmatched_versions || [];
  const unmatchedHtml = unmatched.length > 0
    ? `<div class="chat-row left">
         <div class="bubble bubble-ai">
           <div class="bubble-meta-top"><strong>Weitere CAD-Versionen (ohne Chat-Turn-Zeitfenster)</strong></div>
           <div class="iso-cards-grid">${unmatched.map((v) => _buildIsoCardHtml(v, det.project_name)).join("")}</div>
         </div>
       </div>`
    : "";

  pane.innerHTML = `
    <div class="chat-detail-header">
      <div class="chat-head-top">
        <div>
          <h3>${isProject ? "🧩" : "💬"} ${esc(title)}</h3>
          <div class="chat-head-kpis">
            <span class="kpi-pill">${esc(fmtMoney(sum, cfg))}</span>
            <span class="kpi-pill">${esc(fmtPct(sum.pct_5h, "vom 5h-Limit"))}</span>
            <span class="kpi-pill">${esc(fmtPct(sum.pct_weekly, "vom Weekly-Limit"))}</span>
            <span class="kpi-pill">${esc(fmtTokens(sum.total_tokens))} Tokens</span>
            ${showEco ? `<span class="kpi-pill">⚡ ~${Number(sum.energy_wh || 0).toFixed(1)} Wh · 💧 ~${Number(sum.water_ml || 0).toFixed(1)} ml</span>` : ""}
            ${vsPrintHtml}
          </div>
        </div>
      </div>
      ${simRows ? `<details class="sim-details"><summary>🔄 Was-wäre-wenn: Gleiche Arbeit auf anderem Modell</summary><div class="sim-grid">${simRows}</div></details>` : ""}
    </div>

    <div class="phone-chat-stage">
      <div id="phone-chat-scroll" class="phone-chat-scroll">
        <div class="phone-chat-stream">
          ${turnsHtml || `<div class="status-muted" style="padding:20px;">Keine Chat-Turns zugeordnet.</div>`}
          ${unmatchedHtml}
        </div>
      </div>
      <div id="chat-scanner-line" class="chat-scanner-line">
        <span id="scanner-readout" class="scanner-readout">Scrolle durch die Chat-Timeline …</span>
      </div>
    </div>`;

  // Expandierbare Mini-Prompts
  pane.querySelectorAll(".prompt-mini").forEach((el) => {
    el.addEventListener("click", () => {
      const exp = el.dataset.expanded === "1";
      el.dataset.expanded = exp ? "0" : "1";
      el.querySelector(".prompt-short-txt").classList.toggle("hidden", !exp);
      el.querySelector(".prompt-full-txt").classList.toggle("hidden", exp);
      requestAnimationFrame(updateScannerAccumulator);
    });
  });

  // High-Res Studio Iso Renders asynchron laden
  pane.querySelectorAll(".iso-ver-card").forEach(async (card) => {
    const stlUrl = card.dataset.stlUrl;
    const passed = card.dataset.passed === "1";
    const imgBox = card.querySelector(".iso-img-box");
    const openBtn = card.querySelector(".iso-open-3d");
    if (openBtn) {
      openBtn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        closeUsageModal();
        if (typeof state.onOpenVersion === "function") {
          state.onOpenVersion(card.dataset.project, card.dataset.version);
        }
      });
    }
    try {
      const dataUrl = await renderIsoStl(stlUrl, passed);
      imgBox.innerHTML = `<img src="${dataUrl}" alt="${esc(card.dataset.version)}" class="iso-render-img" />`;
      imgBox.addEventListener("click", () => openIsoLightbox(dataUrl, `${card.dataset.project} · ${card.dataset.version}`));
    } catch {
      imgBox.innerHTML = `<div class="iso-placeholder">Kein STL</div>`;
    }
  });

  if (state.chatResizeObserver) {
    state.chatResizeObserver.disconnect();
    state.chatResizeObserver = null;
  }

  const scroller = document.getElementById("phone-chat-scroll");
  if (scroller) {
    scroller.addEventListener("scroll", () => requestAnimationFrame(updateScannerAccumulator), { passive: true });
    if (typeof ResizeObserver !== "undefined") {
      const ro = new ResizeObserver(() => requestAnimationFrame(updateScannerAccumulator));
      ro.observe(scroller);
      const stream = scroller.querySelector(".phone-chat-stream");
      if (stream) ro.observe(stream);
      state.chatResizeObserver = ro;
    }
    requestAnimationFrame(updateScannerAccumulator);
  }
}

function openIsoLightbox(dataUrl, caption) {
  const lb = document.getElementById("iso-lightbox");
  const img = document.getElementById("iso-lightbox-img");
  const cap = document.getElementById("iso-lightbox-cap");
  if (!lb || !img) return;
  img.src = dataUrl;
  if (cap) cap.textContent = caption || "";
  lb.classList.remove("hidden");
}

export async function openUsageModal(preferProject = null, preferKind = "project") {
  const modal = document.getElementById("usage-modal");
  if (!modal) return;
  modal.classList.remove("hidden");

  await refreshUsageOverview();
  const targetId =
    preferProject ||
    state.selectedId ||
    state.overview?.projects?.[0]?.project_name ||
    state.overview?.conversations?.[0]?.conversation_id;
  const targetKind =
    preferProject
      ? preferKind
      : state.overview?.projects?.[0]?.project_name
        ? "project"
        : "conversation";

  renderDashboardHeader();
  renderCalibrationDrawer();
  renderWorkspaceTree();
  if (targetId) {
    await loadUsageDetail(targetKind, targetId);
  }
}

export function closeUsageModal() {
  if (state.chatResizeObserver) {
    state.chatResizeObserver.disconnect();
    state.chatResizeObserver = null;
  }
  const modal = document.getElementById("usage-modal");
  if (modal) modal.classList.add("hidden");
}

export function initUsageUI({ onOpenProjectVersion, getCurrentProject } = {}) {
  state.onOpenVersion = onOpenProjectVersion || null;
  state.getCurrentProject = getCurrentProject || null;
  if (!state.listenersBound) {
    state.listenersBound = true;
    const btnOpen = document.getElementById("btn-open-usage");
    const pill = document.getElementById("usage-status-pill");
    const btnClose = document.getElementById("btn-usage-close");
    const btnReload = document.getElementById("btn-usage-reload");
    const lb = document.getElementById("iso-lightbox");
    const lbClose = document.getElementById("btn-iso-lb-close");

    if (btnOpen) {
      btnOpen.addEventListener("click", () => {
        const cur = typeof getCurrentProject === "function" ? getCurrentProject() : null;
        openUsageModal(cur, "project");
      });
    }
    if (pill) {
      pill.addEventListener("click", () => {
        const cur = typeof getCurrentProject === "function" ? getCurrentProject() : null;
        openUsageModal(cur, "project");
      });
    }
    if (btnClose) btnClose.addEventListener("click", closeUsageModal);
    const btnAiReload = document.getElementById("btn-ai-cost-reload");
    if (btnAiReload) {
      btnAiReload.addEventListener("click", () => {
        const cur = typeof getCurrentProject === "function" ? getCurrentProject() : null;
        refreshUsageOverview({ selectCurrentProject: cur });
      });
    }
    if (btnReload) {
      btnReload.addEventListener("click", async () => {
        await refreshUsageOverview();
        renderDashboardHeader();
        renderWorkspaceTree();
        if (state.selectedId) await loadUsageDetail(state.selectedKind, state.selectedId);
      });
    }
    if (lbClose && lb) {
      lbClose.addEventListener("click", () => lb.classList.add("hidden"));
      lb.addEventListener("click", (e) => {
        if (e.target === lb) lb.classList.add("hidden");
      });
    }

    // Clipboard Paste (Strg+V) für Screenshots wenn Usage-Modal offen ist
    document.addEventListener("paste", (ev) => {
      const modal = document.getElementById("usage-modal");
      if (!modal || modal.classList.contains("hidden")) return;
      const items = Array.from(ev.clipboardData?.items || []);
      const imgItem = items.find((it) => it.type && it.type.startsWith("image/"));
      const file = imgItem ? imgItem.getAsFile() : Array.from(ev.clipboardData?.files || []).find((f) => f.type.startsWith("image/"));
      if (!file) return;
      ev.preventDefault();
      processScreenshotFile(file);
    });

    // Datei-Drag & Drop im Usage-Modal / Kalibrierungs-Drawer
    const usageModal = document.getElementById("usage-modal");
    if (usageModal) {
      const setDragClass = (active) => {
        const dz = document.getElementById("usage-cal-dropzone");
        if (dz) dz.classList.toggle("drag-over", active);
      };
      usageModal.addEventListener("dragenter", (ev) => {
        if (!Array.from(ev.dataTransfer?.types || []).includes("Files")) return;
        ev.preventDefault();
        setDragClass(true);
      });
      usageModal.addEventListener("dragover", (ev) => {
        if (!Array.from(ev.dataTransfer?.types || []).includes("Files")) return;
        ev.preventDefault();
        if (ev.dataTransfer) ev.dataTransfer.dropEffect = "copy";
        setDragClass(true);
      });
      usageModal.addEventListener("dragleave", (ev) => {
        if (!usageModal.contains(ev.relatedTarget)) setDragClass(false);
      });
      usageModal.addEventListener("drop", (ev) => {
        if (!Array.from(ev.dataTransfer?.types || []).includes("Files")) return;
        ev.preventDefault();
        ev.stopPropagation();
        setDragClass(false);
        const files = Array.from(ev.dataTransfer?.files || []);
        const imgFile = files.find((f) => (f.type && f.type.startsWith("image/")) || /\.(png|jpe?g|webp|bmp)$/i.test(f.name));
        if (imgFile) {
          processScreenshotFile(imgFile);
        }
      });
    }
  }

  refreshUsageOverview({
    selectCurrentProject: typeof getCurrentProject === "function" ? getCurrentProject() : null,
  });

  // Direkt öffnen bei /usage oder ?view=usage
  if (window.location.pathname === "/usage" || new URLSearchParams(window.location.search).get("view") === "usage") {
    const cur = typeof getCurrentProject === "function" ? getCurrentProject() : null;
    openUsageModal(cur, "project");
  }
}
