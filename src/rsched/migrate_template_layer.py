"""MIGRATION(expires=2026-09-30): settings templates stop being a layer (0.269.0).

0.262.0 made a routine's `template:` a live inheritance layer under its own config, so a
routine's `routine.yaml` recorded only its DIFFERENCES from that template. The operator
reversed that on 2026-08-30: a template is a PRESELECTION, copied in once at adoption.

Nothing resolves `template:` any more, so a routine still written in the differences-only shape
would silently LOSE everything its template used to supply — its permissions, its rules, its
capability mapping — at the next boot. This materializes those values into each routine's own
file, once, and drops the two dead keys.

`template_except:` is applied on the way in and then dropped: a subtraction against a layer that
no longer exists is meaningless, and the entries it removed must not reappear.

Runs once at daemon boot, then gets deleted (delete-after-convergence — CLAUDE.md).
"""

from __future__ import annotations

import logging

import yaml

from .paths import atomic_write
from .templates import config_for

log = logging.getLogger("rsched.migrate_template_layer")


def _materialize(raw: dict, tpl: dict) -> dict:
    """The 0.262.0 load-time merge, applied as a WRITE — union for the list keys, fill per key
    for the maps, the routine's own value winning — then `template_except:` subtracted.

    Deliberately re-implemented against `apply_group_config` rather than copied: that function
    IS the merge the layer performed, so using it is what makes "the effective config is
    unchanged" true rather than hoped.
    """
    from .config.groupconfig import apply_group_config

    merged, _ = apply_group_config(raw, tpl)
    drop = {str(x) for x in (merged.get("template_except") or []) if isinstance(x, str)}
    if drop:
        for key in ("permissions", "rules"):
            if merged.get(key):
                merged[key] = [v for v in merged[key] if v not in drop]
        caps = dict(merged.get("capabilities") or {})
        for key in ("actions", "utils", "util_tags"):
            if caps.get(key):
                caps[key] = [v for v in caps[key] if v not in drop]
        if caps:
            merged["capabilities"] = caps
    merged.pop("template", None)
    merged.pop("template_except", None)
    return merged


def migrate_template_layer(server) -> bool:
    """Materialize every routine's template contribution into its own file. True on change."""
    changed = False
    for home in (server.routines_home, server.conversations_home, server.background_home):
        if not home or not home.is_dir():
            continue
        for d in sorted(home.iterdir()):
            path = d / "routine.yaml"
            if not d.is_dir() or d.name.startswith(".") or not path.is_file():
                continue
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError) as exc:
                log.warning("template migration: skipping %s (%s)", path, exc)
                continue
            if not isinstance(raw, dict) or not (raw.get("template")
                                                 or raw.get("template_except")):
                continue
            slug = str(raw.get("template") or "")
            tpl = config_for(server.libraries_home, slug) if slug else {}
            if slug and not tpl:
                # The library lost the template. Under the layer this routine was already
                # running on its own config alone, so dropping the dead keys is the whole job.
                log.warning("template migration: %s names unknown template %r — dropping the "
                            "key only; its effective config is unchanged", d.name, slug)
            merged = _materialize(raw, tpl)
            atomic_write(path, yaml.safe_dump(merged, sort_keys=False, allow_unicode=True))
            log.info("template migration: materialized %r into %s", slug, d.name)
            changed = True
    return changed
