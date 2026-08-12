---
tags: [tool-use, procedures, authoring]
requires:
  actions: [procedure]
---
# permission: procedures — the routine's own deterministic scripts

Unlocks `procedure`: run a PEP 723 script from this routine's OWN `procedures/` dir — the
deterministic half of the recipe/procedure split. A responsibility that is deterministic
(mail/feed polling, parsing, calculations on updated data, assembling a fixed artifact,
termination signaling) belongs in a procedure, so the model does only genuine judgment;
a procedure runs faster, cheaper and reproducibly, every run.

Author one with `write_file` to `procedures/<name>.py`: PEP 723 dependencies, then a
docstring header — first line `<name> — <one-line summary>`, optional `usage:`, `net:
outbound|none` (undeclared = none → the sandbox denies all TCP), `secrets:` naming every
credential env var it reads (only DECLARED names are injected; `NAME?` marks an optional
one, withheld rather than prompted when not granted). Data on stdout, diagnostics on
stderr, meaningful exit codes, `--json` for structured output. Verify a new or revised
procedure by RUNNING it before relying on it, and name it in the finish summary.

A procedure is private to this routine (versioned by its repo — revisions are cheap and
reversible) and runs in the routine's OWN venv (`<routine>/.venv`, dependencies installed
on demand) with the routine's own filesystem permissions — the recipe's file actions and
the procedure read and write the SAME files, and the blast radius is this routine's
permissions, nothing more. Keep each one single-purpose; do not call global
utils from inside a procedure — a step needing a util's capability belongs in the
recipe, where its own gates apply.
