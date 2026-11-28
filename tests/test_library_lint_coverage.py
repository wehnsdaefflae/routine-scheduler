"""Every library kind `lint_all` walks is answered for by the served verdict: a kind the
`/api/library` payload omits is a kind every reader of that payload calls clean — `gu rsched
lint` reports exactly the kinds it finds there — so the omission reads as a PASS over documents
that do not lint.
"""
from __future__ import annotations

import yaml

BROKEN = {"title": "Stale", "summary": "a pattern carrying a retired budget name",
          "workflow": "general-task",
          "settings": {"budgets": {"max_turns": 30, "max_subruns": 0}}}


def _pattern(server, slug: str, doc: dict) -> None:
    home = server.libraries_home / "patterns"
    home.mkdir(parents=True, exist_ok=True)
    home.joinpath(f"{slug}.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")


def test_the_served_verdict_names_a_pattern_that_does_not_lint(api_client):
    """The kind is linted by `lint_all` already; what was missing was carrying its verdict out
    to the one payload every reader of the library's health reads.
    """
    client, _tmp = api_client
    _pattern(client.app.state.server, "stale", BROKEN)

    body = client.get("/api/library").json()

    assert "patterns" in body, "the payload must answer for every kind lint_all walks"
    rows = {row["slug"]: row for row in body["patterns"]}
    assert "stale" in rows, "a pattern in the library must appear in the library payload"
    problems = rows["stale"]["problems"]
    assert problems, "a pattern that does not lint must carry its problems"
    assert any("max_subruns" in p for p in problems), problems


def test_the_verdict_discriminates_between_two_patterns(api_client):
    """The other half: the verdict is per document, or every pattern reads the same — compared
    on the budget vocabulary alone, since a hermetic library holds no workflow for either
    pattern's `workflow:` to resolve against.
    """
    client, _tmp = api_client
    server = client.app.state.server
    _pattern(server, "stale", BROKEN)
    _pattern(server, "sound",
             {"title": "Sound", "summary": "a pattern whose budgets are all governed",
              "workflow": "general-task", "settings": {"budgets": {"max_turns": 30}}})

    rows = {row["slug"]: row for row in client.get("/api/library").json()["patterns"]}

    def budget_problems(slug: str) -> list[str]:
        return [p for p in rows[slug]["problems"] if "budget" in p]

    assert budget_problems("stale"), rows["stale"]["problems"]
    assert budget_problems("sound") == [], rows["sound"]["problems"]
