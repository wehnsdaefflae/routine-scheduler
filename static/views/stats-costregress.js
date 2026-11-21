// Cost regressions — the latest `cost_trend_degraded` finding per routine (operator decision #9,
// 2026-11-21 · `readmodels/cost_regressions.py`).
//
// At a run's end the engine compares a routine's last runs with the ones before them and, before
// naming the routine, reads the same window across the fleet (`readmodels/trend_attribution`,
// F568): a regression is the ROUTINE's, the HOST's (most comparable routines degraded together —
// such a row names no routine), or UNATTRIBUTABLE (too few routines ran the window to tell). The
// stream carried every finding and no surface showed one. This panel is that surface, on Stats in
// the Develop group — not on Blocked work, because a regression is how the fleet is changing, not
// work that was due and did not happen. Each row links to the routine's development view
// (#/changes/<slug>), whose Per-run cost panel says what was actually compared.

import { api } from "/static/api.js";
import { el, emptyState, fmtNum } from "/static/util.js";
import { foldRows, statSection } from "/static/views/stats-parts.js";

const WINDOW_DAYS = 30;
const when = (ts) => (ts || "").replace("T", " ").slice(0, 16) || "—";

// The verdict in a word, coloured as state: only the routine's own regression is a warning —
// a host condition and an unattributable one withhold that claim on purpose.
const SCOPE = {
  routine: ["chip partial", "this routine's", "the routine regressed against its own earlier runs "
    + "while most comparable routines did not"],
  host: ["chip bare", "the host's", "most comparable routines degraded in the same window — a "
    + "condition of the box, not of one routine"],
  unattributable: ["chip bare", "cause unknown", "too few routines had comparable runs in this "
    + "window to tell the routine from the host"],
};

const pct = (v) => (v == null ? "—" : `${Math.round(Number(v) * 100)}%`);
const num = (v) => (v == null ? "—" : fmtNum(v));
const pair = (a, b, fmt) => `${fmt(a)} → ${fmt(b)}`;

function whoseCell(r) {
  const href = r.slug ? `#/changes/${r.slug}` : null;
  if (r.subject) return el("td", {}, el("a", { href }, r.subject));
  return el("td", {}, el("div", {}, "the host"), href
    ? el("div", { class: "faint small" }, "detected on ", el("a", { href }, r.slug)) : null);
}

function attributionCell(r) {
  const [cls, word, why] = SCOPE[r.scope] || ["chip bare", r.scope || "—", ""];
  const peers = r.peers_compared == null ? "" : ` · ${r.peers_degraded ?? 0} of `
    + `${r.peers_compared} comparable routines degraded`;
  return el("td", { class: "small", title: r.detail || "" },
    el("span", { class: cls, title: why }, word),
    el("span", { class: "faint" }, peers),
    el("div", { class: "faint small" }, r.detail || ""));
}

/** The cost-regressions section, or null when the endpoint cannot be read. */
export async function costRegressionsSection() {
  let d;
  try {
    d = await api(`/api/health/cost-regressions?days=${WINDOW_DAYS}`);
  } catch {
    return null;
  }
  const rows = d.rows || [];
  const sub = `the newest cost-trend finding per routine in the last ${d.window_days} days: its `
    + "last runs measured clearly worse than the ones before them, and the fleet read over the "
    + "same window to say whose regression it is. Follow a routine to its development view for "
    + "the per-run cost profile behind the finding.";
  if (!rows.length) {
    return el("div", { class: "stat-section" }, el("h2", {}, "Cost regressions"),
      el("div", { class: "sub" }, sub),
      emptyState("✓", "no cost regressions",
        `no routine's runs got clearly worse in the last ${d.window_days} days`));
  }
  const head = el("tr", {},
    el("th", {}, "whose"), el("th", {}, "attribution"), el("th", { class: "num" }, "turns"),
    el("th", { class: "num" }, "volume"), el("th", { class: "num" }, "fail rate"),
    el("th", {}, "newest"));
  const body = rows.map((r) => el("tr", {},
    whoseCell(r),
    attributionCell(r),
    el("td", { class: "num" }, pair(r.turns_median_before, r.turns_median_after, num)),
    el("td", { class: "num" }, pair(r.volume_median_before, r.volume_median_after, num)),
    el("td", { class: "num" }, pair(r.fail_rate_before, r.fail_rate_after, pct)),
    el("td", { class: "faint small" }, when(r.ts))));
  return statSection("Cost regressions", sub, head, foldRows(body, 8, "routines"));
}
