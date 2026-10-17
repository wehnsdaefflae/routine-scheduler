"""Creation collects the OUTCOME contract (engine/outcome_words.py): `create_routine` creates or
queues nothing without `produces`, writes it into the new routine.yaml as declared, and the
recipe it builds names the measures in `## Done when` — so a routine is born answering "what is
this for, counted how", the question freelance-radar was never asked."""

import json
import os

import yaml

from helpers import PRODUCES
from rsched.config.outcome import OutcomeConfig
from rsched.engine import create_routine, donewhen, outcome_words
from rsched.engine.create_routine import DRAFT_RELPATH
from rsched.readmodels.surface_outcome import names_a_measure
from rsched.workflows import pipeline
from test_create_routine import ACTION, _ctx, _server


def _confirmed(ctx, action: dict) -> dict:
    """Draft, then confirm from a later leg (a reply runs as its own process)."""
    create_routine.handle_create_routine(ctx, action)
    path = ctx.routine.dir / DRAFT_RELPATH
    if path.is_file():
        draft = json.loads(path.read_text(encoding="utf-8"))
        draft["pid"] = os.getpid() + 1
        path.write_text(json.dumps(draft), encoding="utf-8")
    return create_routine.handle_create_routine(ctx, action)


def test_the_words_become_the_contract():
    got = outcome_words.contract(["principal: Mark",
                                  "interviews · count · higher — interviews booked",
                                  "weight_kg | level | lower -- his weight this morning"])
    assert isinstance(got, OutcomeConfig) and got.principal == "Mark"
    assert [(m.id, m.kind, m.better) for m in got.measures] == [
        ("interviews", "count", "higher"), ("weight_kg", "level", "lower")]
    assert got.measures[0].label == "interviews booked"


def test_a_contract_that_is_not_one_is_named_never_guessed():
    assert "no outcome" in outcome_words.contract([])
    assert "neither form" in outcome_words.contract(["principal: M", "lots of interviews"])
    assert "principal" in outcome_words.contract(["x · count · higher — x"])
    assert "repeat" in outcome_words.contract(["principal: M", "x · count · higher — a",
                                               "x · level · lower — b"])
    assert "two `principal:`" in outcome_words.contract(["principal: M", "principal: N"])


def test_the_draft_asks_for_it_and_the_confirm_refuses_without_it(tmp_path):
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    bare = {k: v for k, v in ACTION.items() if k != "produces"}
    draft = create_routine.handle_create_routine(ctx, bare)
    assert draft["draft"] and "`produces`" in draft["next"]
    obs = _confirmed(ctx, bare)
    assert obs["rejected"] and "outcome contract is not settled" in obs["reason"]
    assert not (server.routines_home / ACTION["target"]).exists()


def test_a_malformed_contract_is_refused_at_the_draft(tmp_path):
    server = _server(tmp_path)
    ctx = _ctx(server, home="conversations_home")
    obs = create_routine.handle_create_routine(ctx, {**ACTION, "produces": ["lots of papers"]})
    assert obs["rejected"] and "neither form" in obs["reason"]
    assert not (ctx.routine.dir / DRAFT_RELPATH).exists()


def test_a_scheduled_run_queues_nothing_without_it(tmp_path):
    from rsched import pending

    server = _server(tmp_path)
    bare = {k: v for k, v in ACTION.items() if k != "produces"}
    obs = create_routine.handle_create_routine(_ctx(server, home="routines_home"), bare)
    assert obs["rejected"] and not obs.get("queued")
    assert pending.load_all(server.routines_home) == []


def test_the_created_routine_declares_it_and_its_recipe_names_it(tmp_path):
    from rsched.config import load_routine
    from rsched.readmodels.surface_outcome import _outcome_nodes

    server = _server(tmp_path)
    obs = _confirmed(_ctx(server, home="conversations_home"), dict(ACTION))
    assert obs.get("created")
    made = server.routines_home / ACTION["target"]
    raw = yaml.safe_load((made / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["outcome"] == outcome_words.contract(PRODUCES).model_dump()
    assert any(names_a_measure(line["text"], ["replies"]) for line in donewhen.read(made))
    # born declared and named; what it still owes is the script its first run writes
    cfg, _ = load_routine(made)
    rows = _outcome_nodes(server, cfg)
    assert [r.get("state") for r in rows] == ["unmeasured"]


def test_the_generator_is_told_the_measures_and_the_line_to_write():
    cfg = outcome_words.contract(PRODUCES)
    assert isinstance(cfg, OutcomeConfig)
    prompt = pipeline._outcome_prompt(cfg)
    assert "`replies` (count, higher is better)" in prompt
    assert outcome_words.done_when_line(cfg) in prompt and "scripts/outcome.py" in prompt
    assert pipeline._outcome_prompt(None) == ""


def test_a_hand_made_proposal_without_it_is_not_materialized(tmp_path):
    from conftest import authed_client, seeded_server
    from rsched import pending

    server = seeded_server(tmp_path)
    rec = pending.queue(server.routines_home, kind="create_routine", routine="scout",
                        run_id="scout:20261017-000000", summary="routine 'nox'",
                        fields={"slug": "nox", "name": "Nox", "instruction": "p",
                                "workflow": "general-task"})
    with authed_client(server) as client:
        r = client.post(f"/api/pending-creations/{rec['id']}/materialize")
    assert r.status_code == 422 and "outcome contract" in r.text
    assert not (server.routines_home / "nox").exists()


def test_a_recipe_whose_done_when_names_no_measure_is_regenerated(monkeypatch):
    """The generator is held to the line like the format it is told: a main whose Done-when
    names no measure is refused and asked again — and the second answer is the one kept."""
    from types import SimpleNamespace

    from rsched.engine import donewhen as dw

    cfg = outcome_words.contract(PRODUCES)
    outline = {"stages": [{"name": "work", "scope": "do it", "inputs": "-", "outputs": "-"}]}
    plain = "## Run flow\n\n1. **work** — `stages/work.md`\n\n## Done when\n\n- d1 — it ran\n"
    named = plain + f"- d2 — {outcome_words.done_when_line(cfg)}\n"
    answers = iter([outline, {"main": plain}, {"main": named},
                    {"body": "Read the inbox.\nWrite the record."}])
    endpoint = SimpleNamespace(complete=lambda *_a, **_k: SimpleNamespace(
        parsed=next(answers), text=""))
    ref = SimpleNamespace(model="m", effort="", temperature=None, max_tokens=0)
    monkeypatch.setattr(pipeline, "DECOMPOSE_ATTEMPTS", 2)
    out = pipeline._pipeline(lambda: (endpoint, ref), "raw", "task", pins=[], done_when=[],
                             never=[], slug="general-task", outcome=cfg)
    assert any(names_a_measure(line["text"], ["replies"]) for line in dw.parse(out["main"]))
