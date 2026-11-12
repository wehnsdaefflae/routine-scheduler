"""The post-boot checklist's fifth check: can the fleet's model roles actually fail over?

`conformance.chain_problems` could answer this from resolved config, offline and free, since
R2196 — and its only caller was `rsched validate`, a command somebody has to type. The watcher's
own docstring states what that is worth: *a check that runs only when a human types a command is
a check whose SILENCE MEANS NOTHING.*

What the silence cost, measured 2026-11-12: `deficit-coach:20261111-230002` died with "the model
chain was exhausted after 0 rungs" on a provider connection refusal, and the previous `self-audit`
run died the same way hours earlier. `engine/failover.next_after` searches strictly AFTER the
failed member, so a chain of one dies at its first failure — and 12 of 13 catalog models declare
`fallbacks: []`.

It is a NOTE and never a FAIL, deliberately: `cli_validate` reports these lines without counting
them because whether a single-member or single-domain chain is acceptable is a judgement about
this instance's providers, and a boot check that went red over it would be switched off inside a
week. Making it fatal is the operator's call; what this adds is that the answer reaches the
record at every boot.

A separate module because `tests/test_postdeploy.py` (340 lines) is ten lines from the size
ratchet bar.
"""
from __future__ import annotations

import pytest

from helpers import server_config
from rsched import cli_postdeploy
from rsched.postdeploy import CHECK_NAMES, FAIL, NOTE, PASS, exit_code, model_chain_depth

NAME = CHECK_NAMES[4]


def test_the_check_is_in_the_checklist_under_its_own_name():
    assert NAME == "model-chain-depth"
    assert len(CHECK_NAMES) == 5


def test_a_fleet_that_can_fail_over_passes_and_says_how_many_routines_it_read():
    check = model_chain_depth([], 26, True)
    assert (check.name, check.verdict) == (NAME, PASS)
    assert "26 routine(s)" in check.detail


def test_roles_that_cannot_fail_over_are_a_note_naming_them():
    """The lines come through verbatim: a problem a reader cannot act on is a problem nobody
    fixes, and `chain_problems` already words each one with its evidence."""
    problems = [f"routine-{i}: models.main 'Sonnet' has no reachable fallback" for i in range(5)]

    check = model_chain_depth(problems, 26, True)

    assert check.verdict == NOTE
    assert "5 role(s) across 26 routine(s)" in check.detail
    assert "routine-0: models.main 'Sonnet' has no reachable fallback" in check.detail
    assert "(+2 more)" in check.detail, "three shown, the rest counted — not one line per role"


def test_a_note_never_moves_the_exit_code():
    """Why it is a NOTE at all: the operator decides whether this instance's providers leave
    another domain to reach, and a red boot check over that judgement gets switched off."""
    assert exit_code([model_chain_depth(["a role with no fallback"], 1, True)]) == 0


def test_an_unreadable_catalog_is_reported_as_such_and_never_as_a_clean_bill():
    """The trap this check would otherwise walk into, and it was walked into on 2026-11-12:
    `chain_problems` SKIPS any role whose model is not in the catalog, so an instance whose
    catalog failed to load reports zero problems — identical to a healthy fleet. A sandboxed
    reader got "0 chain problems" out of a config it could not read and nearly filed it as a
    clean result.
    """
    check = model_chain_depth([], 0, False)

    assert check.verdict == NOTE
    assert check.verdict != PASS
    assert "could not be read" in check.detail
    assert "reads exactly like a healthy fleet" in check.detail


# ---- the I/O half: walking this instance's routines ----------------------------------------


def _catalogued(tmp_path, **attrs):
    from rsched.config.modelconf import EndpointConfig, ModelConfig

    eps = {"cliproxy": EndpointConfig(name="cliproxy", kind="openai",
                                      base_url="http://127.0.0.1:8317/v1",
                                      key_var="CLIPROXY_API_KEY"),
           "openrouter": EndpointConfig(name="openrouter", kind="openai",
                                        base_url="https://openrouter.ai/api/v1",
                                        key_var="OPENROUTER_API_KEY")}
    models = {"Lonely": ModelConfig(name="Lonely", endpoint="cliproxy", model="v/lonely",
                                    fallbacks=[]),
              "Paired": ModelConfig(name="Paired", endpoint="cliproxy", model="v/paired",
                                    fallbacks=["Elsewhere"]),
              "Elsewhere": ModelConfig(name="Elsewhere", endpoint="openrouter", model="v/else",
                                       fallbacks=[])}
    home = tmp_path / "routines"
    home.mkdir(exist_ok=True)
    return server_config(models=models, endpoints=eps, routines_home=home, **attrs)


def _routine(server, slug: str, models: dict) -> None:
    import yaml

    d = server.routines_home / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "routine.yaml").write_text(yaml.safe_dump({
        "name": slug, "slug": slug, "description": slug, "enabled": True,
        "schedule": {"cron": "0 3 * * *", "tz": "Europe/Berlin"},
        "workflow": {"library_slug": "general-task"}, "models": models}), encoding="utf-8")


def test_the_walk_names_the_routine_each_problem_belongs_to(tmp_path):
    server = _catalogued(tmp_path)
    _routine(server, "can-fail-over", {"main": "Paired"})
    _routine(server, "one-rung-only", {"main": "Lonely"})

    problems, routines, read = cli_postdeploy._chain_problems(server)

    assert read is True
    assert routines == 2
    assert len(problems) == 1, problems
    assert problems[0].startswith("one-rung-only: "), problems[0]
    assert "no reachable fallback" in problems[0]


def test_an_empty_catalog_says_nothing_was_checked(tmp_path):
    """The same trap at the I/O seam, where it originates: no catalog, no reading — and the
    caller is told so rather than handed an empty list that looks like a pass."""
    server = server_config(models={}, routines_home=tmp_path / "routines")

    assert cli_postdeploy._chain_problems(server) == ([], 0, False)


def test_a_disabled_routine_is_checked_too(tmp_path):
    """A routine enabled tomorrow runs on the chain it has today, so a check that looks only at
    what is scheduled misses exactly the case nobody is watching."""
    import yaml

    server = _catalogued(tmp_path)
    _routine(server, "sleeping", {"main": "Lonely"})
    path = server.routines_home / "sleeping" / "routine.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["enabled"] = False
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    problems, routines, _read = cli_postdeploy._chain_problems(server)

    assert routines == 1
    assert problems and problems[0].startswith("sleeping: ")


def test_an_unparseable_routine_is_left_to_validate_and_breaks_no_boot(tmp_path):
    server = _catalogued(tmp_path)
    _routine(server, "fine", {"main": "Paired"})
    broken = server.routines_home / "broken"
    broken.mkdir()
    (broken / "routine.yaml").write_text(": not yaml :\n  - [", encoding="utf-8")

    problems, routines, read = cli_postdeploy._chain_problems(server)

    assert (problems, routines, read) == ([], 1, True)


@pytest.mark.parametrize(("verdicts", "code"), [([PASS, NOTE], 0), ([PASS, FAIL], 1)])
def test_the_checklists_exit_code_is_unchanged_by_the_new_check(verdicts, code):
    """Adding a fifth check must not change what a clean session means. Only FAIL moves it, and
    this check never returns one."""
    from rsched.postdeploy import Check

    # strict=False on purpose: the point is a PARTIAL checklist (two verdicts against five
    # names), because what is asserted is that only FAIL moves the code, not the length
    pairs = zip(CHECK_NAMES, verdicts, strict=False)
    assert exit_code([Check(n, v, "") for n, v in pairs]) == code
