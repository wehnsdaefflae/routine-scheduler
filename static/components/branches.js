// Conversation branching controls (F325): fork this thread at a turn, hand a branch's result
// back to its parent, and show where this conversation sits in its branch family.
//
// Both actions are deliberately explicit. A fork is a decision about WHERE two lines of work
// diverge, and a hand-back is a decision about whether a branch's result is worth the parent's
// attention — neither can be inferred, so neither happens implicitly. A hand-back is not a
// merge: the branch keeps its own conversation, and the parent receives a summary plus files.

import { api } from "/static/api.js";
import { confirmDialog, openModal, promptDialog } from "/static/components/dialog.js";
import { navigate, remount } from "/static/router.js";
import { el, toast, toastError } from "/static/util.js";

/** The fork dialog (D306 pt 4): a NAME (prefilled "<parent> (branch)") and an OPTIONAL first
 * instruction, in one small panel. Resolves `{ name, instruction }`, or null on cancel.
 *
 * Before this, a fork created the branch and navigated to it, and the person then had to type
 * its first instruction there — two separate acts for one intention ("I want this explored over
 * there"). Given an instruction the route starts the branch at once; left empty, the branch
 * opens idle exactly as it used to, so the dialog never forces an answer. That is also why the
 * instruction is a textarea rather than an input: it is a message to an agent, not a label.
 */
function forkDialog(parentName) {
  return new Promise((resolve) => {
    const msg = el("div", { class: "dlg-msg" }, "Branch this conversation from here.");
    const name = el("input", { type: "text", "data-nopersist": true,
      placeholder: "the branch's name" });
    name.value = `${parentName || "this conversation"} (branch)`;
    const instruction = el("textarea", { "data-nopersist": true,
      placeholder: "what the branch should do first (optional)" });
    const ok = el("button", { class: "btn primary" }, "branch");
    const cancel = el("button", { class: "btn" }, "cancel");
    const panel = el("div", { class: "panel" }, msg,
      el("div", { class: "fork-form" },
        el("label", {}, "name"), name,
        el("label", {}, "first instruction"), instruction,
        el("div", { class: "fork-hint" },
          "with an instruction the branch starts working at once; leave it empty and it opens "
          + "idle, waiting for you")),
      el("div", { class: "row mt", style: "justify-content:flex-end; gap:8px" }, cancel, ok));
    const done = (value) => { close(); resolve(value); };
    ok.onclick = () => done({ name: name.value.trim(), instruction: instruction.value.trim() });
    cancel.onclick = () => done(null);
    // Enter confirms from the NAME field only: the instruction is prose, where Enter is a
    // newline. A focused button keeps its own Enter (dialog.js says why).
    panel.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target === name) { e.preventDefault(); ok.onclick(); }
    });
    const close = openModal(panel, { label: msg, focus: name, onCancel: () => done(null) });
  });
}

// Fork this conversation at `turn` — the fork path used by the per-message "⑂ branch" control in
// the chat, where the clicked reply IS the fork point (R1006). One set of guards, one toast.
// (The header fork button was removed in D113; forking is a per-message act now.)
//
// `name` is the parent's display title, for the dialog's prefill. The dialog is D306 pt 4: one
// click asks for the name and the first instruction together, and the route starts the branch
// when there is one.
export async function forkAt(slug, turn, { isLive, name } = {}) {
  if (isLive?.()) {
    toast("this conversation is mid-reply — branch it once the reply has finished", 4000,
      { error: true });
    return null;
  }
  if (!Number.isInteger(turn) || turn < 1) {
    toast("a branch needs a turn number (1 or higher)", 4000, { error: true });
    return null;
  }
  const asked = await forkDialog(name);
  if (asked == null) return null;
  try {
    const r = await api(`/api/conversations/${slug}/branch`,
      { method: "POST", body: { turn, name: asked.name, instruction: asked.instruction } });
    toast(r.started
      ? `branched at turn ${r.at_turn} — the branch is working on your instruction`
      : `branched at turn ${r.at_turn} — opening the branch`);
    navigate(`#/conversations/${r.slug}`);
    return r;
  } catch (err) { toastError(err); return null; }
}

// Rewind this conversation to `turn` — the D69 remedy for a reply you want to redo: everything
// after that turn is archived (reversible) and the conversation re-opens LIVE from there. The ONE
// rewind path for the chat's per-message ⟲ control (R1006-style: the clicked reply's turn IS the
// cut point, so the reader never translates "this reply" into a number). Terminal-only, like a
// fork — the run view's ⟲ control still owns the fork-at-a-named-turn (prompt) entry.
export async function rewindTo(slug, runId, turn, { isLive } = {}) {
  if (isLive?.()) {
    toast("this conversation is mid-reply — rewind it once the reply has finished", 4000,
      { error: true });
    return null;
  }
  if (!runId || !Number.isInteger(turn) || turn < 1) {
    toast("a rewind needs a finished turn to cut at", 4000, { error: true });
    return null;
  }
  const ok = await confirmDialog(
    `Rewind to turn ${turn}? Every reply after this one is archived (reversible) and the `
    + "conversation re-opens live from here.", { confirmLabel: "rewind" });
  if (!ok) return null;
  try {
    const r = await api(`/api/runs/${runId}/rewind`, { method: "POST", body: { turn } });
    toast(`rewound to turn ${r.kept_through_turn} — reopening…`);
    // remount(), not a page reload: the rewind changes THIS view's transcript and nothing
    // else, while a reload drops the live bus, the LLM dock and the browser dock and re-runs
    // the whole boot sequence.
    setTimeout(remount, 800);
    return r;
  } catch (err) { toastError(err); return null; }
}

/** ONE conversation's branch family, read off the conversation LIST rows the view already
 * holds: `{ parent, branches }` — its own row's `parent` slug (web/conversations_common._item)
 * and how many rows name it as theirs. The whole point of deriving it here is that the answer
 * costs no request: `/lineage` reads every conversation's routine.yaml.
 */
export function familyOf(items, slug) {
  return { parent: (items || []).find((i) => i.slug === slug)?.parent || "",
           branches: (items || []).filter((i) => i.parent === slug).length };
}

// Hand-back button (only on a conversation that HAS a parent) plus a lineage line. Forking is
// now a per-message control on the reply itself (R1006), not a header button (D113). Returns
// { node, refresh } — refresh re-reads the lineage after a fork, or after the conversation list
// loaded and told us this thread has a family at all.
//
// `family()` answers THIS conversation's family off the list rows the view already has
// ({ parent: <slug or "">, branches: <count> }) — its own row carries `parent`, and a branch is
// any row naming this slug as its parent. The `/lineage` route reads EVERY conversation's
// routine.yaml to find children, and it was called on mount for every conversation view: paid
// in full by the overwhelming majority, which have neither a parent nor a branch. With no
// `family` reader the fetch is unconditional, which is what a caller holding no list gets.
export function branchControls(slug, { family } = {}) {
  const lineage = el("span", { class: "faint small" });
  const backBtn = el("button", { class: "btn small", hidden: true,
    title: "hand this branch's result back to the conversation it was forked from" },
    "↩ hand back");

  const renderLineage = (d) => {
    const bits = [];
    if (d.parent) {
      const label = d.parent.exists ? d.parent.name : `${d.parent.slug} (deleted)`;
      const link = el("a", { href: `#/conversations/${d.parent.slug}` }, label);
      // A deleted parent still shows: the history came from somewhere, and saying so beats
      // reading as a root conversation.
      if (!d.parent.exists) link.removeAttribute("href");
      bits.push(el("span", {}, "branched from ", link, ` at turn ${d.parent.turn}`));
    }
    if (d.branches?.length) {
      const kids = el("span", {}, `${d.branches.length} branch${d.branches.length > 1 ? "es" : ""}: `);
      d.branches.forEach((b, i) => {
        if (i) kids.append(", ");
        kids.append(el("a", { href: `#/conversations/${b.slug}`,
          title: `forked at turn ${b.turn}` }, b.name));
      });
      bits.push(kids);
    }
    lineage.replaceChildren();
    bits.forEach((b, i) => { if (i) lineage.append(" · "); lineage.append(b); });
    backBtn.hidden = !d.parent;
  };

  // The signature of the family we last READ for, so the list's own 20 s reload (and every
  // run event that reloads it) cannot turn one lineage read into a poll: the route is asked
  // again only when the family actually CHANGED — a fork, a vanished parent.
  let read = null;
  const refresh = () => {
    const fam = family?.();
    if (fam) {
      const sig = `${fam.parent || ""}:${fam.branches || 0}`;
      if (!fam.parent && !fam.branches) {
        // No family: the route has nothing to tell us, so it is never called. The line stays
        // empty and the hand-back button stays hidden, which is what it renders for a
        // conversation with no parent anyway.
        read = sig;
        return Promise.resolve();
      }
      if (sig === read) return Promise.resolve();
      read = sig;
    }
    return api(`/api/conversations/${slug}/lineage`).then(renderLineage).catch(() => {});
  };

  backBtn.onclick = async () => {
    const summary = await promptDialog(
      "Hand this branch's result back to its parent. Your summary is what the parent reads — "
      + "its transcript is NOT merged; anything in this branch's artifacts/ is copied over too.",
      { placeholder: "what this branch concluded" });
    if (summary == null) return;
    if (!summary.trim()) {
      toast("a hand-back needs a summary — that is what the parent reads", 4000, { error: true });
      return;
    }
    backBtn.disabled = true;
    try {
      const r = await api(`/api/conversations/${slug}/handback`,
        { method: "POST", body: { summary } });
      toast(r.copied
        ? `handed back to ${r.parent} with ${r.copied} artefact(s) — its next reply picks it up`
        : `handed back to ${r.parent} — its next reply picks it up`, 5000);
    } catch (err) { toastError(err); }
    finally { backBtn.disabled = false; }
  };

  refresh();
  return { node: el("span", { class: "row", style: "gap:6px;align-items:center" },
    lineage, backBtn), refresh };
}
