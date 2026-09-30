"""The domains half of the settings-patterns migration — MIGRATION(expires=2026-10-20).

Called once by `migrate_settings_patterns.run_migration` after every routine converted. Domains
end: `domains.json` is kept beside itself as a retired copy, notes written to routines outside
their store reach those routines' inboxes, a store's `steward-hub-tab.txt` gives way to each
routine's own `hub_tab`, and a retired routine leaves its lane.
"""

from __future__ import annotations

from pathlib import Path

from .migrate_settings_patterns_spec import ROUTINES
from .paths import read_json, read_yaml


def end_domains(home: Path) -> dict:
    """Run the domains half; returns what it did, for the migration record."""
    delivered = _stranded_notes(home)
    _retire_domains(home)
    return {"notes_delivered": delivered, "hub_tab_files": _retire_hub_tab_files(home),
            "lanes_left": _leave_lanes(home)}


def _stranded_notes(home: Path) -> list[str]:
    """Notes a routine left for another that never shared its store were never read; each is
    delivered once, as the message it was meant to be.
    """
    from .engine.inbox import file_message

    delivered: list[str] = []
    stores = home / ".control" / "group-stores"
    for note in sorted(stores.glob("*/notes/*/note-*.json")) if stores.is_dir() else []:
        to = note.parent.name
        rec = read_json(note)
        if not (home / to / "routine.yaml").is_file() or not isinstance(rec, dict):
            continue
        if _shares(home, to, note.parents[2]):
            continue                    # a sharer drains it itself
        sender = str(rec.get("from") or "another routine")
        file_message(home / to, f"Note from {sender} ({rec.get('ts') or 'undated'}): "
                                f"{rec.get('text') or ''}", source=sender, via="report")
        note.unlink()
        delivered.append(f"{to}:{note.name}")
    return delivered


def _shares(home: Path, slug: str, store: Path) -> bool:
    raw = read_yaml(home / slug / "routine.yaml", {})
    roots = raw.get("fs_write_roots") if isinstance(raw, dict) else None
    return any(Path(str(r)).expanduser().resolve() == store.resolve() for r in roots or [])


def _retire_domains(home: Path) -> None:
    src = home / ".control" / "domains.json"
    if src.is_file():
        src.rename(src.with_name("domains.retired-2026-09-29.json"))


def _retire_hub_tab_files(home: Path) -> list[str]:
    """A store's `steward-hub-tab.txt` named the card heading its sharers published; the heading
    is each routine's own `hub_tab` now, so the file goes rather than standing as a second source.
    """
    gone = []
    for path in sorted((home / ".control" / "group-stores").glob("*/steward-hub-tab.txt")):
        path.unlink()
        gone.append(path.parent.name)
    return gone


def _leave_lanes(home: Path) -> list[str]:
    """A retired routine leaves every lane holding it, so the chain stops waiting on it."""
    from . import lanes

    left: list[str] = []
    for slug in (s for s, spec in ROUTINES.items() if spec.get("leave_lanes")):
        for lane in lanes.list_lanes(home):
            members = [m for m in lane.get("members") or [] if m.get("slug") != slug]
            if len(members) != len(lane.get("members") or []):
                lanes.update(home, lane["id"], members=members)
                left.append(f"{slug}:{lane['id']}")
    return left
