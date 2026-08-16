// Friendly schedule builder. `initial` is a friendly spec {frequency, time, weekdays, ...};
// returns { node, value(), catchup() }. Pass opts.catchup (a string) to also offer a
// missed-run policy select — routines want it, other schedule editors do not.
// Pass opts.groupManaged ({id, name} — D71) when the routine belongs to a SCHEDULED group:
// the dropdown locks on a selected, disabled "Group managed" state linking to the group
// (the routine's own cron is suppressed by the daemon; value() returns the stored spec
// unchanged so a page save never clobbers it).
//
// Also home to the client half of the friendly vocabulary: cronToFriendly mirrors the
// server's rsched.schedule.cron_to_friendly (same shapes, same custom fallback), and
// specAtInstant re-times a spec to a dropped instant — what the week strip's drag-to-
// reschedule sends back through the schedule.friendly PATCH both routines and groups take.

import { el } from "/static/util.js";

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

export function scheduleEditor(initial = { frequency: "manual" }, serverTz = "", opts = {}) {
  const spec = { time: "07:00", weekdays: [1], day: 1, minute: 0, ...initial };
  const gm = opts.groupManaged || null;
  const freq = el("select", { ...(gm ? { disabled: true } : {}) },
    ...["manual", "hourly", "daily", "weekly", "monthly"].map((f) =>
      el("option", { value: f, ...(!gm && spec.frequency === f ? { selected: true } : {}) },
        f[0].toUpperCase() + f.slice(1))),
    ...(gm ? [el("option", { value: "group-managed", selected: true }, "Group managed")] : []));
  const time = el("input", { type: "time", value: spec.time });
  const minute = el("input", { type: "number", min: 0, max: 59, value: spec.minute, style: "width:70px" });
  // weekly is a SET of days (F347, user order 2026-08-15 — GCal's "repeat on: S M T W T
  // F S"): seven toggles instead of one select, so "not on weekends" is four clicks.
  const dayBoxes = WEEKDAYS.map((d, i) => {
    const box = el("input", { type: "checkbox", "data-nopersist": true,
      ...(Array.isArray(spec.weekdays) && spec.weekdays.includes(i) ? { checked: "" } : {}) });
    return { box, node: el("label", { class: "day-chip", title: d }, box,
      el("span", {}, d.slice(0, 3))) };
  });
  const weekdayRow = el("span", { class: "row", style: "gap:4px;flex-wrap:wrap" },
    dayBoxes.map((c) => c.node));
  const day = el("input", { type: "number", min: 1, max: 31, value: spec.day, style: "width:70px" });
  const detail = el("span", { class: "row", style: "gap:6px" });

  // catchup: what to do when a scheduled fire was missed (daemon down / overrun). Only offered
  // when the caller opts in, and only meaningful for a real schedule — hidden for "manual".
  const hasCatchup = opts.catchup !== undefined;
  const catchupSel = hasCatchup
    ? el("select", {}, ["skip", "run_once"].map((c) =>
        el("option", { value: c, ...(opts.catchup === c ? { selected: true } : {}) },
          c === "skip" ? "skip a missed run" : "run once if missed")))
    : null;
  const catchupRow = hasCatchup
    ? el("label", { class: "row mt", style: "gap:8px" },
        el("span", { class: "muted small" }, "if a run was missed"), catchupSel)
    : null;

  function sync() {
    const f = freq.value;
    detail.replaceChildren();
    if (gm) {
      detail.append(el("span", { class: "muted" }, "fires with group "),
        el("a", { href: "#/routines" }, gm.name || gm.id),
        el("span", { class: "muted" }, " — this routine's own schedule is suppressed while the group is scheduled"));
    } else if (f === "hourly") detail.append(document.createTextNode("at minute"), minute);
    else if (f === "daily") detail.append(document.createTextNode("at"), time);
    else if (f === "weekly") detail.append(document.createTextNode("on"), weekdayRow, document.createTextNode("at"), time);
    else if (f === "monthly") detail.append(document.createTextNode("on day"), day, document.createTextNode("at"), time);
    else detail.append(el("span", { class: "muted" }, "runs only when you click Run now"));
    if (catchupRow) catchupRow.style.display = (f === "manual" || gm) ? "none" : "";
  }
  freq.addEventListener("change", sync);
  sync();

  const node = el("div", {},
    el("div", { class: "row", style: "gap:8px" }, freq, detail),
    catchupRow,
    serverTz ? el("div", { class: "muted small", style: "margin-top:4px" },
      `times are in the server's timezone (${serverTz})`) : null);

  return {
    node,
    value() {
      // group-managed: the stored spec rides back UNCHANGED — the suppression lives in
      // the daemon, and a save from this page must not rewrite the routine's own cron
      if (gm) return initial;
      const f = freq.value;
      if (f === "manual") return { frequency: "manual" };
      if (f === "hourly") return { frequency: "hourly", minute: Number(minute.value) };
      if (f === "daily") return { frequency: "daily", time: time.value };
      if (f === "weekly") return { frequency: "weekly", time: time.value,
        weekdays: dayBoxes.flatMap((c, i) => (c.box.checked ? [i] : [])) };
      return { frequency: "monthly", time: time.value, day: Number(day.value) };
    },
    catchup() {
      return catchupSel ? catchupSel.value : "skip";
    },
  };
}

// Cron string → friendly spec, mirroring rsched.schedule.cron_to_friendly: the four simple
// cadences round-trip, anything else comes back {frequency: "custom", cron} (read-only in
// every editor, and not drag-reschedulable).
// A cron day-of-week field as a sorted weekday set, or null when it isn't one — the
// client half of the server's _parse_dow (digits, commas, simple ranges; anything else
// stays "custom").
function parseDow(dow) {
  const days = new Set();
  for (const part of dow.split(",")) {
    if (/^\d$/.test(part)) days.add(+part);
    else if (/^\d-\d$/.test(part)) {
      const [a, b] = part.split("-").map(Number);
      if (a > b) return null;
      for (let i = a; i <= b; i++) days.add(i);
    } else return null;
  }
  const out = [...days].sort((a, b) => a - b);
  return out.length && out.every((x) => x >= 0 && x <= 6) ? out : null;
}

export function cronToFriendly(cron) {
  const c = (cron || "").trim();
  if (!c) return { frequency: "manual" };
  const p = c.split(/\s+/);
  if (p.length !== 5) return { frequency: "custom", cron: c };
  const [mn, hr, dom, mon, dow] = p;
  const d = (v) => /^\d+$/.test(v);
  if (mon === "*" && dom === "*" && dow === "*" && hr === "*" && d(mn))
    return { frequency: "hourly", minute: +mn };
  if (mon === "*" && d(mn) && d(hr)) {
    const time = `${String(+hr).padStart(2, "0")}:${String(+mn).padStart(2, "0")}`;
    if (dom === "*" && dow === "*") return { frequency: "daily", time };
    if (dom === "*") {
      const days = parseDow(dow);
      if (days) return { frequency: "weekly", time, weekdays: days };
    }
    if (dow === "*" && d(dom)) return { frequency: "monthly", time, day: +dom };
  }
  return { frequency: "custom", cron: c };
}

// Re-time a friendly spec to fire at `date`, keeping its cadence: daily keeps daily but takes
// the drop's time-of-day, weekly also takes the drop's weekday, monthly its day-of-month,
// hourly its minute. Times are read in the SERVER's timezone (`tz`) — that is the zone the
// cron is stored in — falling back to the browser's when unknown. Returns null for manual and
// custom specs: those have no draggable shape.
export function specAtInstant(spec, date, tz = "") {
  const freq = spec?.frequency;
  if (!freq || freq === "manual" || freq === "custom") return null;
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: tz || undefined, hourCycle: "h23",
    hour: "2-digit", minute: "2-digit", weekday: "short", day: "numeric" }).formatToParts(date);
  const get = (t) => parts.find((x) => x.type === t)?.value || "";
  const time = `${get("hour")}:${get("minute")}`;
  if (freq === "hourly") return { frequency: "hourly", minute: +get("minute") };
  if (freq === "daily") return { frequency: "daily", time };
  if (freq === "weekly") {
    // a one-day cadence follows the drop onto its new weekday; a multi-day SET keeps its
    // days (dragging one occurrence must not collapse "Mon-Fri" to just the drop day)
    // and takes only the new time-of-day
    const days = Array.isArray(spec.weekdays) ? spec.weekdays : [];
    const drop = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(get("weekday"));
    return { frequency: "weekly", time, weekdays: days.length > 1 ? days : [drop] };
  }
  return { frequency: "monthly", time, day: +get("day") };
}
