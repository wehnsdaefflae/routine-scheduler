// Themed modal confirm/prompt — the console's replacement for every native confirm() /
// prompt(): same overlay language as the token gate, keyboard-first (Enter confirms,
// Esc or an overlay click cancels), promise-based so call sites stay one line:
//   if (!(await confirmDialog("Delete X?"))) return;
//   const name = await promptDialog("new tag"); if (name == null) return;
//
// `openModal` is the shell under both, and under every other console modal (dirpicker.js, the
// lane editors in lanemanage.js), so what makes an overlay a DIALOG is written once: the role
// and its name, focus moved in on open, trapped while open and given back on close, Escape and
// (unless the caller opts out) a click on the scrim to cancel.

import { el } from "/static/util.js";

const FOCUSABLE = "button, input, select, textarea, a[href], [tabindex]:not([tabindex='-1'])";
let labels = 0;

/**
 * Mount `panel` as a modal dialog and return its close(), which removes it and gives focus back
 * to whatever held it before. `label` is the element that names the dialog (its question or
 * title), `focus` the control that takes focus on open, `onCancel` what Escape and a click on the
 * scrim do. `scrimCancels: false` keeps a stray click on the scrim from cancelling — for an
 * editor holding half-made changes (lanemanage.js), where Escape and its close button stay.
 */
export function openModal(panel, { label, focus, onCancel, scrimCancels = true }) {
  const opener = document.activeElement;
  if (label) {
    label.id ||= `modal-label-${++labels}`;
    panel.setAttribute("aria-labelledby", label.id);
  }
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "true");
  // A click on the dialog's own text moves focus to the nearest focusable ancestor. Without
  // one that was the page BEHIND the dialog, where neither the keys below nor the trap could
  // see it; this panel is that ancestor now, so focus stays inside.
  panel.tabIndex = -1;
  const overlay = el("div", { class: "modal-overlay" }, panel);
  overlay.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); onCancel(); return; }
    if (e.key !== "Tab") return;
    e.preventDefault();
    const stops = [...panel.querySelectorAll(FOCUSABLE)]
      .filter((n) => !n.disabled && n.getClientRects().length);
    if (!stops.length) { panel.focus(); return; }
    const i = stops.indexOf(document.activeElement);
    const next = e.shiftKey ? (i <= 0 ? stops.length - 1 : i - 1)
                            : (i === -1 || i === stops.length - 1 ? 0 : i + 1);
    stops[next].focus();
  });
  if (scrimCancels) overlay.addEventListener("click", (e) => { if (e.target === overlay) onCancel(); });
  // A scrim that does not cancel must not take the focus either: the click would drop it on
  // <body>, outside the overlay, where Escape and the Tab trap above can no longer see it.
  else overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) e.preventDefault(); });
  document.body.append(overlay);
  (focus || panel).focus();
  return () => {
    overlay.remove();
    if (opener && document.contains(opener)) opener.focus();
  };
}

function modal({ message, input = null, confirmLabel, danger }) {
  return new Promise((resolve) => {
    const cancelValue = input ? null : false;
    const ok = el("button", { class: danger ? "btn danger armed" : "btn primary" }, confirmLabel);
    const cancel = el("button", { class: "btn" }, "cancel");
    const msg = el("div", { class: "dlg-msg" }, message);
    const panel = el("div", { class: "panel" }, msg, input,
      el("div", { class: "row mt", style: "justify-content:flex-end; gap:8px" }, cancel, ok));
    const done = (value) => { close(); resolve(value); };
    ok.onclick = () => done(input ? input.value.trim() : true);
    cancel.onclick = () => done(cancelValue);
    // Enter confirms from the input or the dialog itself; a focused BUTTON keeps its own Enter.
    // Taking it for "confirm" everywhere meant Enter on the focused cancel button ran the
    // destructive action the reader had just tabbed away from.
    panel.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target.tagName !== "BUTTON") { e.preventDefault(); ok.onclick(); }
    });
    const close = openModal(panel, { label: msg, focus: input || ok,
                                     onCancel: () => done(cancelValue) });
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
