// A run's own RECIPE REVISIONS, proposed at its finish (D200, engine/recipeproposal.py).
//
// Its own band and its own row for the same reason the swap and unowned cards have theirs: the
// buttons are not the queued-creation pair. Nothing is CREATED here — the proposing run
// already reasoned the change and could not write it (a scheduled run holds no write_recipe),
// so "apply it" hands that run the capability back: the leg is resumed with its recipe writable
// and makes the edits itself (web/api_recipe_revision.py → engine/revise.py). A generic "create
// it" button on this kind could only ever 400, which is what pending.js's own comment warns
// about for an unmatched kind.
//
// The LINES are the card, not a fold-away detail: they are what the operator is approving, in
// the run's own words, and there are at most engine/recipeproposal.MAX_LINES of them.
//
// F790 — and they are EDITABLE here, because the proposal is PROSE rather than a diff: there is
// no patch to adjust, so rewording the lines is the whole of editing them, and the reworded
// lines are what the resumed leg is told to implement. The control is chat.js's revise control
// on a queued message, for the reason D139 gives there — a queued message is the user's until
// the model reads it, and a proposal is the operator's until he approves it. The POST carries
// them as a body the way unowned-card.js carries the operator's routing choice; the cap and the
// "these are HIS words" framing are the route's, because a cap the card enforces is not a cap.

import { api } from "/static/api.js";
import { chooseDialog } from "/static/components/dialog.js";
import { el, toast, toastError, when } from "/static/util.js";

export const RECIPE = "recipe-revision";

// The two things a discard can TRUTHFULLY mean, because the proposing routine is told the
// reason verbatim (pending.notify_proposer) and its next run reasons from it. This card used to
// send one of them unconditionally — "the revision was not wanted" — which is simply false when
// the operator read the proposal, agreed with it, and made the edit himself: the routine then
// learns its own correct reasoning was rejected, and the recipe it reads no longer matches.
const WHY = {
  "not wanted": "the revision was not wanted",
  "already done": "the operator has already made this change himself — there is nothing left to "
                  + "apply, so read your recipe as it now stands before proposing it again",
};

export function recipeRow(rec, { reload, onChanged } = {}) {
  const f = rec.fields || {};
  const lines = f.lines || [];
  const apply = el("button", { class: "btn small primary" }, "apply it");
  const edit = el("button", { class: "btn small" }, "edit");
  const drop = el("button", { class: "btn small danger" }, "discard");
  const btns = [apply, edit, drop];
  const lineRows = () => lines.map((line) => el("div", { class: "small",
    "data-recipe-line": "" }, el("code", {}, "→"), " ", line));
  const shown = el("div", { class: "mt" }, ...lineRows());
  let editing = false;
  const act = async (fn) => {
    btns.forEach((b) => { b.disabled = true; });
    try { await fn(); await reload?.(); onChanged?.(); }
    catch (err) {
      toastError(err, 5000);
      // the editor stays open holding what he typed, so the two controls it suppresses stay
      // suppressed: `edit` would rebuild the box from the run's lines and lose his wording, and
      // `apply it` would send those lines instead of his
      btns.forEach((b) => { b.disabled = editing && (b === edit || b === apply); });
    }
  };
  const revise = (body, said) => act(async () => {
    const r = await api(`/api/pending-creations/${rec.id}/revise`, { method: "POST", body });
    toast(said(r), 5000);
  });
  apply.onclick = () => revise(undefined,
    (r) => `${r.routine} resumed with its recipe writable — it is making the edit`);
  edit.onclick = () => {
    const box = el("textarea", { class: "code", rows: String(Math.max(3, lines.length + 1)),
      "aria-label": "the revision, in your words", style: "width:100%" });
    box.value = lines.join("\n");
    const send = el("button", { class: "btn small primary" }, "apply edited");
    const back = el("button", { class: "btn small" }, "cancel");
    send.onclick = async () => {
      send.disabled = back.disabled = true;
      // split, never filtered: a box he CLEARED must reach the route as an empty edit and be
      // refused there, not silently fall back to the wording he just deleted
      await revise({ lines: box.value.split("\n") },
        (r) => `${r.routine} resumed with its recipe writable — it is making the edit in your `
             + `wording (${r.lines.length} line${r.lines.length === 1 ? "" : "s"})`);
      send.disabled = back.disabled = false;
    };
    back.onclick = () => {
      editing = false;
      edit.disabled = apply.disabled = false;
      shown.replaceChildren(...lineRows());
    };
    shown.replaceChildren(box, el("div", { class: "row mt", style: "gap:8px" }, send, back),
      el("div", { class: "faint small" },
        "one line per revision — your wording replaces the run's, and the leg is told the lines "
        + "are yours rather than its own"));
    editing = true;
    // `apply it` would send the RUN's lines. With his edit on screen that click is a trap: the
    // one thing he would read it as doing is the one thing it does not do. `apply edited` is
    // the only apply while the box is open; cancel brings the other back.
    edit.disabled = apply.disabled = true;
    box.focus();
  };
  drop.onclick = () => act(async () => {
    // a cancelled dialog is a choice, not a failure — it must not reach act()'s error toast
    const why = el("select", { class: "small", "aria-label": "why you are discarding it" },
      el("option", { value: "" }, "why…"),
      el("option", { value: "not wanted" }, "the change is not wanted"),
      el("option", { value: "already done" }, "I have made this change myself"));
    const pick = await chooseDialog(
      `Discard ${rec.routine}'s proposed recipe revision? It is told which of these you pick, `
      + "and its next run reasons from that — so it has to be the true one.", why,
      { confirmLabel: "discard" });
    if (pick == null) throw new Error("");
    await api(`/api/pending-creations/${rec.id}/discard`,
      { method: "POST", body: { reason: WHY[pick] } });
    toast("discarded — the proposing routine was told");
  });
  return el("div", { class: "card mt", "data-recipe": rec.id },
    el("div", { class: "row", style: "gap:10px;align-items:center" },
      el("span", {}, el("strong", {}, rec.routine || "?"),
        ` proposes ${lines.length} revision${lines.length === 1 ? "" : "s"} to its own recipe`),
      el("span", { style: "margin-left:auto" }),
      el("a", { class: "btn small", href: `#/routine/${rec.routine}` }, "open"),
      apply, edit, drop),
    shown,
    el("div", { class: "faint small" },
      rec.run_id
        ? el("span", {}, "proposed in ", el("a", { href: `#/run/${rec.run_id}` }, rec.run_id))
        : "proposed at a finish",
      " · ", when(rec.created_at),
      " · applying resumes that run with its recipe writable"));
}
