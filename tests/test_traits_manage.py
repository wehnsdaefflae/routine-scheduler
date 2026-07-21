"""Post-creation trait management: the traits/ dir as state, the derived Standing-practices
tail, the read-only `read_trait` consult, and the mid-run control.json hand-off.

The invariant under test throughout: a RUN never changes its own trait set — it may only
consult an unheld module for the current run — while the USER may add or remove one at any
time, including during a live run.
"""

from types import SimpleNamespace

import pytest

from rsched import traits as traits_mod
from rsched.engine.actions import KINDS, validate_action
from rsched.engine.executor import do_read_trait
from rsched.engine.observations import format_observation
from rsched.grants import GATED_KINDS
from rsched.workflows.scaffold import PRACTICES_HEADING

TRAIT_A = """---
tags: [a, b, c]
---
# trait: alpha — the first practice

- **Do the thing.** Carefully.
"""

TRAIT_B = """---
tags: [a, b, c]
---
# trait: beta — the second practice

- **Do the other thing.** Also carefully.
"""


@pytest.fixture
def lib(tmp_path):
    home = tmp_path / "library" / "traits"
    home.mkdir(parents=True)
    (home / "alpha.md").write_text(TRAIT_A, encoding="utf-8")
    (home / "beta.md").write_text(TRAIT_B, encoding="utf-8")
    return home


@pytest.fixture
def routine(tmp_path):
    d = tmp_path / "routines" / "demo"
    (d / "traits").mkdir(parents=True)
    (d / "main.md").write_text("# Run flow\n\nDo the work.\n", encoding="utf-8")
    return d


def test_add_copies_verbatim_and_builds_the_tail(lib, routine):
    body = traits_mod.add_trait(lib, routine, "alpha")
    written = (routine / "traits" / "alpha.md").read_text(encoding="utf-8")
    # verbatim = the library BODY (frontmatter stripped), not an adapted rewrite
    assert "# trait: alpha — the first practice" in written
    assert "tags:" not in written
    assert body.strip() == written.strip()
    main = (routine / "main.md").read_text(encoding="utf-8")
    assert PRACTICES_HEADING in main
    assert "- `traits/alpha.md` — the first practice" in main


def test_tail_is_derived_so_remove_prunes_it(lib, routine):
    traits_mod.add_trait(lib, routine, "alpha")
    traits_mod.add_trait(lib, routine, "beta")
    assert traits_mod.current_traits(routine) == ["alpha", "beta"]
    assert traits_mod.remove_trait(routine, "alpha") is True
    main = (routine / "main.md").read_text(encoding="utf-8")
    assert "traits/alpha.md" not in main
    assert "traits/beta.md" in main
    assert "Do the work." in main          # the body above the tail is never touched
    # last one out removes the section entirely rather than leaving an empty heading
    traits_mod.remove_trait(routine, "beta")
    assert PRACTICES_HEADING not in (routine / "main.md").read_text(encoding="utf-8")


def test_tail_rebuild_is_idempotent_and_converges(lib, routine):
    traits_mod.add_trait(lib, routine, "alpha")
    first = (routine / "main.md").read_text(encoding="utf-8")
    traits_mod.sync_practices_tail(routine)
    traits_mod.sync_practices_tail(routine)
    assert (routine / "main.md").read_text(encoding="utf-8") == first


def test_apply_changes_reports_only_real_mutations(lib, routine):
    added, removed = traits_mod.apply_changes(lib, routine, ["alpha"], [])
    assert (added, removed) == (["alpha"], [])
    # re-adding a held module and removing an absent one are both no-ops, not errors
    added, removed = traits_mod.apply_changes(lib, routine, ["alpha"], ["beta"])
    assert (added, removed) == ([], [])


def test_unknown_slug_raises(lib, routine):
    with pytest.raises(KeyError):
        traits_mod.add_trait(lib, routine, "nope")
    with pytest.raises(KeyError):
        traits_mod.add_trait(lib, routine, "../escape")


def _ctx(lib, routine):
    return SimpleNamespace(server=SimpleNamespace(traits_home=lib),
                           routine=SimpleNamespace(dir=routine))


def test_read_trait_returns_prose_without_writing_anything(lib, routine):
    obs = do_read_trait({"kind": "read_trait", "name": "alpha"}, _ctx(lib, routine))
    assert "the first practice" in obs["content"]
    assert obs["held"] is False
    # the CONSULT must not become a standing practice — that is the user's call alone
    assert traits_mod.current_traits(routine) == []
    assert PRACTICES_HEADING not in (routine / "main.md").read_text(encoding="utf-8")
    rendered = format_observation(obs)
    assert "not added to your recipe" in rendered


def test_read_trait_flags_modules_already_held(lib, routine):
    traits_mod.add_trait(lib, routine, "alpha")
    obs = do_read_trait({"kind": "read_trait", "name": "alpha"}, _ctx(lib, routine))
    assert obs["held"] is True
    assert "ALREADY one of your standing practices" in format_observation(obs)


def test_read_trait_list_and_missing(lib, routine):
    traits_mod.add_trait(lib, routine, "beta")
    obs = do_read_trait({"kind": "read_trait", "name": "list"}, _ctx(lib, routine))
    assert {t["slug"]: t["held"] for t in obs["traits"]} == {"alpha": False, "beta": True}
    assert "already yours" in format_observation(obs)
    missing = do_read_trait({"kind": "read_trait", "name": "ghost"}, _ctx(lib, routine))
    assert missing["missing"] is True
    assert "alpha" in format_observation(missing)


def test_user_added_trait_reaches_a_live_run_once(make_routine, tmp_path, lib):
    """The durable copy alone cannot reach a run in flight — its prompt was composed at boot
    and is immutable — so the web layer signals control.json and the engine appends the prose
    at the next turn boundary, exactly once per signal.
    """
    from rsched.config import ServerConfig, load_routine
    from rsched.engine.control import apply_trait_additions
    from rsched.engine.run_context import Budgets, RunContext
    from rsched.engine.transcript import Transcript, read_events
    from rsched.paths import atomic_write_json

    d = make_routine(slug="trait-live")
    cfg, _ = load_routine(d)
    assert cfg is not None
    run_dir = d / "runs" / "20260721-070000"
    run_dir.mkdir(parents=True)
    ctx = RunContext(routine=cfg, server=ServerConfig(), registry=None,
                     run_ts="20260721-070000", run_dir=run_dir,
                     transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    traits_mod.add_trait(lib, d, "alpha")          # what the web layer just wrote
    loop = SimpleNamespace(ctx=ctx, messages=[], _last_traits_ts="")
    atomic_write_json(ctx.root_run_dir / "control.json",
                      {"add_traits": {"slugs": ["alpha"], "ts": "t1"}})
    apply_trait_additions(loop)
    assert len(loop.messages) == 1
    assert "the first practice" in loop.messages[0]["content"]
    assert "applies from now on" in loop.messages[0]["content"]
    events, _off = read_events(ctx.run_dir / "transcript.jsonl")
    assert any(e["type"] == "user_injection" and e["payload"].get("source") == "engine"
               for e in events)
    apply_trait_additions(loop)                     # same ts → edge-triggered no-op
    assert len(loop.messages) == 1
    # a fresh signal repeating a slug already delivered must not re-append the prose
    atomic_write_json(ctx.root_run_dir / "control.json",
                      {"add_traits": {"slugs": ["alpha"], "ts": "t2"}})
    apply_trait_additions(loop)
    assert len(loop.messages) == 1


def test_read_trait_is_a_gated_kind_so_it_is_off_by_default():
    assert "read_trait" in KINDS
    assert "read_trait" in GATED_KINDS
    # the schema layer accepts it; the capability layer is what withholds it
    assert validate_action({"say": "s", "kind": "read_trait", "name": "alpha"}) == []
    denied = validate_action({"say": "s", "kind": "read_trait", "name": "alpha"},
                             allowed_kinds={"util"})
    assert denied and "read_trait" in denied[0]
