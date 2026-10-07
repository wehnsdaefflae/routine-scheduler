"""Who would READ what is sent to a routine — and, when nobody would, where to send it instead.

A report addressed to a routine is delivered into its inbox and read on its next run; a note in
a shared store is read at its addressee's next boot. A routine that starts no run — switched off
by the operator (`enabled: false`) or retired by its finish line — reads neither, so both
channels refuse at the moment of SENDING (F614 for reports, `note_refusal` for notes): the one
moment the sender still holds the context to redirect it.

A refusal that only said "off" left the sender guessing where the work went, and the usual
reason a routine is switched off is that its work MOVED — two routines merged, or several became
the TASKS of one (docs/tasks.md). So every refusal carries SUGGESTIONS: routines that would read
the message, each with why, best first —

1. the routine that CARRIES the target as a task: a task whose `origin` names it. The task is
   named too, so the hand-off lands on the right work inside that routine;
2. the target's lane-mates — routines fired in the same chain;
3. the routines sharing a store with it;
4. the routines sharing a tag with it, most shared tags first.

Every suggestion is a routine that reads (`reads`): offering another switched-off routine would
only buy the sender a second refusal. None at all is an honest answer — triage (a report with
no `target`) is read whatever happens, and every refusal names it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import lanes, registry, sharedstores, tasks
from .config import ServerConfig

#: How many suggestions a refusal carries — enough to choose between, short enough to read.
LIMIT = 6


@dataclass(frozen=True)
class Suggestion:
    slug: str
    why: str
    task: str = ""          # the task inside `slug` that carries the work, when one does

    def as_dict(self) -> dict:
        return {"slug": self.slug, "why": self.why, **({"task": self.task} if self.task else {})}


def reads(info: registry.RoutineInfo) -> bool:
    """Whether a message sent to this routine will be read: it starts runs — or it is off only
    because its routine.yaml could not be READ (the registry substitutes a disabled config and
    says so in `problems`). A report about a config that no longer parses is exactly the one
    somebody needs to file, so refusing it would silence the one channel that can fix it.
    """
    return info.fireable or any("unloadable routine.yaml" in p for p in info.problems)


def why_off(info: registry.RoutineInfo) -> tuple[str, str]:
    """`(state, because)` for a routine that reads nothing. The two "off"s call for different
    moves, so they are never merged: `disabled` is the operator's switch and may be a pause,
    `retired` is the routine's own finish line reached and is permanent.
    """
    if info.retired:
        return "retired", "the routine reached its finish line and is done for good"
    return "disabled", "the operator switched this routine off (`enabled: false`)"


def reader_slugs(server: ServerConfig, home: Path) -> list[str]:
    """Every routine a message would be read by, sorted."""
    return sorted(s for s, i in registry.scan(server, home).items() if reads(i))


def suggest(server: ServerConfig, home: Path, target: str, *,
            exclude: tuple[str, ...] = ()) -> list[Suggestion]:
    """Where a message for `target` should go instead, best first (see the module docstring).

    `exclude` drops the sender: suggesting a routine its own report would be refused for
    addressing (itself) is no suggestion.
    """
    scan = registry.scan(server, home)
    live = {s: i for s, i in scan.items() if reads(i) and s != target and s not in exclude}
    out: dict[str, Suggestion] = {}

    def add(slug: str, why: str, task: str = "") -> None:
        if slug in live and slug not in out:
            out[slug] = Suggestion(slug, why, task)

    for slug, reader in sorted(live.items()):
        for t in tasks.load(reader.cfg.dir)["tasks"]:
            if t.get("origin") == target:
                add(slug, f"carries {target!r} as its task {t['id']!r}", t["id"])
    if (lane := lanes.lane_of(home, target)) is not None:
        for slug in lanes.member_slugs(lane):
            add(slug, f"fires beside it in the lane {lane.get('name') or lane.get('id')!r}")
    if (info := scan.get(target)) is None:
        return list(out.values())[:LIMIT]
    for store in sharedstores.stores_of(home, info.cfg.fs_write_roots):
        for slug in sharedstores.sharers(home, store):
            add(slug, f"shares the store {store.name}")
    if tags := set(info.cfg.tags):
        overlap = sorted(((sorted(tags & set(i.cfg.tags)), s) for s, i in live.items()
                          if tags & set(i.cfg.tags)), key=lambda pair: (-len(pair[0]), pair[1]))
        for common, slug in overlap:
            add(slug, f"shares the tag{'s' if len(common) > 1 else ''} {', '.join(common)}")
    return list(out.values())[:LIMIT]


def render(suggestions: list[dict]) -> str:
    """The suggestions as one sentence the refusal carries — the same words on both channels."""
    if not suggestions:
        return "No routine that would read it is related to this one."
    parts = []
    for s in suggestions:
        line = f"{s['slug']!r} ({s['why']}"
        if s.get("task"):
            line += f" — address {s['slug']!r} and name the task {s['task']!r} in the title"
        parts.append(line + ")")
    return "Routines that would read it: " + "; ".join(parts) + "."


def note_refusal(server: ServerConfig, target: Path) -> str | None:
    """Why a write creating the note `target` must not happen — None when it may.

    The store's own rule first (`sharedstores.note_refusal`: the addressee must share the
    store), then the reader's: a sharer that starts no run would never read it either. The
    suggestions are narrowed to the routines that share THIS store, because a note can reach
    no one else — anyone outside it is reached by an addressed `report`.
    """
    home = server.routines_home
    if err := sharedstores.note_refusal(home, target):
        return err
    if (hit := sharedstores.note_addressee(home, target)) is None:
        return None
    store, to = hit
    info = registry.info(server, home, to)
    if info is None or reads(info):
        return None
    state, because = why_off(info)
    sharing = set(sharedstores.sharers(home, store))
    here = [s.as_dict() for s in suggest(server, home, to) if s.slug in sharing]
    return (f"{to!r} shares the store {store.name} but is {state}: {because} — it starts no run, "
            f"so it would never read this note. Nothing was written. {render(here)} A note "
            "reaches only the routines sharing the store; to reach any other routine, send an "
            "addressed `report`.")
