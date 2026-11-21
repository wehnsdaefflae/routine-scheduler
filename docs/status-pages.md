# Status pages — retired, and the two pages that remain

**The Steward hub is retired (operator, 2026-10-28).** Routines no longer publish a status page
to `steward.markwernsdorfer.com`, and nothing reads feedback or controls back from one. What a page
used to carry now has two homes inside the scheduler:

- **What happened** is the run's own finish summary, in two labelled parts: `**Process:**` — how
  the run went, the mechanics behind it (what it read and checked, what failed, what was out of
  reach) — then `**Work:**` — the task: its outcome with numbers, names and links, its conditions
  and consequences, what is still open. A long artefact (a draft, a digest, a packet) stays a
  FILE in the routine's directory and the summary names its path. The schema and the finish
  gloss ask for exactly this (docs/prompt-anatomy.md §5), and the dashboard's one-line slot shows
  the Work part (`static/md.js` `outcomeLine`).
- **What waits on the operator** is an `ask_user` decision, in the class the engine already
  defines (`engine/askclass.py`): a BLOCKING one (`blocks_work`) the moment a run cannot do the
  work it was fired to do without the answer, every other one CLOSING — batched into one ask just
  before the finish, its answer shaping the next run. A page's sign-off, hold control or go/no-go
  is one of those two now; its free-text feedback is an answer or an inbox message.

The engine pieces that existed only for the hub went with it: the `hub_tab` identity field (and
its `HUB TAB:` contract line) and the `hub_feedback` run-gate check. The `steward-a-project`
workflow and the steward, radar and watcher settings patterns stopped granting
`steward-publishing`, and the permission itself was then removed; the routine
`steward-hub-maintainer` that kept the kit was archived.

**Two pages remain, and they are not status pages.** `deficit-coach`'s page IS its app, and
`birthday-admin` owns a hand-written page. Each carries its publishing conduct in its OWN recipe
(where the contract lives, the kit check before a publish, the three proofs) and the kit's read
root in its own `fs_read_roots`, and the kit below still serves them. Nothing maintains the kit on a schedule any more: it changes when the operator
changes it ("Nobody deploys for anybody" below). The old hub's index and the retired projects'
stores on the host are the operator's to remove by hand; no routine and no kit operation can
delete a project store (`web/api_routine_edit.EXTERNAL_SURFACES`).

The kit's own reference — host layout, interface, gate, store and the publishing contract —
lives with the kit in the library (`web/steward/CONTRACT.md`), which is what the two recipes
read. This page's former copy of it, and the hub's history, are in git.
