// Themed modal confirm/prompt — the console's replacement for every native confirm() /
// prompt(): same overlay language as the token gate, keyboard-first (Enter confirms,
// Esc or an overlay click cancels), promise-based so call sites stay one line:
//   if (!(await confirmDialog("Delete X?"))) return;
//   const name = await promptDialog("new tag"); if (name == null) return;

import { el } from "/static/util.js";

function modal({ message, input = null, confirmLabel, danger }) {
  return new Promise((resolve) => {
    const cancelValue = input ? null : false;
    const done = (value) => { overlay.remove(); resolve(value); };
    const ok = el("button", { class: danger ? "btn danger" : "btn primary" }, confirmLabel);
    const cancel = el("button", { class: "btn" }, "cancel");
    const overlay = el("div", { class: "modal-overlay" },
      el("div", { class: "panel" },
        el("div", { class: "dlg-msg" }, message),
        input,
        el("div", { class: "row mt", style: "justify-content:flex-end; gap:8px" },
          cancel, ok)));
    ok.onclick = () => done(input ? input.value.trim() : true);
    cancel.onclick = () => done(cancelValue);
    overlay.onclick = (e) => { if (e.target === overlay) done(cancelValue); };
    overlay.onkeydown = (e) => {
      if (e.key === "Escape") { e.preventDefault(); done(cancelValue); }
      else if (e.key === "Enter") { e.preventDefault(); ok.onclick(); }
    };
    document.body.append(overlay);
    (input || ok).focus();
  });
}

/** Themed confirm(): resolves true/false. Destructive by default (red confirm). */
export function confirmDialog(message, { confirmLabel = "confirm", danger = true } = {}) {
  return modal({ message, confirmLabel, danger });
}

/** Themed prompt(): resolves the trimmed string, or null on cancel. */
export function promptDialog(message, { placeholder = "", value = "" } = {}) {
  const input = el("input", { type: "text", placeholder, "data-nopersist": true,
    style: "width:100%; margin-top:10px" });
  input.value = value;
  return modal({ message, input, confirmLabel: "ok", danger: false });
}
