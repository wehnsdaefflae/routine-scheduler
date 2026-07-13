"""statemap: state-graph parsing from a routine's own main.md — tolerant by design."""

import json

from rsched import statemap

RUN_FLOW = """---
name: X
---
# Something

## How to run this state machine
1. read the phase.

## Run flow
1. **orient** — read the backlog and pick a focus.
2. **measure** — refresh the baseline from real data.
3. **record-close** — append the LEDGER and finish.

## Completion criteria
- done.
"""

PHASES = """# T

## Phases
- **scan** — gather fresh postings.
- **score**: rate against the profile
- **report** — ping when warranted.
"""


def test_parse_run_flow_bold_items():
    states = statemap.parse_states(RUN_FLOW)
    assert [s["name"] for s in states] == ["orient", "measure", "record-close"]
    assert states[0]["desc"] == "read the backlog and pick a focus"


def test_parse_phases_section_and_separators():
    states = statemap.parse_states(PHASES)
    assert [s["name"] for s in states] == ["scan", "score", "report"]
    assert states[1]["desc"] == "rate against the profile"


def test_parse_plain_numbered_fallback_and_dedup():
    md = "## Run flow\n1. gather — collect the data.\n2. gather — again.\n3. write — emit."
    states = statemap.parse_states(md)
    assert [s["name"] for s in states] == ["gather", "write"]   # duplicate name collapses


def test_state_graph_reads_current_phase_and_steps_fallback(tmp_path):
    d = tmp_path / "r"
    (d / "state").mkdir(parents=True)
    (d / "steps").mkdir()
    (d / "steps" / "b-two.md").write_text("x", encoding="utf-8")
    (d / "steps" / "a-one.md").write_text("x", encoding="utf-8")
    (d / "main.md").write_text("# no flow section here", encoding="utf-8")
    (d / "state" / "phase.json").write_text(json.dumps({"phase": "a-one"}), encoding="utf-8")
    g = statemap.state_graph(d)
    assert [s["name"] for s in g["states"]] == ["a-one", "b-two"]   # steps/ fallback
    assert g["current"] == "a-one"
    # missing/broken pieces degrade to empty, never raise
    assert statemap.state_graph(tmp_path / "absent") == {"states": [], "current": ""}
    (d / "state" / "phase.json").write_text("not json", encoding="utf-8")
    assert statemap.state_graph(d)["current"] == ""


def test_norm_matches_loosely():
    assert statemap.norm("Gather Evidence") == statemap.norm("gather-evidence")
