// Friendly schedule builder. `initial` is a friendly spec {frequency, time, weekday, ...};
// returns { node, value(), catchup() }. Pass opts.catchup (a string) to also offer a
// missed-run policy select — routines want it, other schedule editors do not.
// Pass opts.groupManaged ({id, name} — D71) when the routine belongs to a SCHEDULED group:
// the dropdown locks on a selected, disabled "Group managed" state linking to the group
// (the routine's own cron is suppressed by the daemon; value() returns the stored spec
// unchanged so a page save never clobbers it).

import { el } from "/static/util.js";

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

export function scheduleEditor(initial = { frequency: "manual" }, serverTz = "", opts = {}) {
  const spec = { time: "07:00", weekday: 1, day: 1, minute: 0, ...initial };
  const gm = opts.groupManaged || null;
  const freq = el("select", { ...(gm ? { disabled: true } : {}) },
    ...["manual", "hourly", "daily", "weekly", "monthly"].map((f) =>
      el("option", { value: f, ...(!gm && spec.frequency === f ? { selected: true } : {}) },
        f[0].toUpperCase() + f.slice(1))),
    ...(gm ? [el("option", { value: "group-managed", selected: true }, "Group managed")] : []));
  const time = el("input", { type: "time", value: spec.time });
  const minute = el("input", { type: "number", min: 0, max: 59, value: spec.minute, style: "width:70px" });
  const weekday = el("select", {}, ...WEEKDAYS.map((d, i) =>
    el("option", { value: i, ...(spec.weekday === i ? { selected: true } : {}) }, d)));
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
    else if (f === "weekly") detail.append(document.createTextNode("on"), weekday, document.createTextNode("at"), time);
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
      if (f === "weekly") return { frequency: "weekly", time: time.value, weekday: Number(weekday.value) };
      return { frequency: "monthly", time: time.value, day: Number(day.value) };
    },
    catchup() {
      return catchupSel ? catchupSel.value : "skip";
    },
  };
}
