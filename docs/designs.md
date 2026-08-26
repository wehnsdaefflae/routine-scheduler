# Designs not yet built

Specs for work that is **decided but unbuilt**. Each entry states the problem from real
evidence, the shape, and the FIRST increment — enough that whoever picks it up is not
re-deriving it. An entry is deleted the moment it ships (its narration moves to the
subsystem doc it belongs to); an entry that stops being wanted is deleted too. Nothing here
describes current behaviour, so nothing here is a reference for how the system works today.

Written 2026-08-26 against 0.230.x, on the operator's order to clear the queued design
backlog. Item ids are the Messages-page findings they answer.

---

## F363 — failover support: a per-stage distillate

**Problem.** A catalog model may declare `fallbacks:` (endpoints/failover.py). When the
primary fails hard the run continues on a weaker model — with the *same* prompt. The recipe
was written for the strong model: it says WHAT to do and leaves the how to judgment, which
is exactly what the weaker model does not have. So failover keeps the run alive and quietly
degrades the work.

**Decision (D95, operator 2026-08-20, free text; recommendation A).** Support the fallback
model with a per-STAGE *distillate* — a short, concrete crib for that one stage — injected
**only when failover has engaged**. Not a second recipe, and never in the normal prompt.

**Shape.**

- A distillate is one file per stage: `<routine>/stages/<name>.distillate.md`, ≤ ~25 lines.
  It holds what the strong model actually DID in that stage, reduced to instructions a
  weaker model can follow: the concrete tool calls in order, the shape of the output, the
  two or three traps previous runs hit. It names capabilities, never utils — the
  recipe rule (CLAUDE.md) applies to it unchanged.
- **Derivation is a meta-routine's job, not the engine's.** routine-improver already reads a
  routine's own finished runs; it writes the distillate from runs that (a) completed on the
  routine's `main` model and (b) reached that stage. A stage with fewer than N such runs gets
  no distillate — an invented crib is worse than none.
- **Injection.** `engine/completion.py` already logs the failover switch as a transcript
  `error` event carrying a `failover` payload. That is the trigger: from the moment a run
  switches down the chain, the composer appends the CURRENT stage's distillate to the turn's
  context, under a heading that says plainly what it is ("You are running on a fallback
  model. Here is how this stage was carried out on the primary."). It is dropped again if
  the run climbs back to the primary.
- **The caching contract holds** (CLAUDE.md: the message list is appended-to, never mutated).
  Failover already invalidates the provider cache by switching model, so the append costs
  nothing extra — but it must be an APPEND at the switch, not a per-turn re-render.

**Why not the alternatives.** A whole second recipe doubles what routine-improver maintains
and drifts silently. Injecting the distillate always makes every strong-model run read a crib
written for a weaker one — the failure mode that made `deliberation` a knob in the first
place.

**First increment.** The injection seam only, with distillates written by hand for one
routine: teach the composer to append `stages/<current>.distillate.md` when the run's
transcript carries a `failover` event. That is testable with `ScriptedEndpoint` and proves
the trigger before any generation work exists.

---

## F338 — branches, subtasks and subroutines are ONE concept

**Problem (user order 2026-08-14; evidence R409, R410).** The system has three names for
child work and no single answer for what a child's OUTPUT is. `spawn` (parallel) and
`subtask` (sequential) both build the child its own dir at `runs/<run-id>/sub/<n>/`
(childrun.py), with the parent's fs roots inherited read-side. The orchestrator prompt used
to claim children "share your working directory", which was simply false and cost a run a
recovery detour (R409/R410; the copy was fixed as F352). Conversation *branching* (F325) is
the same shape again and currently does not exist at all.

The copy fix did not settle the real question: **how does a child's work get back?** Today a
parent must know the child dir, `code-search` for the artefact and `copy-file` it up — a
procedure invented per routine, and `copy-file` does not even create parent dirs.

**Shape.**

- **One vocabulary.** A CHILD RUN is the concept: an isolated run with its own dir, its own
  budget, its own recipe or pattern, and a declared relationship to its parent. `spawn`,
  `subtask` and a future `branch` are three *scheduling modes* of it — parallel, sequential,
  and forked-from-a-conversation — not three concepts. Do not add a fourth action kind: the
  existing kinds keep their names and gain a shared child-run contract underneath.
- **A declared RESULT, not a scavenged one.** A child's `finish` gains the artefact it is
  handing back — files it wrote under its own dir that the parent should collect. The engine
  copies exactly those into the parent's `artifacts/` (the deliverable convention,
  web/artifacts.py) and the child-finished observation names the landed paths. A child that
  declares nothing hands back only its summary, as today.
- **The parent stops guessing.** The SUB-WORKFLOW FINISHED observation carries the child's
  dir AND the collected paths, so no run greps the runs tree again.
- **Isolation stays.** A shared writable working dir between concurrent children is a race
  the engine would have to arbitrate; the isolated dir plus a declared hand-back gives the
  same result with no arbitration.

**First increment — SHIPPED 0.233.0**, with one deliberate change from the sketch above: no
new `finish` field. A child hands a file back by WRITING it into its own `artifacts/`, the
convention the Artifacts panel and detached tasks already use; the engine copies that into the
parent's `artifacts/from-sub-<n>/` on exit and the notification names the landed paths. That
needs no action-schema change at all, so it costs every non-child run nothing, and the
hand-back is opt-in by writing. The spawn/subtask contract copy was corrected to say so
(`engine/kindsurface.py`), and `docs/subtasks.md` documents it.

**Still unbuilt:** the vocabulary unification itself — one CHILD RUN concept with `spawn`,
`subtask` and a future `branch` as scheduling modes of it, rather than three names. The
hand-back was the load-bearing half and it is now settled; the rename is cosmetic by
comparison and should follow F325's needs rather than lead them.

---

## F325 — conversation branching and merging

**Blocked on F338** and stays blocked: a branch is a child run of a conversation, so it must
inherit whatever F338 settles about a child's identity and its hand-back. Sequenced after it
by user order 2026-08-14.

**Shape, once F338 lands.** A branch forks a conversation at a chosen message into a new
conversation whose `parent` records the origin slug + message id. It starts with the parent's
config (models, permissions, rules, connections, roots) and a COPY of the transcript up to
the fork point, so the branch reasons with the same history and cannot mutate the original.
Merging is deliberately NOT a transcript merge — two divergent histories cannot be
interleaved into one coherent conversation. It is a HAND-BACK, the F338 child result: the
branch finishes with a summary plus declared artefacts, which land in the parent as a
message and files. The parent chooses what to do with them, exactly as with a background
task.

**First increment.** Fork only, no hand-back: the button, the `parent` record, and the
transcript copy. A fork that can be read and continued is useful on its own, and it makes
the merge question concrete instead of theoretical.

---

## F328 — queued creation and config changes from a scheduled run

**Problem (evidence R353).** `create_routine` and `manage_group` are hard-restricted to
top-level conversations, because a scheduled routine has no user in the loop. The
restriction is right and the consequence is wrong: routine-improver reached a run with a
FULLY DESIGNED, user-approved routine plus a two-phase group ready to materialize
(`state/fau-comms-steward-ready-spec.md`, all five gate questions answered) and could not
create them. The design had to be hand-carried back to the user to paste in.

**Shape.** The missing piece is not permission, it is a QUEUE. D92's preview→confirm already
built the exact shape: a scheduled run stores a DRAFT and the user confirms it later.

- A scheduled run may call `create_routine` / `manage_group` in **draft mode only**. It
  writes the same draft record D92 defined, under `.control/pending-creations/<id>.json`,
  and gets the preview observation back. Nothing is created.
- The **Decisions page** grows a row per pending creation: what it would create, from which
  routine and run, with the full instruction. One click materializes it through
  `workflows.scaffold` — the same single materializer — or discards it.
- The run that queued it learns the outcome the way it learns anything else: a message in
  its inbox when the user acts, drained by its next run.
- **The engine still never writes routine.yaml.** The web layer materializes, exactly as it
  applies forever-grants today.

**First increment.** `create_routine` draft-from-a-scheduled-run plus the Decisions row and
the materialize button. `manage_group` follows the same path once creation works.

---

## F337 — mid-run config changes as in-flow messages

**Problem.** Config edited while a run is live lands in routine.yaml, but the RUN already
booted its policy, its action schema and its prompt. Some changes reach it (a grant decided
mid-run is bridged into the live policy by `engine/requests.py`); most do not. So "I changed
it while it was running" has two different meanings depending on which field was touched,
and the run is never TOLD either way.

**Shape.** A config change made while a run is live becomes an **in-flow message** — the
mechanism that already exists for reaching a running run (`inbox/`, drained at the next turn
boundary), rather than a second, invisible mutation path.

- The web layer, on a PATCH to a routine with a live run, additionally files an inbox message
  naming exactly what changed ("your budgets changed: max_turns 40 → 80"), so the change is
  in the transcript and the model can react to it.
- Fields the engine CAN adopt live (grants, budgets, deliberation) are applied to the run
  context at that turn boundary and the message says so.
- Fields it cannot (models mid-turn, workflow, schedule) say so plainly: "this takes effect
  at the next run."
- The classification lives in ONE table beside the PATCH handler, so a new field must declare
  which half it is in — the drift this finding is about cannot recur silently.

**First increment.** The message half only, for every field: a live run learns about every
config change even when nothing is adopted mid-flight. Adoption follows field by field.

---

## F335 — approval-free intra-group messaging

**Problem (user order 2026-08-14).** Members of a group are a team with a shared purpose, but
one member reaching another goes through the same `report` machinery as reaching a stranger:
an addressed report is filed in the ledger, delivered to the target's `inbox/`, and shows on
the Messages page as an open maintenance item until it is answered. For teammates
coordinating inside one chain that is heavyweight — it turns "here is the file I staged for
you" into a tracked work item somebody has to close.

**Shape.** Group members get a lighter channel that is NOT the report ledger.

- The group STORE already exists (`.control/group-stores/<gid>/`, D67) and is in every
  member's fs roots. Intra-group messaging belongs there, not in a new store: a member
  writes a note for a named sibling, and the sibling's next run in the chain reads it.
- The engine surfaces it, so it is not a filesystem convention each group reinvents: at boot,
  a member's state digest carries "notes from your group" — sender, timestamp, text —
  and drops them once read, mirroring how `inbox/` is drained.
- **No approval, no ledger row, no Messages-page item.** The blast radius is the group the
  operator already composed; a note cannot leave it. That is precisely why it may be
  approval-free, and why it must NOT be reachable to a non-member — the boundary is the
  whole safety argument.
- `report` keeps its meaning: something the OWNER of a problem must act on, tracked until
  answered. A note is coordination; a report is work.

**First increment.** Write + digest-surface within one group, no cross-group case and no UI:
prove the drain semantics against the FAU group, which already coordinates through the store
by hand.
