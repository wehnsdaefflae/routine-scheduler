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
