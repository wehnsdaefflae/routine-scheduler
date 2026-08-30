"""Settings templates — a named starting point a routine COPIES IN, once.

A template is a PRESELECTION, not a layer (operator decision 2026-08-30, reversing 0.262.0).
Adopting one writes its values into the routine's own `routine.yaml` and the link is gone: the
file then says what the routine IS, every value is editable in the panel that owns it, and
removing one is removing it. The property everything rests on is unchanged — adopting SUBTRACTS
nothing — but it is now a property of one WRITE rather than of a merge repeated on every load.

Nothing resolves a template at config load any more, which is why there is no test here for a
routine's `template:` key: there is no such key.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from rsched.templates import adopt_into, config_for, list_templates, read_template
from rsched.workflows.lint import lint_template_text


def _library(tmp_path: Path, **templates: dict) -> Path:
    lib = tmp_path / "lib"
    (lib / "templates").mkdir(parents=True)
    for slug, config in templates.items():
        (lib / "templates" / f"{slug}.md").write_text(
            "---\n" + yaml.safe_dump({"tags": ["a", "b", "c"], "config": config})
            + f"---\n# template: {slug} — does a thing\n\nprose\n", encoding="utf-8")
    return lib


def test_adopting_copies_what_the_routine_does_not_have(tmp_path):
    lib = _library(tmp_path, watcher={"permissions": ["memory", "run-history"],
                                      "rules": ["decision-record"],
                                      "capabilities": {"actions": ["memory_read"],
                                                       "runs": "last"}})
    merged, added = adopt_into({"description": "t"}, config_for(lib, "watcher"))
    assert sorted(merged["permissions"]) == ["memory", "run-history"]
    assert merged["rules"] == ["decision-record"]
    assert merged["capabilities"]["actions"] == ["memory_read"]
    assert merged["capabilities"]["runs"] == "last"
    # what the write contributed is REPORTED — an adoption that silently changed nine things
    # is the layer's illegibility in a different costume
    assert added and all(a.split()[0].isdigit() for a in added)


def test_the_routines_own_settings_win_and_the_lists_union(tmp_path):
    """Adopting can only ever ADD: the routine's own values survive, the template's join them."""
    lib = _library(tmp_path, watcher={"permissions": ["memory"], "rules": ["decision-record"],
                                      "capabilities": {"runs": "last", "confirm": "always"}})
    merged, _ = adopt_into({"permissions": ["shell"], "capabilities": {"confirm": "never"}},
                           config_for(lib, "watcher"))
    assert sorted(merged["permissions"]) == ["memory", "shell"]
    assert merged["rules"] == ["decision-record"]
    assert merged["capabilities"]["confirm"] == "never"      # the routine's own dial wins
    assert merged["capabilities"]["runs"] == "last"          # the template fills what it left


def test_adopting_twice_is_harmless(tmp_path):
    """The write is a union that never overwrites, so a second press changes nothing — and
    adopting a DIFFERENT template afterwards adds to the first rather than replacing it."""
    lib = _library(tmp_path, a={"permissions": ["memory"]}, b={"permissions": ["shell"]})
    once, _ = adopt_into({"description": "t"}, config_for(lib, "a"))
    twice, added = adopt_into(dict(once), config_for(lib, "a"))
    assert twice["permissions"] == once["permissions"] and added == []
    both, _ = adopt_into(dict(once), config_for(lib, "b"))
    assert sorted(both["permissions"]) == ["memory", "shell"]


def test_a_template_never_pre_answers_a_grant(tmp_path):
    """A grant is a settled DECISION a person made about one routine. A template carrying one
    would be a template exposing a secret, so `grants` is the one shared key adoption drops."""
    lib = _library(tmp_path, w={"permissions": ["memory"], "grants": {"secret:FOO": True}})
    merged, _ = adopt_into({"description": "t"}, config_for(lib, "w"))
    assert "grants" not in merged


def test_an_unknown_template_copies_nothing(tmp_path):
    lib = _library(tmp_path, watcher={"permissions": ["memory"]})
    assert config_for(lib, "nope") == {}
    merged, added = adopt_into({"description": "t"}, config_for(lib, "nope"))
    assert added == [] and "permissions" not in merged


def test_lint_rejects_a_template_that_carries_nothing_or_the_wrong_keys(tmp_path):
    good = ("---\n" + yaml.safe_dump({"tags": ["a", "b", "c"],
                                      "config": {"permissions": ["memory"]}})
            + "---\n# template: x — does a thing\n\nprose\n")
    assert lint_template_text(good, filename="x.md") == []

    empty = good.replace("config:\n  permissions:\n  - memory\n", "config: {}\n")
    assert any("non-empty config" in p for p in lint_template_text(empty, filename="x.md"))

    stray = good.replace("permissions:\n  - memory", "schedule:\n    cron: '* * * * *'")
    assert any("not a shareable key" in p for p in lint_template_text(stray, filename="x.md"))

    nested = good.replace("permissions:\n  - memory", "template: other")
    assert any("cannot name another template" in p
               for p in lint_template_text(nested, filename="x.md"))

    untagged = good.replace("- a\n- b\n- c\n", "- a\n")
    assert any("3 tags" in p for p in lint_template_text(untagged, filename="x.md"))


def test_the_shipped_templates_are_well_formed_and_cover_the_jobs(tmp_path):
    """The set was inferred from what the live routines hold, so it has to actually span
    them: a floor, plus the five jobs the 28 routines fall into."""
    seed = Path(__file__).resolve().parents[1] / "library-seed"
    slugs = {t["slug"] for t in list_templates(seed)}
    assert slugs == {"basic", "watcher", "correspondent", "steward", "operator", "maintainer"}
    for slug in sorted(slugs):
        rec = read_template(seed, slug)
        assert rec["summary"], slug
        assert len(rec["tags"]) >= 3, slug
        assert rec["config"].get("permissions") and rec["config"].get("rules"), slug
        raw = (seed / "templates" / f"{slug}.md").read_text(encoding="utf-8")
        assert lint_template_text(raw, filename=f"{slug}.md") == [], slug
    # the two capabilities whose blast radius is the instance itself are NOT template defaults
    assert "recipe-authoring" not in config_for(seed, "maintainer")["permissions"]
    assert "shell" not in config_for(seed, "steward")["permissions"]


# --- creation: a new routine adopts a template instead of inlining the whole surface -------

def test_suggest_fits_a_permission_set_to_the_closest_template():
    """A deterministic fit, not a judgement — creation already decided what the routine asked
    for. An LLM guess here would write a wrong DEFAULT into a config file, which is worse than
    a slightly-narrow one the user widens on the page."""
    from rsched.templates import suggest

    seed = Path(__file__).resolve().parents[1] / "library-seed"
    base = ["global-utils", "memory", "util-authoring"]
    assert suggest(seed, base) == "basic"
    assert suggest(seed, [*base, "run-history", "workflow-generation"]) == "watcher"
    assert suggest(seed, [*base, "outbound-mail", "scripts"]) == "correspondent"
    assert suggest(seed, [*base, "shell", "remote-machines", "scripts"]) == "operator"
    assert suggest(seed, [*base, "rule-authoring", "util-removal", "shell"]) == "maintainer"
    # steward and correspondent hold the same permissions on purpose (the shell is not a
    # publishing tool), so the RULES are what tell them apart
    publishing = [*base, "outbound-mail", "scripts", "run-history", "background-tasks"]
    assert suggest(seed, publishing) == "correspondent"
    assert suggest(seed, publishing, ["status-page", "interface-design"]) == "steward"
    assert suggest(seed, []) == "basic"          # nothing fitting falls to the floor


def test_a_scaffolded_routine_carries_the_template_in_full(tmp_path):
    """The reversal, at creation: the new routine's own file holds the WHOLE set, not the set
    difference. Reading `routine.yaml` has to tell you what the routine can do — under the layer
    it told you only what it did differently from a template resolved somewhere else."""
    seed = Path(__file__).resolve().parents[1] / "library-seed"
    watcher = config_for(seed, "watcher")
    merged, _ = adopt_into({"permissions": ["shell"]}, watcher)
    for p in watcher["permissions"]:
        assert p in merged["permissions"], f"{p} must be written into the routine's own file"
    assert "shell" in merged["permissions"]
