"""MIGRATION(expires=2026-12-01): converge what the reminders rollout left behind.

0.309.0 shipped the reminder layer "on by default" and it was on nowhere. `bootstrap._merge_caps`
carried a private copy of the activation cascade that knew four of the nine capability keys, so
adopting a permission whose `requires:` names any other DIAL wrote the doc into `permissions:`
and left the capability at its default — the doc held, the capability off, and the engine (which
enforces from capabilities alone, by design) behaving exactly as if the permission had never been
adopted. `workflows` and `util_tags` had been falling through the same hole for longer;
`reminders` made it visible only because it was the first dial whose whole point was to arrive
switched on. The setup surface's "held, but its requires: are not switched on" check had the
identical four-of-nine blindness, so the guard whose job was to catch this could not.

All three now go through `grants.capabilities_for`. This module is the one-shot that repairs
what the old code already wrote:

1. ROUTINES — re-raise every routine's capabilities from the permissions it already holds. The
   32 live routines are marked adopted, so the adopt pass will never revisit them.
2. TEMPLATES — the live library's settings templates name every dial but the two newest, so the
   panel an operator reads as "this is what the routine will be" is incomplete. The seed is
   fixed, and seed sync only ever ADDS files, so a live instance needs this.

Both are idempotent by construction — each applies the same transform the ordinary write path
applies, so anything already correct comes out unchanged and is not rewritten.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .paths import atomic_write_yaml, read_yaml

log = logging.getLogger("rsched.migrate")

#: The dials a template must name to describe what it produces, and the value the seed uses.
#: Only ADDED where absent — a template that has since been edited to a deliberate value keeps
#: it, because a migration repairing an omission may not overrule a decision.
_TEMPLATE_DIALS = {"remind_confirm": "always", "reminders": "local"}


def converge_routines(routines_home: Path, permissions_home: Path) -> list[str]:
    """Re-raise every routine's capabilities from the permissions it holds. One note per
    routine CHANGED, naming the keys — a silent config rewrite is the wrong kind of quiet.
    """
    from .grants import capabilities_for, floor_capabilities, read_library_requires

    lib = read_library_requires(permissions_home)
    notes: list[str] = []
    if not Path(routines_home).is_dir():
        return notes
    for rdir in sorted(Path(routines_home).iterdir()):
        cfg = rdir / "routine.yaml"
        if rdir.name.startswith(".") or not cfg.is_file():
            continue
        try:
            raw = read_yaml(cfg, {})
        except (OSError, yaml.YAMLError) as exc:
            notes.append(f"{rdir.name}: skipped — {exc}")
            continue
        if not isinstance(raw, dict):
            continue
        active = [str(p) for p in raw.get("permissions") or []]
        before = dict(raw.get("capabilities") or {})
        after = floor_capabilities(active, lib, capabilities_for(active, lib, dict(before)))
        if after == before:
            continue
        changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        raw["capabilities"] = after
        try:
            atomic_write_yaml(cfg, raw)
        except OSError as exc:
            notes.append(f"{rdir.name}: could not write — {exc}")
            continue
        notes.append(f"{rdir.name}: " + ", ".join(f"{k}={after.get(k)!r}" for k in changed))
    return notes


def converge_templates(libraries_home: Path) -> list[str]:
    """Give every live settings template the two dials the seed now names.

    Text-level, not a parse-and-redump: a template is a hand-written library doc whose comments
    and key order are part of what the operator reads, and rewriting the YAML would flatten
    that to repair two missing lines.
    """
    from .templates import templates_home

    notes: list[str] = []
    home = templates_home(libraries_home)
    if not home.is_dir():
        return notes
    for path in sorted(home.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            notes.append(f"{path.name}: unreadable — {exc}")
            continue
        added, out = [], text
        for dial, value in _TEMPLATE_DIALS.items():
            if f"\n    {dial}:" in out:
                continue
            # anchored on the dial that has always been there, so the two land together and in
            # the seed's order rather than at whatever line a blind append would reach
            if "\n    rule_confirm:" not in out:
                notes.append(f"{path.name}: no capabilities block to extend")
                break
            line = out.split("\n    rule_confirm:")[1].split("\n")[0]
            out = out.replace(f"\n    rule_confirm:{line}",
                              f"\n    rule_confirm:{line}\n    {dial}: {value}", 1)
            added.append(dial)
        if "\n  - reminders\n" not in out and "\n  permissions:\n" in out:
            out = out.replace("\n  permissions:\n", "\n  permissions:\n  - reminders\n", 1)
            added.append("permissions:reminders")
        if not added:
            continue
        try:
            path.write_text(out, encoding="utf-8")
        except OSError as exc:
            notes.append(f"{path.name}: could not write — {exc}")
            continue
        notes.append(f"{path.name}: added {', '.join(added)}")
    return notes


def run(routines_home: Path, permissions_home: Path, libraries_home: Path) -> int:
    """Daemon-boot entry point. Returns the number of files changed; logs each one."""
    notes = converge_routines(routines_home, permissions_home)
    notes += converge_templates(libraries_home)
    if notes:
        log.warning("reminders-rollout migration: %s", "; ".join(notes))
    return len(notes)
