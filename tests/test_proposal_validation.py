"""Every WRITER of a proposed setting validates it — not only the recommender.

F610, from the operator's audit: "Proposed settings carry invalid vocabulary and fail only when
the operator clicks accept — three different fields in one day." `patterns/recommend.py`
validated what the system model proposes; the other call sites of the settings writer
(`patterns/drafts.write` — the page's "follow this pattern", the pattern-diff half of
"Recommend for this routine") did not, so a value the accept route refuses was STORED as a
pending change and 400d on the click — and the accept is whole-draft, so it took every other
change the person kept down with it. The pattern STORE was blind the same way: `store.problems`
checks a pattern's field NAMES and its folder grants, never its VALUES, so a pattern carrying
invalid vocabulary was storable and then offered to every routine that followed it.

The check belongs to the WRITER, not to each caller: a call site added later is validated by
construction instead of by whoever remembers. These tests pin both halves — the refusal (with
the offending key NAMED, per the project's teaching-refusal rule), and the over-strictness that
a refusal invites: every pattern this repo SHIPS must still be followable, which rules out
judging `triggers` and `finish_line` through the routine patch model that does not own them.
"""
import json
from pathlib import Path

import pytest
import yaml

from rsched.patterns import drafts, store, validate

SEED = Path(__file__).resolve().parents[1] / "library-seed"


def pattern_doc(**settings):
    return {"title": "Watcher", "summary": "watches things", "workflow": "watch-and-digest",
            "settings": settings}


#: Values the accept route refuses, one per field the operator actually hit. `budgets`
#: `max_minutes` is the F735 case one seam further on (a budget this repo has never had);
#: `connections: {"fau-mail": ...}` names a util where an OAuth provider belongs — the
#: ewsan-bohrdaten-steward case that cost that routine all nine of its proposed settings.
REFUSED = [
    ({"budgets": {"max_turns": 60, "max_minutes": 900}}, "max_minutes"),
    ({"connections": {"fau-mail": {"scopes": ["read"]}}}, "connections"),
    ({"efforts": {"main": "turbo"}}, "efforts"),
    ({"keep_runs": "lots"}, "keep_runs"),
    ({"no_such_setting": True}, "no_such_setting"),
]


@pytest.mark.parametrize(("settings", "named"), REFUSED)
def test_the_settings_writer_refuses_a_value_the_accept_would_refuse(tmp_path, settings, named):
    """The defect at the writer itself: every one of these was stored without a word and
    refused on the operator's click."""
    home = tmp_path / "routines"
    changes = {k: {"value": v, "reason": "x"} for k, v in settings.items()}
    with pytest.raises(ValueError) as exc:
        drafts.write(home, "alpha", changes=changes, pattern=None, message="check",
                     source="test")
    assert named in str(exc.value), str(exc.value)
    assert drafts.read(home, "alpha") is None      # and nothing was stored


def test_the_refusal_teaches_the_vocabulary_it_wants(tmp_path):
    """A refusal that names no way forward is a defect in this project: the message carries the
    offending key AND what was allowed instead."""
    with pytest.raises(ValueError) as exc:
        drafts.write(tmp_path / "routines", "alpha",
                     changes={"budgets": {"value": {"max_minutes": 900}, "reason": "x"}},
                     pattern=None, message="check", source="test")
    assert "max_minutes" in str(exc.value) and "max_wall_clock_min" in str(exc.value)


@pytest.mark.parametrize(("settings", "named"), REFUSED)
def test_the_pattern_writer_refuses_invalid_vocabulary(tmp_path, settings, named):
    """The creation-time seam: a pattern's settings become a new routine's values and its
    pending changes, so a pattern storable with invalid vocabulary is a proposal defect on
    every routine made from it. `store.problems` judged names only."""
    with pytest.raises(ValueError) as exc:
        store.create(tmp_path / "library", "watcher", pattern_doc(**settings))
    assert named in str(exc.value), str(exc.value)
    assert store.read(tmp_path / "library", "watcher") is None


def test_following_a_pattern_that_carries_invalid_vocabulary_is_refused_at_the_click(
        api_client, make_routine):
    """The "follow this pattern" seam (`web/api_settings.propose_pattern`): it copied a
    pattern's values into the draft unvalidated, so the refusal arrived on the ACCEPT — the one
    click where it costs the person every other change in the draft. The pattern is written
    straight to the library file here, which is what a pattern stored before `store.create`
    validated (or hand-edited since) looks like.
    """
    c, tmp = api_client
    make_routine("alpha")
    lib = store.home(tmp / "library")
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "watcher.yaml").write_text(yaml.safe_dump({
        "title": "Watcher", "summary": "watches things", "workflow": "watch-and-report",
        "settings": {"connections": {"fau-mail": {"scopes": ["read"]}}, "keep_runs": 12},
    }), encoding="utf-8")
    r = c.post("/api/routines/alpha/settings/follow", json={"pattern": "watcher"})
    assert r.status_code == 422, r.text
    assert "connections" in r.text
    assert c.get("/api/routines/alpha/settings").json()["draft"] is None


def test_every_shipped_pattern_can_still_be_proposed_whole(tmp_path):
    """The over-strictness half, and the reason the writer cannot just judge every field by the
    routine patch model: `triggers` and `finish_line` are not fields of it at all (the accept
    routes them to their own owners), so judging them by it would refuse all 14 shipped
    patterns — a guard that blocks the operator is worse than the defect it closes.
    """
    home = tmp_path / "routines"
    shipped = store.list_all(SEED)
    assert len(shipped) == 14
    for p in shipped:
        changes = {k: {"value": v, "reason": "the value this pattern carries"}
                   for k, v in p["settings"].items()}
        drafts.write(home, p["slug"], changes=changes, pattern=p["slug"],
                     message="check", source="test")
        assert drafts.read(home, p["slug"]) is not None, p["slug"]


@pytest.mark.parametrize(("value", "ok"), [
    ([], True),
    ([{"type": "report", "cooldown_s": 900, "max_fires_per_day": 24}], True),
    ([{"type": "webook"}], False),                               # the typo a person makes
    ([{"type": "report", "cooldown_s": "soon"}], False),
    ([{"type": "webhook", "secret": "x"}], False),               # not a trigger's key
    ([{"type": "report"}, {"type": "report"}], False),           # one inbox, one watcher
])
def test_a_trigger_list_is_judged_by_its_own_owner_not_the_patch_model(value, ok):
    """`triggers` has no field on `RoutinePatch` — the accept routes it to
    `api_hooks.reconciled_triggers` — so the patch model refuses every list, `[]` included.
    Judged by the trigger vocabulary instead, which is what makes the shipped patterns (every
    one of which carries `triggers: []`) proposable."""
    assert validate.valid("triggers", value) is ok
    if not ok:
        assert "trigger" in validate.problem("triggers", value)


def test_a_draft_written_before_the_check_does_not_break_the_page(tmp_path):
    """`prune` runs on every settings-page load and rewrites what is left. A document stored
    before the writer validated can hold a value the accept refuses: it is DROPPED there rather
    than raised, because a page that 500s cannot even show the person the draft to discard."""
    home = tmp_path / "routines"
    path = home / drafts.DIR / "alpha.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "created": "t", "source": "legacy", "message": "check", "pattern": None, "reason": "",
        "changes": {"keep_runs": {"value": 12, "reason": "x"},
                    "efforts": {"value": {"main": "turbo"}, "reason": "x"}}}),
        encoding="utf-8")
    left = drafts.prune(home, "alpha", {"keep_runs": 30, "efforts": {}}, assigned="")
    assert left is not None and set(left["changes"]) == {"keep_runs"}
