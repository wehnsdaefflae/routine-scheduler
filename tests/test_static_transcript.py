"""The console's transcript renderer knows every event type the engine writes.

engine/transcript.py's EVENT_TYPES is read by two consumers — the web renderer and the meta
routine — and "an unknown kind reads as corruption", so the tuple and both readers are extended
together (CLAUDE.md, "Transcript events"). Nothing checked the web half: `stages_skipped`
(F521) joined the tuple and the CLI renderer, and the console silently drew nothing for it.
The renderer dispatches in components/transcript.js `add()` — three types by an explicit
`ev.type === "…"` branch, the rest through the SIMPLE table — so both are read here.
"""

import re
from pathlib import Path

from rsched.engine.transcript import EVENT_TYPES

TRANSCRIPT_JS = (Path(__file__).resolve().parents[1] / "static" / "components"
                 / "transcript.js").read_text(encoding="utf-8")


def _simple_keys(src: str) -> set[str]:
    """The keys of `const SIMPLE = { … }`, read at its own indentation (4 spaces)."""
    start = src.index("const SIMPLE = {")
    end = src.index("\n  };", start)
    return set(re.findall(r"^    ([a-z_]+)\s*:", src[start:end], flags=re.MULTILINE))


def test_every_event_type_has_a_renderer():
    branched = set(re.findall(r'ev\.type === "([a-z_]+)"', TRANSCRIPT_JS))
    rendered = branched | _simple_keys(TRANSCRIPT_JS)
    missing = set(EVENT_TYPES) - rendered
    assert not missing, f"transcript event types the console cannot render: {sorted(missing)}"
    unknown = rendered - set(EVENT_TYPES)
    assert not unknown, f"renderers for types the engine never writes: {sorted(unknown)}"
