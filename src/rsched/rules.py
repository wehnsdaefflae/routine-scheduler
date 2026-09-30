"""The general RULES a routine practises: the held set and when each one applies.

A rule lives in exactly ONE place — `<libraries_home>/rules/<slug>.md`. A routine holds
SLUGS (routine.yaml `rules:`), never copies, so a library revision reaches every holder at
once and a run reads the prose on demand (`read_rule`) instead of carrying its own drifted
fork. That is the whole point of the layer: a rule is general, the run applies it to its
particular case, and the rules-review meta routine improves the shared text from what runs
actually did with it.

The two halves are owned separately. The SET is config: no run writes routine.yaml, so
binding and unbinding is the user's, and this module is the web layer's arm for it. The PROSE
is the library's, editable on the Library tab and — for a routine holding the rule-authoring
capability — with the `write_rule` action, under its own approval level (grants.rule_confirm)
because a revision lands on every holder. The prompt names each held rule beside the one line
saying WHEN it applies (`when_lines`), read from the rule itself at every boot, so a routine's
picture of its rules is never a copy that drifted.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from . import library_docs
from .ids import is_slug
from .paths import atomic_write_yaml, read_yaml

CONFIG_FILE = "routine.yaml"


def current_rules(routine_dir: Path) -> list[str]:
    """The slugs this routine practises — routine.yaml's `rules:` list IS the state.

    Lenient on purpose: a hand-broken yaml reads as "no rules" rather than crashing the
    routine page or a run boot. The strict parse belongs to config.load_routine.
    """
    try:
        raw = read_yaml(routine_dir / CONFIG_FILE, {})
    except (OSError, yaml.YAMLError):
        return []
    held = raw.get("rules") if isinstance(raw, dict) else None
    return [str(s) for s in held] if isinstance(held, list) else []


def _write_rules(routine_dir: Path, slugs: list[str]) -> None:
    """Persist the held set into routine.yaml, leaving every other key untouched."""
    path = routine_dir / CONFIG_FILE
    raw = read_yaml(path, {})
    raw["rules"] = slugs
    atomic_write_yaml(path, raw)


def summaries(rules_home: Path, slugs: list[str]) -> dict[str, str]:
    """{slug: summary} for the held rules, in the held order. A slug the library no longer
    carries keeps its de-slugged name — the index still names what the routine expects, and
    the run's `read_rule` reports the miss with the available set.
    """
    known = {d["slug"]: d["summary"] for d in library_docs.list_docs(rules_home)}
    return {slug: known.get(slug) or slug.replace("-", " ") for slug in slugs}


def when_lines(rules_home: Path, slugs: list[str]) -> list[str]:
    """`<slug> — <when it applies>` per held rule, in the held order — what the prompt shows
    so a run knows which rule to read before which moment. A rule the library no longer carries
    says so; `read_rule` names what is there.
    """
    docs = {d["slug"]: d for d in library_docs.list_docs(rules_home)}
    out = []
    for slug in slugs:
        doc = docs.get(slug)
        when = ((doc.get("effect") or {}).get("when") or doc.get("summary") or "") if doc \
            else "not in the library any more"
        out.append(f"{slug} — {when}")
    return out


def apply_changes(rules_home: Path, routine_dir: Path, add: list[str],
                  remove: list[str]) -> tuple[list[str], list[str]]:
    """One picker submission: add then remove, config written once. Returns
    (added, removed) — the slugs that actually changed, so the caller can report honestly
    and skip the git commit when nothing did. An add naming no library rule raises KeyError,
    which the caller turns into a 400.
    """
    held = current_rules(routine_dir)
    known = set(library_docs.slugs(rules_home))
    added = []
    for slug in add:
        if not is_slug(slug) or slug not in known:
            raise KeyError(slug)
        if slug not in held:
            held.append(slug)
            added.append(slug)
    removed = [slug for slug in remove if slug in held]
    held = [slug for slug in held if slug not in set(removed)]
    if added or removed:
        _write_rules(routine_dir, held)
    return added, removed
