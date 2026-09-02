// Conversation branching controls (F325): fork this thread at a turn, hand a branch's result
// back to its parent, and show where this conversation sits in its branch family.
//
// Both actions are deliberately explicit. A fork is a decision about WHERE two lines of work
// diverge, and a hand-back is a decision about whether a branch's result is worth the parent's
// attention — neither can be inferred, so neither happens implicitly. A hand-back is not a
// merge: the branch keeps its own conversation, and the parent receives a summary plus files.

import { api } from "/static/api.js";
import { confirmDialog, promptDialog } from "/static/components/dialog.js";
import { navigate } from "/static/router.js";
import { el, toast } from "/static/util.js";

// Fork this conversation at `turn` — the fork path used by the per-message "branch from here"
// control in the chat, where the clicked reply IS the fork point (R1006). One set of guards, one
// toast. (The header fork button was removed in D113; forking is a per-message act now.)
export async function forkAt(slug, turn, { isLive } = {}) {
  if (isLive?.()) {
    toast("this conversation is mid-reply — branch it once the reply has finished", 4000,
      { error: true });
    return null;
  }
  if (!Number.isInteger(turn) || turn < 1) {
    toast("a branch needs a turn number (1 or higher)", 4000, { error: true });
    return null;
  }
  try {
    const r = await api(`/api/conversations/${slug}/branch`, { method: "POST", body: { turn } });
    toast(`branched at turn ${r.at_turn} — opening the branch`);
    navigate(`#/conversations/${r.slug}`);
    return r;
  } catch (err) { toast(err.message, 4000, { error: true }); return null; }
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
    setTimeout(() => window.location.reload(), 800);
    return r;
  } catch (err) { toast(err.message, 4000, { error: true }); return null; }
}

// Hand-back button (only on a conversation that HAS a parent) plus a lineage line. Forking is
// now a per-message control on the reply itself (R1006), not a header button (D113). Returns
// { node, refresh } — refresh re-reads the lineage after a fork.
export function branchControls(slug) {
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

  const refresh = () => api(`/api/conversations/${slug}/lineage`)
    .then(renderLineage).catch(() => {});

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
    } catch (err) { toast(err.message, 4000, { error: true }); }
    finally { backBtn.disabled = false; }
  };

  refresh();
  return { node: el("span", { class: "row", style: "gap:6px;align-items:center" },
    lineage, backBtn), refresh };
}
