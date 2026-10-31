"""R3108 increment one: when a classifier cannot decide a provider failure STRUCTURALLY, say so.

Three places decide retry, failover and prompt-window policy by matching ENGLISH SENTENCES in a
provider's error body: `endpoints/base.py`'s `transient_402`, `engine/overflow.py`'s
`_OVERFLOW_HINTS`/`_NEVER_OVERFLOW_STATUS`, and `engine/degrade.py`'s `_FAILOVER_REASONS`. Both
failure modes are measured: on 2026-10-16 conversation `c-20261016-075101` died twice in 30 s
logging `model_chain_exhausted … after 0 rungs` while the condition cleared itself, and on
2026-09-21 the Anthropic overflow shape matched none of the openai-flavoured hints, so
`self-audit:20260921-000321` 400'd four times with its prompt growing 1,017,305 → 1,045,385
while the engine failed over. `overflow.py`'s own comment states the mode: *"a hint list that
misses a vendor silently DISARMS this whole guard for it"*.

THIS INCREMENT CHANGES NO POLICY. Nothing about which errors are retried, failed over or shrunk
moves; every existing decision comes out identical (pinned below in
`test_no_existing_decision_changed`). What it adds is the one number nobody has: how often a
structured decision is actually UNAVAILABLE on this instance — which is what tells the next
increment whether the prose lists were ever load-bearing. Removing the prose matching is a later
increment.

Two seams file the event, and the FIRST one is the reason the hook is not on `_log_failover`:

(a) `_switch_to_fallback`, where every hard failure's class is computed. The specimen above died
    as `model_chain_exhausted … after 0 rungs`, and that branch never reaches `_log_failover` —
    hooking the log would have missed exactly the case that motivated the item.
(b) `_recover_oversize_prompt`'s "not ours" return, where BOTH overflow nets have declined. A
    400 that quotes a token count and is still not read as an overflow is either a vendor shape
    the hint list misses or a correctly-excluded rate limit, and `declined_by` says which.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from rsched.endpoints.base import EndpointError
from rsched.engine import degrade, errorclass, overflow

EVENT = "error_class_undecided"

#: The 2026-09-21 Anthropic shape — a REAL overflow, and the hint list carries it today.
ANTHROPIC_400 = ('claude: HTTP 400: {"type":"error","error":{"type":"invalid_request_error",'
                 '"message":"prompt is too long: 1045385 tokens > 1000000 maximum"}}')
#: The shape (b) exists for: a 400 quoting a token count in words no hint list carries.
UNKNOWN_400 = ('proxy: HTTP 400: {"error":{"message":"input too large: 41000 tokens supplied, '
               'limit 32000","type":"invalid_request_error","code":"too_large"}}')
#: A rate limit wearing an overflow's clothes — `_NEVER_OVERFLOW_STATUS` declines it ON PURPOSE.
RATE_LIMIT_400 = ("proxy: HTTP 400: Request too large: 32000 tokens > 30000 maximum "
                  "tokens per minute")


def _events(home) -> list[dict]:
    path = home / ".control" / "health-events.jsonl"
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _rows(home) -> list[dict]:
    return [e for e in _events(home) if e["event"] == EVENT]


def _loop(tmp_path):
    """A loop-shaped stand-in and the ref that failed — the two seams' whole world."""
    (tmp_path / ".control").mkdir(parents=True, exist_ok=True)
    ref = SimpleNamespace(endpoint="ep", model="m", name="m", context_tokens=32_768,
                          multimodal=False)
    ctx = SimpleNamespace(
        routine=SimpleNamespace(dir=tmp_path / "r", slug="victim", models={}, efforts={}),
        server=SimpleNamespace(routines_home=tmp_path),
        transcript=SimpleNamespace(event=lambda *_a, **_k: None),
        run_id="victim:20261031-140002", first_failover_cause="", failover_rungs=0, depth=0)
    return SimpleNamespace(ctx=ctx, messages=[{"role": "user", "content": "hi"}],
                           resume=False), ref


# --- (a) the failover class ---------------------------------------------------------------

def test_a_failover_class_of_other_files_the_event(tmp_path):
    """`_failover_reason` returning `other` is an honest bucket — and an unmeasured one. This
    is the `after 0 rungs` path: an empty chain, so `_log_failover` is never reached."""
    loop, ref = _loop(tmp_path)
    exc = EndpointError('ep/m: HTTP 418: {"error":{"message":"i am a teapot"}}')

    assert degrade._switch_to_fallback(loop, [], ref, exc) is None   # chain exhausted, as before

    rows = _rows(tmp_path)
    assert len(rows) == 1, _events(tmp_path)
    assert rows[0]["classifier"] == "failover_reason"
    assert rows[0]["reason"] == "other"
    assert rows[0]["status"] == 418
    assert rows[0]["routine"] == "victim" and rows[0]["run_id"] == "victim:20261031-140002"
    # the policy event still fires, unchanged: this increment adds a row, it replaces none
    assert [e["event"] for e in _events(tmp_path)].count("model_chain_exhausted") == 1


def test_a_recognized_failover_class_files_nothing(tmp_path):
    """Every ordinary hard failure. A row filed when the class WAS decidable would make the
    number this surface exists to produce meaningless."""
    loop, ref = _loop(tmp_path)

    assert degrade._switch_to_fallback(
        loop, [], ref, EndpointError("ep/m: HTTP 429: rate limit exceeded")) is None

    assert _rows(tmp_path) == []
    assert [e["event"] for e in _events(tmp_path)] == ["model_chain_exhausted"]


# --- (b) the overflow decline -------------------------------------------------------------

def test_an_overflow_decline_on_a_400_quoting_tokens_files_the_event(tmp_path):
    """The 2026-09-21 failure mode, caught as a COUNT: a 400 quoting a token count that no
    hint matches. `_recover_oversize_prompt` still declines it exactly as before."""
    loop, ref = _loop(tmp_path)

    assert overflow._recover_oversize_prompt(
        loop, SimpleNamespace(), ref, EndpointError(UNKNOWN_400)) is None

    rows = _rows(tmp_path)
    assert len(rows) == 1, _events(tmp_path)
    assert rows[0]["classifier"] == "overflow_text"
    assert rows[0]["declined_by"] == "no_hint_match"
    assert rows[0]["status"] == 400
    # the finding the NEXT increment needs: was anything structured even in the excerpt?
    assert rows[0]["error_keys_in_excerpt"] is True
    assert "41000 tokens" in rows[0]["detail"]


def test_a_deliberate_never_overflow_decline_is_attributed_to_that_rule(tmp_path):
    """A rate limit quoting two token counts is declined ON PURPOSE, and reading it as an
    overflow would pin the run's window to a per-minute budget. The row must say so, or the
    count cannot be read: `no_hint_match` is the dangerous bucket, this one is correctness."""
    loop, _ref = _loop(tmp_path)

    errorclass.note_overflow_declined(loop, EndpointError(RATE_LIMIT_400))

    rows = _rows(tmp_path)
    assert len(rows) == 1, _events(tmp_path)
    assert rows[0]["declined_by"] == "never_overflow_status"


def test_a_real_overflow_files_nothing(tmp_path):
    """The hint list DOES carry the Anthropic shape today — nothing declined, nothing to say."""
    loop, _ref = _loop(tmp_path)

    errorclass.note_overflow_declined(loop, EndpointError(ANTHROPIC_400))

    assert _events(tmp_path) == []


def test_a_decline_with_no_token_count_files_nothing(tmp_path):
    """Most 400s are a malformed body, not a window — the event is about the classifier having
    no structured basis for a decision it nonetheless made, not about every rejection."""
    loop, _ref = _loop(tmp_path)

    errorclass.note_overflow_declined(loop, EndpointError("proxy: HTTP 400: bad request"))
    errorclass.note_overflow_declined(loop, EndpointError("proxy: HTTP 401: unauthorized"))

    assert _events(tmp_path) == []


# --- the increment's own guarantee --------------------------------------------------------

def test_no_existing_decision_changed():
    """THE WHOLE POINT of increment one: it measures, it does not decide. Every classifier
    verdict the three prose lists produced before this change comes out identical."""
    assert degrade._failover_reason(EndpointError("HTTP 429 rate limit")) == "rate_limit"
    assert degrade._failover_reason(EndpointError("HTTP 401 bad api key")) == "auth"
    assert degrade._failover_reason(EndpointError("m refused the turn")) == "refusal"
    assert degrade._failover_reason(EndpointError("empty completion x2")) == "empty"
    assert degrade._failover_reason(EndpointError("HTTP 503 overloaded")) == "server"
    assert degrade._failover_reason(EndpointError("i am a teapot")) == "other"

    assert overflow._is_overflow_text(ANTHROPIC_400) is True
    assert overflow._is_overflow_text(UNKNOWN_400) is False
    assert overflow._is_overflow_text(RATE_LIMIT_400) is False
    assert overflow.parse_overflow_limit(ANTHROPIC_400) == 1_000_000
    assert overflow.parse_overflow_limit(UNKNOWN_400) is None
    assert overflow.parse_overflow_limit(
        "This model's maximum context length is 65536 tokens") == 65_536


def test_the_notifiers_never_raise():
    """They sit on two failure paths. A diagnostic that can raise turns a failure the engine
    recovers from into a dead run, which is the opposite of what it is for."""
    assert errorclass.note_overflow_declined(SimpleNamespace(), EndpointError("x")) is None
    assert errorclass.note_failover_class(SimpleNamespace(ctx=None), None, "other") is None
    assert errorclass.note_overflow_declined(None, None) is None


def test_the_event_is_in_the_documented_vocabulary():
    """An event at a call site but not in the header enum is invisible by construction — the
    `prompt_oversize_shrunk` failure `tests/test_health_events.py` exists to catch. Pinned next
    to the thing that emits it, so the registration is not a separate chore."""
    from rsched import health_events

    head = (health_events.__doc__ or "").split('"routine": <slug>')[0]
    assert EVENT in head
    assert errorclass.EVENT == EVENT
