// Friendly schedule builder. `initial` is a friendly spec {frequency, time, weekdays, ...};
// returns { node, value(), catchup() }. Pass opts.catchup (a string) to also offer a
// missed-run policy select — routines want it, other schedule editors do not.
// Pass opts.laneManaged ({id, name} — D71) when the routine belongs to a SCHEDULED lane:
// the dropdown locks on a selected, disabled "Lane managed" state linking to the lane
// (the routine's own cron is suppressed by the daemon; value() returns the stored spec
// unchanged so a page save never clobbers it).
// In that state the caller may also pass opts.onClearCron — a save the editor awaits when the
// reader clears a cron the lane already suppresses. It appears ONLY where the stored spec still
// names one, because it is the single schedule edit a lane-managed routine has left: the lane
// goes on deciding when the routine fires; the file stops recording a time of its own.
// Pass opts.onChange to hear every edit as it happens — `{friendly: value(), catchup:
// catchup()}` — for a caller that keeps a draft (the routine page's settings form) rather than
// saving on a button of its own. CUSTOM is the cadence the reader writes as a five-field cron
// — for what the friendly vocabulary cannot say ("06:00 and 14:00") — and a stored cron the
// others cannot express opens on it unchanged, so building the editor never rewrites it.
// Switching to it starts the field from the cadence it replaces, ready to be extended.
//
// Also home to the client half of the friendly vocabulary: cronToFriendly mirrors the
// server's rsched.schedule.cron_to_friendly (same shapes, same custom fallback), and
// specAtInstant re-times a spec to a dropped instant — what the week strip's drag-to-
// reschedule sends back through the schedule.friendly PATCH both routines and lanes take.

import { el } from "/static/util.js";

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

export function scheduleEditor(initial = { frequency: "manual" }, serverTz = "", opts = {}) {
  const spec = { time: "07:00", weekdays: [1], day: 1, minute: 0, ...initial };
  const lm = opts.laneManaged || null;
  const frequencies = lm ? [] : ["manual", "hourly", "daily", "weekly", "monthly", "custom"];
  if (opts.allowDisabled) frequencies.unshift("disabled");
  const label = (f) => (f === "custom" ? "Custom cron" : f[0].toUpperCase() + f.slice(1));
  const freq = el("select", { "data-nopersist": true,
    ...(lm && !opts.allowDisabled ? { disabled: true } : {}) },
    ...frequencies.map((f) =>
      el("option", { value: f, ...(spec.frequency === f ? { selected: true } : {}) }, label(f))),
    ...(lm ? [el("option", { value: "lane-managed",
      selected: spec.frequency !== "disabled" }, "Lane managed")] : []));
  const time = el("input", { type: "time", value: spec.time, "data-nopersist": true });
  const minute = el("input", { type: "number", min: 0, max: 59, value: spec.minute,
    style: "width:70px", "data-nopersist": true });
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
  const day = el("input", { type: "number", min: 1, max: 31, value: spec.day,
    style: "width:70px", "data-nopersist": true });
  const cron = el("input", { type: "text", value: spec.cron || "", "data-custom-cron": "",
    "data-nopersist": true, spellcheck: "false", placeholder: "0 6,14 * * *",
    "aria-label": "cron expression", class: "mono", style: "width:16ch" });
  // the shape only — five fields; whether each field parses is the server's answer on save
  const cronShape = el("span", { class: "muted small" });
  const checkShape = () => {
    const n = cron.value.trim().split(/\s+/).filter(Boolean).length;
    cron.setAttribute("aria-invalid", String(n !== 5));
    cronShape.textContent = n === 5 ? "minute · hour · day of month · month · day of week"
      : `a cron has five fields (minute · hour · day of month · month · day of week) — this has ${n}`;
  };
  const detail = el("span", { class: "row", style: "gap:6px;flex-wrap:wrap" });

  // catchup: what to do when a scheduled fire was missed (daemon down / overrun). Only offered
  // when the caller opts in, and only meaningful for a real schedule — hidden for "manual".
  const hasCatchup = opts.catchup !== undefined;
  const catchupSel = hasCatchup
    ? el("select", { "data-nopersist": true }, ["skip", "run_once"].map((c) =>
        el("option", { value: c, ...(opts.catchup === c ? { selected: true } : {}) },
          c === "skip" ? "skip a missed run" : "run once if missed")))
    : null;
  const catchupRow = hasCatchup
    ? el("label", { class: "row mt", style: "gap:8px" },
        el("span", { class: "muted small" }, "if a run was missed"), catchupSel)
    : null;

  // The other half of D71. The lane owns WHEN a lane-managed routine fires, so every control
  // above is locked — but the routine's own file can still record a cron the daemon suppresses.
  // A file naming a time nothing acts on is what the surface's `schedule:cron` row reports.
  // Clearing that line changes no firing at all, which is why it survives the lock; it is
  // offered only where there IS a stored cadence to clear — never to a caller with no save.
  // `data-clear-cron` is the address the surface's fix link lands on.
  const staleCron = lm && spec.frequency && spec.frequency !== "manual";
  async function clearCron() {
    clearBtn.disabled = true;
    try {
      await opts.onClearCron();
      clearRow.replaceChildren(el("div", { class: "muted small" },
        "cleared — this file records no time of its own; the lane's clock is the only one"));
    } catch { clearBtn.disabled = false; }   // the toast already said why; let it be retried
  }
  const clearBtn = staleCron && opts.onClearCron
    ? el("button", { type: "button", class: "btn small", "data-clear-cron": "",
        onclick: clearCron }, "clear the stored cron")
    : null;
  const clearRow = clearBtn
    ? el("div", { class: "mt" },
        el("div", { class: "muted small" }, "this file records a cron of its own — ",
          el("code", {}, spec.cron || spec.frequency),
          " — which nothing fires on while the lane is scheduled"),
        el("div", { class: "row mt", style: "gap:8px" }, clearBtn,
          el("span", { class: "muted small" },
            "clearing changes no firing: the routine still runs with the lane, on the lane's "
            + "clock. What changes is the file — it stops claiming a time of its own.")))
    : null;

  let shown = freq.value;
  function sync() {
    const f = freq.value;
    // entering Custom from a cadence: start from that cadence's cron, so "daily at 06:00"
    // becomes "0 6 * * *" to extend rather than a blank field to write from memory
    if (f === "custom" && shown !== "custom" && !cron.value.trim()) {
      cron.value = friendlyToCron(specFor(shown));
    }
    shown = f;
    detail.replaceChildren();
    if (f === "disabled") {
      detail.append(el("span", { class: "muted" }, "no new runs — including manual and triggered starts; existing runs are unchanged"));
    } else if (lm) {
      detail.append(el("span", { class: "muted" }, "fires with lane "),
        el("a", { href: "#/routines" }, lm.name || lm.id),
        el("span", { class: "muted" }, " — this routine's own schedule is suppressed while the lane is scheduled"));
    } else if (f === "hourly") detail.append(document.createTextNode("at minute"), minute);
    else if (f === "daily") detail.append(document.createTextNode("at"), time);
    else if (f === "weekly") detail.append(document.createTextNode("on"), weekdayRow, document.createTextNode("at"), time);
    else if (f === "monthly") detail.append(document.createTextNode("on day"), day, document.createTextNode("at"), time);
    else if (f === "custom") { checkShape(); detail.append(cron, cronShape); }
    else detail.append(el("span", { class: "muted" }, "runs only when you click Run now"));
    if (catchupRow) catchupRow.style.display = (f === "manual" || f === "disabled" || lm) ? "none" : "";
  }
  freq.addEventListener("change", sync);
  sync();
  // every control that moves the value reports it — only a change the reader made, never the
  // build, so an untouched editor never reads as an edit
  const notify = () => opts.onChange?.({ friendly: api.value(), catchup: api.catchup() });
  for (const input of [freq, time, minute, day, catchupSel, ...dayBoxes.map((c) => c.box)]) {
    input?.addEventListener("change", notify);
  }
  cron.addEventListener("input", () => { checkShape(); notify(); });

  const node = el("div", {},
    el("div", { class: "row", style: "gap:8px" }, freq, detail),
    catchupRow,
    clearRow,
    serverTz ? el("div", { class: "muted small", style: "margin-top:4px" },
      `times are in the server's timezone (${serverTz})`) : null);

  // the spec the controls hold for cadence `f` — value() for the selected one, and the
  // cadence Custom starts from
  function specFor(f) {
    // lane-managed: the stored spec rides back UNCHANGED — the suppression lives in
    // the daemon, and a save from this page must not rewrite the routine's own cron
    if (f === "disabled") return { frequency: "disabled" };
    if (lm) return initial.frequency === "disabled" ? { frequency: "manual" } : initial;
    if (f === "custom") return { frequency: "custom", cron: cron.value.trim() };
    if (f === "manual") return { frequency: "manual" };
    if (f === "hourly") return { frequency: "hourly", minute: Number(minute.value) };
    if (f === "daily") return { frequency: "daily", time: time.value };
    if (f === "weekly") return { frequency: "weekly", time: time.value,
      weekdays: dayBoxes.flatMap((c, i) => (c.box.checked ? [i] : [])) };
    return { frequency: "monthly", time: time.value, day: Number(day.value) };
  }

  const api = {
    node,
    value() {
      return specFor(freq.value);
    },
    catchup() {
      return catchupSel ? catchupSel.value : "skip";
    },
  };
  return api;
}

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

// Friendly spec → cron, the client half of rsched.schedule.friendly_to_cron — only ever the
// starting text of a Custom field, so a spec it cannot render starts that field blank.
function friendlyToCron(s) {
  const [hh, mm] = String(s.time || "07:00").split(":").map(Number);
  if (s.frequency === "hourly") return `${s.minute} * * * *`;
  if (s.frequency === "daily") return `${mm} ${hh} * * *`;
  if (s.frequency === "weekly" && s.weekdays?.length) return `${mm} ${hh} * * ${s.weekdays.join(",")}`;
  if (s.frequency === "monthly") return `${mm} ${hh} ${s.day} * *`;
  return "";
}

// Cron string → friendly spec, mirroring rsched.schedule.cron_to_friendly: the four simple
// cadences round-trip, anything else comes back {frequency: "custom", cron} (edited as cron
// text in every editor, and not drag-reschedulable).
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
