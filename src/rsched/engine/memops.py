"""The curated stores a run reads and writes by NAME, not by path — `.memory/` and the rule library.

Split out of `fileops.py` (F393). These look like file actions and are deliberately not: the
generic file kinds are REJECTED inside `.memory/`, so `memory_read`/`memory_write` are the only
way in. That is what lets the engine own `INDEX.md` (built from each write's `about`) and the
note cap, instead of a run quietly reorganising its own memory.

`read_rule` is here for the same reason — a rule is addressed by slug from the ONE library copy,
never forked into the routine — and is ungated: a routine must be able to read what binds it.
"""

from __future__ import annotations

from ..paths import atomic_write
from .observations import truncate
from .run_context import RunContext


def _memory_topics(mem_dir) -> list[str]:
    if not mem_dir.is_dir():
        return []
    return sorted(p.stem for p in mem_dir.glob("*.md") if p.name != "INDEX.md")

def _memory_index_upsert(mem_dir, name: str, about: str | None) -> None:
    """INDEX.md is engine-owned: one `- <name>.md: <about>` line per note, updated in the
    same operation as the note itself so the catalog can never drift. about=None removes.
    """
    index = mem_dir / "INDEX.md"
    lines = index.read_text(encoding="utf-8").splitlines() if index.exists() else []
    prefix = f"- {name}.md:"
    lines = [ln for ln in lines if not ln.startswith(prefix)]
    if about is not None:
        lines.append(f"{prefix} {about.strip()}")
    atomic_write(index, "\n".join(lines) + ("\n" if lines else ""))

def do_memory_read(action: dict, ctx: RunContext) -> dict:
    name = action["name"]
    mem_dir = ctx.routine.dir / ".memory"
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
    composed prompt — main.md's Standing practices tail names the held slugs and the run
    fetches the one it needs, so an unread rule costs nothing every turn. `name: "list"`
    returns the catalog (mirroring `util name=list`), which is how a run reaches a rule it
    does not hold: it applies for this run only, and asking the user to make it permanent is
    a finish-summary or deferred-ask_user matter.
    """
    from .. import library_docs

    name = action["name"]
    home = ctx.server.rules_home
    catalog = library_docs.list_docs(home)
    # "held" = bound by this routine's own config, so the model can tell a rule it should
    # ALREADY be applying from one it is consulting for the first time.
    held = set(ctx.routine.rules)
    if name == "list":
        return {"kind": "read_rule", "name": "list",
                "rules": [{"slug": d["slug"], "summary": d["summary"],
                           "held": d["slug"] in held} for d in catalog]}
    raw = library_docs.read_doc(home, name)
    if raw is None:
        return {"kind": "read_rule", "name": name, "missing": True,
                "available": [d["slug"] for d in catalog]}
    body = library_docs.doc_body(raw).strip()
    return {"kind": "read_rule", "name": name, "content": body,
            "lines": len(body.splitlines()), "held": name in held}

def do_memory_write(action: dict, ctx: RunContext) -> dict:
    name = action["name"]
    mem_dir = ctx.routine.dir / ".memory"
    path = mem_dir / f"{name}.md"
    if action.get("delete"):
        existed = path.is_file()
        if existed:
            path.unlink()
            _memory_index_upsert(mem_dir, name, None)
        return {"kind": "memory_write", "name": name, "deleted": True, "existed": existed}
    mem_dir.mkdir(exist_ok=True)
    created = not path.exists()
    data = str(action["content"]).rstrip() + "\n"
    atomic_write(path, data)
    _memory_index_upsert(mem_dir, name, str(action["about"]))
    return {"kind": "memory_write", "name": name, "created": created,
            "lines": len(data.splitlines())}
