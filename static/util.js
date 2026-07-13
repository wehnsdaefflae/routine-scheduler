// Small DOM + formatting helpers (no framework, no build). el() is textContent-only — it has
// no HTML pathway, so strings passed to it can never become markup. The ONE sanctioned
// innerHTML pathway is md.js (simple markdown for model-authored prose), which escapes first.

// localStorage can throw (private mode / embedded contexts) — degrade to in-memory.
const mem = new Map();
export const storage = {
  get(key) { try { return localStorage.getItem(key); } catch { return mem.get(key) ?? null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { mem.set(key, value); } },
  remove(key) { try { localStorage.removeItem(key); } catch { mem.delete(key); } },
};

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

// ---- time: absolute + relative, always together ---------------------------------------------
// Accepts an ISO string or a run-ts ("20260708-220004").
export function toDate(v) {
  if (!v) return null;
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})?/.exec(String(v));
  if (m) return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
  const d = new Date(v);
  return isNaN(d) ? null : d;
}

const p2 = (n) => String(n).padStart(2, "0");

export function fmtAbs(v) {
  const d = toDate(v);
  if (!d) return String(v || "");
  return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`;
}

export const fmtTs = fmtAbs;   // run-ts and ISO render identically

export function fmtTime(v) {
  const d = toDate(v);
  return d ? `${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}` : "";
}

export function relTime(v) {
  const d = toDate(v);
  if (!d) return "";
  const diff = (d - Date.now()) / 1000;
  const abs = Math.abs(diff);
  for (const [s, label] of [[86400, "d"], [3600, "h"], [60, "m"]]) {
    if (abs >= s) return diff < 0 ? `${Math.round(abs / s)}${label} ago` : `in ${Math.round(abs / s)}${label}`;
  }
  return diff < 0 ? "just now" : "in <1m";
}

// A timestamp element showing the absolute time AND its relative age (ops users need both).
// mode "abs" (default): "2026-07-08 22:14 · 3d ago". mode "rel": relative visible, absolute
// in the title — for tight spots. The relative part self-refreshes (see startTimeTicker).
export function when(v, { mode = "abs" } = {}) {
  const d = toDate(v);
  if (!d) return el("span", { class: "when" }, String(v || ""));
  const rel = el("span", { class: "rel", "data-when": d.toISOString() }, relTime(d));
  const node = el("span", { class: `when${mode === "rel" ? " rel-first" : ""}`, title: `${fmtAbs(d)} (${d.toISOString()})` },
    el("span", { class: "abs" }, fmtAbs(d)), rel);
  return node;
}

let ticker = null;
export function startTimeTicker() {
  if (ticker) return;
  ticker = setInterval(() => {
    document.querySelectorAll("[data-when]").forEach((n) => {
      n.textContent = relTime(n.dataset.when);
    });
  }, 30000);
}

export function fmtDur(secs) {
  if (secs == null || secs < 0 || isNaN(secs)) return "";
  if (secs < 60) return `${Math.round(secs)}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${Math.round(secs % 60)}s`;
  return `${Math.floor(secs / 3600)}h ${Math.round((secs % 3600) / 60)}m`;
}

export function fmtTokens(usage) {
  if (!usage) return "";
  const f = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n ?? 0));
  const cost = usage.cost > 0
    ? ` · $${usage.cost >= 0.1 ? usage.cost.toFixed(2) : usage.cost.toFixed(4)}` : "";
  // cache traffic (cheap re-reads, ~0.1x) is reported separately from fresh input —
  // showing it makes cache hit rates visible per run/turn
  const cached = usage.cached_in > 0 ? ` (+${f(usage.cached_in)} cached)` : "";
  return `${f(usage.in || 0)} in${cached} / ${f(usage.out || 0)} out${cost}`;
}

export function fmtCost(usage) {
  if (!usage || !(usage.cost > 0)) return "";
  return `$${usage.cost >= 0.1 ? usage.cost.toFixed(2) : usage.cost.toFixed(4)}`;
}

// ---- chips / tags ----------------------------------------------------------------------------
export function chip(text, cls = "") {
  return el("span", { class: `chip ${cls}` }, text);
}

// A permission's machine-enforced grants (from the LIBRARY copy) as one human line, e.g.
// "grants write_util (every change needs your approval) · util: discord". Empty when the
// doc grants nothing.
export function grantsSummary(g) {
  const caps = [...(g?.actions || []), ...(g?.utils || []).map((u) => `util: ${u}`)];
  if (g?.runs) caps.push(g.runs === "last" ? "read the previous run" : "read all previous runs");
  if (!caps.length) return "";
  const confirm = (g.actions || []).includes("write_util")
    ? { always: " (every util change needs your approval)",
        creations: " (new utils need your approval; revisions are auto-approved)",
        never: " (no approval needed)" }[g.confirm] || ""
    : "";
  return `grants ${caps.join(" · ")}${confirm}`;
}

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

// ---- loading / empty / busy -----------------------------------------------------------------
// An instant skeleton so no view ever paints blank. widths are % strings for variety.
export function skeleton(rows = ["40%", "100%", "100%", "65%"]) {
  return el("div", { class: "skel-block", "aria-hidden": "true" },
    rows.map((w) => el("div", { class: "skel", style: `width:${w}` })));
}

export function emptyState(glyph, title, detail) {
  return el("div", { class: "empty" },
    el("span", { class: "glyph", "aria-hidden": "true" }, glyph),
    el("div", { class: "t" }, title),
    detail ? el("div", { class: "d" }, detail) : null);
}

export function busy(message) {
  return el("div", { class: "busy" },
    el("span", { class: "spinner", "aria-hidden": "true" }),
    el("span", {}, message));
}

// The live-stream status pill fed by stream.js liveTail's onStatus.
export function streamStatus() {
  const node = el("span", { class: "stream-status", hidden: true });
  const LABEL = { live: "live", reconnecting: "reconnecting…", ended: "stream ended" };
  return {
    node,
    set(state) {
      node.hidden = false;
      node.className = `stream-status ${state}`;
      node.textContent = LABEL[state] || state;
    },
  };
}

// ---- toast -------------------------------------------------------------------------------------
let toastTimer = null;
export function toast(msg, ms = 2600, { error = false } = {}) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = error ? "err" : "";
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), ms);
  if (error) {
    // error toasts are UI-friction evidence for the improve-ui audit lens
    import("/static/trace.js").then(({ trace }) => trace("error", "toast", msg)).catch(() => {});
  }
}
