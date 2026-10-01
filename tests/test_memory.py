"""memory_read / memory_write handlers: engine-owned .memory/ notes + INDEX.md upkeep."""

from helpers import run_context, server_config
from rsched.engine import executor
from rsched.engine.observations import format_observation


def _ctx(make_routine, tmp_path):
    return run_context(make_routine(slug="memr"), "20260712-070000",
                       server=server_config(libraries_home=tmp_path / "libraries"))


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


def test_a_multiline_about_stays_one_index_line(make_routine, tmp_path):
    """The upsert finds a note's entry by its `- <name>.md:` prefix, so an `about` with a line
    break left its continuation behind as an orphan that no revision or delete could reach."""
    ctx = _ctx(make_routine, tmp_path)
    index = ctx.routine.dir / ".memory" / "INDEX.md"
    _write(ctx, "portal", "x", "scraping gotchas:\n  rate limits\tand captchas")
    assert index.read_text() == "- portal.md: scraping gotchas: rate limits and captchas\n"
    _write(ctx, "portal", "x", "revised")
    executor.dispatch({"kind": "memory_write", "name": "other", "content": "y",
                       "about": "second"}, ctx)
    executor.dispatch({"kind": "memory_write", "name": "portal", "delete": True}, ctx)
    assert index.read_text() == "- other.md: second\n"


def test_read_missing_lists_topics_and_delete_is_idempotent(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path)
    _write(ctx, "one", "x", "first")
    obs = executor.dispatch({"kind": "memory_read", "name": "nope"}, ctx)
    assert obs["missing"] and obs["topics"] == ["one"]
    assert "Existing topics: one" in format_observation(obs)
    obs = executor.dispatch({"kind": "memory_write", "name": "nope", "delete": True}, ctx)
    assert obs["deleted"] and not obs["existed"]
