"""Which settings pattern a routine follows when no pick can be made (decided 2026-11-05).

`patterns/recommend.choose` used to fall back to whichever pattern sorted first — an alphabetical
accident presented as a recommendation. The neutral `one-job` pattern is the fallback now.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rsched.patterns import recommend


def _pattern(slug: str, workflow: str) -> dict:
    return {"slug": slug, "workflow": workflow, "summary": slug, "when": ""}


@pytest.fixture
def library(monkeypatch):
    def install(patterns: list[dict], pick: object) -> None:
        monkeypatch.setattr(recommend.store, "list_all", lambda _home: patterns)
        monkeypatch.setattr("rsched.workflows.suggest._ask_json",
                            lambda *_a, **_k: (pick, ""))
    return install


SERVER = SimpleNamespace(libraries_home="/nonexistent")
ALL = [_pattern("code-maintainer", "maintain"), _pattern("daily-operator", "operate"),
       _pattern("one-job", "one-job")]


def test_an_unusable_pick_falls_back_to_the_neutral_pattern(library):
    library(ALL, {"pattern": "not-a-pattern"})
    assert recommend.choose(SERVER, workflow="unmatched", name="n", task="t") == "one-job"


def test_no_model_answer_falls_back_to_the_neutral_pattern(library):
    library(ALL, None)
    assert recommend.choose(SERVER, workflow="unmatched", name="n", task="t") == "one-job"


def test_a_valid_pick_still_wins(library):
    library(ALL, {"pattern": "daily-operator"})
    assert recommend.choose(SERVER, workflow="unmatched", name="n", task="t") == "daily-operator"


def test_the_one_pattern_on_the_workflow_needs_no_pick(library):
    library(ALL, None)
    assert recommend.choose(SERVER, workflow="maintain", name="n", task="t") == "code-maintainer"
