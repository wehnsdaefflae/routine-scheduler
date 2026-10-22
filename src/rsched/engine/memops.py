"""The curated stores a run reads and writes by NAME, not by path — `.memory/` and the rule library.

Split out of `fileops.py` (F393). These look like file actions and are deliberately not: the
generic file kinds are REJECTED inside `.memory/`, so `memory_read`/`memory_write` are the only
way in. That is what lets the engine own `INDEX.md` (built from each write's `about`) and the
note cap, instead of a run quietly reorganising its own memory.

`read_rule` is here for the same reason — a rule is addressed by slug from the ONE library copy,
never forked into the routine — and is ungated: a routine must be able to read what binds it.
"""

from __future__ import annotations

from ..ids import is_slug
from ..paths import atomic_write
from .actions import MEMORY_NOTE_MAX_LINES
from .authoring import anchor_edit
from .observations import truncate
from .run_context import RunContext, work_dir


def _memory_topics(mem_dir) -> list[str]:
    if not mem_dir.is_dir():
        return []
    return sorted(p.stem for p in mem_dir.glob("*.md") if p.name != "INDEX.md")

def _memory_index_upsert(mem_dir, name: str, about: str | None) -> None:
    """INDEX.md is engine-owned: one `- <name>.md: <about>` line per note, updated in the
    same operation as the note itself so the catalog can never drift. about=None removes.

    ONE line is the invariant every later upsert relies on — the replace finds a note's entry
    by its prefix — so `about` is folded onto a single line: a line break in it left the
    continuation behind as an orphan line no revision or delete could ever reach.
    """
    index = mem_dir / "INDEX.md"
    lines = index.read_text(encoding="utf-8").splitlines() if index.exists() else []
    prefix = f"- {name}.md:"
    lines = [ln for ln in lines if not ln.startswith(prefix)]
    if about is not None:
        lines.append(f"{prefix} {' '.join(about.split())}")
    atomic_write(index, "\n".join(lines) + ("\n" if lines else ""))

def do_memory_read(action: dict, ctx: RunContext) -> dict:
    name = action["name"]
    mem_dir = work_dir(ctx) / ".memory"
    path = mem_dir / f"{name}.md"
    if not path.is_file():
        return {"kind": "memory_read", "name": name, "missing": True,
                "topics": _memory_topics(mem_dir)}
    content, truncated = truncate(path.read_text(encoding="utf-8", errors="replace"))
    return {"kind": "memory_read", "name": name, "content": content,
            "lines": len(content.splitlines()), "truncated": truncated}

def do_read_rule(action: dict, ctx: RunContext) -> dict:
    """Read a general RULE from the shared library — the only way a run sees rule prose.

    Rules live in ONE place and are read-only to every run: this action never writes, and the
    held set is routine.yaml config the user owns. The prose is deliberately NOT in the
    composed prompt — the digest names each held rule beside when it applies; the run
    fetches the one it needs, so an unread rule costs nothing every turn. `name: "list"`
    returns the catalog (mirroring `util name=list`), which is how a run reaches a rule it
    does not hold: it applies for this run only, and asking the user to make it permanent is
    a finish-summary or deferred-ask_user matter.
    """
    from .. import library_docs

    name = action["name"]
    home = ctx.server.rules_home
    # "held" = bound by this routine's own config, so the model can tell a rule it should
    # ALREADY be applying from one it is consulting for the first time.
    held = set(ctx.routine.rules)
    if name == "list":
        return {"kind": "read_rule", "name": "list",
                "rules": [{"slug": d["slug"], "summary": d["summary"],
                           "held": d["slug"] in held} for d in library_docs.list_docs(home)]}
    # A rule is addressed by its SLUG, never by a path. `read_doc` joins the name onto the
    # library dir, so `../../<routine>/.memory/<note>` — or an absolute path, which a join
    # simply adopts — read any .md file on the host past the run's fs jail, through a kind
    # every routine holds. validate_action refuses such a name first; this is the backstop.
    raw = library_docs.read_doc(home, name) if is_slug(name) else None
    if raw is None:
        return {"kind": "read_rule", "name": name, "missing": True,
                "available": library_docs.slugs(home)}
    body = library_docs.doc_body(raw).strip()
    return {"kind": "read_rule", "name": name, "content": body,
            "lines": len(body.splitlines()), "held": name in held}

def do_memory_write(action: dict, ctx: RunContext) -> dict:
    name = action["name"]
    mem_dir = work_dir(ctx) / ".memory"
    path = mem_dir / f"{name}.md"
    if action.get("delete"):
        existed = path.is_file()
        if existed:
            path.unlink()
            _memory_index_upsert(mem_dir, name, None)
        return {"kind": "memory_write", "name": name, "deleted": True, "existed": existed}
    mem_dir.mkdir(exist_ok=True)
    created = not path.exists()
    replacements = 0
    if action.get("anchor") is not None:
        # EDIT MODE (R2432/R2456), the third caller of authoring's one anchor rule. Adding two
        # facts to a 95-line note required re-emitting all 95 — expensive, and exactly how
        # content gets lost: one routine composed a 104-line revision, was rejected at the cap,
        # hand-trimmed it and composed it again, on a note created by the previous fix for the
        # same problem. The note is patched engine-side, so the cap is then measured against the
        # RESULT and work that was never recomposed can never be rejected for its size.
        if created:
            return {"kind": "memory_write", "name": name, "error":
                    f"no note {name!r} exists to edit — pass 'content' (the whole note) and "
                    "'about' to create one", "topics": _memory_topics(mem_dir)}
        before = path.read_text(encoding="utf-8")
        patched, problem = anchor_edit(
            before, action, current="the note",
            verbatim_from=f'{{"kind": "memory_read", "name": "{name}"}}',
            whole="'content' (the whole note)")
        if problem:
            return {"kind": "memory_write", "name": name, "error": problem}
        replacements = before.count(str(action["anchor"]))
        data = patched.rstrip() + "\n"
        if len(data.splitlines()) > MEMORY_NOTE_MAX_LINES:
            # EVERY number the caller needs to size the next attempt, because the result size
            # alone does not determine one (R2610): five consecutive rejections in one run, three
            # on the same note, which sat at 92 of 100 lines — so NO net addition could ever have
            # succeeded and "make the replacement shorter" invited a trim-and-retry loop for
            # something only a larger anchor can fix. Current + anchor gives the budget; the way
            # out when the budget is zero is replacing MORE of the note, not writing less.
            #
            # R2753 + R2773, two unrelated routines on 2026-10-21, are the rest of it. The
            # message stated the allowance and never the SUBMISSION, so a run still could not
            # compute its own overrun: funscript-trainer retried SIX times on one 98-line note,
            # each attempt a blind guess at its own line count. Hence the replacement's size and
            # the exact cut. And the remedy order matters because the wrong remedy is the
            # tempting one — moltbook-heartbeat sat permanently AT the cap while trying to
            # CORRECT an assertion its own measurement had refuted, and "condense what is
            # already there" pushes a run to delete established evidence to make room for a
            # correction. Splitting the topic and leaving a pointer behind loses nothing, so it
            # is named as the way out rather than as the third option.
            anchor_lines = len(str(action["anchor"]).splitlines())
            before_lines, result_lines = len(before.splitlines()), len(data.splitlines())
            repl_lines = len(str(action.get("replacement") or "").splitlines())
            budget = MEMORY_NOTE_MAX_LINES - (before_lines - anchor_lines)
            return {"kind": "memory_write", "name": name, "error":
                    f"the edit would leave the note at {result_lines} lines — notes are capped "
                    f"at {MEMORY_NOTE_MAX_LINES}. The note is UNCHANGED on disk. It currently "
                    f"has {before_lines} lines, your anchor covers {anchor_lines} and your "
                    f"replacement is {repl_lines} line(s), so a replacement fits in "
                    f"{max(budget, 0)} line(s): cut {result_lines - MEMORY_NOTE_MAX_LINES} "
                    "line(s) from it, or replace a LARGER anchor (more of the note for your new "
                    "text to fit in). Do NOT condense what is already there to make room — that "
                    "spends established evidence on a correction: SPLIT the topic into a new "
                    "note and leave a short pointer block behind in this one"}
    else:
        data = str(action["content"]).rstrip() + "\n"
    atomic_write(path, data)
    if action.get("about") is not None or not action.get("anchor"):
        # an edit does not change the note's SUBJECT, so `about` is optional there; a revised
        # one still updates the engine-owned INDEX line
        _memory_index_upsert(mem_dir, name, str(action["about"]))
    out = {"kind": "memory_write", "name": name, "created": created,
           "lines": len(data.splitlines())}
    if action.get("anchor") is not None:
        out.update(edited=True, replacements=replacements)
    return out
