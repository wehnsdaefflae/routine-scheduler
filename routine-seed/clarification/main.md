# Routine clarification — template

This is the protected template behind the "+ New routine" wizard. Every clarification
session copies its **budgets**, **models**, and **rules** from this routine when it
starts, so editing this page tunes all future sessions.

The template itself never runs: it has no schedule, cannot be fired directly, and cannot
be archived — each wizard session materializes the `clarify-instruction` library workflow
against the user's draft in its own hidden session directory.

What is configurable here:
- **Budgets** — turn/wall-clock/token caps each clarify session runs under.
- **Models** — the model roles a session resolves (falls back to the system model).
- **Rules** — the general rules every session is bound by (the prose lives in the library).

## Standing practices

These general rules bind this routine. Each states a principle, not a procedure — read one with read_rule before the situation it governs and apply it to the case in front of you:
- `ask-policy` — when and how to involve the user
- `intent-inference` — read every intervention as a standing preference
