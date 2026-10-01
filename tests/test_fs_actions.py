"""The native filesystem actions (D120=A): delete / move / mkdir — the same write-root
jail and the same seals as write_file (fileops._write_gate), fs-ops' proven semantics
(recursive required for a tree, move never overwrites, mkdir -p), and the destructive-op
grounding rule: a path OUTSIDE the routine's own dir that this run has not read cannot be
deleted or moved away.

Validation lives in engine.actions.validate_action; the handlers in engine.fsops.
"""

from __future__ import annotations

from helpers import action_ctx
from rsched.engine.actions import validate_action
from rsched.engine.fileops import do_read_file
from rsched.engine.fsops import do_delete, do_mkdir, do_move

# ---- validation -----------------------------------------------------------------------------


def test_validation_requires_the_right_fields():
    assert validate_action({"kind": "delete", "say": "x"})
    assert not validate_action({"kind": "delete", "say": "x", "path": "state/old.json"})
    assert validate_action({"kind": "move", "say": "x", "src": "a", "dst": "b",
                            "path": "rogue"})            # stray field rejected
    assert validate_action({"kind": "mkdir", "say": "x"})   # path required
    assert validate_action({"kind": "mkdir", "say": "x", "path": "state/new",
                            "recursive": True})         # recursive is delete's, not mkdir's


def test_validation_refuses_memory_paths_for_all_three():
    for kind, action in (("delete", {"kind": "delete", "path": ".memory/INDEX.md"}),
                         ("move", {"kind": "move", "src": ".memory/note.md",
                                   "dst": "state/note.md"}),
                         ("mkdir", {"kind": "mkdir", "path": ".memory/new"})):
        problems = validate_action(action)
        assert any("may not touch .memory/" in p for p in problems), kind


# ---- handlers: happy paths -----------------------------------------------------------------


def test_mkdir_parents_and_existing(make_routine, tmp_path):
    ctx = action_ctx(make_routine(), tmp_path)
    deep = "state/a/b/c"
    assert do_mkdir({"path": deep, "parents": True}, ctx)["created"] is True
    assert (ctx.routine.dir / deep).is_dir()
    # plain mkdir on the existing path is an error; parents: true treats it as success
    assert "error" in do_mkdir({"path": deep}, ctx)
    assert do_mkdir({"path": deep, "parents": True}, ctx)["created"] is False


def test_move_relocates_and_refuses_overwrite(make_routine, tmp_path):
    ctx = action_ctx(make_routine(), tmp_path)
    src = ctx.routine.dir / "state" / "draft.md"
    src.parent.mkdir(exist_ok=True)
    src.write_text("hello", encoding="utf-8")
    obs = do_move({"src": "state/draft.md", "dst": "state/final/draft.md"}, ctx)
    assert obs["moved"] is True and obs["bytes_moved"] == 5
    assert not src.exists() and (ctx.routine.dir / "state/final/draft.md").read_text(
        encoding="utf-8") == "hello"
    # a second move onto the SAME dst is refused — move never overwrites
    again = ctx.routine.dir / "state" / "draft2.md"
    again.write_text("x", encoding="utf-8")
    err = do_move({"src": "state/draft2.md", "dst": "state/final/draft.md"}, ctx)
    assert "already exists" in err["error"] and again.exists()


def test_delete_file_and_directory_tree(make_routine, tmp_path):
    ctx = action_ctx(make_routine(), tmp_path)
    f = ctx.routine.dir / "state" / "old.json"
    f.parent.mkdir(exist_ok=True)
    f.write_text("x" * 10, encoding="utf-8")
    assert do_delete({"path": "state/old.json"}, ctx)["bytes_freed"] == 10
    assert not f.exists()
    tree = ctx.routine.dir / "state" / "stale"
    tree.mkdir()
    (tree / "a").write_text("y" * 4, encoding="utf-8")
    (tree / "b").write_text("z" * 6, encoding="utf-8")
    # a directory without recursive: true is refused
    assert "recursive: true" in do_delete({"path": "state/stale"}, ctx)["error"]
    gone = do_delete({"path": "state/stale", "recursive": True}, ctx)
    assert gone["type"] == "dir" and gone["bytes_freed"] == 10 and not tree.exists()


def test_a_not_found_refusal_names_the_read_that_would_show_what_is_there(make_routine, tmp_path):
    """Every refusal carries its own way out — the setup surface's `fix` discipline, applied to
    the one family that lacked it. "no such path" states a fact the run already suspected and
    leaves it guessing at the name; the parent directory's LISTING is the answer, and read_file
    returns one for a directory path.
    """
    ctx = action_ctx(make_routine(), tmp_path)
    err = do_delete({"path": "state/nope.json"}, ctx)["error"]
    assert "no such path" in err and "read_file its parent directory" in err
    err = do_move({"src": "state/nope.json", "dst": "state/x.json"}, ctx)["error"]
    assert "no such source path" in err and "read_file its parent directory" in err


# ---- the seals ------------------------------------------------------------------------------


def test_runs_and_routine_yaml_are_sealed(make_routine, tmp_path):
    ctx = action_ctx(make_routine(), tmp_path)
    assert "runs/ is engine-owned" in do_delete(
        {"path": "runs/20260716-070000", "recursive": True}, ctx)["error"]
    assert "routine.yaml is config" in do_delete({"path": "routine.yaml"}, ctx)["error"]
    assert "routine.yaml is config" in do_move(
        {"src": "routine.yaml", "dst": "state/config-copy.yaml"}, ctx)["error"]


def test_a_removal_never_takes_a_sealed_path_with_it(make_routine, tmp_path):
    """Every seal asked whether a path lies INSIDE something sealed; a removal also takes what
    lies inside the path. `delete path: "." recursive: true` passed every seal and removed the
    whole routine — routine.yaml, .memory/, runs/ with the live transcript, .git."""
    ctx = action_ctx(make_routine(), tmp_path)
    d = ctx.routine.dir
    for whole in (".", "state/.."):
        err = do_delete({"path": whole, "recursive": True}, ctx)["error"]
        assert "a routine's own directory" in err, whole
    # by its absolute name the entry's PARENT is the routines home, outside every root
    assert "outside the allowed roots" in do_delete({"path": str(d), "recursive": True},
                                                    ctx)["error"]
    assert "a routine's own directory" in do_move(
        {"src": ".", "dst": "state/elsewhere"}, ctx)["error"]
    assert (d / "routine.yaml").is_file() and (d / "runs").is_dir()
    # the operator's finish line goes with no removal of the directory holding it…
    (d / "state" / "finish-line.json").write_text('{"outcomes": []}\n', encoding="utf-8")
    assert "finish line is the operator's" in do_delete(
        {"path": "state", "recursive": True}, ctx)["error"]
    assert (d / "state" / "finish-line.json").is_file()
    # …and no tree holding a routine's config is removed with it (by name, like every write)
    other = tmp_path / "projects" / "nested-routine"
    other.mkdir(parents=True)
    (other / "routine.yaml").write_text("slug: nested-routine\n", encoding="utf-8")
    ctx.routine.fs_write_roots = [tmp_path / "projects"]
    ctx.seen_paths.add(str(other))
    assert "routine.yaml is config" in do_delete(
        {"path": str(other), "recursive": True}, ctx)["error"]
    assert (other / "routine.yaml").is_file()


def test_delete_and_move_act_on_a_link_not_on_what_it_points_to(make_routine, tmp_path):
    """`rm link` and `mv link x` semantics. Resolving the whole path followed the link, so
    deleting one deleted its target and moving one moved the target away — each leaving the
    link behind, dangling."""
    ctx = action_ctx(make_routine(), tmp_path)
    data = ctx.routine.dir / "state" / "data"
    data.mkdir(parents=True)
    (data / "keep.txt").write_text("precious", encoding="utf-8")
    (ctx.routine.dir / "state" / "alias.txt").symlink_to(data / "keep.txt")
    (ctx.routine.dir / "state" / "shortcut").symlink_to(data)
    gone = do_delete({"path": "state/alias.txt"}, ctx)
    assert gone["type"] == "link"
    assert not (ctx.routine.dir / "state" / "alias.txt").is_symlink()
    assert (data / "keep.txt").read_text(encoding="utf-8") == "precious"
    assert do_move({"src": "state/shortcut", "dst": "state/renamed"}, ctx)["moved"] is True
    assert (ctx.routine.dir / "state" / "renamed").is_symlink() and data.is_dir()
    # a link outside the own dir destroys nothing but itself, so it needs no prior read…
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "dangling").symlink_to(tmp_path / "nowhere")
    ctx.routine.fs_write_roots = [outside]
    assert do_delete({"path": str(outside / "dangling")}, ctx)["removed"] is True


# ---- destructive-op grounding --------------------------------------------------------------


def test_unseen_outside_dir_delete_and_move_are_refused(make_routine, tmp_path):
    """The write_file overwrite-gate reasoning, extended to destruction: a path OUTSIDE
    the routine's own dir that this run has never read cannot be deleted or moved away —
    the model must have looked at what it destroys. Reading it once lifts the gate."""
    ctx = action_ctx(make_routine(), tmp_path)
    ext = tmp_path / "conversations" / "c-1"
    ext.mkdir(parents=True)
    ctx.routine.fs_write_roots = [tmp_path / "conversations"]
    target = ext / "LEDGER.md"
    target.write_text("precious", encoding="utf-8")

    err = do_delete({"path": str(target)}, ctx)
    assert "never read" in err["error"] and target.exists()
    err = do_move({"src": str(target), "dst": str(ext / "moved.md")}, ctx)
    assert "never read" in err["error"] and target.exists()

    ctx.seen_paths.add(str(target))                # read it (read_file does this)
    assert do_move({"src": str(target), "dst": str(ext / "moved.md")}, ctx)["moved"] is True
    moved = ext / "moved.md"
    ctx.seen_paths.add(str(moved))                 # the run also read what it now deletes
    assert do_delete({"path": str(moved)}, ctx)["removed"] is True


def test_a_listing_or_a_size_read_grounds_destruction(make_routine, tmp_path):
    """The 2026-09-14 chain, closed: `delete` of a season-pack directory was refused (never
    read), `read_file` on the directory errored (Is a directory), a shell `ls` did not count,
    so the run `read_file`'d a 1.5 GB .mkv and the host swap-thrashed. A directory now reads
    as its LISTING and a binary/oversized file as its SIZE — both are a look, both satisfy
    the gate, and neither decodes a byte of media. The gate itself is unchanged: a path this
    run has not pointed read_file at is still refused."""
    ctx = action_ctx(make_routine(), tmp_path)
    videos = tmp_path / "videos"
    pack = videos / "Show S01"
    pack.mkdir(parents=True)
    (pack / "e01.mkv").write_bytes(b"\x1aE\xdf\xa3" + b"\x00" * 64)
    loose = videos / "loose.mkv"
    loose.write_bytes(b"\x00" * 64)
    ctx.routine.fs_write_roots = [videos]

    err = do_delete({"path": str(pack), "recursive": True}, ctx)["error"]
    assert "never read" in err and "a directory reads as its listing" in err
    obs = do_read_file({"path": str(pack)}, ctx)
    assert obs["directory"] is True and "e01.mkv" in obs["content"]
    assert do_delete({"path": str(pack), "recursive": True}, ctx)["removed"] is True
    assert not pack.exists()

    assert "never read" in do_delete({"path": str(loose)}, ctx)["error"]
    refusal = do_read_file({"path": str(loose)}, ctx)
    assert refusal["error"].startswith("binary file (64 bytes)") and refusal["size"] == 64
    assert do_delete({"path": str(loose)}, ctx)["removed"] is True
