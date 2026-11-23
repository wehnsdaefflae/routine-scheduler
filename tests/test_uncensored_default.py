"""R3680: a routine created through the create path is born with a RESOLVABLE `uncensored`
role, read off this instance (fleet agreement, else the catalog's sole guardrail-free model)
rather than hardcoded, and an explicitly chosen role is never overwritten.
"""

from pathlib import Path

import yaml

from rsched.config import EndpointConfig, ModelConfig, ServerConfig, load_routine
from rsched.endpoints import EndpointRegistry
from rsched.workflows import uncensored_default
from rsched.workflows.scaffold import scaffold

SEED = Path(__file__).resolve().parents[1] / "library-seed"

CATALOG = {
    "Sonnet high": ModelConfig(name="Sonnet high", endpoint="e1", model="claude-sonnet"),
    "GLM 5.3 uncensored": ModelConfig(name="GLM 5.3 uncensored", endpoint="e1",
                                      model="z-ai/glm-5.3-uncensored"),
}


def _server(tmp_path, *, catalog=None):
    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    server.routines_home.mkdir(parents=True)
    server.libraries_home = SEED
    server.endpoints = {"e1": EndpointConfig(name="e1", kind="openai", base_url="http://x")}
    server.models = dict(CATALOG if catalog is None else catalog)
    return server


def _fleet_member(server, slug, models):
    """An existing routine whose routine.yaml carries `models` — what the standard is read off."""
    d = server.routines_home / slug
    d.mkdir(parents=True)
    (d / "routine.yaml").write_text(yaml.safe_dump({"name": slug, "slug": slug,
                                                    "models": models}), encoding="utf-8")


def test_a_new_routine_is_born_with_the_fleet_uncensored_role(tmp_path):
    server = _server(tmp_path)
    for slug in ("alpha", "beta"):
        _fleet_member(server, slug, {"main": "Sonnet high",
                                     "uncensored": "GLM 5.3 uncensored"})
    d = scaffold(server, slug="newborn", name="Newborn", instruction="x",
                 workflow_slug="general-task")
    written = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    assert written["models"]["uncensored"] == "GLM 5.3 uncensored"
    cfg, problems = load_routine(d)
    assert problems == [] and cfg.models["uncensored"] == "GLM 5.3 uncensored"


def test_the_born_role_resolves_to_an_endpoint_and_model(tmp_path):
    """The whole point: the refusal path has a target. `for_uncensored` returned None for a
    routine created with `models: {}`, so `degrade._handle_refusal` could not re-drive the turn.
    """
    server = _server(tmp_path)
    _fleet_member(server, "alpha", {"uncensored": "GLM 5.3 uncensored"})
    d = scaffold(server, slug="newborn", name="Newborn", instruction="x",
                 workflow_slug="general-task")
    cfg, _problems = load_routine(d)
    resolved = EndpointRegistry(server).for_uncensored(cfg.models, cfg.efforts)
    assert resolved is not None
    _endpoint, ref = resolved
    assert ref.model == "z-ai/glm-5.3-uncensored"


def test_a_role_the_caller_chose_is_never_overwritten(tmp_path):
    server = _server(tmp_path)
    _fleet_member(server, "alpha", {"uncensored": "GLM 5.3 uncensored"})
    assert uncensored_default.with_uncensored({"uncensored": "Sonnet high"}, server) == \
        {"uncensored": "Sonnet high"}


def test_a_fleet_name_this_catalog_no_longer_serves_is_not_written(tmp_path):
    """F643's shape: a name taken weeks ago and renamed underneath kills the run at resolution,
    so a stale fleet value is passed over — here leaving the catalog's sole candidate.
    """
    server = _server(tmp_path)
    _fleet_member(server, "alpha", {"uncensored": "Sonnet"})      # pre-rename, not in catalog
    assert uncensored_default.fleet_standard(server) == "GLM 5.3 uncensored"


def test_the_most_common_fleet_value_wins_and_ties_break_alphabetically(tmp_path):
    catalog = {**CATALOG,
               "Zed unfiltered": ModelConfig(name="Zed unfiltered", endpoint="e1",
                                             model="zed-unfiltered")}
    server = _server(tmp_path, catalog=catalog)
    _fleet_member(server, "alpha", {"uncensored": "Zed unfiltered"})
    _fleet_member(server, "beta", {"uncensored": "GLM 5.3 uncensored"})
    _fleet_member(server, "gamma", {"uncensored": "GLM 5.3 uncensored"})
    assert uncensored_default.fleet_standard(server) == "GLM 5.3 uncensored"
    _fleet_member(server, "delta", {"uncensored": "Zed unfiltered"})
    assert uncensored_default.fleet_standard(server) == "GLM 5.3 uncensored"   # 2-2 tie


def test_an_instance_with_no_fleet_takes_the_catalogs_sole_candidate(tmp_path):
    server = _server(tmp_path)
    assert uncensored_default.fleet_standard(server) == "GLM 5.3 uncensored"


def test_nothing_is_written_when_the_instance_names_no_standard(tmp_path):
    """An empty catalog, or several guardrail-free models and no fleet agreement, leaves the
    role unset — exactly the behaviour that shipped before, since picking among them would be
    this code inventing the operator's policy.
    """
    bare = _server(tmp_path, catalog={})
    assert uncensored_default.fleet_standard(bare) == ""
    assert uncensored_default.with_uncensored({"main": "x"}, bare) == {"main": "x"}

    ambiguous = _server(tmp_path / "two", catalog={
        "a-uncensored": ModelConfig(name="a-uncensored", endpoint="e1", model="a"),
        "b-abliterated": ModelConfig(name="b-abliterated", endpoint="e1", model="b"),
    })
    assert uncensored_default.fleet_standard(ambiguous) == ""


def test_an_unreadable_fleet_file_never_stops_a_creation(tmp_path):
    server = _server(tmp_path)
    broken = server.routines_home / "broken"
    broken.mkdir()
    (broken / "routine.yaml").write_text("models: [not, a, mapping\n", encoding="utf-8")
    _fleet_member(server, "alpha", {"uncensored": "GLM 5.3 uncensored"})
    assert uncensored_default.fleet_standard(server) == "GLM 5.3 uncensored"
