"""Seed ↔ steps provenance: the drift baseline that ties a routine's instruction (the SEED)
to the main.md + steps/ that were compiled from it.

Every compile (scaffold, first-run decompose, and the recompile action) stamps two hashes into
main.md's frontmatter: `seed_sha256` (the instruction) and `compiled_sha256` (main.md body + all
step modules). `drift()` recomputes both live so the routine page can tell the user when the seed
was edited without recompiling, or when the steps changed on their own (hand edits or the
routine-improver) and the seed no longer describes what the routine does.

Pure + dependency-light on purpose (api_routines imports only `drift`): the WRITE side stays with
the callers, which own dump_markdown — `stamp()` just returns the enriched meta.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import frontmatter


def read_steps(routine_dir: Path) -> dict[str, str]:
    """Every step module by stem → its raw text (the unit the compiled hash is taken over)."""
    steps = routine_dir / "steps"
    if not steps.is_dir():
        return {}
    return {p.stem: p.read_text(encoding="utf-8")
            for p in sorted(steps.glob("*.md")) if p.is_file()}


def seed_hash(instruction: str) -> str:
    return hashlib.sha256(instruction.strip().encode("utf-8")).hexdigest()


def compiled_hash(main_body: str, steps: dict[str, str]) -> str:
    """A stable digest of the compiled recipe: main.md BODY (frontmatter excluded — it carries the
    hash itself) plus each step module, name-sorted. `.strip()` on every part so a trailing-newline
    difference between the in-memory write and the on-disk read never reads as drift."""
    h = hashlib.sha256()
    h.update(main_body.strip().encode("utf-8"))
    h.update(b"\0")
    for name in sorted(steps):
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(steps[name].strip().encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def stamp(meta: dict, *, routine_dir: Path, main_body: str, instruction: str) -> dict:
    """Return `meta` enriched with the seed + compiled provenance hashes. Call AFTER every step
    file is on disk (read_steps reads them) and with the exact `main_body` about to be written."""
    return {**meta,
            "seed_sha256": seed_hash(instruction),
            "compiled_sha256": compiled_hash(main_body, read_steps(routine_dir))}


def drift(routine_dir: Path, instruction: str) -> dict:
    """{tracked, instruction, steps}: whether this routine has a compile baseline, and if so whether
    the seed / the steps have changed since it was stamped. Routines compiled before provenance
    existed report tracked=False (a recompile establishes the baseline)."""
    main = routine_dir / "main.md"
    if not main.exists():
        return {"tracked": False, "instruction": False, "steps": False}
    meta, body = frontmatter.parse(main.read_text(encoding="utf-8"))
    seed, comp = meta.get("seed_sha256"), meta.get("compiled_sha256")
    if not seed or not comp:
        return {"tracked": False, "instruction": False, "steps": False}
    return {"tracked": True,
            "instruction": seed_hash(instruction) != seed,
            "steps": compiled_hash(body, read_steps(routine_dir)) != comp}
