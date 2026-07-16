# Routine clarification — template

This is the protected template behind the "+ New routine" wizard. Every clarification
session copies its **budgets**, **models**, and **traits/** from this routine when it
starts, so editing this page tunes all future sessions.

The template itself never runs: it has no schedule, cannot be fired directly, and cannot
be archived — each wizard session materializes the `clarify-instruction` library workflow
against the user's draft in its own hidden session directory.

What is configurable here:
- **Budgets** — turn/wall-clock/token caps each clarify session runs under.
- **Models** — the model roles a session resolves (falls back to the system model).
- **Traits** — practice modules copied into every session.

## Standing practices

Sessions copy every practice module under this routine's `traits/` directory. None are
bundled by default — add one here to give every future clarification session that
standard.
