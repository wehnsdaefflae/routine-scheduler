"""The ONE tag rule: a model's `tags` STRING is split, never iterated per character.

The operator saved a playbook on 2026-11-19 and its tags read `e v a l u a` in the Library
tab. The cause: the system model answered `"tags": "evaluation…"` where PLAYBOOK_SCHEMA says
array, and both consumers of a model-supplied tag list did `for t in (data.get("tags") or [])`
— which iterates a string's CHARACTERS. `playbook_distill` capped the result at 6 and
`conversations.autolabel` at 3, so the artefact is a row of single letters either way.

`ids.tag_list` is the one place that reduces a tag value to the tag alphabet (beside
`ids.slugify`, the ONE slug rule), and the playbook lint grew the detector that would have
caught the live row: nothing did, so `rsched lint --kind playbooks` reported pass on it.
"""

from __future__ import annotations

from types import SimpleNamespace

import yaml

from conftest import seeded_server
from rsched import conversations as conv_mod
from rsched import ids
from rsched.endpoints.base import Completion

#: The front matter of the operator's own saved playbook, verbatim from
#: `<library>/playbooks/threshold-driven-text-edit-loop/MAIN.md` as the defect wrote it.
BROKEN_PLAYBOOK = """---
slug: threshold-driven-text-edit-loop
title: Raise one document's evaluator score past a threshold
when: When a single document must cross a numeric scoring threshold.
tags:
- e
- v
- a
- l
- u
- a
axis: 'Fixed: the loop. Varies: the document.'
updated: '2026-11-19'
---

## Instructions

1. Measure the baseline once.
"""


def test_a_tags_string_is_split_not_iterated() -> None:
    """The defect itself, at the shared rule: a string is ONE tag (or the tags it names),
    never one tag per character."""
    assert ids.tag_list("evaluation") == ["evaluation"]
    assert ids.tag_list("evaluation, text-editing  scoring") == [
        "evaluation", "text-editing", "scoring"]
    # a real list keeps working exactly as before: lowercased, punctuation to the alphabet
    assert ids.tag_list(["Garden", "WEEKLY!"]) == ["garden", "weekly"]
    assert ids.tag_list(["data-cleaning"]) == ["data-cleaning"]
    # empties and non-lists reduce to nothing rather than raising
    assert ids.tag_list(None) == [] and ids.tag_list([]) == [] and ids.tag_list("   ") == []
    assert ids.tag_list(["", "-", "!!"]) == []
    assert ids.tag_list(["x", "x"]) == ["x"]                      # a tag set, not a bag
    assert ids.tag_list(["a", "b", "c"], limit=2) == ["a", "b"]   # the caller's cap


def test_a_distilled_playbook_keeps_a_string_tag_whole() -> None:
    """`playbook_distill._normalize` on the exact reply shape that produced the live row."""
    from rsched.playbook_distill import _normalize

    out = _normalize({"slug": "threshold-driven-text-edit-loop", "title": "Raise a score",
                      "when": "when a doc must cross a threshold", "axis": "the document",
                      "tags": "evaluation", "main": "## Instructions\n1. measure\n"})
    assert out["tags"] == ["evaluation"]
    # and a well-formed list is untouched, still capped at 6
    many = _normalize({"slug": "s", "title": "t", "when": "w", "axis": "a",
                       "tags": [f"t{n}" for n in range(9)], "main": "## Instructions\n1. x\n"})
    assert many["tags"] == [f"t{n}" for n in range(6)]


def test_autolabel_keeps_a_string_tag_whole(tmp_path, monkeypatch) -> None:
    """The second site: a conversation's own model answering a string must not tag it `a i`."""
    server = seeded_server(tmp_path)
    d = conv_mod.create_conversation(server, slug="c-tagstr", first_message="score a document")

    class FakeEndpoint:
        def complete(self, *a, **k):
            return Completion(parsed={"title": "Score a document", "tags": "evaluation"}, text="")

    class FakeRegistry:
        def __init__(self, _server): ...
        def for_model(self, kind, models, efforts):
            return FakeEndpoint(), SimpleNamespace(model="m", effort=None, temperature=None,
                                                   max_tokens=None)

    monkeypatch.setattr("rsched.endpoints.EndpointRegistry", FakeRegistry)
    conv_mod.autolabel(server, d, "score a document")
    assert yaml.safe_load((d / "routine.yaml").read_text())["tags"] == ["evaluation"]


def test_the_playbook_lint_catches_the_per_character_artefact() -> None:
    """Why the live row survived: the lint asked for at least ONE non-empty tag, and six
    single letters satisfied that. `rsched lint --kind playbooks` reported pass on it.
    """
    from rsched.workflows.lint import lint_playbook_text

    problems = lint_playbook_text(BROKEN_PLAYBOOK, filename="threshold/MAIN.md")
    assert [p for p in problems if "'e'" in p], problems
    # the repaired front matter lints clean, so the PUT repair route can land it
    repaired = BROKEN_PLAYBOOK.replace("- e\n- v\n- a\n- l\n- u\n- a\n",
                                       "- evaluation\n- text-editing\n- scoring\n")
    assert lint_playbook_text(repaired, filename="threshold/MAIN.md") == []
