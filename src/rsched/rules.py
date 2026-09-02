"""The general RULES a routine practises: the held set, and main.md's derived index.

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
because a revision lands on every holder. `main.md`'s `## Standing practices` tail is a derived
index rebuilt from the config on every change (`sync_practices_tail`), so bind and unbind need
no special-casing and a hand-edited tail converges back.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from . import library_docs
from .ids import is_slug
from .paths import atomic_write, atomic_write_yaml, read_yaml

CONFIG_FILE = "routine.yaml"

PRACTICES_HEADING = "## Standing practices"
TAIL_LEAD = ("These general rules bind this routine. Each states a principle, not a "
             "procedure — read one with read_rule before the situation it governs and apply "
             "it to the case in front of you:")


def render_practices_tail(rule_lines: list[str]) -> str:
    """The Standing-practices section body — heading, lead, one line per rule. The ONE
    place the tail's shape lives: scaffold/conversation creation appends it, rules.py's
    post-change resync rebuilds it (two drifted copies of the lead once disagreed).
    """
    return "\n".join([PRACTICES_HEADING, "", TAIL_LEAD, *rule_lines])


def rule_line(slug: str, summary: str) -> str:
    return f"- `{slug}` — {summary or slug.replace('-', ' ')}"


def with_practices_tail(main_body: str, rule_summaries: dict[str, str]) -> str:
    """Guarantee main.md ends with a Standing practices section naming every held rule —
    the generator is asked to write one, but the reference must survive a forgetful LLM (and
    the no-LLM fallback).
    """
    if not rule_summaries:
        return main_body
    if PRACTICES_HEADING.lower() in main_body.lower():
        return main_body
    lines = [rule_line(slug, summary) for slug, summary in rule_summaries.items()]
    return main_body.rstrip() + "\n\n" + render_practices_tail(lines) + "\n"


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


def sync_practices_tail(routine_dir: Path, rules_home: Path) -> None:
    """Rewrite main.md's Standing practices tail to match routine.yaml's `rules:`.

    Everything from the heading to the end of the file is the derived index, so it is
    replaced wholesale; a routine holding no rules loses the section entirely.
    """
    main = routine_dir / "main.md"
    if not main.is_file():
        return
    body = main.read_text(encoding="utf-8")
    head = body
    for line in body.splitlines():
        if line.strip().lower() == PRACTICES_HEADING.lower():
            head = body[:body.index(line)]
            break
    held = summaries(rules_home, current_rules(routine_dir))
    if not held:
        atomic_write(main, head.rstrip() + "\n")
        return
    lines = [rule_line(slug, summary) for slug, summary in held.items()]
    atomic_write(main, head.rstrip() + "\n\n" + render_practices_tail(lines) + "\n")


def apply_changes(rules_home: Path, routine_dir: Path, add: list[str],
                  remove: list[str]) -> tuple[list[str], list[str]]:
    """One picker submission: add then remove, config and tail written once. Returns
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
        sync_practices_tail(routine_dir, rules_home)
    return added, removed
