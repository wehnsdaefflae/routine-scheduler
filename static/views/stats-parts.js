// The building blocks the Stats view's tables share — the fold that keeps a long table short,
// the frame every stat table sits in, a routine's deep link — and the monthly spend section,
// the durable series (readmodels/stats.monthly_spend). Split from views/stats.js so neither
// file grows past the size bar.

import { el, fmtNum, fmtUsd } from "/static/util.js";

export const NBSP = " ";

// Show the first `keep` rows and hide the rest behind ONE row that reveals them. Stats is a
// page of KPI tiles and two charts followed, today, by 20 000px of rows nobody scans — 172
// monthly rows, 196 util rows. Every row is still here and still one click away; the page just
// stops spending its height on them before anyone asks. The reveal row goes LAST, because a
// hidden <tr> takes no space and the button therefore sits under what is shown.
export function foldRows(rows, keep, label) {
  if (rows.length <= keep) return rows;
  const rest = rows.slice(keep);
  for (const r of rest) r.hidden = true;
  const btn = el("button", { class: "btn small ghost" }, `show all ${rows.length} ${label}`);
  btn.onclick = () => {
    const opening = rest[0].hidden;
    for (const r of rest) r.hidden = !opening;
    btn.textContent = opening ? `show fewer ${label}` : `show all ${rows.length} ${label}`;
  };
  return [...rows, el("tr", { class: "fold-row" }, el("td", { colspan: "99" }, btn))];
}

// The frame every stat table on this page shares: a titled section, an optional sub-line
// explaining where the numbers come from, and one horizontally scrollable table. `sub` takes a
// string, an array of strings (el flattens its children), or null when the title already says
// everything — el drops the null child, so there is no empty .sub div to style around.
export const statSection = (title, sub, head, body) => el("div", { class: "stat-section" },
  el("h2", {}, title),
  sub ? el("div", { class: "sub" }, sub) : null,
  el("div", { class: "tablewrap boxed" },
    el("table", { class: "stat-table" },
      el("thead", {}, head), el("tbody", {}, ...body))));

// Deep link for a routine/conversation row — null (plain text) when the home is unknown.
export const routineHref = (slug, kind) =>
  kind === "routine" ? `#/routine/${slug}`
    : kind === "conversation" ? `#/conversations/${slug}` : null;

// Monthly spend by routine — the durable series (workflow-usage stream, survives run
// retention): one row per routine, one column per month, context volume + notional cost per
// cell, a growth chip when the latest month's volume runs >20% over the one before.
export function monthlySection(monthly, kinds) {
  const months = (monthly?.months || []).slice(-6);
  const rows = Object.entries(monthly?.by_routine || {});
  if (!months.length || !rows.length) return null;
  const latest = months[months.length - 1];
  const prev = months[months.length - 2];
  const cell = (c) => (c ? `${fmtNum(c.volume)} · ${fmtUsd(c.notional)}` : NBSP);
  const head = el("tr", {}, el("th", {}, "routine"),
    ...months.map((m) => el("th", { class: "num" }, m)),
    el("th", {}, "trend"));
  const rowFor = ([slug, cells]) => {
    const cur = cells[latest];
    const before = prev ? cells[prev] : null;
    const growing = cur && before && cur.volume > before.volume * 1.2;
    const shrinking = cur && before && cur.volume < before.volume * 0.8;
    const href = routineHref(slug, kinds?.[slug]);
    return el("tr", {},
      el("td", {}, href ? el("a", { href }, slug) : slug),
      ...months.map((m) => el("td", { class: "num" }, cell(cells[m]))),
      el("td", {}, growing ? el("span", { class: "chip partial" }, "↑ growing")
        : shrinking ? el("span", { class: "chip ok" }, "↓ shrinking")
        : before && cur ? el("span", { class: "chip bare" }, "→ steady") : NBSP));
  };
  // A CONVERSATION is a one-off with a timestamp for a name, and the fleet has a hundred of
  // them: as peers of the routines they made this table a list of `c-2026MMDD-HHMMSS` ids and
  // buried the twenty rows a reader came for. They subtotal into one row that expands.
  const isConv = ([slug]) => kinds?.[slug] === "conversation";
  const convs = rows.filter(isConv);
  const body = rows.filter((r) => !isConv(r)).map(rowFor);
  if (convs.length) {
    const totals = {};
    for (const [, cells] of convs)
      for (const m of months) {
        const c = cells[m];
        if (!c) continue;
        totals[m] = totals[m] || { volume: 0, notional: 0 };
        totals[m].volume += c.volume || 0;
        totals[m].notional += c.notional || 0;
      }
    body.push(...foldRows([el("tr", { class: "subtotal" },
      el("td", {}, `conversations · ${convs.length}`),
      ...months.map((m) => el("td", { class: "num" }, cell(totals[m]))),
      el("td", {}, NBSP)), ...convs.map(rowFor)], 1, "conversations"));
  }
  return statSection("Monthly spend by routine",
    "context volume · notional cost per calendar month, from the durable usage stream — unlike the tables above, this survives run retention",
    head, body);
}
