"""A catalog model carries a "when to use" note, and `list_models` returns it — D279(a).

A run choosing a `model` for `spawn`/`subtask`/`llm`, or a `role_swap` position, sees a catalog
of NAMES. Nothing told it that one model is cheap and blunt, that another is the only one here
with vision, or that a third is rate-limited to uselessness before noon. The operator knows all
three and had nowhere to write them.

What is pinned here: the field exists and survives the settings round trip, `list_models`
returns it beside the name, an UNSET note prints nothing at all (an empty value would read as
"no opinion recorded" where there is simply no field), and — the part that keeps it honest —
nothing in the engine READS it to decide anything. It is advice, not a routing rule.
"""

from __future__ import annotations

from pathlib import Path

from helpers import tmp_server

ROOT = Path(__file__).resolve().parents[1]


def _ctx(tmp_path, make_routine, **models):
    """A run context whose server carries `models` as its catalog."""
    from helpers import run_context

    d = make_routine(slug="chooser")
    server = tmp_server(tmp_path, create=False)
    for name, cfg in models.items():
        server.models[name] = cfg
    return run_context(d, "20260714-070000", server=server)


def test_the_field_exists_and_defaults_to_nothing():
    """Unset is the normal state: the note is advice an operator may not have written yet."""
    from rsched.config.modelconf import ModelConfig

    cfg = ModelConfig(name="m", endpoint="e", model="prov/m")

    assert cfg.note == ""


def test_the_note_survives_the_settings_round_trip(api_client):
    """A field the save path drops is a control that looks like it works. The row the card
    reads back must carry what was sent.
    """
    c, _tmp = api_client

    r = c.post("/api/settings/models", json={
        "name": "scribe", "endpoint": "dummy", "model": "prov/scribe",
        "note": "cheap and blunt — fine for a long gate wait, never for a judgement"})
    assert r.status_code == 200, r.text

    rows = c.get("/api/settings/endpoints").json()
    scribe = next(m for ep in rows["endpoints"] for m in ep.get("models") or []
                  if m["name"] == "scribe")
    assert scribe["note"].startswith("cheap and blunt")


def test_a_blank_note_is_dropped_rather_than_stored_as_empty(api_client):
    """The config file keeps unset fields OUT, so a model with no advice has no key — the
    same rule every other inherit-by-default attribute follows.
    """
    c, _tmp = api_client

    assert c.post("/api/settings/models", json={
        "name": "plain", "endpoint": "dummy", "model": "prov/plain", "note": ""}).status_code == 200

    rows = c.get("/api/settings/endpoints").json()
    plain = next(m for ep in rows["endpoints"] for m in ep.get("models") or []
                 if m["name"] == "plain")
    assert plain["note"] == ""


def test_list_models_returns_the_note_beside_the_name(tmp_path, make_routine):
    """The whole point: the run that is choosing reads WHY, not only what exists."""
    from rsched.config.modelconf import ModelConfig
    from rsched.engine.llmaction import do_list_models

    ctx = _ctx(tmp_path, make_routine,
               m=ModelConfig(name="m", endpoint="dummy", model="prov/m",
                             note="the only one here with vision"))

    out = do_list_models(ctx)

    row = next(r for r in out["models"] if r["name"] == "m")
    assert row["when_to_use"] == "the only one here with vision"


def test_a_model_without_a_note_carries_no_key_at_all(tmp_path, make_routine):
    """Not an empty string: a blank line in a catalog listing reads as "no opinion recorded"
    where there is simply no field, and it costs prompt bytes on every model of every catalog.
    """
    from rsched.config.modelconf import ModelConfig
    from rsched.engine.llmaction import do_list_models

    ctx = _ctx(tmp_path, make_routine,
               m=ModelConfig(name="m", endpoint="dummy", model="prov/m"))

    out = do_list_models(ctx)

    row = next(r for r in out["models"] if r["name"] == "m")
    assert "when_to_use" not in row


def test_nothing_in_the_engine_reads_the_note_to_decide_anything():
    """It is ADVICE. The moment something routes on it, an operator's sentence becomes a
    behaviour nobody tested — so the only readers are the surface that stores it, the surface
    that serves it, and the run that is told it.
    """
    import subprocess

    hits = subprocess.run(
        ["grep", "-rn", r"\.note\b", "src/rsched/"], cwd=ROOT,
        capture_output=True, text=True, check=False).stdout.splitlines()
    readers = {line.split(":", 1)[0] for line in hits if "models[" in line or "mc.note" in line}

    assert readers <= {"src/rsched/engine/llmaction.py",
                       "src/rsched/web/settings/endpoints.py"}, readers
