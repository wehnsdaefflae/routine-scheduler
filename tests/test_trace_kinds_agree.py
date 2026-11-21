"""F860: every trace kind the console EMITS is one the store ACCEPTS — pinned, not assumed."""

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
    """The mirror: an allowlist entry nothing produces reads as "this never happens"."""
    emitted = _emitted_kinds()
    server_side = {"stream-close"}        # written by record_server_trace, not by trace()
    unexplained = sorted(set(KINDS) - emitted - server_side - set(NOT_EMITTED_BY_THE_CLIENT))
    assert not unexplained, (
        f"KINDS accepts kinds nothing emits and nothing explains: {unexplained}. Either wire the "
        "emitter, or record why it is reserved in NOT_EMITTED_BY_THE_CLIENT — an allowlist entry "
        "with no producer makes an empty store read as a quiet system.")


def test_the_run_question_diagnostic_is_accepted():
    """The instance that raised this, pinned by name so it cannot regress to silence."""
    assert "run-question" in KINDS
    assert "run-question" in _emitted_kinds()
