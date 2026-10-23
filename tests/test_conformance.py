"""Is THIS INSTANCE's config flyable? (R2196 — rsched/conformance.py)

The suite proves the code correct against fixtures and never asked whether the config this
instance runs on can fly. The property that killed eight routines at turn 0 on 2026-09-14 —
`Astra high`'s only fallback was `Opus high`, both on the `cliproxy` endpoint, so one expired
credential took the whole chain out — is checkable with NO network call at all, and nothing
checked it.

So these tests build that exact shape out of fixture config and assert the check names it. They
are hermetic: no live config is read anywhere here (`tests/production_guard.py` forbids it), and
the production reading happens only in `rsched validate`, which is where the live config is
already read.
"""

from helpers import server_config
from rsched.conformance import chain_problems, system_model_problems


def _server(models: dict, endpoints: dict, **attrs):
    """A ServerConfig whose catalog is exactly what the test declares.

    `models` maps catalog NAME → (endpoint name, fallback names); `endpoints` maps endpoint
    name → (base_url, key_var). Those two pairs are all `conformance` reads.
    """
    from rsched.config.modelconf import EndpointConfig, ModelConfig

    eps = {name: EndpointConfig(name=name, kind="openai", base_url=url, key_var=key)
           for name, (url, key) in endpoints.items()}
    mcs = {name: ModelConfig(name=name, endpoint=ep, model=f"vendor/{name}", fallbacks=list(fbs))
           for name, (ep, fbs) in models.items()}
    return server_config(models=mcs, endpoints=eps, **attrs)


# ---- the 2026-09-14 shape: a chain that cannot fail over


def test_a_chain_whose_every_member_shares_one_credential_is_named():
    """The exact incident. Two models, two names, one endpoint — so the chain reads like a
    failover and is not one. 0.344.0 fixed chain DEPTH; nothing ever checked DOMAIN.
    """
    server = _server(
        models={"Astra high": ("cliproxy", ["Opus high"]), "Opus high": ("cliproxy", [])},
        endpoints={"cliproxy": ("http://127.0.0.1:8317/v1", "CLIPROXY_API_KEY")})

    problems = chain_problems(server, {"main": "Astra high"})

    assert len(problems) == 1, problems
    line = problems[0]
    assert "ONE failure domain" in line
    assert "Astra high" in line and "Opus high" in line     # the chain is named, not just counted
    assert "CLIPROXY_API_KEY" in line                       # and so is the thing that expires
    assert "2026-09-14" in line                             # with the evidence a reader can check


def test_a_chain_across_two_providers_is_clean():
    server = _server(
        models={"Astra high": ("cliproxy", ["GLM"]), "GLM": ("openrouter", [])},
        endpoints={"cliproxy": ("http://127.0.0.1:8317/v1", "CLIPROXY_API_KEY"),
                   "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY")})

    assert chain_problems(server, {"main": "Astra high"}) == []


def test_the_same_host_with_two_keys_is_still_one_domain():
    """A failure domain is the PAIR (base_url, key_var): the host going down takes both out,
    whatever the keys are. Checking only the key would call this chain safe.
    """
    server = _server(
        models={"A": ("ep_a", ["B"]), "B": ("ep_b", [])},
        endpoints={"ep_a": ("http://one.host/v1", "KEY_A"),
                   "ep_b": ("http://one.host/v1", "KEY_B")})

    assert "ONE failure domain" in chain_problems(server, {"main": "A"})[0]


def test_two_hosts_sharing_one_key_var_is_also_one_domain():
    """And the other half of the pair: one credential expiring is the 2026-09-14 cause, so two
    base_urls reading the same env var are one domain too.
    """
    server = _server(
        models={"A": ("ep_a", ["B"]), "B": ("ep_b", [])},
        endpoints={"ep_a": ("http://one.host/v1", "SHARED_KEY"),
                   "ep_b": ("http://two.host/v1", "SHARED_KEY")})

    assert "ONE failure domain" in chain_problems(server, {"main": "A"})[0]


# ---- a chain of one: a different finding with a different fix


def test_a_role_with_no_fallback_is_named_as_that_and_not_as_a_shared_domain():
    """Reported separately on purpose: "add a fallback" and "move one member elsewhere" are
    different fixes, and R1492 is why the first one matters — a classifier refusal advances the
    chain, so a model with nowhere to go turns a refusal into a dead run.
    """
    server = _server(models={"Solo": ("cliproxy", [])},
                     endpoints={"cliproxy": ("http://127.0.0.1:8317/v1", "CLIPROXY_API_KEY")})

    problems = chain_problems(server, {"main": "Solo"})

    assert len(problems) == 1
    assert "no reachable fallback" in problems[0]
    assert "ONE failure domain" not in problems[0]
    assert "R1492" in problems[0]


def test_the_transitive_walk_is_what_resolve_chain_does():
    """A fallback's OWN fallbacks are reachable (0.344.0 made resolution transitive: "the
    reachable set finally matches what a reader of config.yaml would already assume"). A check
    that walked only the primary's list would call this chain single-domain and be wrong.
    """
    server = _server(
        models={"A": ("ep_a", ["B"]), "B": ("ep_a", ["C"]), "C": ("ep_c", [])},
        endpoints={"ep_a": ("http://a/v1", "KEY_A"), "ep_c": ("http://c/v1", "KEY_C")})

    assert chain_problems(server, {"main": "A"}) == []


def test_a_cycle_terminates_and_an_unknown_fallback_is_skipped():
    """`resolve_chain` skips self-references, duplicates and unresolvable names rather than
    breaking the primary's resolution; this walk must behave identically or it reports a
    phantom.
    """
    server = _server(
        models={"A": ("ep_a", ["B", "ghost"]), "B": ("ep_a", ["A"])},
        endpoints={"ep_a": ("http://a/v1", "KEY_A")})

    problems = chain_problems(server, {"main": "A"})
    assert len(problems) == 1
    assert "A, B" in problems[0]            # the cycle contributed each member once
    assert "ghost" not in problems[0]       # the unresolvable name is roles_problem's line


# ---- what this check does NOT claim


def test_a_role_naming_an_uncatalogued_model_is_left_to_roles_problem():
    """One finding per defect: `trials.roles_problem` already says "not in the model catalog",
    and saying it twice in one `validate` run teaches nobody anything.
    """
    server = _server(models={"A": ("ep_a", [])},
                     endpoints={"ep_a": ("http://a/v1", "KEY_A")})

    assert chain_problems(server, {"main": "Sonnet"}) == []
    assert chain_problems(server, None) == []
    assert chain_problems(server, {}) == []


def test_every_role_is_checked_not_just_main():
    """`uncensored` resolves only when a refusal is referred — rarer than turn one, so it stays
    broken longer unnoticed. Same reasoning `roles_problem` gives for including it.
    """
    server = _server(
        models={"Solo": ("ep_a", []), "Other": ("ep_a", [])},
        endpoints={"ep_a": ("http://a/v1", "KEY_A")})

    problems = chain_problems(server, {"main": "Solo", "uncensored": "Other"})

    assert len(problems) == 2
    assert any("models.uncensored" in p for p in problems)


# ---- the instance's own model, which belongs to no routine


def test_the_system_model_is_checked_as_the_instance_and_not_as_a_routine():
    """Every role a routine leaves unset runs on `system_model`, so its chain is a FLEET
    condition that no per-routine surface can see.
    """
    server = _server(
        models={"Sys": ("ep_a", ["Also"]), "Also": ("ep_a", [])},
        endpoints={"ep_a": ("http://a/v1", "KEY_A")},
        system_model="Sys")

    problems = system_model_problems(server)

    assert len(problems) == 1
    assert problems[0].startswith("system_model ")
    assert "models.system_model" not in problems[0]     # it is not one of a routine's roles


def test_an_unresolvable_system_model_says_every_routine_dies_on_it():
    server = _server(models={"A": ("ep_a", [])},
                     endpoints={"ep_a": ("http://a/v1", "KEY_A")}, system_model="Ghost")

    problems = system_model_problems(server)
    assert len(problems) == 1
    assert "not in the model catalog" in problems[0]


def test_no_system_model_configured_is_not_a_problem():
    server = _server(models={}, endpoints={}, system_model="")
    assert system_model_problems(server) == []


def test_the_system_model_check_survives_a_minimal_server_stand_in():
    """`rsched validate`'s own tests hand `_instance_problems` a stand-in carrying only the
    fields that test needs (tests/test_lanes.py gives it `routines_home` alone). A hard
    attribute read there failed the whole command for a question it was not asked — the same
    shape as the four-attribute server the fresh-container boot test hands `cmd_daemon`.
    """
    from types import SimpleNamespace

    assert system_model_problems(SimpleNamespace(routines_home="/nowhere")) == []


# ---- reported, never fatal


def test_a_single_domain_chain_prints_a_note_and_never_changes_the_exit_code(
        tmp_path, monkeypatch, capsys):
    """Whether a single-domain chain is acceptable is a judgement about THIS instance's
    providers — a one-provider instance has no other domain to reach. So it prints beside the
    setup and recipe notes and leaves the exit code alone; making it fatal is the operator's
    call. A check that turned that judgement into a failed command would be switched off
    inside a week (the reasoning `cmd_validate` already gives for the recipe notes).

    The exit code is compared WITH and WITHOUT the shared domain over the same routine, so the
    assertion is about this check's contribution and not about whatever else that routine's
    validation says.
    """
    from rsched import cli_validate
    from rsched.config.modelconf import EndpointConfig, ModelConfig

    home = tmp_path / "routines"
    (home / "solo").mkdir(parents=True)
    (home / "solo" / "routine.yaml").write_text(
        "name: Solo\nenabled: false\nmodels:\n  main: A\n", encoding="utf-8")

    def _run(second_base_url: str) -> tuple[int, str]:
        server = server_config(
            routines_home=home,
            models={"A": ModelConfig(name="A", endpoint="ep", model="v/a", fallbacks=["B"]),
                    "B": ModelConfig(name="B", endpoint="ep2", model="v/b")},
            endpoints={"ep": EndpointConfig(name="ep", kind="openai",
                                            base_url="http://one/v1", key_var="K"),
                       "ep2": EndpointConfig(name="ep2", kind="openai",
                                             base_url=second_base_url, key_var="K2")})
        monkeypatch.setattr(cli_validate, "load_server_config", lambda: (server, []))
        rc = cli_validate.cmd_validate(_Args())
        return rc, capsys.readouterr().out

    shared_rc, shared_out = _run("http://one/v1")      # both members behind one host
    spread_rc, spread_out = _run("http://two/v1")      # two hosts, two credentials

    assert "NOTE  failover ·" in shared_out
    assert "ONE failure domain" in shared_out
    assert "failover" not in spread_out
    assert shared_rc == spread_rc, (
        "the failover note changed `rsched validate`'s exit code — it must be reported, "
        "never counted")


class _Args:
    """`cmd_validate` reads exactly one attribute off its args; argparse is not worth building."""

    routine = None
