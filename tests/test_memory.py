"""memory_read / memory_write handlers: engine-owned .memory/ notes + INDEX.md upkeep."""

from rsched.config import ServerConfig, load_routine
from rsched.engine import executor
from rsched.engine.observations import format_observation
from rsched.engine.run_context import Budgets, RunContext
from rsched.engine.transcript import Transcript


def _ctx(make_routine, tmp_path):
    d = make_routine(slug="memr")
    cfg, _problems = load_routine(d)
    assert cfg is not None
    run_dir = d / "runs" / "20260712-070000"
    run_dir.mkdir(parents=True)
    server = ServerConfig()
    server.libraries_home = tmp_path / "libraries"
    return RunContext(routine=cfg, server=server, registry=None, run_ts="20260712-070000",
                      run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                      budgets=Budgets.from_config(cfg.budgets))


def _write(ctx, name, content, about):
    return executor.dispatch({"kind": "memory_write", "name": name,
                              "content": content, "about": about}, ctx)


def test_write_read_revise_delete_maintain_index(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path)
    mem = ctx.routine.dir / ".memory"

    obs = _write(ctx, "portal-quirks", "# quirks\n- portal X blocks headless", "scraping gotchas")
    assert obs["created"] and obs["lines"] == 2
    assert (mem / "portal-quirks.md").read_text() == "# quirks\n- portal X blocks headless\n"
    assert (mem / "INDEX.md").read_text() == "- portal-quirks.md: scraping gotchas\n"
    assert "INDEX.md updated" in format_observation(obs)

    _write(ctx, "scoring", "# scoring", "what 8/10 means")
    obs = _write(ctx, "portal-quirks", "# quirks v2", "scraping gotchas, incl. rate limits")
    assert not obs["created"]
    index = (mem / "INDEX.md").read_text().splitlines()
    assert index == ["- scoring.md: what 8/10 means",
                     "- portal-quirks.md: scraping gotchas, incl. rate limits"]

    obs = executor.dispatch({"kind": "memory_read", "name": "portal-quirks"}, ctx)
    assert obs["content"].startswith("# quirks v2") and obs["lines"] == 1
    assert "# quirks v2" in format_observation(obs)

    obs = executor.dispatch({"kind": "memory_write", "name": "scoring", "delete": True}, ctx)
    assert obs["deleted"] and obs["existed"]
    assert not (mem / "scoring.md").exists()
    assert (mem / "INDEX.md").read_text() == "- portal-quirks.md: scraping gotchas, incl. rate limits\n"


def test_read_missing_lists_topics_and_delete_is_idempotent(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path)
    _write(ctx, "one", "x", "first")
    obs = executor.dispatch({"kind": "memory_read", "name": "nope"}, ctx)
    assert obs["missing"] and obs["topics"] == ["one"]
    assert "Existing topics: one" in format_observation(obs)
    obs = executor.dispatch({"kind": "memory_write", "name": "nope", "delete": True}, ctx)
    assert obs["deleted"] and not obs["existed"]
