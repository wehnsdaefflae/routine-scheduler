// The corner controls a TRANSCRIPT ROW carries: ⧉ copy what the row says, 🐞 file it as a bug.
//
// Operator, F217 (handed 2026-11-06, passed over by two runs): "A run transcript's messages
// carry NO copy button and nothing anywhere carries report-as-bug — copy shipped for
// conversations only."
//
// WHY ITS OWN MODULE, and why `copyBtn` MOVED here instead of being copied. The helper was
// written for the conversation chat (operator order 2026-08-22) and lived in
// components/chat.js — which imports FROM components/transcript.js. A transcript row reaching
// back into chat.js would be an import CYCLE, and a second clipboard implementation would be a
// second place to fix the next time a browser changes its permission rules. So the one helper
// lives here, in a module both renderers import, and chat.js keeps its three call sites (whole
// message, per-fence, summary) unchanged — glyph, feedback and default class included.
//
// THE CLIPBOARD SUBTLETY, kept as chat.js had it: `navigator.clipboard.writeText` REJECTS
// wherever the browser denies clipboard access (an insecure origin, a document that is not
// focused, a user who declined the prompt). That is a copy that does not happen, never an error
// painted at the reader — the button answers ✕ for a moment, and select-and-copy still works
// there. The glyph itself is `user-select: none` (R3085), so it never rides a dragged selection.
//
// WHERE A BUG REPORT GOES — the decision F217 asked to be taken deliberately and recorded.
// There is NO report-filing route in `src/rsched/web/`: `def .*report` under that tree finds
// only readers and closers (api_items.discard_orphan, api_messages.retract_outbox_report,
// api_questions._relay_report_answer, api_audit.answered_decisions). The channel that already
// exists is the operator's own — `POST /api/routines/{slug}/messages`
// (api_messages.create_message), which queues a message into that routine's inbox, drained at
// its next run. So "report as bug" is a MESSAGE to the routine that owns the run, pre-filled
// with the row's address and its text: no new route, no new outward channel, nothing invented.
// That split is the house reading of docs/messages.md — a `report` is a RUN's utterance, a
// person's is a message — and it is why this control does not pretend to file one.
//
// The dialog is PRE-FILLED AND EDITABLE, not a one-click send: the row says what happened, and
// only the reader can say what is wrong with it. It names the routine the message will reach, so
// nothing leaves the page without the operator seeing where it goes.

import { apiUpload } from "/static/api.js";
import { openModal } from "/static/components/dialog.js";
import { el, toast, toastError } from "/static/util.js";

/** Copy-to-clipboard button: ⧉, feedback ✓ on success and ✕ where the browser denies the
 *  clipboard. Moved from components/chat.js:45 — its three callers pass the same arguments. */
export function copyBtn(getText, cls = "copy-msg", title = "copy message text") {
  const btn = el("button", { class: cls, title,
    onclick: async () => {
      let mark = "✓";
      try { await navigator.clipboard.writeText(getText()); } catch { mark = "✕"; }
      btn.textContent = mark;
      setTimeout(() => { btn.textContent = "⧉"; }, 1200);
    } }, "⧉");
  return btn;
}

/** One transcript row's ADDRESS, as a person reads it back: which run, which turn, which call.
 *  A sub-run's turn numbers are its own, so the sub is named rather than silently read as the
 *  parent's turn — a report pointing at the wrong turn is worse than one pointing at none. */
export function rowAddress(ev, a, opts = {}) {
  const run = opts.runId ? `run ${opts.runId}` : opts.slug ? `routine ${opts.slug}` : "this run";
  const where = opts.sub != null && opts.sub !== "" ? `, sub-run ${opts.sub}` : "";
  const kind = a?.kind ? `${a.kind}${a.kind === "util" && a.name ? ` ${a.name}` : ""}` : "turn";
  return `${run}${where}, turn ${ev?.turn ?? "?"} (${kind})`;
}

/** Everything the row SAYS, as the text a reader wants in a paste: the address, the narration,
 *  the brief, and the result if it has arrived. The result is read off the row's own `.obs`
 *  element at click time rather than plumbed through, because the observation lands in a later
 *  event than the action — there is nothing to hold and nothing to keep in sync. */
export function rowText(ev, a, turn, opts = {}, brief = "") {
  const out = [rowAddress(ev, a, opts)];
  if (a?.say) out.push(a.say);
  if (brief) out.push(`${a?.kind || ""}: ${brief}`.trim());
  const obs = turn?.querySelector?.(".obs");
  const said = obs ? obs.textContent.trim() : "";
  if (said) out.push(`result: ${said}`);
  return out.join("\n");
}

function bugBody(ev, a, turn, opts, brief) {
  return `The run transcript shows this, and it looks wrong:\n\n${rowText(ev, a, turn, opts, brief)}`
    + "\n\nWhat is wrong with it: ";
}

/** The pre-filled, editable message, with where it is going said on it. Resolves once it is
 *  queued (or the reader cancels); a refusal reports in the toast and the box stays open. */
function bugDialog(slug, prefill) {
  return new Promise((resolve) => {
    const box = el("textarea", { class: "bug-box", "data-nopersist": true });
    box.value = prefill;
    const send = el("button", { class: "btn primary" }, "queue it");
    const cancel = el("button", { class: "btn" }, "cancel");
    const msg = el("div", { class: "dlg-msg" }, `Report this row to ${slug}`);
    const note = el("div", { class: "faint small" },
      `goes to ${slug}'s inbox — read at its next run, editable and withdrawable until then`);
    const panel = el("div", { class: "panel" }, msg, note, box,
      el("div", { class: "row mt", style: "justify-content:flex-end; gap:8px" }, cancel, send));
    const close = openModal(panel, { label: msg, focus: box,
                                     onCancel: () => { close(); resolve(false); } });
    cancel.onclick = () => { close(); resolve(false); };
    send.onclick = async () => {
      const text = box.value.trim();
      if (!text) return;
      send.disabled = true;
      try {
        const fd = new FormData();
        fd.append("text", text);
        const r = await apiUpload(`/api/routines/${slug}/messages`, fd);
        close();
        toast(r.delivery === "mid-run"
          ? `queued for ${slug} — the RUNNING run picks it up at its next turn`
          : `queued for ${slug} — read at its next run`);
        resolve(true);
      } catch (err) {
        send.disabled = false;
        toastError(`could not queue it: ${err.message}`);
      }
    };
  });
}

/** The row's controls, as the children a turn box appends: ⧉ always, 🐞 wherever the mount
 *  named the routine that owns the run. Without that routine there is no inbox to reach, so the
 *  control is absent rather than present-and-broken (the conversation chat's work folds mount
 *  the same renderer and pass no routine of their own). */
export function rowTools(ev, a, turn, opts = {}, brief = "") {
  const tools = [copyBtn(() => rowText(ev, a, turn, opts, brief), "row-copy",
                         "copy this row — its address, what was said and the result")];
  if (opts.slug) {
    tools.push(el("button", { class: "row-bug",
      title: `report this row as a bug — a message to ${opts.slug}, pre-filled and editable`,
      onclick: () => bugDialog(opts.slug, bugBody(ev, a, turn, opts, brief)) }, "🐞"));
  }
  return tools;
}
