"""The INSTANCE config surface of `config_patch` — `{"server": {...}}` and its apply route.

The operator's decision, 2026-10-19: *"build the server surface + D191's apply route: a
decision's config_patch may name {"server": {...}} with its own declared field list, applied
autonomously only for fields on that list, everything else still a click"*. This file proves the
first half — the surface and the route that applies it. Until it existed, a run holding his
explicit approval for an instance-wide change had no mechanism at all to land it: every
`config_patch` was judged against the two routine-shaped vocabularies, so a server key was
refused at filing, and two approved changes sat 10 and 3 days in config-optimizer's ledger
(R2702).

Both of those changes appear below as the concrete cases, because a surface that cannot land the
two patches it was built for is not built.
"""

from __future__ import annotations

import yaml

from conftest import finish
from helpers import server_for, transcript_events
from rsched.engine.runtime import run_routine
from rsched.paths import read_json

TS = "20261019-150000"


def _cfg(tmp_path) -> dict:
    return yaml.safe_load((tmp_path / "config.yaml").read_text())


# ---------------------------------------------------------------- the filing check (engine)

def test_a_server_patch_is_filed_with_its_surface_intact(make_routine, scripted):
    """The engine accepts `{"server": {...}}` and files it whole. `server` is NOT a routing key
    like `routine`: it names the surface AND carries the body, so it must survive onto the
    record for the apply to find it."""
    d = make_routine(slug="cfgopt")
    patch = {"server": {"decision_model": "jev", "decision_media_model": ""}}
    scripted([
        {"say": "propose the instance change", "kind": "ask_user", "mode": "deferred",
         "question": "Switch the default decision model to jev?", "config_patch": patch},
        finish(),
    ])
    status, _ = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"
    recs = [read_json(p) for p in (d / "questions" / "pending").glob("*.json")]
    assert len(recs) == 1 and recs[0]["config_patch"] == patch
    # the instance is nobody's routine, so there is no target slug
    assert "config_target" not in recs[0]


def test_a_server_key_outside_the_allow_list_is_refused_at_filing(make_routine, scripted):
    """The refusal arrives on the turn that asked, naming the vocabulary — never on the
    operator's click. A patch of `token` would be a decision card wearing a button that 422s."""
    d = make_routine(slug="cfgopt2")
    scripted([
        {"say": "propose a sealed field", "kind": "ask_user", "mode": "deferred",
         "question": "Change the API token?", "config_patch": {"server": {"token": "sk-new"}}},
        finish(),
    ])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"
    assert not list((d / "questions" / "pending").glob("*.json"))
    obs = next(e for e in transcript_events(run_dir)
               if e["type"] == "observation" and e["payload"].get("error"))
    err = obs["payload"]["error"]
    assert "'token'" in err and "decision_model" in err      # refused, and the vocabulary named


def test_a_patch_mixing_the_server_surface_with_routine_fields_is_refused(make_routine,
                                                                         scripted):
    """One patch, one apply route. A mixed body would need two PATCHes, and the half that
    landed would settle the decision as handled while the other half silently did not."""
    d = make_routine(slug="cfgopt3")
    scripted([
        {"say": "propose both at once", "kind": "ask_user", "mode": "deferred",
         "question": "Both?",
         "config_patch": {"server": {"sandbox": "strict"}, "budgets": {"max_turns": 10}}},
        finish(),
    ])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"
    assert not list((d / "questions" / "pending").glob("*.json"))
    obs = next(e for e in transcript_events(run_dir)
               if e["type"] == "observation" and e["payload"].get("error"))
    assert "two decisions" in obs["payload"]["error"]


def test_an_empty_server_patch_is_refused(make_routine, scripted):
    """`{"server": {}}` reads as a change and applies nothing — the quietest way to report a
    config change that never happened."""
    d = make_routine(slug="cfgopt4")
    scripted([
        {"say": "propose nothing", "kind": "ask_user", "mode": "deferred",
         "question": "Change it?", "config_patch": {"server": {}}},
        finish(),
    ])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"
    assert not list((d / "questions" / "pending").glob("*.json"))
    obs = next(e for e in transcript_events(run_dir)
               if e["type"] == "observation" and e["payload"].get("error"))
    assert "names no field" in obs["payload"]["error"]


# ------------------------------------------------------------------- the apply route (web)

def test_the_apply_route_lands_r2702s_stuck_decision_defaults(api_client):
    """R2702's stuck change 2, verbatim: `decision_model: predator-vl8b` → `jev` and
    `decision_media_model` CLEARED. His words after reversing himself twice: *"Ah well. Okay,
    that's really cheap. Changed my mind again. Do it then."* It waited 3 days for a door."""
    c, tmp_path = api_client
    assert c.post("/api/settings/decision-endpoints", json={
        "name": "or-jev", "protocol": "systemone", "base_url": "https://openrouter.ai/api/v1",
        "key_var": "OPENROUTER_API_KEY"}).json()["ok"]
    assert c.post("/api/settings/decision-models", json={
        "name": "jev", "endpoint": "or-jev", "model": "typesafe/jev-1.13"}).json()["ok"]
    assert c.put("/api/settings/decision-defaults", json={
        "decision_model": "jev", "decision_media_model": ""}).json()["ok"]

    r = c.patch("/api/settings/server", json={"decision_model": "jev",
                                              "decision_media_model": ""})
    assert r.status_code == 200, r.text
    # `updated` is what the Decisions page's honesty gate compares its sent keys against, so it
    # must name every applied field — an under-report tells the operator it did not land
    assert r.json()["updated"] == ["decision_media_model", "decision_model"]
    cfg = _cfg(tmp_path)
    assert cfg["decision_model"] == "jev" and cfg["decision_media_model"] == ""


def test_the_apply_route_changes_only_what_the_patch_names(api_client):
    """PATCH semantics are the point: a decision names one or two fields, and everything else
    keeps its value. The settings panel's PUT sends the whole knob block; this does not."""
    c, tmp_path = api_client
    assert c.put("/api/settings/server", json={
        "sandbox": "strict", "max_concurrent_runs": 3, "registry_rescan_s": 45}).json()["ok"]

    assert c.patch("/api/settings/server", json={"registry_rescan_s": 90}).json()["updated"] \
        == ["registry_rescan_s"]
    cfg = _cfg(tmp_path)
    assert cfg["registry_rescan_s"] == 90
    assert cfg["sandbox"] == "strict" and cfg["max_concurrent_runs"] == 3


def test_the_live_server_object_mirrors_the_patch(api_client):
    """The daemon holds ONE long-lived ServerConfig that every request and scheduler tick
    reads; a write that is not mirrored back is invisible until a restart — so the operator
    would see "applied" and the running instance would keep the old value."""
    c, _ = api_client
    assert c.patch("/api/settings/server", json={"sandbox": "off"}).json()["ok"]
    assert c.get("/api/settings/server").json()["sandbox"] == "off"


def test_the_apply_route_refuses_a_field_outside_the_allow_list(api_client):
    """THE boundary claim (R2702's caution, from the routine that once held a capability it had
    merely inherited): the allow-list is enforced in the APPLY ROUTE, not in the calling run's
    restraint. A caller reaching this route directly — the Decisions button today, an
    autonomous apply next — meets the same gate."""
    c, tmp_path = api_client
    before = _cfg(tmp_path)
    # the VALUES are irrelevant — the claim is that the field is refused whatever it carries
    for sealed in ({"token": "sk-new"}, {"bind": "127.0.0.9"},
                   {"routines_home": "elsewhere"}, {"models": {"x": {}}}):
        r = c.patch("/api/settings/server", json=sealed)
        assert r.status_code == 422, (sealed, r.status_code, r.text)
        assert next(iter(sealed)) in r.text          # the refusal names the field
    assert _cfg(tmp_path) == before                  # and nothing was written


def test_the_apply_route_refuses_a_value_the_loader_would_reject(api_client):
    """ALL OR NOTHING, judged before anything is written: a config.yaml a restart cannot load
    would leave the instance with a decision recorded as applied and a value not in effect."""
    c, tmp_path = api_client
    before = _cfg(tmp_path)
    for bad in ({"sandbox": "paranoid"}, {"max_concurrent_runs": 0},
                {"system_model": "not-in-the-catalog"},
                {"decision_model": "not-a-decision-model"},
                {"browser_view_url": "host:6080"}):
        r = c.patch("/api/settings/server", json=bad)
        assert r.status_code == 400, (bad, r.status_code, r.text)
    assert _cfg(tmp_path) == before


def test_a_blank_pointer_clears_it_rather_than_being_refused(api_client):
    """Clearing is a real intent — his F7 answer was `decision_media_model` → blank — and `""`
    is what every one of these fields means by "unset". A catalog check that refused blanks
    would make the stuck change unappliable in the one direction he asked for."""
    c, tmp_path = api_client
    assert c.patch("/api/settings/server", json={"compaction_model": ""}).json()["ok"]
    assert _cfg(tmp_path)["compaction_model"] == ""


def test_the_url_fields_must_be_absolute_addresses(api_client):
    """They are dialled by a server-side relay, so a bare host is a connection error at use
    time rather than a refusal at save time. A trailing slash is normalized, not refused."""
    c, tmp_path = api_client
    assert c.patch("/api/settings/server",
                   json={"browser_view_url": "http://box:6080/vnc.html/"}).json()["ok"]
    assert _cfg(tmp_path)["browser_view_url"] == "http://box:6080/vnc.html"


def test_max_concurrent_runs_says_it_needs_a_restart(api_client):
    """It sizes the run semaphore at daemon startup, so saving it is not the same as it taking
    effect — the response says so, as the PUT route does."""
    c, _ = api_client
    assert c.patch("/api/settings/server",
                   json={"max_concurrent_runs": 4}).json()["restart_for"] \
        == ["max_concurrent_runs"]
