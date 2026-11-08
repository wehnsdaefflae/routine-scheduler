"""The curated stores a run reads and writes by NAME, not by path — `.memory/` and the rule library.

Split out of `fileops.py` (F393). These look like file actions and are deliberately not: the
generic file kinds are REJECTED inside `.memory/`, so `memory_read`/`memory_write` are the only
way in. That is what lets the engine own `INDEX.md` (built from each write's `about`) instead
of a run quietly reorganising its own memory.

A note's SIZE is advice, not a gate (P3: a guard whose fix is mechanical applies it). The 100-line
bound used to REFUSE a write — schema-side for a whole note, here for an edit's result — and runs
then spent turns trimming to fit: three refusals for one pointer line ("shortening by one line to
fit"), six blind retries on one 98-line note (R2753), established evidence deleted to make room
for a correction (R2773). Nothing about the note is wrong at 101 lines, so the write lands and its
observation carries one line saying how far over the advised size it is.

`read_rule` is here for the same reason — a rule is addressed by slug from the ONE library copy,
never forked into the routine — and is ungated: a routine must be able to read what binds it.
"""

from __future__ import annotations

from ..ids import is_slug
from ..paths import atomic_write
from . import notemeta
from .authoring import anchor_edit
from .observations import truncate
from .run_context import RunContext, work_dir

#: The ADVISED size of one note — read by the observation's over-size line, never a refusal.
MEMORY_NOTE_ADVISED_LINES = 100


def _memory_topics(mem_dir) -> list[str]:
    if not mem_dir.is_dir():
        return []
    return sorted(p.stem for p in mem_dir.glob("*.md") if p.name != "INDEX.md")

def _memory_index_upsert(mem_dir, name: str, about: str | None,
                         meta: dict | None = None) -> None:
    """INDEX.md is engine-owned: one `- <name>.md: <about>` line per note, updated in the
    same operation as the note itself so the catalog can never drift. about=None removes.

    ONE line is the invariant every later upsert relies on — the replace finds a note's entry
    by its prefix — so `about` is folded onto a single line: a line break in it left the
    continuation behind as an orphan line no revision or delete could ever reach.

    `meta` is the note's expiry and provenance (D268, engine/notemeta.py), rendered onto the
    SAME line for the same reason: a second store for a note's freshness could drift from the
    catalog, and this one cannot. A note carrying none renders exactly as it always did.
    """
    index = mem_dir / "INDEX.md"
    lines = index.read_text(encoding="utf-8").splitlines() if index.exists() else []
    prefix = f"- {name}.md:"
    previous = next((ln for ln in lines if ln.startswith(prefix)), "")
    lines = [ln for ln in lines if not ln.startswith(prefix)]
    if about is None and meta and previous:
        # marking an existing note verified without restating its SUBJECT: carry the `about`
        # forward off the line being replaced, markers stripped, so the catalog keeps saying
        # what the note is about
        about = notemeta.about_of(previous)
    if about is not None:
        # MERGE, never replace: a revision that says nothing about expiry keeps the date an
        # earlier run recorded, and a write stating only `verified` leaves it standing
        merged = {**notemeta.meta_of(previous), **(meta or {})}
        lines.append(f"{prefix} {' '.join(about.split())}{notemeta.index_suffix(merged)}")
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
        # content gets lost. The note is patched engine-side, so nothing is ever recomposed.
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
    else:
        data = str(action["content"]).rstrip() + "\n"
    atomic_write(path, data)
    meta = notemeta.from_action(action)
    if action.get("about") is not None or not action.get("anchor") or meta:
        # an edit does not change the note's SUBJECT, so `about` is optional there; a revised
        # one still updates the engine-owned INDEX line — and so does metadata alone (D268),
        # which is how a run marks an unchanged note verified
        about = action.get("about")
        _memory_index_upsert(mem_dir, name, None if about is None else str(about), meta)
    lines = len(data.splitlines())
    out = {"kind": "memory_write", "name": name, "created": created, "lines": lines}
    if meta:
        # named in the observation because what notemeta could not render was LEFT OUT: a run
        # that sent `stale_after: "soon"` sees that nothing was recorded (notemeta's docstring)
        out["meta"] = meta
    if lines > MEMORY_NOTE_ADVISED_LINES:
        out["over_advised"] = lines - MEMORY_NOTE_ADVISED_LINES
    if action.get("anchor") is not None:
        out.update(edited=True, replacements=replacements)
    return out
