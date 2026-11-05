"""The fs-root notes must not tell a run to stop trying something that works (F829).

R3164, from `llmsectest-weekday`: the operator granted an `fs-write` root permanently while the
run was live, and the config-change note the web sends after every forever-decision
(`web/api_questions._tell_live_run`) said *"takes effect at your NEXT RUN … widening it mid-run
would not reach the jails already created"*. The very next identical util call succeeded with
the new root in effect.

The note was wrong because `sandbox.policy_for_ctx` builds the jail PER CALL from
`ctx.write_roots()` — the config roots **plus `ctx.granted_now`**, the run's grant overlay that
`requests.apply_decision` writes the moment the operator answers. What genuinely waits is a
CONFIG EDIT: `ctx.routine` is the config composed at boot. The `fs_read_roots` entry always drew
exactly that line; `fs_write_roots` never did. Same shape as F802's `machines` entry
(`tests/test_configflow_machines.py`): compare the prose against the behaviour, and pin both
fs-root fields to one distinction so they cannot drift apart again.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import run_context, server_config
from rsched import configflow, sandbox


def test_a_mid_run_fs_grant_reaches_the_next_jail(make_routine, tmp_path):
    """The behaviour half: `granted_now` alone — no root in the config — widens the policy the
    next util subprocess is jailed under, for both directions."""
    routine_dir = make_routine("grantee")
    ctx = run_context(routine_dir, "20261105-000000",
                      server=server_config(routines_home=tmp_path))
    root = tmp_path / "granted"
    assert root not in sandbox.policy_for_ctx(ctx).write_roots

    ctx.granted_now.add(f"fs-write:{root}")
    policy = sandbox.policy_for_ctx(ctx)
    assert root in policy.write_roots, (
        "a write root granted mid-run must reach the jail the next util call is built with — "
        "this is what R3164 measured live and what the old note denied")
    assert root in policy.read_roots, "a write grant implies read (R244/F294)"

    ctx.granted_now.add(f"fs-read:{tmp_path / 'readable'}")
    assert Path(tmp_path / "readable") in sandbox.policy_for_ctx(ctx).read_roots


@pytest.mark.parametrize("field", ["fs_read_roots", "fs_write_roots"])
def test_both_fs_root_notes_draw_the_request_versus_edit_line(field):
    """The prose half. The entries stay NEXT_RUN — a config edit really does wait for the next
    boot — but each reason must say that an access-REQUEST decided mid-run reaches the run."""
    half, why = configflow.CLASSIFICATION[field]
    assert half == configflow.NEXT_RUN, "a config edit reaches the next run only"
    assert "access-REQUEST decided mid-run does reach the run" in why
    assert "a config edit does not" in why
    assert "would not reach the jails already created" not in why, (
        "the pre-F829 wording: it denies the per-call jail sandbox.policy_for_ctx builds from "
        "ctx.write_roots() on every util call")


def test_the_two_fs_root_reasons_are_one_sentence():
    """One distinction, one wording: the drift F829 records was one sibling corrected and the
    other not."""
    assert (configflow.CLASSIFICATION["fs_read_roots"][1]
            == configflow.CLASSIFICATION["fs_write_roots"][1])


def test_the_note_a_forever_fs_grant_triggers_says_it_is_live():
    """What the run actually reads after the click: the field is listed under the next-run
    half, and its reason right there names the request as live."""
    note = configflow.change_note(["fs_write_roots"], {})
    assert "fs_write_roots" in note
    assert "access-REQUEST decided mid-run does reach the run" in note
