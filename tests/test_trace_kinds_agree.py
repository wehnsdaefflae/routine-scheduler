"""F860: every trace kind the console EMITS is one the store ACCEPTS — pinned, not assumed.

`api_traces.ingest` drops any event whose `kind` is not in `KINDS`, silently and by design: the
allowlist is what stops the store growing fields nobody reads. The failure mode is the mirror
image — a kind the frontend emits that the allowlist never gained. Those rows are discarded on
arrival, the POST answers 200, `recorded` counts them out, and nothing anywhere says a
telemetry path is dead.

MEASURED: `static/views/run.js:344` has emitted `trace("run-question", qid, state)` since
commit `5afed71b` on **2026-07-17** — a diagnostic for whether and when the run page rendered a
given clarify question. `run-question` was never in `KINDS`. 115 days, and
`grep -rl run-question /home/mark/routines/.ui-traces/` finds it in no day file: not one row
was ever stored. The emitter is written, the ingestion refuses it, and both halves look fine
from where they sit.

This is the cause rather than the instance: the two lists are 100 lines apart in different
languages, and nothing has ever compared them. The test reads the SHIPPED source for both
sides, so a kind added to either half without the other reds here — at the cheapest seam there
is, instead of after the next 115 days of discarded rows.
"""

from __future__ import annotations

import re
from pathlib import Path

from rsched.web.api_traces import KINDS, record_server_trace

STATIC = Path(__file__).resolve().parents[1] / "static"

#: `record_server_trace` is imported to NAME the server's own door in one place: `stream-close`
#: is written through it rather than through `trace()`, which is why the mirror check below
#: exempts that kind and nothing else.
_SERVER_DOOR = record_server_trace

#: A kind named in the allowlist that nothing in `static/` emits. Legitimate: `submit` is
#: reserved and unused today, and the SERVER writes `stream-close` through
#: `record_server_trace` rather than through `trace()`. Each entry says why, because an
#: unexplained exemption is how an allowlist rots.
NOT_EMITTED_BY_THE_CLIENT = {
    "submit": "reserved for form submissions; nothing emits it yet (0 rows in 14 days)",
}


def _emitted_kinds() -> set[str]:
    """Every literal kind passed to `trace(...)` anywhere in `static/`."""
    found: set[str] = set()
    for path in sorted(STATIC.rglob("*.js")):
        for match in re.finditer(r"""\btrace\(\s*["']([a-z][a-z0-9-]*)["']""",
                                 path.read_text(encoding="utf-8")):
            found.add(match.group(1))
    return found


def test_every_emitted_kind_is_accepted_by_the_store():
    """The defect itself: an emitter the allowlist never gained drops its rows in silence."""
    orphans = sorted(_emitted_kinds() - set(KINDS))
    assert not orphans, (
        "static/ emits trace kinds that api_traces.KINDS rejects, so every one of those rows is "
        f"discarded at ingestion with a 200: {orphans}. Add them to KINDS, or delete the dead "
        "emitter — an emitter whose rows are dropped is worse than no emitter, because the "
        "store looks like evidence that the thing never happened.")


def test_every_accepted_kind_is_emitted_or_explained():
    """The mirror: an allowlist entry nothing produces reads as "this never happens".

    `freeze` had 0 rows for 14 days and that was a real measurement gap (F858), not an absent
    allowlist entry — but a reader cannot tell those apart without this list. So an unemitted
    kind is allowed only with its reason recorded here.
    """
    emitted = _emitted_kinds()
    server_side = {"stream-close"}        # written by record_server_trace, not by trace()
    unexplained = sorted(set(KINDS) - emitted - server_side - set(NOT_EMITTED_BY_THE_CLIENT))
    assert not unexplained, (
        f"KINDS accepts kinds nothing emits and nothing explains: {unexplained}. Either wire the "
        "emitter, or record why it is reserved in NOT_EMITTED_BY_THE_CLIENT — an allowlist entry "
        "with no producer makes an empty store read as a quiet system.")


def test_the_run_question_diagnostic_is_accepted():
    """The instance that raised this, pinned by name so it cannot regress to silence.

    `run.js` traces a real transition of the question shown on the run page — the evidence for
    whether a blocking question ever reached the surface the operator reads, which is the one
    thing the question ledger cannot tell from its own records.
    """
    assert "run-question" in KINDS
    assert "run-question" in _emitted_kinds()
