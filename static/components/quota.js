// The subscription-quota readout — ONE place, two readers.
//
// A subscription quota is a property of the ACCOUNT, not of the inference transport: the
// numbers are the same whichever endpoint you ask through, so the console reads them once and
// renders them in two registers — the full per-window line on the endpoint's Settings card,
// and a single worst-window chip on the Routines header, where only "can anything still run
// today" matters.
//
// It lives here because the dashboard used to import these helpers FROM the settings view — a
// view depending on another view's module, which meant a syntax error in a 654-line settings
// file could stop the dashboard rendering, and any settings refactor was silently a dashboard
// change.

import { api } from "/static/api.js";

//: "5h 61% left (resets in 2h10m) · 7d 68% left". `remaining` is what the operator asked for;
//: the API reports `utilization` (percent USED), so the complement is computed once, server-side.
//: Only the two UNSCOPED windows have fixed names (D277) — every weekly per-model and
//: per-surface limit now arrives in `scoped` with its OWN display name, because the API is
//: moving those out of top-level keys into a `limits[]` array and a fixed label map cannot name
//: a model it was not compiled with. A limit we could not read is NAMED in `unknown` and said
//: so, never silently absent: "we cannot see it" must not render as "it is not there".
const WINDOW_LABEL = { five_hour: "5h", seven_day: "7d" };

const rel = (s) => {
  if (s == null) return "";
  const h = Math.floor(s / 3600); const m = Math.round((s % 3600) / 60);
  return h ? ` (resets in ${h}h${m ? `${m}m` : ""})` : ` (resets in ${m}m)`;
};

/** Every row the readout carries, tightest LAST in payload order: the two unscoped windows,
 *  then the scoped limits by their own names. `[label, remaining, seconds_until_reset]`.
 */
export function quotaRows(q) {
  const rows = Object.entries(q.windows || {})
    .map(([k, w]) => [WINDOW_LABEL[k] || k, w.remaining, w.seconds_until_reset]);
  // the server's own name for a scoped limit — `7d Opus`, `7d Claude Code` — so a model the
  // console never heard of still reads as itself
  for (const row of Object.values(q.scoped || {})) {
    rows.push([`7d ${row.name}`, row.remaining, row.seconds_until_reset]);
  }
  return rows;
}

export function quotaLine(q) {
  const parts = quotaRows(q)
    .map(([label, remaining, secs]) => `${label} ${Math.round(remaining)}% left` + rel(secs));
  // a limit the server could not read is said, with no figure: the alternative is a readout
  // that looks complete while a weekly cap is missing from it
  for (const name of q.unknown || []) parts.push(`${name} (unreadable)`);
  return parts.join(" · ");
}

//: A subscription quota source is independent of the inference transport. The quota is per
//: ACCOUNT, so the FIRST one answers for all of them — asking each would print the same numbers
//: twice. Silent on every failure: this is a convenience chip, not a status the page depends on.
export async function loadQuota(chip) {
  try {
    const eps = (await api("/api/settings/endpoints")).endpoints || [];
    // the endpoint whose card carries the quota line — a binding resolves AND its models are
    // Claude's; the Codex endpoint of the same proxy carries the binding but not the quota
    const cli = eps.find((e) => e.has_subscription_quota);
    if (!cli) return;
    const q = await api(`/api/settings/endpoints/${encodeURIComponent(cli.name)}/quota`);
    if (!q.supported) return;
    if (!q.ok) {
      chip.className = "chip disabled";
      chip.title = q.error || "";
      chip.textContent = "subscription quota unavailable";
      chip.hidden = false;
      return;
    }
    // ONE number on the chip: the window with least left, which is the only one that can
    // summon anybody. The full per-window breakdown belongs on the endpoint's own Settings
    // card, where it renders as wrapping prose — printing all of it HERE made a single
    // unbreakable ~120-character chip (`.chip` is white-space: nowrap) that broke the
    // horizontal viewport on a phone, which is where the operator reads this page.
    // Over EVERY row, scoped limits included (D277): the tightest cap is the one that can
    // summon anybody, and before this the chip read only the unscoped windows — so a weekly
    // Opus limit at 2% was invisible on the page the operator actually looks at.
    const rows = quotaRows(q);
    if (!rows.length) return;
    const [tightestLabel, lowest] = rows.slice().sort((a, b) => a[1] - b[1])[0];
    chip.className = `chip ${lowest <= 10 ? "blocking" : lowest <= 25 ? "partial" : "idle"}`;
    // the tooltip keeps every window, so the detail is one hover away rather than gone
    chip.title = "Claude subscription — the whole account, including your interactive "
      + `sessions\n${quotaLine(q)}`;
    // The chip needs a NOUN. Its "Claude quota" label is desktop-only (the phone header has no
    // room for both), so on the surface where this page is actually read the chip said
    // "7d sonnet 54% left" with nothing saying WHAT is 54% left — and a reader takes "7d" for
    // a countdown to something ending in seven days.
    chip.textContent = `quota · ${tightestLabel} ${Math.round(lowest)}% left`;
    chip.hidden = false;
  } catch { /* a chip that cannot load is simply not shown */ }
}
