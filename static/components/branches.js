// Conversation branching controls (F325): fork this thread at a turn, hand a branch's result
// back to its parent, and show where this conversation sits in its branch family.
//
// Both actions are deliberately explicit. A fork is a decision about WHERE two lines of work
// diverge, and a hand-back is a decision about whether a branch's result is worth the parent's
// attention — neither can be inferred, so neither happens implicitly. A hand-back is not a
// merge: the branch keeps its own conversation, and the parent receives a summary plus files.

import { api } from "/static/api.js";
import { promptDialog } from "/static/components/dialog.js";
import { navigate } from "/static/router.js";
import { el, toast } from "/static/util.js";

// Fork button + hand-back button (the latter only on a conversation that HAS a parent), plus a
// lineage line. Returns { node, refresh } — refresh re-reads the lineage after a fork.
export function branchControls(slug, { isLive }) {
  const lineage = el("span", { class: "faint small" });
  const forkBtn = el("button", { class: "btn small",
    title: "fork this conversation at a turn into a new one that inherits its history" },
    "⑂ branch");
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

  forkBtn.onclick = async () => {
    if (isLive()) {
      toast("this conversation is mid-reply — branch it once the reply has finished", 4000,
        { error: true });
      return;
    }
    const ans = await promptDialog(
      "Branch this conversation: it inherits the history THROUGH which turn? The new "
      + "conversation starts from that point with this one's config; this one is untouched.",
      { placeholder: "turn number" });
    if (ans == null) return;
    const turn = parseInt(ans, 10);
    if (!Number.isInteger(turn) || turn < 1) {
      toast("enter a turn number (1 or higher)", 4000, { error: true }); return;
    }
    forkBtn.disabled = true;
    try {
      const r = await api(`/api/conversations/${slug}/branch`,
        { method: "POST", body: { turn } });
      toast(`branched at turn ${r.at_turn} — opening the branch`);
      navigate(`#/conversations/${r.slug}`);
    } catch (err) { toast(err.message, 4000, { error: true }); }
    finally { forkBtn.disabled = false; }
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
    } catch (err) { toast(err.message, 4000, { error: true }); }
    finally { backBtn.disabled = false; }
  };

  refresh();
  return { node: el("span", { class: "row", style: "gap:6px;align-items:center" },
    lineage, forkBtn, backBtn), refresh };
}
