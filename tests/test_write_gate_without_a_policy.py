"""The engine's OWN seals hold for a context with no grant policy (F698).

`RunContext.grants` is documented as `None (direct construction) = unrestricted`, and
`_write_gate`'s docstring says its structural refusals "hold with no policy loaded". Two of
them did not: `_engine_owned` (the `runs/`, `.util_outputs/`, `.doc_cache/` record the engine
keeps OF the run) and the `routine.yaml` refusal both sat BELOW the `if g is None: return None`
early return, so the strongest refusals in the file were the ones a policy-less context
skipped — the inverse of what both docstrings promise.

Only the RECIPE seal reads the policy (`g.recipe_unlocked`), so it alone belongs below that
return. These tests pin the ordering: with grants=None the engine's record and the user's
config are still refused, while an ordinary path in the routine's own dir still writes.
"""

from __future__ import annotations

from helpers import run_context, server_config
from rsched.engine.fileops import do_write_file
from rsched.engine.run_context import RunContext


def _unrestricted(routine_dir, tmp_path) -> RunContext:
    """A context in the state run_context.py:106 calls unrestricted — no policy at all."""
    ctx = run_context(routine_dir, "20261016-070000", grants=None,
                      server=server_config(libraries_home=tmp_path / "libraries"))
    assert ctx.grants is None          # the premise of every assertion below
    return ctx


def test_the_engines_own_runs_record_is_refused_without_a_policy(make_routine, tmp_path):
    d = make_routine()
    ctx = _unrestricted(d, tmp_path)
    target = d / "runs" / "20260101-000000" / "status.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"outcome": "ok"}', encoding="utf-8")

    obs = do_write_file({"kind": "write_file", "path": str(target),
                         "content": '{"outcome": "failed"}'}, ctx)

    assert "engine-owned" in obs["error"]
    assert target.read_text(encoding="utf-8") == '{"outcome": "ok"}'


def test_routine_yaml_is_refused_without_a_policy(make_routine, tmp_path):
    d = make_routine()
    ctx = _unrestricted(d, tmp_path)
    before = (d / "routine.yaml").read_text(encoding="utf-8")

    obs = do_write_file({"kind": "write_file", "path": str(d / "routine.yaml"),
                         "content": "name: mine now\n"}, ctx)

    assert "routine.yaml is config" in obs["error"]
    assert (d / "routine.yaml").read_text(encoding="utf-8") == before


def test_an_ordinary_write_still_lands_without_a_policy(make_routine, tmp_path):
    """The seals are the only thing unrestricted means less of — not every write."""
    d = make_routine()
    ctx = _unrestricted(d, tmp_path)

    obs = do_write_file({"kind": "write_file", "path": "state/ordinary.txt",
                         "content": "kept"}, ctx)

    assert not obs.get("error"), obs
    assert (d / "state" / "ordinary.txt").read_text(encoding="utf-8") == "kept"


def test_the_memory_seal_was_already_above_the_early_return(make_routine, tmp_path):
    """Regression guard for the ordering itself: the four seals that were ALREADY structural
    stay structural, so a later edit cannot quietly push one back below the policy check.
    """
    d = make_routine()
    (d / ".memory").mkdir()
    ctx = _unrestricted(d, tmp_path)

    obs = do_write_file({"kind": "write_file", "path": str(d / ".memory" / "INDEX.md"),
                         "content": "mine now"}, ctx)

    assert "memory_read / memory_write" in obs["error"]
