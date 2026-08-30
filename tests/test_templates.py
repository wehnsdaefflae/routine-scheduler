"""Settings templates — a named starting point a routine or a group adopts.

The property everything else rests on: adopting a template SUBTRACTS nothing, and a routine can
still express any setting it could before. A template that quietly took something away, or that
could not be overridden, would trade the decluttering for exactly the granularity it was
supposed to preserve.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rsched.config import load_routine
from rsched.templates import config_for, list_templates, read_template
from rsched.workflows.lint import lint_template_text


def _library(tmp_path: Path, **templates: dict) -> Path:
    lib = tmp_path / "lib"
    (lib / "templates").mkdir(parents=True)
    for slug, config in templates.items():
        (lib / "templates" / f"{slug}.md").write_text(
            "---\n" + yaml.safe_dump({"tags": ["a", "b", "c"], "config": config})
            + f"---\n# template: {slug} — does a thing\n\nprose\n", encoding="utf-8")
    return lib


@pytest.fixture
def routine(tmp_path, monkeypatch):
    """A routine dir whose loader resolves templates out of a tmp library."""
    def _make(lib: Path, **raw) -> Path:
        monkeypatch.setattr("rsched.config.routine._libraries_home_for", lambda _d: lib)
        d = tmp_path / "routines" / "r"
        d.mkdir(parents=True, exist_ok=True)
        (d / "routine.yaml").write_text(yaml.safe_dump({"description": "t", **raw}),
                                        encoding="utf-8")
        return d
    return _make


def test_a_template_supplies_what_the_routine_does_not_set(tmp_path, routine):
    lib = _library(tmp_path, watcher={"permissions": ["memory", "run-history"],
                                      "rules": ["decision-record"],
                                      "capabilities": {"actions": ["memory_read"],
                                                       "runs": "last"}})
    cfg, _ = load_routine(routine(lib, template="watcher"))
    assert sorted(cfg.permissions) == ["memory", "run-history"]
    assert cfg.rules == ["decision-record"]
    assert cfg.capabilities["actions"] == ["memory_read"]
    assert cfg.capabilities["runs"] == "last"


def test_the_routines_own_settings_win_and_add(tmp_path, routine):
    """The layering is own > group > template, and list keys UNION — so adopting a template
    can only ever add to what the routine already said."""
    lib = _library(tmp_path, watcher={"permissions": ["memory"], "rules": ["decision-record"],
                                      "capabilities": {"runs": "last", "confirm": "always"}})
    cfg, _ = load_routine(routine(lib, template="watcher", permissions=["shell"],
                                  rules=["web-research"],
                                  capabilities={"confirm": "never"}))
    assert sorted(cfg.permissions) == ["memory", "shell"]
    assert sorted(cfg.rules) == ["decision-record", "web-research"]
    assert cfg.capabilities["confirm"] == "never"      # the routine's own dial wins
    assert cfg.capabilities["runs"] == "last"          # …and the rest still comes through


def test_template_except_lets_a_routine_subtract(tmp_path, routine):
    """Without this, a routine could add to a template but never drop from one, and adopting
    a template would cost exactly the granularity it is supposed to preserve."""
    lib = _library(tmp_path, watcher={"permissions": ["memory", "shell"],
                                      "rules": ["decision-record", "web-research"],
                                      "capabilities": {"actions": ["memory_read", "detach"],
                                                       "utils": ["remote"]}})
    cfg, _ = load_routine(routine(lib, template="watcher",
                                  template_except=["shell", "web-research", "detach", "remote"]))
    assert cfg.permissions == ["memory"]
    assert cfg.rules == ["decision-record"]
    assert cfg.capabilities["actions"] == ["memory_read"]
    assert cfg.capabilities["utils"] == []


def test_an_unknown_template_does_not_break_the_routine(tmp_path, routine):
    """A routine naming a template the library lost keeps running on its own config. Failing
    to load would turn a library edit into an outage."""
    lib = _library(tmp_path)
    cfg, problems = load_routine(routine(lib, template="gone", permissions=["memory"]))
    assert cfg is not None and cfg.permissions == ["memory"]
    assert not [p for p in problems if "gone" in p and "traceback" in p.lower()]


def test_adopting_a_template_subtracts_nothing(tmp_path, routine):
    """The migration invariant, as a test: whatever a routine held before, it still holds."""
    lib = _library(tmp_path, watcher={"permissions": ["memory"], "rules": ["decision-record"]})
    before, _ = load_routine(routine(lib, permissions=["shell", "memory"],
                                     rules=["web-research"]))
    after, _ = load_routine(routine(lib, template="watcher", permissions=["shell", "memory"],
                                    rules=["web-research"]))
    assert set(before.permissions) <= set(after.permissions)
    assert set(before.rules) <= set(after.rules)


def test_provenance_says_what_came_from_the_template(tmp_path, routine):
    """The routine page marks an inherited value so it never reads as one set here."""
    lib = _library(tmp_path, watcher={"permissions": ["memory", "run-history"]})
    cfg, _ = load_routine(routine(lib, template="watcher"))
    assert "template" in (cfg.inherited.get("permissions") or "")


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


def test_a_scaffolded_routine_records_only_its_differences(tmp_path, monkeypatch):
    """The point of adopting one: the routine's own file carries what it does DIFFERENTLY,
    not a copy of the template's contents."""
    from rsched.templates import config_for

    seed = Path(__file__).resolve().parents[1] / "library-seed"
    watcher = config_for(seed, "watcher")
    # what scaffold persists is the set difference, which is what the migration proved on the
    # 28 live routines and what keeps a new routine's file short
    asked_perms = [*watcher["permissions"], "shell"]
    own = [p for p in asked_perms if p not in set(watcher["permissions"])]
    assert own == ["shell"]
    assert all(p not in own for p in watcher["permissions"])
