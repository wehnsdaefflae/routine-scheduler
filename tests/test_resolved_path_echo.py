"""A relative path that lands in a GRANTED ROOT says so, and a jailed path says it was the jail
— the two halves of a failure that read as a missing file. A run created a directory, wrote into
it, handed the same relative path to a util whose cwd is the working directory, and got
`FileNotFoundError`: the resolver had put the file in another granted root and nothing said so.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from helpers import action_ctx
from rsched import toolchainroots
from rsched.engine import jailnotes, pathecho
from rsched.engine.actionroute import dispatch_action
from rsched.sandbox import SandboxPolicy


class _Loop:
    """What `dispatch_action` reads off a loop for a file action: nothing. It is passed through
    to the handlers, which take only the action and the context."""


@pytest.fixture
def ctx_with_root(make_routine, tmp_path):
    """A run in its routine dir, with `tmp_path/repo` granted as a write root and `repo/work`
    the only `work/` on disk — the shape that makes a relative path resolve away from home."""
    ctx = action_ctx(make_routine(), tmp_path)
    repo = tmp_path / "repo"
    (repo / "work").mkdir(parents=True)
    ctx.routine.fs_write_roots = [repo]
    return ctx, repo


def _dispatch(ctx, action):
    return dispatch_action(_Loop(), action, ctx)


# ---- where the path landed ------------------------------------------------------------------

def test_a_write_into_a_granted_root_says_where_it_went(ctx_with_root):
    """The defect's first half: the observation echoed the relative path back unchanged, so the
    next step read it as the working directory."""
    ctx, repo = ctx_with_root

    obs = _dispatch(ctx, {"kind": "write_file", "path": "work/original.md", "content": "hi\n"})

    assert "error" not in obs, obs.get("error")
    assert obs["resolved_path"] == str(repo / "work" / "original.md")
    assert "cwd" in obs["resolved_note"], obs["resolved_note"]


def test_a_mkdir_in_a_granted_root_says_where_it_went(ctx_with_root):
    """The action that started it: `mkdir tmp/x` created the directory in another root, and the
    run reasoned from then on as if it were in its own dir."""
    ctx, repo = ctx_with_root

    obs = _dispatch(ctx, {"kind": "mkdir", "path": "work/sub", "parents": True})

    assert obs.get("created") is True
    assert obs["resolved_path"] == str(repo / "work" / "sub")


def test_a_read_in_the_working_directory_says_nothing(ctx_with_root):
    """The ordinary case must stay silent — every `state/x` a run touches goes through here."""
    ctx, _repo = ctx_with_root
    (ctx.routine.dir / "state" / "own.md").write_text("mine\n", encoding="utf-8")

    obs = _dispatch(ctx, {"kind": "read_file", "path": "state/own.md"})

    assert "resolved_path" not in obs and "resolved_note" not in obs


def test_an_absolute_path_says_nothing(ctx_with_root):
    """An absolute path was never ambiguous: the caller already knows where it is."""
    ctx, repo = ctx_with_root
    target = repo / "work" / "abs.md"
    target.write_text("there\n", encoding="utf-8")

    obs = _dispatch(ctx, {"kind": "read_file", "path": str(target)})

    assert "resolved_path" not in obs


def test_a_move_names_both_ends_that_left_the_working_directory(ctx_with_root):
    """`move` carries src/dst rather than path, and either end can resolve elsewhere. The read
    first is the grounding gate's, not this test's subject: moving a path outside the own dir
    requires having seen it."""
    ctx, repo = ctx_with_root
    (repo / "work" / "from.md").write_text("x\n", encoding="utf-8")
    _dispatch(ctx, {"kind": "read_file", "path": "work/from.md"})

    obs = _dispatch(ctx, {"kind": "move", "src": "work/from.md", "dst": "state/to.md"})

    assert obs.get("moved") is True
    assert obs["resolved_src"] == str(repo / "work" / "from.md")
    assert "resolved_dst" not in obs      # state/ is the working directory's own


def test_the_annotation_never_breaks_an_action():
    """It runs before EVERY dispatch and callers hand in context stand-ins carrying only what
    their own subject needs, so a context it cannot read must leave the observation alone
    rather than raise."""
    obs = {"kind": "write_file", "ok": True}

    assert pathecho.annotate({"kind": "write_file", "path": "work/x.md"}, obs, object()) is obs
    assert "resolved_path" not in obs


def test_the_resolution_itself_swallows_a_context_it_cannot_read():
    """The INNER layer, alone: `_elsewhere` has no guard of its own, so a stand-in context
    reaching it proves the resolution's catch rather than the outer one."""
    assert pathecho._elsewhere(object(), "work/x.md", write=True) == ""


# ---- and when the JAIL was the reason ------------------------------------------------------

@pytest.fixture
def narrow_toolchain(monkeypatch):
    """Every test path lives under `/tmp`, which IS a jail-RW toolchain root — so with the real
    table no temp path is ever outside the jail (correctly: a command CAN read /tmp). The
    toolchain is narrowed here so the subject under test is the GRANTED roots alone."""
    monkeypatch.setattr(toolchainroots, "toolchain", lambda: ([], []))


@pytest.mark.usefixtures("narrow_toolchain")
def test_a_blocked_path_is_named_as_the_jail_not_as_a_missing_file(tmp_path):
    """The reporter's fallback: a path that EXISTS on the host but outside the jail must be
    distinguishable from a wrong path, because Landlock's EACCES says neither."""
    own = tmp_path / "routines" / "r"
    own.mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    target = outside / "secret.md"
    target.write_text("x\n", encoding="utf-8")
    policy = SandboxPolicy(mode="permissive", own_dir=own)

    notes = jailnotes.jail_notes(policy, "", f"PermissionError: {target}", 1)

    assert notes["blocked_paths"]["paths"] == [str(target)]
    assert "OUTSIDE" in notes["blocked_paths"]["note"]


@pytest.mark.usefixtures("narrow_toolchain")
def test_a_path_that_is_simply_absent_is_not_blamed_on_the_jail(tmp_path):
    """Existence is the whole distinction: calling a missing file a jail denial would send the
    reader after the wrong repair."""
    own = tmp_path / "routines" / "r"
    own.mkdir(parents=True)
    policy = SandboxPolicy(mode="permissive", own_dir=own)

    gone = tmp_path / "elsewhere" / "never-written.md"
    notes = jailnotes.jail_notes(policy, "", f"FileNotFoundError: {gone}", 1)

    assert "blocked_paths" not in notes


@pytest.mark.usefixtures("narrow_toolchain")
def test_a_path_inside_the_jail_is_never_reported_as_blocked(tmp_path):
    """A path under the routine's own dir or a granted root is mounted, so an error naming it
    is the command's own business."""
    own = tmp_path / "routines" / "r"
    (own / "state").mkdir(parents=True)
    inside = own / "state" / "mine.md"
    inside.write_text("x\n", encoding="utf-8")
    policy = SandboxPolicy(mode="permissive", own_dir=own)

    notes = jailnotes.jail_notes(policy, "", f"OSError: {inside}", 1)

    assert "blocked_paths" not in notes


@pytest.mark.usefixtures("narrow_toolchain")
def test_a_successful_call_is_asked_nothing(tmp_path):
    """The question is only asked of a FAILED call: a path mentioned in passing by a command
    that worked is not a diagnosis of anything."""
    own = tmp_path / "routines" / "r"
    own.mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "x.md").write_text("x\n", encoding="utf-8")
    policy = SandboxPolicy(mode="permissive", own_dir=own)

    assert "blocked_paths" not in jailnotes.jail_notes(
        policy, f"read {outside / 'x.md'}", "", 0)


def test_the_toolchain_roots_are_inside_every_jail(tmp_path):
    """The temp dir and the interpreter's own tree are mounted for every command, so an error
    naming one of them is never a jail denial — the roots reading has to include them, and this
    one deliberately uses the REAL table rather than the narrowed one."""
    own = tmp_path / "routines" / "r"
    own.mkdir(parents=True)
    policy = SandboxPolicy(mode="permissive", own_dir=own)
    scratch = Path(tempfile.gettempdir())

    assert jailnotes.blocked_paths(policy, f"OSError: {scratch}") == []


@pytest.mark.usefixtures("narrow_toolchain")
def test_at_most_a_handful_of_blocked_paths_are_named(tmp_path):
    """A failing command can print the same kind of path hundreds of times; the first few
    distinct ones are the diagnosis and the rest are budget."""
    own = tmp_path / "routines" / "r"
    own.mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    printed = []
    for i in range(jailnotes.MAX_BLOCKED + 3):
        p = outside / f"f{i}.md"
        p.write_text("x\n", encoding="utf-8")
        printed.append(str(p))
    policy = SandboxPolicy(mode="permissive", own_dir=own)

    assert len(jailnotes.blocked_paths(policy, " ".join(printed))) == jailnotes.MAX_BLOCKED
