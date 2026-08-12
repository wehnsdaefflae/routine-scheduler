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
reversible) and is the recipe's EQUAL under the routine's settings: it runs in the
routine's own venv (`<routine>/.venv`, dependencies installed on demand) with the same
filesystem roots (recipe and procedure read and write the SAME files), the routine's
GRANTED secrets, its OAuth connections and machine bindings in the environment, and the
util library on PATH (`gu <name>` works inside a procedure, inside the same jail). The
blast radius is this routine's permissions, nothing more. Keep each one single-purpose.
A procedure NEVER routes around a rule: behavior a rule gates — asking before an
irreversible outward act, evidencing a claim, recording a decision — stays under the
recipe's judgment, and authoring or invoking a procedure is itself rule-bound conduct.
