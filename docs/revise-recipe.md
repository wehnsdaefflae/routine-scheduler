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
happened) and edits its OWN recipe files — `main.md`, the `stages/` modules, `traits/`, and
`tuning.yaml` — with its normal `read_file` / `edit_file` / `write_file` tools. It verifies the edit,
notes it in `LEDGER.md`, and finishes. The engine commits the change; it takes effect on the
routine's **next run**.

## What it can and can't change

- **Recipe** (main.md / stages / traits / tuning.yaml) — edited directly. This is the routine's
  *behaviour*: what it does, in what order, how it words things, how much it deliberates.
- **Config** (`routine.yaml` — schedule, budgets, models, permissions/capabilities, filesystem
  roots) — a run can **never** edit this; config is yours. If your request is a config change, the
  revise run instead files it as a **decision** carrying the exact proposed change. Open the
  **Decisions** page and click **"approve & apply"** — it applies the change to the routine and
  closes the decision. (You can also just make the change yourself on the routine page.)

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
