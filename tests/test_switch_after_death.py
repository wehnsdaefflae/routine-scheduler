"""F837 — a model switch written while a run is TERMINAL lands on its resumed leg's first turn.

The web half (`POST /runs/{id}/model` no longer 409s a finished/failed/aborted run, and says the
switch is armed for the resume) is pinned in `tests/test_api.py`; this file pins the ENGINE half
the API's reply promises, which needed no new code: `switches._fresh` edge-triggers on
`control.json` against the `control-applied.json` ledger the resumed leg re-seeds from, and
`loop._turn_boundary` applies the switch BEFORE the leg's first completion. Without this pin the
console could say "takes effect at this run's resume" over a mechanism that quietly stopped
honouring it.
"""

from __future__ import annotations

import json

from conftest import ScriptedRegistry, finish, write_file
from helpers import server_for
from rsched.config import ModelConfig
from rsched.engine.runtime import run_routine
from rsched.paths import atomic_write_json

TS = "20261105-010000"


def _injections(run_dir):
    rows = [json.loads(line) for line in (run_dir / "transcript.jsonl").read_text().splitlines()]
    return [r["payload"].get("text", "") for r in rows if r["type"] == "user_injection"]


def test_a_switch_armed_on_a_dead_run_serves_the_resumed_legs_first_turn(make_routine, scripted,
                                                                         monkeypatch):
    # every catalog name resolves to the one scripted endpoint, so the model each COMPLETION
    # asked for is read where a turn resolves its chain (`turntakeover`), not from boot-time
    # resolutions (CAPABILITIES, the prompt floor) that run before any turn boundary.
    #
    # One of those boot-time readers now resolves the MAIN CHAIN too (0.535.0,
    # `engine/schemaindex.wire_enforces`: whether every chain member enforces the action schema
    # on the wire decides whether the prompt carries the field index or the whole dump, and it
    # must be known before the prompt is composed — the prompt is a caching contract and cannot
    # be rewritten at the first turn). It is cached on the run context, so it is ONE extra
    # resolution per leg and it happens before the switch is applied, which is why the leg's
    # record opens with the pre-switch value. The assertion below therefore reads the
    # COMPLETIONS — the tail — rather than every resolution, which is what this test's subject
    # has always been.
    asked: list[str | None] = []
    chain = ScriptedRegistry.for_model_chain
    monkeypatch.setattr(ScriptedRegistry, "for_model_chain", lambda self, kind, models, efforts: (
        asked.append((models or {}).get(kind)), chain(self, kind, models, efforts))[1])
    d = make_routine(slug="deadswitch")
    server = server_for(d)
    server.models = {"big": ModelConfig(name="big", endpoint="scripted", model="big-id")}
    scripted([write_file("state/a.txt"), finish("failed", "died on its model")])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "failed"
    assert asked and "big" not in asked

    # what the console writes on a terminal run (api_run_control.switch_model)
    atomic_write_json(run_dir / "control.json",
                      {"switch_model": {"main": "big", "ts": "2026-11-05T01:05:00"}})
    asked.clear()
    scripted([write_file("state/b.txt"), finish("ok", "resumed on the new model")])
    status, _ = run_routine(d, server, run_ts=TS, resume_from=TS)
    assert status == "ok"
    # from the leg's FIRST completion on — the boot-time schema-surface resolution (see above)
    # precedes the switch, so the completions are the tail and every one of them is the new model
    assert asked[-2:] == ["big", "big"], asked
    assert "big" not in asked[:-2], asked    # nothing after the switch still asks for the old
    assert any("model switched mid-run: main → big" in t for t in _injections(run_dir))
    applied = json.loads((run_dir / "control-applied.json").read_text())
    assert applied["switch_model"] == "2026-11-05T01:05:00"
