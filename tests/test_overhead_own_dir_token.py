"""A command that NAMES the routine's own directory is not thereby the task's work. `cd <dir> &&
<bookkeeping>` does exactly what the same command does without the prefix, so it has to classify
the same; before this, the prefix alone flipped a run's whole shell population from process to
task, hiding overhead where the invariant reads it and manufacturing regressions between runs
whose only difference was shell style.
"""

from rsched.engine.overhead import PROCESS, TASK, _bucket, _relative, classify

RD = "/home/mark/routines/tv-show-tracker-seedbox-manager"
INNER = 'grep -n "Leftovers" state/shows.json'


def test_the_own_dir_named_absolutely_is_relative_dot():
    assert _relative(RD, RD) == "."
    assert _relative(RD + "/", RD) == "."


def test_a_path_inside_the_own_dir_is_still_relative_to_it():
    assert _relative(RD + "/state/shows.json", RD) == "state/shows.json"
    assert _relative("/home/mark/routines/other/state/x.json", RD) is None


def test_a_cd_into_the_own_dir_classifies_as_the_bare_command_does():
    bare = classify({"kind": "shell", "command": INNER}, None, RD, "")
    prefixed = classify({"kind": "shell", "command": f"cd {RD} && {INNER}"}, None, RD, "")
    slashed = classify({"kind": "shell", "command": f"cd {RD}/ && {INNER}"}, None, RD, "")
    assert bare == PROCESS
    assert prefixed == bare
    assert slashed == bare


def test_a_cd_into_the_own_dir_does_not_make_a_task_command_process():
    cmd = f"cd {RD} && python3 scripts/fetch_episodes.py --feed https://example.invalid/rss"
    assert classify({"kind": "shell", "command": cmd}, None, RD, "") == TASK


def test_a_cd_into_someone_elses_directory_is_still_named_and_still_counts():
    other = "/home/mark/routines/folder-reorg"
    assert _relative(other, RD) is None
    assert _bucket(other, RD, "") == TASK
    cmd = f"cd {other} && cat state/plan.md"
    assert classify({"kind": "shell", "command": cmd}, None, RD, "") == TASK
