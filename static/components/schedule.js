// Friendly schedule builder. `initial` is a friendly spec {frequency, time, weekday, ...};
// returns { node, value() } where value() yields the current spec for the API.

import { el } from "/static/util.js";

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

export function scheduleEditor(initial = { frequency: "manual" }, serverTz = "") {
  const spec = { time: "07:00", weekday: 1, day: 1, minute: 0, ...initial };
  const freq = el("select", {},
    ...["manual", "hourly", "daily", "weekly", "monthly"].map((f) =>
      el("option", { value: f, ...(spec.frequency === f ? { selected: true } : {}) },
        f[0].toUpperCase() + f.slice(1))));
  const time = el("input", { type: "time", value: spec.time });
  const minute = el("input", { type: "number", min: 0, max: 59, value: spec.minute, style: "width:70px" });
  const weekday = el("select", {}, ...WEEKDAYS.map((d, i) =>
    el("option", { value: i, ...(spec.weekday === i ? { selected: true } : {}) }, d)));
  const day = el("input", { type: "number", min: 1, max: 31, value: spec.day, style: "width:70px" });
  const detail = el("span", { class: "row", style: "gap:6px" });

  function sync() {
    const f = freq.value;
    detail.replaceChildren();
    if (f === "hourly") detail.append(document.createTextNode("at minute"), minute);
    else if (f === "daily") detail.append(document.createTextNode("at"), time);
    else if (f === "weekly") detail.append(document.createTextNode("on"), weekday, document.createTextNode("at"), time);
    else if (f === "monthly") detail.append(document.createTextNode("on day"), day, document.createTextNode("at"), time);
    else detail.append(el("span", { class: "muted" }, "runs only when you click Run now"));
  }
  freq.addEventListener("change", sync);
  sync();

  const node = el("div", {},
    el("div", { class: "row", style: "gap:8px" }, freq, detail),
    serverTz ? el("div", { class: "muted small", style: "margin-top:4px" },
      `times are in the server's timezone (${serverTz})`) : null);

  return {
    node,
    value() {
      const f = freq.value;
      if (f === "manual") return { frequency: "manual" };
      if (f === "hourly") return { frequency: "hourly", minute: Number(minute.value) };
      if (f === "daily") return { frequency: "daily", time: time.value };
      if (f === "weekly") return { frequency: "weekly", time: time.value, weekday: Number(weekday.value) };
      return { frequency: "monthly", time: time.value, day: Number(day.value) };
    },
  };
}
