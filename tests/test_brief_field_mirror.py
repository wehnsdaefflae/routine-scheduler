"""The console's copy of BRIEF_FIELD, held in lockstep with the engine's — its own module because
the responsibility is distinct from the action contract: this is the only guard that READS
`static/` to check a Python table. It left `tests/test_actions.py` when D288 pushed that file
over its size ceiling, and the seam, not the slack, chose the split."""

from __future__ import annotations

import re
from pathlib import Path

from rsched.engine.actionschema import BRIEF_FIELD
from rsched.engine.renamedkinds import RETIRED

JS = (Path(__file__).resolve().parents[1] / "static" / "components" / "transcript.js")


def _js_map() -> dict[str, str]:
    m = re.search(r"const BRIEF_FIELD = \{(.*?)\};", JS.read_text(encoding="utf-8"), re.DOTALL)
    assert m, "transcript.js lost its BRIEF_FIELD map"
    return dict(re.findall(r'(\w+): "(\w+)"', m.group(1)))


def test_transcript_js_brief_field_mirrors_python():
    """The web transcript keeps its own copy (static/ is no-build, so it cannot import the
    Python map); a kind missing there silently renders an EMPTY turn brief — the 2026-08-21
    hygiene sweep found the copy 10 kinds behind.
    """
    parsed = _js_map()
    assert set(BRIEF_FIELD) - set(parsed) == set(), (
        "a live kind is missing from transcript.js: its turns render an EMPTY brief")
    assert {k: v for k, v in parsed.items() if k in BRIEF_FIELD} == BRIEF_FIELD, (
        "the two maps disagree about which field identifies a turn")


def test_the_only_extra_entries_are_retired_kinds():
    """The one asymmetry, and it is deliberate: the console renders runs recorded at ANY
    version, so a kind that RETIRED (`engine/renamedkinds.RETIRED`) keeps its entry there
    while the engine's map holds live kinds only. Anything else is a typo rendering a brief
    for an action nobody can emit.
    """
    assert set(_js_map()) - set(BRIEF_FIELD) <= set(RETIRED)


def test_a_retired_kind_keeps_its_brief_in_the_console():
    """The other direction, which is the one that loses data silently: drop `llm` from the JS
    map and every recorded llm turn in every past transcript renders without its brief.
    """
    parsed = _js_map()
    for kind in RETIRED:
        assert kind in parsed, (
            f"{kind!r} retired but left transcript.js, so every recorded {kind} turn in the "
            "console now renders without its brief")
