"""The merge step that assigns the version and classifies what main gained under a candidate."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

MERGE = Path(__file__).resolve().parents[1] / "deploy/merge-to-main.py"


def _load():
    """The script, imported by path — and REGISTERED, which a dataclass inside it requires.

    `@dataclass` resolves `cls.__module__` through `sys.modules` to find a `KW_ONLY` sentinel,
    so a module that was executed but never registered raises `AttributeError: 'NoneType' object
    has no attribute '__dict__'` at class-creation time, which pytest reports as a COLLECTION
    error with no test having run.
    """
    spec = importlib.util.spec_from_file_location("merge_to_main", MERGE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


merge_to_main = _load()


def _changelog(*versions: str) -> str:
    body = "\n\n".join(f"## [{v}] — 2026-11-22\n\n### Fixed\n- something" for v in versions)
    return f"# Changelog\n\n{body}\n"


# ---- the version is assigned at MERGE time ----------------------------------------------

def test_the_next_version_is_the_minor_above_what_main_already_shipped():
    assert merge_to_main.next_version(_changelog("0.583.0", "0.582.0")) == "0.584.0"


def test_a_candidate_whose_number_main_took_is_renumbered_above_it():
    """The whole point: a candidate that called itself 0.581.0 while main shipped 0.583.0 lands
    as 0.584.0 without anyone resolving a conflict by hand."""
    main_log = _changelog("0.583.0", "0.582.0", "0.581.0")
    candidate = _changelog("0.581.0", "0.580.0")
    version = merge_to_main.next_version(main_log)
    log, init = merge_to_main.renumber(candidate, '__version__ = "0.581.0"\n',
                                       version, "2026-11-22")
    assert version == "0.584.0"
    assert log.splitlines()[2] == "## [0.584.0] — 2026-11-22"
    assert init.strip() == '__version__ = "0.584.0"'


def test_only_the_top_header_is_renumbered():
    """Every header below the top is history main already carries; rewriting one would make the
    file repeat a version, which the repo's own policy test reads the whole file to catch."""
    log, _ = merge_to_main.renumber(_changelog("0.581.0", "0.580.0"),
                                    '__version__ = "0.581.0"\n', "0.584.0", "2026-11-22")
    assert "## [0.584.0] — 2026-11-22" in log
    assert "## [0.580.0] — 2026-11-22" in log
    assert "## [0.581.0]" not in log


def test_the_renumbered_file_still_matches_the_header_grammar_the_policy_test_pins():
    log, _ = merge_to_main.renumber(_changelog("0.581.0"), '__version__ = "0.581.0"\n',
                                    "0.584.0", "2026-11-22")
    assert merge_to_main.HEADER.search(log).groups() == ("0", "584", "0", "2026-11-22")


def test_a_changelog_with_no_release_header_is_refused_not_guessed():
    with pytest.raises(merge_to_main.MergeRefused):
        merge_to_main.next_version("# Changelog\n\nnothing shipped yet\n")


def test_a_version_declared_twice_or_never_is_refused():
    """`__version__` is the single source of truth; two of them means the rewrite would leave
    one behind, and none means this is not the file the caller thinks it is."""
    for init in ("x = 1\n", '__version__ = "a"\n__version__ = "b"\n'):
        with pytest.raises(merge_to_main.MergeRefused):
            merge_to_main.renumber(_changelog("0.581.0"), init, "0.584.0", "2026-11-22")


# ---- the re-gate rule is code, not judgement --------------------------------------------

def test_a_prose_only_delta_owes_no_re_gate():
    delta = merge_to_main.classify(["CHANGELOG.md", "docs/designs.md", "README.md"])
    assert delta.kind == "prose"
    assert "nothing" in delta.owes


def test_leaf_test_modules_owe_only_themselves_and_quality():
    delta = merge_to_main.classify(["tests/test_loop.py", "tests/ui/test_flows.py", "docs/a.md"])
    assert delta.kind == "tests"
    assert delta.modules == ["tests/test_loop.py", "tests/ui/test_flows.py"]
    assert "quality" in delta.owes


@pytest.mark.parametrize("name", [
    "src/rsched/engine/loop.py",          # source anything reaches
    "static/components/transcript.js",    # the browser suite executes it
    "tests/conftest.py",                  # every test is handed its fixtures
    "tests/ui/conftest.py",               # the same, for the browser suite
    "tests/helpers.py",                   # a shared builder many modules import
    "tests/ui/helpers.py",                # the browser suite's own shared helper
    "pyproject.toml",                     # config: the lint gates and the suite's own options
    "Dockerfile",                         # the interpreter the tests run on
])
def test_anything_that_reaches_every_test_owes_a_full_re_gate(name):
    delta = merge_to_main.classify(["docs/a.md", name])
    assert delta.kind == "source", f"{name} was classed {delta.kind}"
    assert "full re-gate" in delta.owes


def test_an_unmoved_main_owes_nothing_at_all():
    delta = merge_to_main.classify([])
    assert delta.kind == "prose"
    assert delta.files == []


def test_the_class_is_the_most_expensive_file_in_the_delta():
    """A delta is answered as a whole: one source file among fifty prose files still owes the
    full suite, because the cheap answer would skip exactly the file that can break it."""
    files = [f"docs/{n}.md" for n in range(50)] + ["src/rsched/engine/loop.py"]
    assert merge_to_main.classify(files).kind == "source"


# ---- the lease is taken only by a green candidate ---------------------------------------

def _evidence(tmp_path: Path, stamp: str, summary: dict) -> Path:
    evidence = tmp_path / ".audit-wt" / ".gate-evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / f"{stamp}.result.json").write_text(json.dumps(summary), encoding="utf-8")
    return tmp_path


def test_a_green_stamp_over_this_tree_is_accepted(tmp_path):
    tree = tmp_path / "wt"
    tree.mkdir()
    repo = _evidence(tmp_path, "111", {"verdict": "green", "root": str(tree)})
    assert merge_to_main.gate_verdict(repo, "111", tree)["verdict"] == "green"


def test_a_flaky_stamp_is_accepted_because_only_the_gate_may_call_a_failure_flaky(tmp_path):
    tree = tmp_path / "wt"
    tree.mkdir()
    repo = _evidence(tmp_path, "111", {"verdict": "flaky", "root": str(tree)})
    assert merge_to_main.gate_verdict(repo, "111", tree)["verdict"] == "flaky"


@pytest.mark.parametrize("verdict", ["red", "timeout", "started", "unfinished"])
def test_a_stamp_that_is_not_green_takes_no_lease(tmp_path, verdict):
    tree = tmp_path / "wt"
    tree.mkdir()
    repo = _evidence(tmp_path, "111", {"verdict": verdict, "root": str(tree)})
    with pytest.raises(merge_to_main.MergeRefused, match="not green"):
        merge_to_main.gate_verdict(repo, "111", tree)


def test_a_green_stamp_from_a_different_tree_is_refused(tmp_path):
    """A stamp covers BYTES. A sibling campaign's green verdict says nothing about this
    candidate, and accepting it would merge untested code with a verdict attached."""
    tree, other = tmp_path / "wt", tmp_path / "elsewhere"
    tree.mkdir()
    other.mkdir()
    repo = _evidence(tmp_path, "111", {"verdict": "green", "root": str(other)})
    with pytest.raises(merge_to_main.MergeRefused, match="ran over"):
        merge_to_main.gate_verdict(repo, "111", tree)


def test_a_stamp_with_no_recorded_result_is_refused(tmp_path):
    with pytest.raises(merge_to_main.MergeRefused, match="no recorded result"):
        merge_to_main.gate_verdict(tmp_path, "nosuch", tmp_path)


# ---- the CLI's own contract --------------------------------------------------------------

def test_merge_without_a_gate_stamp_is_a_usage_error():
    proc = subprocess.run([str(MERGE), "merge", "--branch", "audit/x"],
                          capture_output=True, text=True, check=False, timeout=60)
    assert proc.returncode == 2
    assert "--gate-stamp" in proc.stderr


def test_a_refusal_prints_its_reason_and_exits_one_rather_than_raising(tmp_path):
    proc = subprocess.run([str(MERGE), "merge", "--repo", str(tmp_path), "--branch", "audit/x",
                           "--gate-stamp", "nosuch"],
                          capture_output=True, text=True, check=False, timeout=60)
    assert proc.returncode == 1
    assert "merge-to-main:" in proc.stderr
    assert "nosuch" in proc.stderr


# ---- WHICH checkout is the main one -------------------------------------------------------
#
# The first real use of this script, 0.588.0's own release, answered 0.589.0 when main had
# shipped 0.587.0. The script lives in `deploy/`, so the candidate's worktree holds a copy, that
# copy is what a run calls, and `parents[1]` therefore resolved to the WORKTREE: the next version
# came from the candidate's own CHANGELOG (the number it had just written), the gate evidence was
# looked for under a path only the main checkout has, and the fast-forward would have run in a
# tree that already had the candidate branch checked out — merging nothing while reporting
# success. These hold the resolution from both ends.

def _repo_with_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """A real repository with a `main` branch and one worktree on a branch of its own."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *args, cwd=repo: subprocess.run(  # noqa: E731 — one local shorthand
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True, timeout=60)
    run("init", "--quiet", "--initial-branch=main")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (repo / "CHANGELOG.md").write_text(_changelog("0.587.0", "0.586.0"), encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src/rsched").mkdir()
    (repo / "src/rsched/__init__.py").write_text('__version__ = "0.587.0"\n', encoding="utf-8")
    run("add", "-A")
    run("commit", "--quiet", "-m", "main at 0.587.0")
    tree = repo / ".audit-wt" / "cand"
    run("worktree", "add", "--quiet", "-b", "audit/cand", str(tree))
    return repo, tree


def test_the_main_checkout_is_found_from_inside_a_worktree_not_assumed_to_be_the_cwd(tmp_path):
    repo, tree = _repo_with_worktree(tmp_path)
    assert merge_to_main.main_worktree(tree).resolve() == repo.resolve()
    assert merge_to_main.main_worktree(repo).resolve() == repo.resolve()


def test_the_next_version_comes_from_mains_ref_even_when_the_candidate_renumbered_itself(tmp_path):
    """The whole point: a candidate that wrote 0.588.0 into its own CHANGELOG must still be
    numbered against what MAIN shipped (0.587.0), not against its own file."""
    repo, tree = _repo_with_worktree(tmp_path)
    (tree / "CHANGELOG.md").write_text(_changelog("0.588.0", "0.587.0"), encoding="utf-8")
    assert merge_to_main.next_version(merge_to_main.main_changelog(repo)) == "0.588.0"
    assert merge_to_main.next_version(merge_to_main.main_changelog(tree)) == "0.588.0"


def test_a_dirty_main_checkout_does_not_change_the_number_either(tmp_path):
    """`main`'s CHANGELOG is read out of the REF: a session mid-release has already edited the
    file in the main checkout, and that edit is not something main has shipped."""
    repo, _tree = _repo_with_worktree(tmp_path)
    (repo / "CHANGELOG.md").write_text(_changelog("0.599.0", "0.587.0"), encoding="utf-8")
    assert merge_to_main.next_version(merge_to_main.main_changelog(repo)) == "0.588.0"


def test_merging_into_the_tree_that_holds_the_candidate_is_refused(tmp_path):
    repo, _tree = _repo_with_worktree(tmp_path)
    _evidence(repo, "111", {"verdict": "green", "root": str(repo)})
    with pytest.raises(merge_to_main.MergeRefused, match="same directory"):
        merge_to_main.merge(repo, repo, "audit/cand", "111")


def test_a_main_checkout_on_another_branch_is_refused(tmp_path):
    """A fast-forward lands on whatever HEAD is: a main checkout parked on another branch would
    take the candidate there instead, and the push would report success."""
    repo, tree = _repo_with_worktree(tmp_path)
    _evidence(repo, "111", {"verdict": "green", "root": str(tree)})
    subprocess.run(["git", "-C", str(repo), "checkout", "--quiet", "-b", "parked"],
                   capture_output=True, text=True, check=True, timeout=60)
    with pytest.raises(merge_to_main.MergeRefused, match="not main"):
        merge_to_main.merge(repo, tree, "audit/cand", "111")
