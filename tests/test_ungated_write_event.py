"""The `ungated_write` marker's READER — one health event per run (R2654).

D158 traded a seal for a marker: every write protection lives in the file-action gate, a
`bash -c` redirect calls none of them, and deciding which writes a shell line performs is
undecidable — so prevention was replaced by `ungated_write` on the observation, "so a person
auditing the run afterwards can find the writes that no gate saw".

The detection half was never connected. Measured at 0.432.0: `ungated_write` appeared at
exactly two lines of `src/rsched`, both the writer — nothing in `readmodels/`, nothing in
`web/`, nothing in `static/`, and `components/transcript.js` had no case for the flag while
carrying two for `timed_out`. The only consumer in existence was the four in-process assertions
in `tests/test_shell_action.py`. A compensating control nothing reads is not one.

So the first write-shaped command of a run files an `ungated_write` health event into
`.control/health-events.jsonl` — the one durable, machine-checked stream an audit opens. What
is pinned here is that it arrives, that it arrives ONCE per run (the stream's own lesson: one
resumed run once wrote 26 of 1,289 rows for a single condition), and that a read-only run files
nothing, which is what keeps the stream worth reading.
"""

from pathlib import Path

import pytest

from helpers import run_context, server_config
from rsched.engine.executor import dispatch
from rsched.grantpolicy import GrantPolicy


@pytest.fixture
def shell_ctx(make_routine, tmp_path):
    """A run holding `shell`, under a routines home in tmp (conftest's hermetic home keeps the
    real ~/routines out of reach, which matters doubly here: this test asserts on the health
    stream the engine writes)."""
    server = server_config(libraries_home=tmp_path / "libraries", sandbox="off")
    (server.libraries_home / "utils").mkdir(parents=True)
    return run_context(make_routine(slug="sheller"), "20260903-120000", server=server,
                       grants=GrantPolicy(actions=frozenset({"shell"})))


def _rows(ctx) -> list[dict]:
    from rsched.health_events import HEALTH_EVENTS_FILE
    from rsched.paths import read_jsonl

    path = Path(ctx.server.routines_home) / ".control" / HEALTH_EVENTS_FILE
    return [r for r in read_jsonl(path) if r.get("event") == "ungated_write"]


def test_an_ungated_write_reaches_the_health_stream(shell_ctx):
    obs = dispatch({"kind": "shell", "command": "echo hi > state/audited.txt"}, shell_ctx)
    assert obs["ungated_write"] is True            # the marker D158 bought
    rows = _rows(shell_ctx)
    assert len(rows) == 1, rows
    assert rows[0]["routine"] == "sheller"
    assert rows[0]["run_id"] == shell_ctx.run_id   # the row's job is to name the run to open
    assert "state/audited.txt" in rows[0]["detail"]


def test_a_runs_many_writes_file_one_row(shell_ctx):
    """One row per RUN, not per command: the per-call record is already on every observation
    the marker rode, and `health_cooldown`'s docstring holds what per-occurrence filing did to
    this stream once."""
    for i in range(4):
        dispatch({"kind": "shell", "command": f"echo {i} > state/w{i}.txt"}, shell_ctx)
    assert len(_rows(shell_ctx)) == 1


def test_a_read_only_run_files_nothing(shell_ctx):
    """The event has to be worth reading, so a run that wrote nothing must not appear."""
    for command in ("ls state", "wc -l main.md", "cat state/x 2>/dev/null; true"):
        dispatch({"kind": "shell", "command": command}, shell_ctx)
    assert _rows(shell_ctx) == []


def test_the_event_is_not_counted_as_blocked_work():
    """Nothing was prevented — the writes happened — so the blocked-fleet fold must not claim
    the fleet is stuck on it (`health_stream.BLOCKED_EVENTS` is 'work that was DUE did not
    happen'). It is also why the event may carry a cooldown at all, which no blocked event may.
    """
    from rsched.readmodels.health_stream import BLOCKED_EVENTS

    assert "ungated_write" not in BLOCKED_EVENTS
