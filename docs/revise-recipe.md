# Revise recipe

Change a routine's **recipe** in natural language, straight from a finished run — the moment you've
seen it do the wrong thing is the moment you have the most context to fix it.

## Using it

On a finished routine run's page, the message box has a mode selector. Pick
**"→ revise this routine's recipe"**, type the change, and send:

- *"make the report shorter — three bullets, no preamble"*
- *"stop re-checking sources you already verified this run"*
- *"add a step that emails me the summary at the end"*

The run **resumes with its full transcript rehydrated** (it just executed, so it knows exactly what
happened) and edits its OWN recipe files — `main.md`, the `stages/` modules, and
`tuning.yaml` — with its normal `read_file` / `edit_file` / `write_file` tools. It verifies the edit,
notes it in `LEDGER.md`, and finishes. The engine commits the change; it takes effect on the
routine's **next run**.

## When the run proposes it first

Every finishing run is asked whether its own recipe should change, and what it answers becomes one
**recipe revision** card on the **Decisions** page — in the run's own words, at most eight lines.
You have three dispositions there, and the proposing routine is told which one you picked, because
its next run reasons from that:

- **apply it** — resume that run with its recipe writable, to make the edit it proposed.
- **edit** — reword the lines first. The proposal is prose, not a patch, so editing it *is*
  rewording what the resumed run is told to do: the box is prefilled with the run's own lines (one
  per line), and `apply edited` sends yours instead. The run is told plainly that the wording is
  **yours**, so it implements your version rather than restoring its own phrasing.
- **discard** — pick why: *the change is not wanted*, or *I have made this change myself*. The
  second exists because the first was once sent for both, and a routine told its revision "was not
  wanted" after you implemented it by hand will propose it again, or reason from a recipe it thinks
  it never got.

## What it can and can't change

- **Recipe** (main.md / stages / tuning.yaml) — edited directly. This is the routine's
  *behaviour*: what it does, in what order, how it words things, how much it deliberates.
- **Config** (`routine.yaml` — schedule, budgets, models, permissions/capabilities, filesystem
  roots) — a run can **never** edit this; config is yours. If your request is a config change, the
  revise run instead files it as a **decision** carrying the exact proposed change. Open the
  **Decisions** page and click **"approve & apply"** — it applies the change to the routine and
  closes the decision. (You can also just make the change yourself on the routine page.)

## When ANOTHER routine proposes the revision

A routine that holds **recipe-authoring** can also propose a revision for a *different* routine's
recipe — `create_routine` with `revises: true`, whose `prompt` carries the change one line per
change, in prose, exactly as a run's own proposal does. That is the path for a routine whose job is
watching how others run (the routine-improver is the case it was built for), and it replaces the
only route that existed before: a routine holding both recipe-authoring and a write root over
`~/routines` could already rewrite another routine's `main.md` with `edit_file` — silently, with no
record and no dial, and invisible to the routine whose instructions changed.

**Who approves it is the dial of the routine being revised, never the proposer's** — `recipe
approval` (`revise_confirm`) in its *Permissions & capabilities* panel:

- **`always`** (the default for every routine) — the proposal becomes one decision card for you,
  and nothing reaches the recipe until you approve it.
- **`invited`** — a recipe-authoring routine's revision is handed to this routine's next run with
  no card. You chose that when you set the dial.

Either way the **edit is made by a run of the routine that owns the recipe**, under the same
one-leg unlock below — never by the proposer. Its next ordinary fire boots with the lines as its
instruction, told plainly whose words they are, and it may refuse a line that is wrong about what
its runs actually do, saying so in its summary instead. `routine.yaml` stays yours at every level.

## How the unlock works

A revise run is granted recipe self-write for **that one leg only** — a marker
(`engine/revise.py`) the endpoint drops in the run dir, which the turn loop reads once and then
clears. There is no persisted grant: every ordinary run stays recipe-sealed, exactly as before, and
`routine.yaml` stays sealed even during a revise. This is the same enforcement the routine-improver
relies on, pointed at a single routine on demand.

## Revise vs. the routine-improver

The **routine-improver** is the scheduled, cold, comprehensive sweep across every routine. **Revise
recipe** is the on-demand, warm, single-routine, user-directed edit. They share the recipe-write
mechanism and the record-it-in-the-LEDGER discipline; use revise when you have a specific change in
mind and the evidence fresh in front of you.

Conversations have no revise mode — their recipe is the fixed `converse` workflow, not a
routine-specific recipe.
