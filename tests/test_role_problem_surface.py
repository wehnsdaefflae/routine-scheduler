"""A STALE MODEL ROLE is a problem the ROUTINE PAGE says (F643, rsched/trials.roles_problem).

`roles_problem` has answered "which of this routine's stored `models:` names can this instance no
longer serve" since F643, where `tv-show-tracker-seedbox-manager` and `library-sync` each carried
a bare `Sonnet` taken nine weeks earlier: after the catalog rename to `Sonnet medium`/`Sonnet
high` both died at boot — rc=1, no finish, no summary, an `orphaned_run` and nothing to read. But
only `rsched validate`, a command a person types, asked it, while its weaker sibling
`ignored_problem` — a trial merely IGNORED at every fire — already reached this payload. The
lesser failure had a console surface and the total one did not, so it is said here too: `main`
resolves on the first turn of every run.

The two DECISION roles sit in the same `models:` map but resolve against the DECISION catalog
(`config/decisionconf.py`), so they are never judged against the chat one — a page calling a
working decision model a run-killer is a false alarm on the one panel that must be believed.
"""

from __future__ import annotations

import pytest
import yaml

from conftest import authed_client, make_test_server


@pytest.fixture
def web(tmp_path, make_routine):
    make_routine(slug="webr")
    server = make_test_server(tmp_path, models={
        "m": {"endpoint": "dummy", "model": "m"},
        "Sonnet medium": {"endpoint": "dummy", "model": "sonnet"}})
    with authed_client(server) as c:
        yield c, tmp_path / "routines" / "webr"


def _roles(d, models):
    """Hand-edit the routine's `models:` — the write edges check the catalog, so a stale name
    only ever arrives this way: the catalog was renamed under a file nobody edited."""
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    raw["models"] = models
    (d / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")


def test_a_role_the_catalog_cannot_serve_is_a_routine_problem(web):
    c, d = web
    _roles(d, {"main": "Sonnet"})
    problems = c.get("/api/routines/webr").json()["problems"]
    assert any("models.main 'Sonnet'" in p for p in problems), problems
    assert any("not in the model catalog" in p for p in problems), problems


def test_a_role_the_catalog_serves_is_no_problem(web):
    c, d = web
    _roles(d, {"main": "Sonnet medium", "tool_call": "m"})
    assert c.get("/api/routines/webr").json()["problems"] == []


def test_a_decision_role_is_not_judged_against_the_chat_catalog(web):
    c, d = web
    _roles(d, {"main": "Sonnet", "decision": "jev", "decision_media": "vl8b"})
    problems = c.get("/api/routines/webr").json()["problems"]
    assert any("models.main 'Sonnet'" in p for p in problems), problems
    assert not any("decision" in p for p in problems), problems
