// Triggers (routine page): event-driven fires alongside the schedule — WEBHOOK triggers (a third
// party POSTs to a hook URL) and REPORT triggers (fire when a report or message from another
// routine lands in this routine's inbox; bursts coalesce into one run per cooldown window).
//
// The list is a SETTING like every other on the page: it is edited in the draft and saved by the
// page's one accept, which adds the triggers the list gained and removes the ones it lost. What
// the server mints is IDENTITY — a trigger's id and, for a webhook, the token that is the hook's
// only auth — so a trigger that is new in the draft has no URL yet: it gets one when it is
// accepted. A row is matched to the trigger it stands for by what it configures (type, cooldown,
// daily cap), the way the accept itself matches them, so a row whose bounds are edited stands for
// a NEW trigger: accepting it replaces the old one (a webhook's URL with it), as the row says.

import { el, toast, when } from "/static/util.js";

const COOLDOWN_HINT = "minimum seconds between trigger-initiated fires — events arriving "
  + "inside the window coalesce into one run";
const CAP_HINT = "most trigger-initiated fires this routine may spend in one day (0 = "
  + "uncapped) — the backstop the cooldown cannot be: it bounds the rate, not the total. "
  + "Work that arrives past the cap waits for the next scheduled run.";
const DEFAULTS = { webhook: { cooldown_s: 60, max_fires_per_day: 0 },
                   report: { cooldown_s: 900, max_fires_per_day: 24 } };

const capOf = (t) => Number(t.max_fires_per_day ?? DEFAULTS[t.type]?.max_fires_per_day ?? 0);
const same = (row, t) => row.type === t.type
  && Number(row.cooldown_s ?? 0) === Number(t.cooldown_s ?? 0) && capOf(row) === capOf(t);

/**
 * triggersEditor({ value, described, set }) → node
 * `value` is the draft `triggers` list (type + bounds); `described` the routine detail's
 * `triggers` (the saved ones, with id, hook path and fire ledger); `set(rows)` reports an edit.
 */
export function triggersEditor({ value, described = [], set }) {
  const rows = (value || []).map((r) => ({ ...r }));
  const body = el("div", { class: "triggers-body" });
  // which saved trigger each row stood for when the editor opened — so a row edited away from
  // it can say what accepting it costs
  const origin = pair(rows);
  render();
  return el("div", {},
    el("div", { class: "muted small", style: "margin-bottom:8px" },
      "fire this routine on an external EVENT, alongside the schedule: POST anything to a ",
      "webhook trigger's hook URL and the daemon queues one run — bursts and hits during an ",
      "active run coalesce into a single fire whose run receives every payload as a message. ",
      "The URL's token is the only auth on the hook: share it like a secret."),
    body);

  function pair(list) {
    const pool = [...described];
    return list.map((row) => {
      const i = pool.findIndex((t) => same(row, t));
      return i < 0 ? null : pool.splice(i, 1)[0];
    });
  }

  function report() { set(rows.map((r) => ({ ...r }))); }

  function render() {
    const paired = pair(rows);
    const hasReport = rows.some((t) => t.type === "report");
    body.replaceChildren(
      rows.length ? el("div", {}, ...rows.map((r, i) => row(r, i, paired[i])))
        : el("div", { class: "muted small" }, "no triggers — this routine fires on schedule, "
            + "manually or when you answer a question it asked; a report from another routine "
            + "waits for its next run"),
      el("div", { class: "row mt", style: "gap:8px;flex-wrap:wrap" },
        el("button", { type: "button", class: "btn", onclick: () => add("webhook") },
          "+ add webhook trigger"),
        el("button", { type: "button", class: "btn", disabled: hasReport ? "" : null,
          title: hasReport
            ? "one inbox, one watcher — this routine already has a report trigger (edit its cooldown above)"
            : "fire this routine when a report or message from ANOTHER ROUTINE lands in its inbox — "
              + "for a routine whose job is its inbox; bursts within the cooldown become one run",
          onclick: () => add("report") }, "+ add report trigger")));
  }

  function add(type) {
    rows.push({ type, ...DEFAULTS[type] });
    origin.push(null);
    render();
    report();
  }

  function row(t, i, live) {
    const was = origin[i];
    const replaced = !live && was && t.type === "webhook";
    const meta = live ? el("div", { class: "row muted small", style: "gap:14px;flex-wrap:wrap" },
      el("span", {}, "last fired · ", live.last_fired ? when(live.last_fired) : "never"),
      el("span", {}, `fired events · ${live.events || 0}`),
      el("span", { title: "events recorded but not yet turned into a run (coalescing)" },
        `pending · ${live.pending || 0}`),
      el("span", { title: CAP_HINT }, `fires today · ${live.fires_today || 0}`)) : null;
    const urlLine = t.type === "webhook"
      ? (live ? webhookUrl(live)
        : el("div", { class: replaced ? "tr-warn small" : "muted small" }, replaced
            ? "accepting this change mints a NEW hook URL — the current one stops working"
            : "its hook URL is minted when you accept"))
      : t.type === "report"
        ? el("div", { class: "muted small" },
            "fires when a report or message lands in this routine's inbox — deliveries within "
            + "the cooldown coalesce into one run")
        : el("div", { class: "muted small" }, "reserved trigger type — nothing fires it yet");
    return el("div", { class: "trigger-row", "data-trigger": live?.id || "new" },
      el("div", { class: "row spread", style: "margin-bottom:6px" },
        el("div", { class: "row", style: "gap:10px" },
          el("span", { class: "ref-tag" }, t.type),
          el("span", { class: "muted small" }, live ? live.id : "new — created when you accept")),
        el("button", { type: "button", class: "btn small danger", title: "remove this trigger — "
          + "on accept it stops firing; a webhook's URL stops working",
          onclick: () => { rows.splice(i, 1); origin.splice(i, 1); render(); report(); } },
          "remove")),
      urlLine,
      el("div", { class: "row small", style: "gap:14px;flex-wrap:wrap;margin-top:4px" },
        numberCell(t, i, "cooldown_s", "cooldown", "s", COOLDOWN_HINT, "cooldown-in"),
        numberCell(t, i, "max_fires_per_day", "daily cap", "per day", CAP_HINT, "cap-in")),
      meta);
  }

  // A bound is edited in place; a value that is not a whole number snaps back to what it was.
  function numberCell(t, i, key, label, unit, hint, cls) {
    const input = el("input", { type: "number", min: "0", value: String(t[key] ?? 0), class: cls,
      style: "width:78px", title: hint, "data-nopersist": true,
      onchange: () => {
        const next = parseInt(input.value, 10);
        if (!Number.isFinite(next) || next < 0) {
          input.value = String(t[key] ?? 0);
          toast(`${label} is a whole number, 0 or more`, 4000, { error: true });
          return;
        }
        rows[i] = { ...t, [key]: next };
        render();
        report();
      } });
    return el("label", { class: "row", style: "gap:5px;align-items:center", title: hint },
      label, input, unit);
  }

  function webhookUrl(t) {
    const url = `${location.origin}${t.url_path}`;
    const input = el("input", { type: "text", readonly: true, value: url,
      class: "code", style: "flex:1;min-width:240px", onclick: (e) => e.target.select() });
    const copy = el("button", { type: "button", class: "btn small", onclick: async () => {
      try { await navigator.clipboard.writeText(url); toast("hook URL copied"); }
      catch { input.select(); toast("clipboard blocked — URL selected, press Ctrl-C"); }
    } }, "copy");
    return el("div", { class: "row", style: "gap:8px;margin-bottom:6px" }, input, copy);
  }
}
