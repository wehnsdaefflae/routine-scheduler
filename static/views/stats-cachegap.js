// The BETWEEN-RUNS prompt-cache gap, per routine (F507/D163) — the one part of what a routine
// costs that nothing measured. Every other cost view on this tab reads WITHIN a run; this one
// reads the interval BETWEEN two fires, where the provider's prompt cache expires and turn 1
// re-writes the whole system+recipe+digest prefix at the cache-WRITE rate instead of reading it
// back at the cache-READ rate. Served by /api/stats → readmodels/cache_gap.py, which prices the
// re-write from the engine's own recorded price table.
//
// `premium` is an UPPER BOUND, and the table says so: a warm turn 1 still writes part of its
// prefix (the `warm read share` column), so the warm counterfactual is cheaper than a cold
// start, not free. It is what the cold re-write was BILLED above a cache read — never a saving
// anyone has banked.

import { el, fmtInt, fmtNum, fmtUsd } from "/static/util.js";
import { routineHref, statSection } from "/static/views/stats-parts.js";

const pct = (share) => (share === null || share === undefined ? "—" : `${Math.round(share * 100)}%`);

// A routine cold on every fire pays the premium every time: the cache expired before the next
// one. One warm fire proves the prefix CAN survive the interval, which is what makes the row
// actionable (fire more often, or accept it).
function rowFor(slug, r, kinds) {
  const href = routineHref(slug, kinds?.[slug]);
  const coldShare = r.fires ? r.cold / r.fires : 0;
  return el("tr", {},
    el("td", {}, href ? el("a", { href }, slug) : slug),
    el("td", { class: "num" }, fmtInt(r.fires)),
    el("td", { class: coldShare === 1 && r.fires > 1 ? "num warn" : "num" },
      `${fmtInt(r.cold)} (${Math.round(coldShare * 100)}%)`),
    el("td", { class: "num" }, r.warm ? fmtInt(r.warm) : "—"),
    el("td", { class: "num" }, fmtNum(r.cold_tokens)),
    el("td", { class: "num" }, fmtUsd(r.premium_usd)),
    el("td", { class: "num" }, fmtUsd(r.per_cold_fire)),
    el("td", { class: "num" }, pct(r.warm_read_share)),
    el("td", { class: r.unpriced ? "num warn" : "num" }, r.unpriced ? fmtInt(r.unpriced) : "—"));
}

export function cacheGapSection(gap, kinds) {
  const rows = Object.entries(gap?.by_routine || {});
  if (!rows.length) {
    return el("div", { class: "stat-section" },
      el("h2", {}, "Between-runs prompt-cache gap"),
      el("div", { class: "sub" }, "no retained transcript carries a metered first turn yet — "
        + "this fills in as runs land."));
  }
  const t = gap.totals || {};
  const head = el("tr", {},
    el("th", {}, "routine"),
    el("th", { class: "num", title: "retained runs whose first turn was metered" }, "fires"),
    el("th", { class: "num", title: "fires whose first turn wrote the prompt cache and read none of it: the cache had expired since the previous fire" }, "cold"),
    el("th", { class: "num", title: "fires whose first turn READ the cache: the prefix survived the interval" }, "warm"),
    el("th", { class: "num", title: "prompt tokens re-written by cold first turns" }, "re-written"),
    el("th", { class: "num", title: "what those re-writes were billed ABOVE a cache read, at the model's recorded rates — an upper bound on what keeping the cache warm could save" }, "premium"),
    el("th", { class: "num" }, "per cold fire"),
    el("th", { class: "num", title: "of a WARM first turn's cache traffic, how much was read rather than written — the observed counterfactual, well under 100%, which is why the premium is an upper bound" }, "warm read share"),
    el("th", { class: "num", title: "cold fires whose model the recorded price table does not price: counted, never estimated" }, "unpriced"));
  const body = rows.map(([slug, r]) => rowFor(slug, r, kinds));
  return statSection("Between-runs prompt-cache gap",
    [`${fmtInt(t.cold || 0)} of ${fmtInt(t.fires || 0)} retained fires started COLD, `,
     `re-writing ${fmtNum(t.cold_tokens || 0)} prompt tokens for ${fmtUsd(t.premium_usd || 0)} `,
     `above what a cache read would have cost (${fmtUsd(t.per_cold_fire || 0)} per cold fire). `,
     `A warm first turn still writes ${pct(1 - (t.warm_read_share ?? 0))} of its traffic, so this `,
     "is an upper bound on the saving, not a banked one — and runs that fell to retention are not in it."],
    head, body);
}
