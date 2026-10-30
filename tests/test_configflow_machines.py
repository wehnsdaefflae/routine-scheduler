"""The `machines` note must not tell a run to stop trying something that works (F802).

R3057, from conversation `c-20261029-080013`: the operator granted `machine:omen` mid-run and the
grant-applied note said *"Saved, but it takes effect at your NEXT RUN — you are still running
under what you booted with: machines: the machine bindings and share mounts are resolved at
boot"*. The very next action in the SAME process ran `gu remote list --json` and got omen back
with `"ready": true`, then `gu remote test omen` succeeded.

The note was wrong because `exec_env._machine_env` composes the machine env PER UTIL CALL from
`ctx.routine.machines` **plus `ctx.granted_now`** — the run's own grant overlay, which
`requests.apply_decision` writes the moment the operator answers an access request. So for
EXEC, a mid-run machine grant reaches the run already going. What genuinely waits for the next
run is the sshfs SHARE MOUNT, which the daemon makes at binding time; `_machine_env`'s own
docstring draws exactly that line ("A one-time grant covers EXEC only").

`tests/test_configflow.py` cannot catch this: it asserts only that every field declares one of
the two halves and that the reason is non-empty, never that the reason is TRUE. These two tests
compare the prose against the behaviour, which is the only thing that could have caught it — the
same shape as the `fs_read_roots` entry, whose note has always drawn this distinction.
"""

from __future__ import annotations

from helpers import run_context, server_config
from rsched import configflow
from rsched.engine.exec_env import _machine_env


def test_a_mid_run_machine_grant_reaches_the_run_already_going(make_routine, tmp_path):
    """The behaviour half: `granted_now` alone — no `machines:` in the config at all — is enough
    for the machine env to be composed, so the exec path is live mid-run."""
    routine_dir = make_routine("grantee")
    ctx = run_context(routine_dir, "20261030-000000",
                      server=server_config(routines_home=tmp_path))
    assert not ctx.routine.machines, "the fixture routine must declare no bindings"

    assert _machine_env(ctx) == {}, "no binding and no grant is no machine env"

    ctx.granted_now.add("machine:omen")
    env = _machine_env(ctx)
    assert "RSCHED_MACHINES" in env and "RSCHED_MACHINE_KEYS" in env, (
        "a machine granted mid-run must reach the env the next util call receives — this is "
        "what R3057 measured live and what the old note denied")


def test_the_machines_note_does_not_deny_what_a_mid_run_grant_does():
    """The prose half. The entry stays NEXT_RUN — the share mount really does wait — but its
    reason may not say the BINDINGS wait, because they do not. A note a run believes and acts
    on is load-bearing: this one told a conversation to stop trying something that worked."""
    half, why = configflow.CLASSIFICATION["machines"]
    assert half == configflow.NEXT_RUN, "the share mount is still made at binding time"
    assert "mid-run" in why, (
        "the reason must tell the operator that an access-REQUEST for machine:<name> reaches "
        "the live run, not only that a config edit does not")
    assert "bindings and share mounts are resolved at boot" not in why, (
        "the pre-F802 wording: it denies the exec path that exec_env._machine_env serves from "
        "ctx.granted_now on every util call")
