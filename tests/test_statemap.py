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


def test_state_graph_reads_current_phase_and_stages_fallback(tmp_path):
    d = tmp_path / "r"
    (d / "state").mkdir(parents=True)
    (d / "stages").mkdir()
    (d / "stages" / "b-two.md").write_text("x", encoding="utf-8")
    (d / "stages" / "a-one.md").write_text("x", encoding="utf-8")
    (d / "main.md").write_text("# no flow section here", encoding="utf-8")
    (d / "state" / "phase.json").write_text(json.dumps({"phase": "a-one"}), encoding="utf-8")
    g = statemap.state_graph(d)
    assert [s["name"] for s in g["states"]] == ["a-one", "b-two"]   # stages/ fallback
    assert g["current"] == "a-one"
    # missing/broken pieces degrade to empty, never raise
    assert statemap.state_graph(tmp_path / "absent") == {"states": [], "current": ""}
    (d / "state" / "phase.json").write_text("not json", encoding="utf-8")
    assert statemap.state_graph(d)["current"] == ""


def test_state_graph_accepts_state_key(tmp_path):
    """Recipes that name the current-phase field 'state' (e.g. self-audit) still light up
    the live diagram — statemap accepts 'phase' OR 'state'."""
    d = tmp_path / "r"
    (d / "state").mkdir(parents=True)
    (d / "main.md").write_text(
        "## Run flow\n1. **gather** — collect.\n2. **write** — emit.", encoding="utf-8")
    (d / "state" / "phase.json").write_text(json.dumps({"state": "write"}), encoding="utf-8")
    assert statemap.state_graph(d)["current"] == "write"


def test_norm_matches_loosely():
    assert statemap.norm("Gather Evidence") == statemap.norm("gather-evidence")


def test_state_graph_accepts_step_key(tmp_path):
    """Recipes that name the pointer field 'step' (routine-improver / workflow-curator) light up
    the diagram too — statemap accepts phase / state / step."""
    d = tmp_path / "r"
    (d / "state").mkdir(parents=True)
    (d / "main.md").write_text(
        "## Run flow\n1. **orient** — go.\n2. **record** — done.", encoding="utf-8")
    (d / "state" / "phase.json").write_text(json.dumps({"step": "record"}), encoding="utf-8")
    assert statemap.state_graph(d)["current"] == "record"


def test_outline_extracts_headings_skipping_fences():
    md = ("# Title\n\n## Run flow\n\n```python\n# not a heading\ndef run(): ...\n```\n\n"
          "## Completion criteria\n### Sub point\ntext\n#### deep\n")
    got = [(h["level"], h["text"]) for h in statemap.outline(md)]
    # H1 title excluded (levels 2-4 only); the # comment inside the ``` fence is NOT a heading
    assert got == [(2, "Run flow"), (2, "Completion criteria"), (3, "Sub point"), (4, "deep")]


def test_recipe_tree_orders_stages_by_run_flow(tmp_path):
    d = tmp_path / "r"
    (d / "stages").mkdir(parents=True)
    (d / "traits").mkdir()
    (d / "main.md").write_text(
        "## Run flow\n1. **collect** — c.\n2. **draft** — d.\n\n## Completion criteria\n- done\n",
        encoding="utf-8")
    (d / "stages" / "draft.md").write_text("## How\ndo it\n", encoding="utf-8")
    (d / "stages" / "collect.md").write_text("text\n", encoding="utf-8")
    (d / "stages" / "extra.md").write_text("no flow entry\n", encoding="utf-8")   # extras sort last
    (d / "traits" / "ask-policy.md").write_text("# trait\n## When\n", encoding="utf-8")
    tree = statemap.recipe_tree(d)
    assert tree["main"]["path"] == "main.md"
    assert [h["text"] for h in tree["main"]["outline"]] == ["Run flow", "Completion criteria"]
    # stages ordered by ## Run flow (collect, draft); extras with no flow entry appended
    assert [s["name"] for s in tree["stages"]] == ["collect", "draft", "extra"]
    assert [h["text"] for h in tree["stages"][1]["outline"]] == ["How"]
    assert [t["name"] for t in tree["traits"]] == ["ask-policy"]


def test_phase_stats_aggregates_turns_tokens_time(tmp_path):
    """Per-phase instrumentation from a transcript: dispatch time lands on the acting
    phase, completion time on the phase that produced the next action, the tail after
    the last action on the last phase; tokens/cost sum per phase; unphased turns keep
    their own bucket."""
    import json

    from rsched.statemap import phase_stats

    lines = [
        {"ts": "2026-07-15T10:00:00+00:00", "type": "header", "payload": {}},
        {"ts": "2026-07-15T10:00:10+00:00", "type": "assistant_action",
         "usage": {"in": 100, "out": 10}, "payload": {}},
        {"ts": "2026-07-15T10:00:15+00:00", "type": "observation", "payload": {}},
        {"ts": "2026-07-15T10:00:30+00:00", "type": "assistant_action", "phase": "gather",
         "usage": {"in": 200, "out": 20, "cost": 0.5}, "payload": {}},
        {"ts": "2026-07-15T10:00:40+00:00", "type": "observation", "payload": {}},
        {"ts": "2026-07-15T10:01:00+00:00", "type": "assistant_action", "phase": "gather",
         "usage": {"in": 50, "out": 5}, "payload": {}},
        {"ts": "2026-07-15T10:01:05+00:00", "type": "finish", "payload": {}},
    ]
    (tmp_path / "transcript.jsonl").write_text(
        "".join(json.dumps(ln) + "\n" for ln in lines), encoding="utf-8")
    assert phase_stats(tmp_path) == [
        {"phase": "", "turns": 1, "tokens": 110, "cost": 0.0, "elapsed_s": 15},
        {"phase": "gather", "turns": 2, "tokens": 275, "cost": 0.5, "elapsed_s": 50},
    ]


def test_phase_stats_empty_without_transcript(tmp_path):
    from rsched.statemap import phase_stats

    assert phase_stats(tmp_path) == []
