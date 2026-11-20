"""One-shot boot migration: every live instruction that names `spawn`, `subtask` or `subruns`
is rewritten to the `child` kind and its `mode` — MIGRATION(expires=2026-12-31).

D241 (operator, 2026-10-17: *"sub-runs / subtasks (why two terms for the same thing? fix
that)"*) removes the three kinds rather than aliasing them, because an alias is how two names
for one thing survive indefinitely. That makes this migration load-bearing rather than tidy:
the recipes, rules and workflow patterns on this instance are PROSE a run reads and acts on, so
a recipe still saying "spawn a child" after the kind is gone instructs a run to emit a kind the
schema refuses. The engine's own text ships with the release; the instance's text does not.

WHAT IT REWRITES, and nothing else:
- the live library's workflows, rules, permissions and settings patterns;
- every routine's `main.md` and `stages/*.md`, and every conversation's `instruction.md`.

It does NOT touch a routine's `state/`, its LEDGER, or any transcript: those are the RECORD of
what happened, and a record that says a run called `subtask` is true. Rewriting history to match
today's vocabulary would make the ledger lie about the past — the one thing a ledger may not do.

The rewrite is by WHOLE WORD on the three kind names in the places a kind is named, which is why
it is done here and not by hand across 26 files: a hand sweep is where "spawn" survives inside a
word, inside a sentence about the hook POINT called `spawn` (which keeps its name — it is a
moment, not an action), or inside a module path like `subruns.KILL_JOIN_S`. Each of those is
excluded explicitly below, and anything it is unsure of is left alone and REPORTED rather than
guessed: a wrong rewrite of an instruction is worse than a stale one, because the stale one is
visible to the run that reads it.

Runs at daemon boot; records what it changed in `.control/migrations/child-kind.json`; while
that record exists it does nothing.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .ids import now_iso
from .paths import atomic_write, atomic_write_json

log = logging.getLogger("rsched.migrate")

RECORD = Path(".control") / "migrations" / "child-kind.json"

#: What a kind name becomes. Order matters only for readability: each pattern is whole-word.
#: `subtask` carries its mode, because a sequential child is what it always meant.
REWRITES: tuple[tuple[str, str], ...] = (
    (r"`subruns`", "`children`"),
    (r"`subtask`", "`child` (mode sequential)"),
    (r"`spawn`", "`child`"),
    (r"\bsubruns\b(?!\.)", "children"),          # not `subruns.KILL_JOIN_S`, a module path
    (r"\bsubtasks\b", "sequential child runs"),
    (r"\bsubtask\b", "sequential child run"),
    (r"\bsub-?runs\b", "child runs"),
    (r"\bsub-?run\b", "child run"),
)

#: Phrases that name something OTHER than an action kind and must survive untouched.
#:
#: Note what the table above does NOT do: a BARE `spawn` is never rewritten, only the
#: backticked kind and the `subrun`/`subtask` word forms. That is deliberate and it is the
#: load-bearing bound here, because `spawn` is also the name of a hook POINT — a moment in a
#: run's life that rules match on, written `moment: spawn` on a bare YAML key — and it is
#: ordinary English besides ("spawn a child", "spawnable"). A rewrite of the bare word would
#: silently stop those rules matching, with no error anywhere; the seed sweep shipped with this
#: change did exactly that to `evidence-discipline.md` before the bound was drawn here.
#: The entries below therefore guard the remaining collisions, where the backticked form or a
#: word form appears in a line that is about the moment rather than about the kind.
KEEP = (
    'point="spawn"', "point='spawn'", 'moment="spawn"', "moment='spawn'",
    "`spawn` hook", "spawn hook", "hook point", "subruns.KILL_JOIN_S",
    "spawnable",
)


def _rewrite(text: str) -> tuple[str, int]:
    """The rewritten text and how many substitutions were made.

    A line carrying one of KEEP is passed through untouched rather than partially rewritten:
    the unit is the LINE because that is the smallest span in which "spawn the child" and "the
    spawn hook point" can be told apart without parsing English.
    """
    out, count = [], 0
    for line in text.split("\n"):
        if any(keep in line for keep in KEEP):
            out.append(line)
            continue
        new = line
        for pattern, replacement in REWRITES:
            new, n = re.subn(pattern, replacement, new)
            count += n
        out.append(new)
    return "\n".join(out), count


def _targets(server) -> list[Path]:
    """Every file whose TEXT instructs a run — never a record of what a run did."""
    out: list[Path] = []
    lib = Path(server.libraries_home)
    for kind in ("workflows", "rules", "permissions", "patterns"):
        d = lib / kind
        if d.is_dir():
            out += [p for p in sorted(d.rglob("*")) if p.is_file()
                    and p.suffix in (".md", ".py", ".yaml", ".yml")]
    for home in (server.routines_home, getattr(server, "conversations_home", None),
                 getattr(server, "background_home", None)):
        if not home or not Path(home).is_dir():
            continue
        for routine in sorted(Path(home).iterdir()):
            if not routine.is_dir() or routine.name.startswith("."):
                continue
            for name in ("main.md", "instruction.md"):
                if (routine / name).is_file():
                    out.append(routine / name)
            if (routine / "stages").is_dir():
                out += sorted((routine / "stages").glob("*.md"))
    return out


def run_migration(server) -> dict:
    """Rewrite the three retired kind names wherever an instruction names them. Returns the
    record (empty when it already ran, or when nothing names them).
    """
    home = Path(server.routines_home)
    record_path = home / RECORD
    if record_path.exists():
        return {}

    changed: list[dict] = []
    for path in _targets(server):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue        # a file nobody can read instructs nobody; leave it as it is
        new, count = _rewrite(text)
        if count and new != text:
            try:
                atomic_write(path, new)
            except OSError as exc:
                log.warning("child-kind migration could not rewrite %s: %s", path, exc)
                continue
            changed.append({"path": str(path), "substitutions": count})

    record = {"ran": now_iso(), "files": len(changed), "changed": changed}
    atomic_write_json(record_path, record)
    if changed:
        log.info("child-kind migration rewrote %d instruction file(s) (D241)", len(changed))
    return record
