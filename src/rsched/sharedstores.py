"""SHARED STORES — directories several routines read and write together, plus the light NOTES
channel between the routines sharing one (F335).

A shared store is a directory directly under `<routines_home>/.control/group-stores/`. Nothing
else records one: a routine SHARES a store when that directory is one of its own
`fs_write_roots`, so a store reaches a run as an ordinary read-write root — the file actions and
the util sandbox already honour it; nothing injects it — and which routines share it is read
back out of their routine.yaml files. Sharing lives in exactly one place, so it cannot disagree
with itself; it is the operator's decision like every other root: no run writes routine.yaml.

A root ABOVE the stores (a meta routine that may write the whole routines home) covers every
store and shares none. Sharing is naming the store itself, which is what keeps the set of
routines a note can reach the set the operator put there.

The directory name `group-stores` is FROZEN: routines address these paths in their own memory
(one live routine carries "READ …/group-stores/grp-8bfd2aa6/fau-mark-preferences.md before …" as
a standing prevention rule it wrote for itself after an incident; several more name a store in a
ledger), so renaming the directory would mean editing agent-authored memory to keep it true.

Writers are whole-file atomic (the engine's write path) and collisions are last-write-wins PER
FILE, so the routines sharing a store write per-routine filenames and treat shared files as
read-mostly.

**Notes.** A NOTE is coordination; a `report` is work an owner must act on, tracked until
answered. For routines coordinating over one store a report is heavyweight — it turns "here is
the file I staged for you" into a ledger row and a Messages-page item somebody has to close. So:

    <store>/notes/<to-slug>/note-<anything>.json     {"from": slug, "ts": iso, "text": …}

A routine WRITES one with an ordinary file write; the engine READS them at the addressee's boot
from every store it shares, renders them into the state digest and DELETES them once read — a
note is delivered exactly once, like `inbox/`; delivery never starts a run. No approval, no
ledger row, no Messages-page item: the store is in its sharers' write roots and nobody else's.

The one mistake the channel can express is a note addressed to a routine that does NOT share the
store: it would sit there unread forever while its writer believed it delivered. The engine's
write gate refuses exactly that (`note_refusal`) and names the channel that reaches any routine —
an addressed `report`. A sharer that starts no run would never read it either; that refusal needs
the registry, so it lives in `recipients.note_refusal`, which the gate calls. A shell command, a
script or a util writes past that gate, which is why the harness contract says who shares each
store.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import yaml

from .paths import expand, read_json, read_yaml

#: The directory every shared store sits in, under `.control/` like all other run data. FROZEN —
#: see the module docstring.
STORES_DIRNAME = "group-stores"
NOTES_DIRNAME = "notes"
# What one boot surfaces. A note is a nudge, not a mailbox: past this the run is being handed a
# backlog it will not read; the oldest are the least likely to still matter.
MAX_NOTES_SHOWN = 20
TEXT_CAP = 2000


def stores_home(routines_home: Path) -> Path:
    return Path(routines_home) / ".control" / STORES_DIRNAME


def _resolved(path: object) -> Path | None:
    """`path` with `~`/`$VARS` expanded and symlinks resolved — the one form two spellings of the
    same directory compare equal in. None for a value that is not a path at all.
    """
    if not isinstance(path, (str, Path)) or not str(path).strip():
        return None
    try:
        return expand(path).resolve()
    except (OSError, RuntimeError):
        return None


def stores_of(routines_home: Path, fs_write_roots: Iterable[object]) -> list[Path]:
    """The shared stores among `fs_write_roots`, resolved, in the order the routine lists them:
    each root that IS a directory directly under the stores home. A root above it covers every
    store and shares none.
    """
    home = _resolved(stores_home(routines_home))
    out: list[Path] = []
    for root in fs_write_roots:
        p = _resolved(root)
        if p is not None and p.parent == home and p not in out:
            out.append(p)
    return out


def sharers(routines_home: Path, store: Path) -> list[str]:
    """Every routine slug whose OWN routine.yaml names `store` among its `fs_write_roots`, sorted.

    A SCAN of every routine.yaml in the home, so a file that cannot be read — one caught
    mid-save — is a NON-sharer rather than an exception: the harness contract, the state digest
    and the write gate all run through here; none of them may fail because somebody is
    halfway through saving an unrelated routine.
    """
    target = _resolved(store)
    home = Path(routines_home)
    if target is None or not home.is_dir():
        return []
    return [rdir.name for rdir in sorted(home.iterdir())
            if not rdir.name.startswith(".") and (rdir / "routine.yaml").is_file()
            and target in _write_roots_of(rdir / "routine.yaml")]


def _write_roots_of(cfg: Path) -> list[Path]:
    """The resolved `fs_write_roots` one routine.yaml names — [] when it cannot be read.

    Memoized on the file's stat fingerprint: every harness contract and every guarded note write
    scans the whole home; a saved file changes inode+mtime+size, so an edit is never served
    stale.
    """
    from .readmodels import memo

    def read() -> list[Path]:
        try:
            raw = read_yaml(cfg, {})
        except (OSError, yaml.YAMLError):        # a broken file shares nothing
            return []
        roots = raw.get("fs_write_roots") if isinstance(raw, dict) else None
        return [p for p in map(_resolved, roots if isinstance(roots, list) else [])
                if p is not None]

    return memo.memoized(f"write-roots:{cfg}", [cfg], read)


def notes_dir(store: Path, to_slug: str) -> Path:
    return Path(store) / NOTES_DIRNAME / to_slug


def drain(routines_home: Path, slug: str, fs_write_roots: Iterable[object]) -> list[dict]:
    """Every note waiting for `slug` in the stores it shares, oldest first, REMOVED as read.

    Read-and-drop, like `inbox/`: a note is delivered exactly once, so a run that dies after
    boot loses its notes — the right trade here, because a note that survived would be re-shown
    every run until someone deleted it by hand, which is the tracked-work-item shape this channel
    exists to avoid. The text cap runs on the READ because the read is the only half the engine
    owns: the writer is another routine's ordinary file write, straight into this prompt.
    """
    found: list[tuple[float, str, dict]] = []
    for store in stores_of(routines_home, fs_write_roots):
        d = notes_dir(store, slug)
        if not d.is_dir():
            continue
        for path in sorted(d.glob("note-*.json")):
            try:
                written = path.stat().st_mtime
            except OSError:
                continue
            rec = read_json(path)
            path.unlink(missing_ok=True)
            if isinstance(rec, dict) and str(rec.get("text") or "").strip():
                found.append((written, path.name, {
                    "from": str(rec.get("from") or "?"), "ts": str(rec.get("ts") or ""),
                    "text": str(rec["text"])[:TEXT_CAP]}))
    return [rec for _, _, rec in sorted(found, key=lambda row: (row[0], row[1]))]


def digest_section(notes: list[dict]) -> str:
    """The state-digest block for the notes boot drained (`drain`) — "" when there are none.

    Pure: it renders what it is handed. The drain is boot's own step, so the digest builder
    has no side effect and the once-per-run delivery is visible where it happens. Past the
    cap the NEWEST notes are shown and the run is told how many older ones were dropped, so
    the drop never reads as "that was all".
    """
    if not notes:
        return ""
    dropped = max(0, len(notes) - MAX_NOTES_SHOWN)
    lines = [f"- from {n['from']} ({n['ts']}): {n['text']}" for n in notes[dropped:]]
    tail = (f"\n({dropped} older note(s) were dropped unread — this channel is a nudge between "
            "routines sharing a store, not a mailbox.)" if dropped else "")
    return ("NOTES FROM ROUTINES YOU SHARE A STORE WITH (coordination — read once, now gone; "
            "they are NOT tracked anywhere and nobody is waiting on a reply):\n"
            + "\n".join(lines) + tail)


def contract_section(routines_home: Path, slug: str, fs_write_roots: Iterable[object]) -> str:
    """The SHARED STORES paragraph of the harness contract — "" when this routine shares none.

    Each store is listed with the OTHER routines sharing it, because "leave a note for a sharer"
    is not actionable without their slugs and a channel a run does not know about is a channel
    that does not exist.
    """
    stores = stores_of(routines_home, fs_write_roots)
    if not stores:
        return ""
    rows = []
    for store in stores:
        others = [s for s in sharers(routines_home, store) if s != slug]
        rows.append(f"- {store} — shared with "
                    + (", ".join(others) if others else "no other routine yet"))
    return ("\nSHARED STORES (read+write roots you share with other routines — exchange files "
            "with them there):\n" + "\n".join(rows)
            + "\nWrites are whole-file and last write wins per file, so prefer per-routine "
            "filenames (<your-slug>-<topic>.md) and treat shared files as read-mostly. To tell a "
            "routine sharing a store something, write a note for it: a JSON file "
            f'{{"from": "{slug}", "text": "…"}} at <store>/{NOTES_DIRNAME}/<their-slug>/'
            "note-<anything>.json. Their next run reads it once and it is gone — no approval, "
            "no tracking, nobody waiting on a reply — and only a routine sharing THAT store ever "
            "reads it. Use a note for coordination ('I staged X for you'); use `report` when "
            "someone must ACT on a problem and it has to be tracked until they answer — and to "
            "reach any routine that shares no store with you.")


def note_addressee(routines_home: Path, target: Path) -> tuple[Path, str] | None:
    """`(store, addressee)` when `target` lies inside `<store>/notes/<addressee>/`, else None."""
    home, path = _resolved(stores_home(routines_home)), _resolved(target)
    if home is None or path is None:
        return None
    try:
        parts = path.relative_to(home).parts
    except ValueError:
        return None
    if len(parts) < 3 or parts[1] != NOTES_DIRNAME:
        return None
    return home / parts[0], parts[2]


def note_refusal(routines_home: Path, target: Path) -> str | None:
    """Why a write creating `target` must not happen — None when it may.

    Only a path inside `<store>/notes/<x>/` is judged; it is refused when `x` does not share
    that store: a routine reads notes only from the stores among its own write roots, so the
    note would never be read. Deleting one is never judged here — clearing a stranded note is
    the repair, not the defect. A sharer that starts no run would never read it either; that
    half needs the registry and is `recipients.note_refusal`, which the write gate calls.
    """
    if (hit := note_addressee(routines_home, target)) is None:
        return None
    store, to = hit
    who = sharers(routines_home, store)
    if to in who:
        return None
    return (f"{to!r} does not share the store {store} — a routine reads notes only from the "
            "stores among its own read-write roots, so this note would never be read. Routines "
            f"sharing it: {', '.join(who) or 'none'}. To reach {to!r}, send an addressed "
            f"`report` with target {to!r} instead.")
