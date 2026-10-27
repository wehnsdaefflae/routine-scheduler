// The ONE accept on the routine page: a bar at the foot of the viewport while the draft holds
// changes, carrying their count, "accept changes" and "discard".
//
// Every control on the page edits the draft; nothing reaches the routine until this bar says so.
// It is SUMMONS — the changes wait on a person — and it is fixed rather than parked at the foot
// of the settings, because a proposal laid over the page at load ("check the changes i
// recommend.") has to be answerable from wherever the reader happens to be.

import { confirmDialog } from "/static/components/dialog.js";
import { reveal } from "/static/components/settings-field.js";
import { el, toast, toastError } from "/static/util.js";

/** acceptBar(form, { onAccepted(next), onRefused(err), describeSwitch() }) → node
 *  `onRefused` runs after a 409: the accept was HELD (D178), and it is the WHOLE answer to
 *  that click — the bar sends no error toast with it (F761), because a held accept is a state
 *  rather than a failure. It must therefore both re-read the document AND bring the held state
 *  to the reader, who pressed a button at the foot of the viewport. A caller that passes no
 *  `onRefused` has nowhere to render it and keeps the toast. */
export function acceptBar(form, { onAccepted, onRefused, describeSwitch } = {}) {
  const count = el("span", { class: "ab-count", "data-accept-count": "" });
  const what = el("span", { class: "ab-what" });
  const show = el("button", { type: "button", class: "btn small ghost",
    title: "go to the first change", onclick: () => {
      reveal(document.querySelector(".sf-field.sf-changed") || document.querySelector("[data-pattern-bar]"));
    } }, "show");
  const discard = el("button", { type: "button", class: "btn small", "data-discard": "",
    onclick: async () => {
      const proposal = form.proposal;
      if (proposal && !(await confirmDialog(
        "Discard every pending change, the proposal included? The routine keeps the settings "
        + "it holds now.", { confirmLabel: "discard" }))) return;
      try { await form.discardAll(); toast("changes discarded — nothing was applied"); }
      catch (err) { toastError(err); }
    } }, "discard");
  const accept = el("button", { type: "button", class: "btn primary", "data-accept": "",
    onclick: run }, "accept changes");
  const node = el("div", { class: "accept-bar", role: "region", "aria-label": "pending changes",
    hidden: true },
    el("div", { class: "ab-inner" },
      el("div", { class: "ab-text" }, count, what),
      el("div", { class: "ab-acts" }, show, discard, accept)));

  async function run() {
    const n = form.count();
    for (const b of [accept, discard]) b.disabled = true;
    accept.textContent = "accepting…";
    try {
      const next = await form.accept();
      const applied = next.applied || [];
      toast(applied.length ? `accepted — ${applied.join(", ")} saved` : `accepted ${n} change${n === 1 ? "" : "s"}`);
      onAccepted?.(next);
    } catch (err) {
      // Either way the draft stays exactly as it was, ready to accept again — but the two
      // refusals are not the same KIND of answer, so they do not share a channel.
      //
      // 409: a live run keeps the settings it booted with, so the accept was HELD (D178) and
      // `onRefused` renders that state where the page keeps it. F761: it used to toast through
      // the ERROR channel as well, and a 409 arriving as a failure reads as "your click did not
      // work" — on 2026-10-23 he pressed this button at 03:06:27, 03:06:34 and 03:06:43, and
      // four of the console's eight `error` events those two days were this one refusal.
      // A held accept is a state, not a failure; nothing here says otherwise.
      //
      // 422: an invalid value IS a failure — the accept wrote nothing and will not until the
      // value changes — so it keeps the error channel, as does anything else that lands here.
      //
      // The fallback is deliberate: a caller that renders no held state has no channel for the
      // refusal at all, and SILENCE is worse than the wrong channel — so one without
      // `onRefused` keeps the toast.
      if (err?.status === 409 && onRefused) await onRefused(err);
      else toastError(err, 6000);
    } finally {
      for (const b of [accept, discard]) b.disabled = false;
      accept.textContent = "accept changes";
    }
  }

  function paint() {
    const n = form.count();
    node.hidden = n === 0;
    count.textContent = `${n} change${n === 1 ? "" : "s"}`;
    const sw = form.patternSwitch() ? describeSwitch?.() : "";
    what.textContent = ` wait for your accept${sw ? ` · ${sw}` : ""}`;
  }
  paint();
  form.subscribe(paint);
  return node;
}
