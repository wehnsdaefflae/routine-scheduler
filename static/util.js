// Small DOM + formatting helpers (no framework, no build).

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

export const esc = (s) => String(s ?? "");

export function fmtTs(ts) {
  // "20260708-220004" → "2026-07-08 22:00"
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(ts || "");
  return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}` : (ts || "");
}

export function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function relTime(iso) {
  if (!iso) return "";
  const diff = (new Date(iso) - Date.now()) / 1000;
  const abs = Math.abs(diff);
  const units = [[86400, "d"], [3600, "h"], [60, "m"]];
  for (const [s, label] of units) {
    if (abs >= s) return `${diff < 0 ? "" : "in "}${Math.round(abs / s)}${label}${diff < 0 ? " ago" : ""}`;
  }
  return diff < 0 ? "just now" : "in <1m";
}

export function fmtTokens(usage) {
  if (!usage) return "";
  const f = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n ?? 0));
  return `${f(usage.in || 0)} in / ${f(usage.out || 0)} out`;
}

export function chip(text, cls = "") {
  return el("span", { class: `chip ${cls}` }, text);
}

// A tag pill. onClick makes it a filter toggle (active → highlighted); onRemove adds an × for
// inline editing. `meta` is styled distinctly.
export function tagChip(text, { onClick, onRemove, active } = {}) {
  const cls = ["tag", text === "meta" ? "meta" : "", onClick ? "click" : "", active ? "on" : ""]
    .filter(Boolean).join(" ");
  const attrs = { class: cls };
  if (onClick) attrs.onclick = onClick;
  const node = el("span", attrs, text);
  if (onRemove) node.append(el("span", { class: "x", title: "remove",
    onclick: (e) => { e.stopPropagation(); onRemove(); } }, "×"));
  return node;
}

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

// A friendly schedule builder. `initial` is a friendly spec {frequency, time, weekday, ...}.
// Returns { node, value() } where value() yields the current friendly spec for the API.
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
    detail.innerHTML = "";
    if (f === "hourly") detail.append(document.createTextNode("at minute"), minute);
    else if (f === "daily") detail.append(document.createTextNode("at"), time);
    else if (f === "weekly") detail.append(document.createTextNode("on"), weekday, document.createTextNode("at"), time);
    else if (f === "monthly") detail.append(document.createTextNode("on day"), day, document.createTextNode("at"), time);
    else if (f === "manual") detail.append(el("span", { class: "muted" }, "runs only when you click Run now"));
  }
  freq.addEventListener("change", sync);
  sync();

  const node = el("div", {},
    el("div", { class: "row", style: "gap:8px" }, freq, detail),
    serverTz ? el("div", { class: "muted", style: "font-size:12px;margin-top:4px" },
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

// Human-readable descriptions for the self-* standards + util confirmation, shown in the UI.
export const TOGGLE_INFO = {
  audit: ["Self-audit", "Each run, judge the routine's own health across six lenses (goal drift, broken steps, improvement openings) before finishing."],
  improve: ["Self-improvement", "Act on the audit in the same run — heal broken steps, correct drift, tune configuration. Turn this off to freeze the routine's process."],
  ledger: ["Change journal (LEDGER)", "Keep an append-only record of what changed each run and why, so the routine never re-tries a known dead end."],
  fresh_eyes: ["Fresh-eyes review", "Periodically re-read the routine's accumulated output as a first-time reader to catch slow drift and 'functional but bad' rot."],
  hygiene: ["File hygiene", "Keep the routine's own files small, present-tense, and consolidated as they grow."],
  confirm_util_changes: ["Approve new tools", "Ask you to approve before the routine creates or revises a global util. Off = the routine adds tools autonomously (still selftested)."],
};

let toastTimer = null;
export function toast(msg, ms = 2600) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), ms);
}

export function confirmDialog(msg) {
  return window.confirm(msg);
}
