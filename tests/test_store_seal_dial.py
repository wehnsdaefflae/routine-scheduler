"""The job-store seal follows the routine's `jobs` DIAL, not the path (F752).

`state/jobs.json` was sealed for EVERY routine purely by name, and the refusal pointed the
refused run at the `job` action — which, with the job layer off, is projected out of its schema
and does not exist for it. `voice-model-trainer` met exactly that: no `jobs:` key in its
routine.yaml, its own 174 KB hand-maintained record of remote training jobs at that path, and a
recipe stage built around writing it (R2787 → F752). Renaming either side would have fixed one
routine and left the collision for the next; the dial fixes all of them at once.

What must NOT change with it: the seal is one of the structural refusals `_write_gate` applies
ABOVE its `if grants is None` early return (F698 found the strongest seals were the ones a
policy-less context skipped), and it must survive a policy held across a release that cannot
answer `jobs_on` (F677). So only POSITIVE knowledge that the dial is off unseals the file —
every "I cannot tell" keeps it sealed.
"""

from __future__ import annotations

from helpers import run_context, server_config
from rsched.engine import storeseal
from rsched.engine.fileops import do_write_file
from rsched.grantpolicy import GrantPolicy

OWN_RECORD = '{"jobs": {"train-7b": {"state": "running"}}}'


def _ctx(routine_dir, tmp_path, **fields):
    return run_context(routine_dir, "20261023-060000",
                       server=server_config(libraries_home=tmp_path / "libraries"), **fields)


def _write_own_jobs_json(routine_dir, ctx):
    return do_write_file({"kind": "write_file",
                          "path": str(routine_dir / "state" / "jobs.json"),
                          "content": OWN_RECORD}, ctx)


def test_a_routine_without_the_job_layer_writes_its_own_jobs_json(make_routine, tmp_path):
    """The reported case: the engine refused a routine its own file, over a name collision."""
    d = make_routine()
    ctx = _ctx(d, tmp_path, grants=GrantPolicy(jobs="off"))

    obs = _write_own_jobs_json(d, ctx)

    assert not obs.get("error"), obs.get("error")
    assert (d / "state" / "jobs.json").read_text(encoding="utf-8") == OWN_RECORD


def test_a_routine_that_keeps_jobs_is_still_refused_its_job_store(make_routine, tmp_path):
    """The seal's whole point, unchanged: a generic write could fake a checkpoint."""
    d = make_routine()
    ctx = _ctx(d, tmp_path, grants=GrantPolicy(jobs="on"))

    obs = _write_own_jobs_json(d, ctx)

    assert "job store" in obs["error"]
    assert not (d / "state" / "jobs.json").exists()


def test_without_a_policy_the_job_store_stays_sealed(make_routine, tmp_path):
    """F698: `_write_gate` applies this seal above its `grants is None` return. No policy is
    not knowledge that the dial is off, so the engine's store stays the engine's."""
    d = make_routine()
    ctx = _ctx(d, tmp_path, grants=None)
    assert ctx.grants is None                      # the premise of the assertion below

    assert "job store" in _write_own_jobs_json(d, ctx)["error"]


def test_a_policy_from_the_old_build_keeps_the_seal_rather_than_failing_open(make_routine,
                                                                            tmp_path):
    """F677: a policy object held across a release cannot answer `jobs_on` — `buildskew` then
    reads the routine's OWN capabilities. The one thing it must not do is unseal."""
    d = make_routine()

    class _Held:                                   # the shape 0.415.x handed the new code
        tasks_on = True

    ctx = _ctx(d, tmp_path, grants=_Held())
    ctx.routine.capabilities["jobs"] = "on"

    assert "job store" in _write_own_jobs_json(d, ctx)["error"]


def test_the_obligations_store_is_sealed_whatever_the_jobs_dial_says(make_routine, tmp_path):
    """No setting turns obligations off, so that half of the seal is unconditional."""
    d = make_routine()

    for dial in ("off", "on"):
        sealed = storeseal.stores(_ctx(d, tmp_path, grants=GrantPolicy(jobs=dial)))
        assert "state/obligations.json" in sealed, dial


def test_a_removal_of_the_tree_follows_the_dial_too(make_routine, tmp_path):
    """The removal gate asks the same question of everything a delete would take WITH it, and
    reads the same dial: a kept store cannot be removed by deleting its directory, while the
    routine that owns its own file may remove it."""
    from rsched.engine.fileops import _write_gate

    for dial, expect_refusal in (("on", True), ("off", False)):
        d = make_routine(f"rm{dial}")
        (d / "state" / "jobs.json").write_text(OWN_RECORD, encoding="utf-8")
        ctx = _ctx(d, tmp_path, grants=GrantPolicy(jobs=dial))

        err = _write_gate(ctx, (d / "state").resolve(), creates=False)

        assert bool(err and "job store" in err) is expect_refusal, (dial, err)
