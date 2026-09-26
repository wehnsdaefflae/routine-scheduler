"""statemap: the state graph derived from a routine's stage modules — nothing parsed
from prose, so every routine with stage modules has a diagram, unconditionally."""

import json

from rsched.readmodels import statemap


def test_stage_states_ordered_by_main_md_mention(tmp_path):
    """The stage modules ARE the states; node order follows where main.md first mentions
    each module (its Run-flow routing), never-mentioned extras append alphabetically; the
    desc is the module's leading heading."""
    d = tmp_path / "r"
    (d / "stages").mkdir(parents=True)
    (d / "stages" / "act.md").write_text("# Step: apply the fixes\n…\n", encoding="utf-8")
    (d / "stages" / "gather.md").write_text("prose without a heading\n", encoding="utf-8")
    (d / "stages" / "extra.md").write_text("# leftover\n", encoding="utf-8")
    (d / "main.md").write_text(
        # the frontmatter's ALPHABETICAL module list must not pose as a first mention
        "---\nname: R\nmodules:\n- act\n- extra\n- gather\n---\n"
        "## Standing practices come first in prose\n\n## Run flow\n"
        "1. `stages/gather.md` — collect.\n2. `stages/act.md` — apply.\n", encoding="utf-8")
    states = statemap.stage_states(d)
    assert [s["name"] for s in states] == ["gather", "act", "extra"]
    assert states[1]["desc"] == "Step: apply the fixes"
    assert states[0]["desc"] == ""   # no heading — no description
    # 'act' matched its own mention, not the 'practices' substring (word-ish boundaries)
    assert statemap.module_rank((d / "main.md").read_text(encoding="utf-8"), "act") \
        > statemap.module_rank((d / "main.md").read_text(encoding="utf-8"), "gather")


def test_stage_states_file_reference_outranks_name_drop(tmp_path):
    """Intro prose that name-drops a module ("… until record finishes the run") must not
    steal its flow position — the module's `<stem>.md` FILE reference is the order signal."""
    d = tmp_path / "r"
    (d / "stages").mkdir(parents=True)
    (d / "stages" / "orient.md").write_text("# Step: orient\n", encoding="utf-8")
    (d / "stages" / "record.md").write_text("# Step: record\n", encoding="utf-8")
    (d / "main.md").write_text(
        "Work until `record` finishes the run.\n\n## Run flow\n"
        "1. `stages/orient.md` — look around.\n2. `stages/record.md` — close.\n",
        encoding="utf-8")
    assert [s["name"] for s in statemap.stage_states(d)] == ["orient", "record"]


def test_state_graph_current_from_latest_run_status(tmp_path):
    """`current` is the LATEST run's recorded phase (status.json — the stage module the
    run last read, stamped by the executor); state/phase.json is recipe-private and does
    not drive the diagram."""
    d = tmp_path / "r"
    (d / "stages").mkdir(parents=True)
    (d / "stages" / "a-one.md").write_text("x", encoding="utf-8")
    (d / "stages" / "b-two.md").write_text("x", encoding="utf-8")
    (d / "main.md").write_text("# no flow prose needed", encoding="utf-8")
    (d / "state").mkdir()
    (d / "state" / "phase.json").write_text(json.dumps({"phase": "ignored"}), encoding="utf-8")
    old = d / "runs" / "20260701-070000"
    new = d / "runs" / "20260716-070000"
    for run, phase in ((old, "a-one"), (new, "b-two")):
        run.mkdir(parents=True)
        (run / "status.json").write_text(json.dumps({"phase": phase}), encoding="utf-8")
    g = statemap.state_graph(d)
    assert [s["name"] for s in g["states"]] == ["a-one", "b-two"]   # alphabetical: unmentioned
    assert g["current"] == "b-two"
    # missing/broken pieces degrade to empty, never raise
    assert statemap.state_graph(tmp_path / "absent") == {"states": [], "current": ""}
    (new / "status.json").write_text("not json", encoding="utf-8")
    assert statemap.state_graph(d)["current"] == ""


def test_outline_extracts_headings_skipping_fences():
    md = ("# Title\n\n## Run flow\n\n```python\n# not a heading\ndef run(): ...\n```\n\n"
          "## Completion criteria\n### Sub point\ntext\n#### deep\n")
    got = [(h["level"], h["text"]) for h in statemap.outline(md)]
    # H1 title excluded (levels 2-4 only); the # comment inside the ``` fence is NOT a heading
    assert got == [(2, "Run flow"), (2, "Completion criteria"), (3, "Sub point"), (4, "deep")]


def test_recipe_tree_orders_stages_by_run_flow(tmp_path):
    d = tmp_path / "r"
    (d / "stages").mkdir(parents=True)
    (d / "main.md").write_text(
        "## Run flow\n1. **collect** — c.\n2. **draft** — d.\n\n## Completion criteria\n- done\n",
        encoding="utf-8")
    (d / "stages" / "draft.md").write_text("## How\ndo it\n", encoding="utf-8")
    (d / "stages" / "collect.md").write_text("text\n", encoding="utf-8")
    (d / "stages" / "extra.md").write_text("no flow entry\n", encoding="utf-8")   # extras sort last
    tree = statemap.recipe_tree(d)
    assert tree["main"]["path"] == "main.md"
    assert [h["text"] for h in tree["main"]["outline"]] == ["Run flow", "Completion criteria"]
    # stages ordered by ## Run flow (collect, draft); extras with no flow entry appended
    assert [s["name"] for s in tree["stages"]] == ["collect", "draft", "extra"]
    assert [h["text"] for h in tree["stages"][1]["outline"]] == ["How"]


def test_phase_stats_aggregates_turns_tokens_time(tmp_path):
    """Per-phase instrumentation from a transcript: dispatch time lands on the acting
    phase, completion time on the phase that produced the next action, the tail after
    the last action on the last phase; tokens/cost sum per phase; unphased turns keep
    their own bucket."""
    import json

    from rsched.readmodels.statemap import phase_stats

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
    from rsched.readmodels.statemap import phase_stats

    assert phase_stats(tmp_path) == []


def test_stage_coverage_counts_a_stage_the_run_only_declared_via_phase_json(tmp_path):
    """A stage worked WITHOUT re-reading its module still counts as entered (F563).

    `phases_entered` is appended by fileops only when a run performs a `read_file` on
    `stages/<name>.md`, so the raw signal measures module RE-READS, not stage work. A
    routine whose recipe the model already holds routes by writing `state/phase.json`
    and never re-opens the module — and reported every stage skipped: measured across
    the fleet on 2026-09-26, 24 of 73 runs reported >=60% of declared stages skipped,
    with llmsectest-weekday reporting 0 of 7 entered on three runs of 404-423 turns.

    So a phase the run RECORDED for itself is coverage evidence too. This does not make
    phase.json drive the diagram's `current` (test_state_graph_current_from_latest_run_status
    still owns that boundary) — it only stops a worked stage being reported as skipped.
    """
    d = tmp_path / "r"
    (d / "stages").mkdir(parents=True)
    for stem in ("orient", "gather", "act", "record"):
        (d / "stages" / f"{stem}.md").write_text(f"# Step: {stem}\n", encoding="utf-8")
    (d / "main.md").write_text(
        "## Run flow\n1. `stages/orient.md`\n2. `stages/gather.md`\n"
        "3. `stages/act.md`\n4. `stages/record.md`\n", encoding="utf-8")

    # The run re-read only ONE module; it recorded the other two as its phase in turn.
    cov = statemap.stage_coverage(d, ["orient"], recorded=["gather", "act"])
    assert cov["declared"] == ["orient", "gather", "act", "record"]
    assert cov["entered"] == ["orient", "gather", "act"]   # flow order, not arrival order
    assert cov["skipped"] == ["record"]                    # the only one genuinely untouched

    # A recorded phase that is not a declared stage is not evidence about this recipe.
    assert statemap.stage_coverage(d, [], recorded=["not-a-stage"])["entered"] == []
    # Absent `recorded` keeps the old behaviour exactly — every caller need not pass it.
    assert statemap.stage_coverage(d, ["orient"])["skipped"] == ["gather", "act", "record"]
