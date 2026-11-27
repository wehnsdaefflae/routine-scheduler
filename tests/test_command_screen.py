"""COMMAND TEXT is screened for stored credentials on the way INTO a record (D317) — the output
seam (`captured_output.read_capped`) screened what a command PRINTED while what the command WAS
went into the record verbatim, so a credential inline on a command line (HTTP basic auth
`curl -u user:pass`, URL userinfo, `--password=`) survived in retained run records next to the
output it had already been removed from. Every value below is synthetic.
"""

from __future__ import annotations

import pytest

from helpers import health_events, run_context, server_config
from rsched import credneedles, secrets
from rsched.captured_output import REDACT_MIN_CHARS, redaction
from rsched.engine import commandscreen
from rsched.engine.executor import dispatch
from rsched.engine.loop import EngineLoop
from rsched.grantpolicy import GrantPolicy

#: A stored credential, and the name it is stored under. The name matters: the marker names it.
PASSWORD_NAME = "FAU_PASSWORD"
PASSWORD = "synthetic-pw-4f81ba23"
#: A credential the instance does NOT store — the limit this seam cannot close.
FOREIGN = "typed-by-hand-9c2e7f55"

TS = "20261127-090000"

#: Every test here reasons about one STORED credential, so the seam has something to match.
pytestmark = pytest.mark.usefixtures("stored_credential")


@pytest.fixture
def stored_credential():
    """Seed the central store with one credential and drop the cached needle set — effect
    only, since `conftest._hermetic_home` already points RSCHED_CONFIG into the test's tmp dir,
    so this writes a fixture store and never the instance's own.
    """
    secrets.set_secret(PASSWORD_NAME, PASSWORD)
    commandscreen.reset()
    yield
    commandscreen.reset()


@pytest.fixture
def shell_ctx(make_routine, tmp_path):
    server = server_config(libraries_home=tmp_path / "libraries", sandbox="off",
                           routines_home=tmp_path / "rhome")
    (server.libraries_home / "utils").mkdir(parents=True)
    return run_context(make_routine(slug="screener"), TS, server=server,
                       grants=GrantPolicy(actions=frozenset({"shell"})))


# -- the needle set's own redaction -----------------------------------------------------------

def test_a_stored_value_is_replaced_by_the_name_it_is_stored_under():
    """The shape that escaped every pattern: HTTP basic auth inline on a command line."""
    needles = credneedles.collect()
    out = needles.redact(f'curl -sS -u "user:{PASSWORD}" https://example.invalid')
    assert PASSWORD not in out
    assert out == f'curl -sS -u "user:{redaction(PASSWORD_NAME)}" https://example.invalid'


def test_the_marker_vocabulary_is_the_one_the_output_seam_uses():
    """Two spellings for one thing is how a reader learns to trust only one of them."""
    assert redaction("GH_TOKEN") == "[secret GH_TOKEN redacted]"


def test_a_value_under_the_shared_floor_is_not_a_needle():
    """The floor is `captured_output.REDACT_MIN_CHARS`, shared with the output seam: masking
    every short word would corrupt records without protecting anything a guess could not reach.
    """
    secrets.set_secret("TINY_TOKEN", "abc")
    commandscreen.reset()
    assert len("abc") < REDACT_MIN_CHARS
    assert credneedles.collect().redact("echo abc") == "echo abc"


def test_a_value_the_instance_does_not_store_stays_verbatim():
    """THE LIMIT, stated as a test rather than left to be discovered: only a credential this
    instance holds is a needle, so one typed into a command line is matched by nothing."""
    assert credneedles.collect().redact(f"curl -u u:{FOREIGN} x") == f"curl -u u:{FOREIGN} x"


def test_the_longest_match_wins_so_no_remainder_is_left_behind():
    """A stored value that CONTAINS another must be replaced whole; replacing the short one
    first would leave the rest of the long one in the record."""
    secrets.set_secret("OUTER_TOKEN", "abcdefgh-inner-xyz")
    secrets.set_secret("INNER_TOKEN", "inner-xyz")
    commandscreen.reset()
    out = credneedles.collect().redact("run abcdefgh-inner-xyz now")
    assert out == f"run {redaction('OUTER_TOKEN')} now"


# -- the acceptance predicate: a shell observation's stored command ---------------------------

def test_a_shell_observations_stored_command_shows_the_marker(shell_ctx):
    """The decision's own proof: the command store shows `[secret NAME redacted]`."""
    obs = dispatch({"kind": "shell",
                    "command": f'echo -n "" ; : -u "user:{PASSWORD}"'}, shell_ctx)
    assert PASSWORD not in obs["command"]
    assert redaction(PASSWORD_NAME) in obs["command"]


def test_what_runs_is_the_command_the_run_wrote_not_the_screened_one(shell_ctx):
    """Screening the record must never change the effect: the whole value reaches bash. Proven
    by its LENGTH, so the assertion never carries a credential."""
    obs = dispatch({"kind": "shell",
                    "command": f"printf '%s' '{PASSWORD}' | wc -c"}, shell_ctx)
    assert obs["exit"] == 0
    assert obs["stdout"].strip() == str(len(PASSWORD))
    assert redaction(PASSWORD_NAME) in obs["command"]


def test_the_ungated_write_health_row_is_screened_too(shell_ctx):
    """The audit stream keeps the command for 200 characters — a second retained copy."""
    obs = dispatch({"kind": "shell",
                    "command": f"echo '{PASSWORD}' > out.txt"}, shell_ctx)
    assert obs["ungated_write"] is True
    rows = health_events(shell_ctx.server.routines_home, event="ungated_write")
    assert rows, "the write-shaped command files one row per run"
    assert PASSWORD not in rows[0]["detail"]
    assert redaction(PASSWORD_NAME) in rows[0]["detail"]


# -- the transcript copy of the same command --------------------------------------------------

def test_the_landed_action_event_carries_the_screened_command(make_routine):
    """`assistant_action` records the action itself, so screening only the observation would
    leave the same credential in the same transcript, one line above."""
    from rsched.engine.transcript import read_events
    from test_loop import _server

    d = make_routine(slug="lander")
    loop = EngineLoop(run_context(d, TS, server=_server(d), registry=None), "## Run flow", "i")
    action = {"say": "fetch it", "kind": "shell",
              "command": f'curl -sS -u "user:{PASSWORD}" https://example.invalid'}
    loop._land(action)
    events, _ = read_events(loop.ctx.run_dir / "transcript.jsonl")
    landed = [e for e in events if e["type"] == "assistant_action"]
    assert len(landed) == 1
    assert PASSWORD not in landed[0]["payload"]["command"]
    assert redaction(PASSWORD_NAME) in landed[0]["payload"]["command"]
    # and the action the RUN reasons from is untouched — the record is a copy
    assert action["command"].count(PASSWORD) == 1


def test_a_util_calls_argument_vector_is_screened():
    """A credential inline in `args` reads the same as one on a command line."""
    out = commandscreen.screened_action(
        {"kind": "util", "name": "web-request", "args": ["get", f"https://u:{PASSWORD}@h/x"]})
    assert PASSWORD not in out["args"][1]
    assert redaction(PASSWORD_NAME) in out["args"][1]


def test_a_recorded_brief_is_screened_and_capped_after_the_screen():
    """`action_cancelled` and the admin-leg audit line keep a one-line BRIEF instead of the
    action, and for a `shell` action the brief IS the command — so an unscreened brief is the
    same exposure, and capping before screening could cut a marker in half.
    """
    brief = commandscreen.screened_brief(
        {"kind": "shell", "command": f'curl -sS -u "user:{PASSWORD}" https://example.invalid'})
    assert PASSWORD not in brief
    assert redaction(PASSWORD_NAME) in brief
    assert len(commandscreen.screened_brief({"kind": "shell", "command": "x" * 500})) == 200


def test_an_action_with_no_command_text_is_recorded_unchanged():
    """The common turn must pay nothing but one dict copy — no field is rewritten."""
    action = {"say": "read it", "kind": "read_file", "path": "state/notes.md"}
    assert commandscreen.screened_action(action) == action


def test_a_collection_that_fails_screens_nothing_and_costs_no_turn(monkeypatch):
    """A needle set that cannot be read must not raise into the turn loop — the behaviour that
    preceded this seam, logged rather than silent."""
    monkeypatch.setattr(credneedles, "collect",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("no store")))
    commandscreen.reset()
    assert commandscreen.screened("curl -u u:p x") == "curl -u u:p x"
    commandscreen.reset()
