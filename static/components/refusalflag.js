// ⚑ Flag a conversation reply as a REFUSAL (operator decision 2026-10-01). Behind a warning
// gate the reply and every reply after it are archived, the message that produced it is
// re-sent, and the conversation's uncensored model answers it — and stays its main model from
// then on. This is the one place the uncensored model answers and acts: the automatic refusal
// path keeps treating it as a honeypot (engine/refusal.py), because there nobody decided.
//
// The gate quotes what the SERVER will re-send (GET /api/runs/{id}/flag-refusal), never a
// second reading of the transcript in the browser: which message opened a reply depends on the
// leg it ran in — commands before it, a branch's inherited history, a first message that lives
// in instruction.md — and a destructive confirm must promise exactly what happens.

import { api } from "/static/api.js";
import { chooseDialog, confirmDialog } from "/static/components/dialog.js";
import { remount } from "/static/router.js";
import { el, modelOption, toast, toastError } from "/static/util.js";

// The gate's text — every consequence, in the order it happens, the irreversible one last.
function gateText(plan, takeover) {
  return el("div", { class: "flag-gate" },
    el("p", {}, el("strong", {}, "Flag this reply as a refusal?")),
    el("p", {}, "This reply and every reply after it are discarded — archived beside the "
      + "transcript, reversible by hand."),
    el("p", {}, "The message that produced it is re-sent:"),
    el("blockquote", { class: "flag-quote" }, plan.message),
    el("p", {}, takeover),
    el("p", {}, "Actions already taken after that point — files written, messages sent — are ",
      el("strong", {}, "NOT"), " undone."));
}

// `reply` is { turn, ts } — the reply's turn AND its own timestamp, because a reply that ran
// no turn of its own (an endpoint failure, a classifier refusal) shares the turn number of the
// reply before it. `catalog`/`catalogMeta` are the conversation detail's model list, the one
// its settings offer, for the pick a conversation without an uncensored model needs.
export async function flagRefusal(runId, reply, { isLive, catalog = [], catalogMeta = {} } = {}) {
  if (isLive?.()) {
    toast("this conversation is mid-reply — flag a reply once the reply has finished", 4000,
      { error: true });
    return null;
  }
  if (!runId || !Number.isInteger(reply?.turn)) {
    toast("a flag needs a finished reply", 4000, { error: true });
    return null;
  }
  let plan;
  try {
    const q = new URLSearchParams({ turn: String(reply.turn), ts: reply.ts || "" });
    plan = await api(`/api/runs/${runId}/flag-refusal?${q}`);
  } catch (err) { toastError(err); return null; }

  let model = plan.model;
  if (model) {
    const ok = await confirmDialog(gateText(plan, [el("strong", {}, model),
      " answers it, and becomes this conversation's main model from now on."]),
      { confirmLabel: "flag & re-send" });
    if (!ok) return null;
  } else {
    const pick = el("select", { class: "flag-pick", "data-nopersist": "",
      "aria-label": "the model that takes over" },
      el("option", { value: "" }, "pick the model that takes over…"),
      catalog.map((n) => modelOption(n, catalogMeta[n])));
    model = await chooseDialog(gateText(plan,
      "This conversation has no uncensored model. Pick one below: it answers the message, and "
      + "becomes this conversation's uncensored and main model from now on."), pick,
      { confirmLabel: "flag & re-send" });
    if (!model) return null;
  }
  try {
    const r = await api(`/api/runs/${runId}/flag-refusal`, { method: "POST",
      body: { turn: reply.turn, ts: reply.ts || "", ...(plan.model ? {} : { model }) } });
    toast(`flagged as a refusal — ${r.model} takes over and answers your message…`, 5000);
    // remount(), like the ⟲ rewind: the cut changes THIS view's transcript and nothing else
    setTimeout(remount, 800);
    return r;
  } catch (err) { toastError(err); return null; }
}
