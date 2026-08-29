"""MIGRATION(expires=2026-09-30): declare `kind: template` on the clarification routine (0.167.0).

The wizard's clarification template was identifiable only by its SLUG, hardcoded across the
web layer — so every guard (cannot run, cannot archive, cannot be messaged, rules are fixed)
and the routine card's `protected` flag keyed off a string comparison. `kind` is now a
declared field, and the guards read it, so a second template would be protected by
construction rather than by remembering to add its slug in five places.

An existing instance needs the marker written into its own `routine.yaml` — nothing else
would ever add it, and without it the live template silently becomes runnable and archivable.

Also repairs one stale line the 0.164.0 rules rename missed in the template's `main.md`. That
file IS user-editable (the recipe editor does not guard it), so the repair is a targeted
replace of the exact retired sentence — never a wholesale rewrite, which would discard whatever
the user has since written there.

Runs once at daemon boot, then gets deleted (delete-after-convergence — CLAUDE.md).
"""

from __future__ import annotations

import logging

import yaml

from .paths import atomic_write

log = logging.getLogger("rsched.migrate_template_kind")

TEMPLATE_SLUG = "clarification"
_STALE_LINE = "- **Traits** — practice modules copied into every session."
_FIXED_LINE = ("- **Rules** — the general rules every session is bound by "
               "(the prose lives in the library).")


def migrate_template_kind(server) -> bool:
    """Both repairs, independently idempotent. True when anything changed."""
    d = server.routines_home / TEMPLATE_SLUG
    if not d.is_dir():
        return False
    changed = False
    cfg_path = d / "routine.yaml"
    try:
        if cfg_path.is_file():
            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict) and raw.get("kind") != "template":
                raw["kind"] = "template"
                atomic_write(cfg_path,
                             yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
                log.warning("marked the %s routine as kind: template", TEMPLATE_SLUG)
                changed = True
        main = d / "main.md"
        if main.is_file():
            body = main.read_text(encoding="utf-8")
            if _STALE_LINE in body:
                atomic_write(main, body.replace(_STALE_LINE, _FIXED_LINE, 1))
                log.warning("repaired the retired 'Traits' line in %s/main.md", TEMPLATE_SLUG)
                changed = True
    except (OSError, yaml.YAMLError) as exc:
        log.warning("template-kind migration failed: %s", exc)
    return changed
