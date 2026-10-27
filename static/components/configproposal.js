// The config bridge on a decision card, as ONE factory both card shapes call (R2189). A run
// can't edit config, so it proposes the change as a `config_patch` on the decision; approving
// it here PATCHes the owning config and resolves the ask.
//
// THREE surfaces, each with its own PATCH route (R102: a conversation's decision must hit
// /api/conversations — PATCHing /api/routines with a conversation slug 404s, so the patch
// silently never landed):
//   - a routine        → PATCH /api/routines/<slug>
//   - a conversation   → PATCH /api/conversations/<slug>
//   - the INSTANCE     → PATCH /api/settings/server, when the patch names {"server": {...}}
//     (operator, 2026-10-19). Until that surface existed, an approved instance-wide change had
//     no door at all: two sat 10 and 3 days in config-optimizer's ledger (R2702).
// A detached task / clarify workspace has none — its proposal renders read-only rather than
// pretending a button would work.
//
// Lives in its own module because `views/questions.js` is at its size ceiling, and because the
// surface routing is now a table rather than a nested ternary.
//
// Why a factory and not one node moved between panels: an ANSWERED card renders through the
// settled-receipt branch of `item`, a different element tree with no proposal body in it, so
// the open card's node is not reachable from there. `append` RELOCATES a mounted node, so
// reusing one instance carried the live apply button into whatever had just been wiped. Each
// panel therefore mounts its OWN proposal with its own handler; `host` is the element that
// panel wants the applied-receipt written into, and `clearWarn` drops its blocking tint.

import { api } from "/static/api.js";
import { markAnswered } from "/static/questions-store.js";
import { chip, el, toast, toastError } from "/static/util.js";

// The server patch's routing key, and the one place this client knows it (engine:
// configflow.SERVER_KEY).
const SERVER_KEY = "server";

// What a patch is FOR → where it lands. Resolved from the PATCH BODY first (a `server` key
// names the instance whoever asked), then from the asker's home.
function surfaceOf(q) {
  if (q.config_patch && Object.hasOwn(q.config_patch, SERVER_KEY)) {
    // The instance config. There is no target slug and no "elsewhere": config.yaml is nobody's
    // routine, which is why the engine refuses a patch mixing `server` with routine fields.
    return { url: "/api/settings/server", noun: "instance", body: q.config_patch[SERVER_KEY],
             label: "proposed instance config change — a run can't edit config.yaml, so "
                    + "approve it here:" };
  }
  // The engine resolved and validated the target at ask time (engine/interact.py) against the
  // ROUTINES home whoever asked, so a named target is always a routine; without one the
  // proposal is the asker's own and patches the asker's own surface.
  const home = q.config_target ? "routines"
    : q.conversation ? "conversations" : q.background ? "" : "routines";
  // what the button patches is named by the surface it posts to, never by who asked: a
  // conversation's proposal for a routine patches a ROUTINE
  const noun = home === "conversations" ? "conversation" : "routine";
  // D123/F458: a config_patch may be FOR another routine (config-optimizer's whole job), so the
  // patch goes to the TARGET, not to whoever asked — the old hardwiring to q.routine silently
  // rewrote the asker's own config and reported success (R1343). That holds for a conversation
  // too: forcing q.routine here posted the patch to /api/routines/<the conversation>.
  const target = q.config_target || q.routine;
  const elsewhere = target !== q.routine;
  return {
    url: home ? `/api/${home}/${target}` : "", noun, target, elsewhere, body: q.config_patch,
    label: home
      ? (elsewhere
         ? `proposed config change for ${target} — ${q.routine} asked for it on that `
           + `${noun}'s behalf; approving it patches ${target}, not ${q.routine}:`
         : "proposed config change — a run can't edit routine.yaml, so approve it here:")
      : "proposed config change — this decision's home has no config to patch "
        + "(a detached task / setup workspace is one-shot); answer in text, and make "
        + "any lasting change on the owning conversation or routine:",
  };
}

export function configProposal(q, { host, clearWarn, onApplied }) {
  if (!q.config_patch || q.meta || q.config_patch_applied) return null;
  const s = surfaceOf(q);
  const where = s.elsewhere ? s.target : `the ${s.noun}`;
  const btn = s.url ? el("button", { class: "btn small primary" }, "approve & apply") : null;
  if (btn) btn.onclick = async () => {
    btn.disabled = true;
    try {
      const res = await api(s.url, { method: "PATCH", body: s.body });
      // Honesty gate (R102): a field the endpoint doesn't support is silently dropped
      // server-side — verify every patch key was actually applied before telling the routine
      // (and the user) it was. `updated` is the endpoint's applied-field list.
      const missing = Object.keys(s.body).filter((k) => !(res.updated || []).includes(k));
      if (missing.length) {
        throw new Error(`${missing.join(", ")}: not applicable to a ${s.noun} — `
          + "the proposal needs a different route; answer in text instead");
      }
      // An ALREADY-ANSWERED card must not be answered again: the answer route is what files a
      // decision, and re-filing one that is settled would resurrect it as open. Applying the
      // patch is the whole act there; the answer already exists.
      //
      // R2778: either way the RECORD has to say the patch was applied, because the asking run
      // reads that field now (engine/inbox_questions → the state digest). A fresh answer
      // carries the signal; an answer already filed is corrected through its own route, which
      // touches no answer text and re-opens nothing. Without this the run would be told "NOT
      // APPLIED" about a change that IS live — worse than the silence R2778 replaced.
      if (!q.answered) {
        const text = "approved & applied the proposed config change";
        await api(`/api/questions/${q.qid}/answer`,
          { method: "POST", body: { text, config_patch_applied: true } });
        // Apply files a real answer, so it has the same race as the answer form: record it in
        // the shared store or a read that began before this POST repaints the card as open
        // (F780). The form's own path goes through `settled`; this is the other writer.
        markAnswered(q.qid, text);
      } else {
        await api(`/api/questions/${q.qid}/patch-applied`, { method: "POST" });
      }
      q.config_patch_applied = true;
      toast(`config change applied to ${where}`);
      clearWarn();
      host.replaceChildren(el("div", { class: "flow-note" },
        chip("applied", "ok"),
        el("span", {}, `the config change was applied to ${where}`)));
      onApplied();
    } catch (err) { toastError(err, 5000); btn.disabled = false; }
  };
  return el("div", { class: "flow-note mt" },
    el("div", { class: "small", style: "margin-bottom:4px" }, s.label),
    el("pre", { class: "doc", style: "margin:0 0 6px;white-space:pre-wrap" },
      JSON.stringify(q.config_patch, null, 2)),
    btn);
}
