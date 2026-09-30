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

/** acceptBar(form, { onAccepted(next), describeSwitch() }) → node */
export function acceptBar(form, { onAccepted, describeSwitch } = {}) {
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
      // a live run keeps the settings it booted with (409), an invalid value is named (422):
      // either way the draft stays exactly as it was, ready to accept again
      toastError(err, 6000);
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
