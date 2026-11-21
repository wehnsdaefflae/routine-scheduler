"""`marks_a_write()` shape pins — the noise cases and the shape that never fired (F730)."""

from __future__ import annotations

import pytest

from rsched.engine.shellwrite import marks_a_write

# Commands that must NOT be marked: reads, probes and state-filtering one-liners. Each is either
# a real fleet command of 2026-10-19 or the minimal shape of one.
READS = [
    pytest.param('ls "$d" >/dev/null 2>&1 && echo ok', id="stdout-discarded-probe"),
    pytest.param("ls /tmp 1>/dev/null", id="fd-1-discarded"),
    pytest.param("cat state/x 2>/dev/null; true", id="stderr-discarded"),
    pytest.param("python3 -c \"\nfor r in rows:\n    if r.get('ts','') >= '2026-10-19': t += 1\"",
                 id="inline-python-date-filter"),
    pytest.param('python3 -c "print(1) if a >= b else print(2)"', id="inline-python-ge"),
    pytest.param('python3 -c "def f(x) -> int: return x"', id="inline-python-arrow"),
    pytest.param("python3 -c \"import json; print(json.load(open('f.json')))\"",
                 id="inline-python-read-open"),
    pytest.param("python3 -c \"print(open('f','r').read())\"", id="inline-python-open-mode-r"),
    pytest.param("python3 - <<'PY'\nimport json\nd = json.load(open('f'))\nprint(d)\nPY",
                 id="heredoc-read"),
    pytest.param('grep -n "a > b" file', id="quoted-gt-in-pattern"),
    pytest.param("echo 'x > y'", id="quoted-gt-in-echo"),
    pytest.param("git status --short", id="git-read-verb"),
    pytest.param("wc -l main.md", id="wc"),
    # `ln` is Python's commonest loop variable, and `\\bln\\b` read it as the symlink command:
    # 64 of 3,934 fleet commands flagged on this alone (2026-11-10), every one a pure read.
    pytest.param("python3 - <<'PY'\nfor ln in open('f.jsonl'):\n    ln = ln.strip()\n"
                 "    print(ln)\nPY", id="ln-is-a-loop-variable"),
    pytest.param('python3 -c "rows=[json.loads(ln) for ln in open(p) if ln.strip()]"',
                 id="ln-in-a-comprehension"),
    # A write verb inside NARRATION or a search pattern is text, not a command.
    pytest.param('wc -l api.js && echo "=== files I would touch ==="', id="touch-in-prose"),
    pytest.param('grep -n "def rm\\b" src/store.py; echo "=== cli rm impl ==="',
                 id="rm-in-a-grep-pattern"),
    pytest.param("tagfs --store /srv/data rm --help", id="rm-as-a-subcommands-help"),
]

# Commands that MUST be marked. The `open`-family cases are the ones the dead anchor dropped.
WRITES = [
    pytest.param("python3 -c \"open('x','w')\"", id="open-mode-w"),
    pytest.param("python3 -c \"open('x','a').write('y')\"", id="open-mode-a"),
    pytest.param("python3 -c \"import os; os.unlink('f')\"", id="unlink"),
    pytest.param("python3 -c \"Path('p').write_text('x')\"", id="write-text"),
    pytest.param("python3 - <<'PY'\nimport json\njson.dump(d, open(p,'w'))\nPY",
                 id="heredoc-json-dump"),
    pytest.param("python3 - <<'PY'\nopen('notes.md','a').write('x')\nPY", id="heredoc-append"),
    pytest.param("perl -e \"open(F, '>out.txt')\"", id="perl-open"),
    pytest.param("echo hi > state/x.txt", id="plain-redirect"),
    pytest.param('echo hi > "state/x"', id="redirect-to-quoted-target"),
    pytest.param("ls /tmp 1>out.txt", id="fd-1-to-file"),
    pytest.param("cat >> LEDGER.md", id="append-redirect"),
    pytest.param("echo x | tee /tmp/a", id="tee"),
    pytest.param("sed -i s/a/b/ f.txt", id="sed-in-place"),
    pytest.param("git add -A && git commit -m x", id="git-commit"),
    pytest.param("rm -f /srv/videos/tmp/*", id="rm-glob"),
    # Narrowing the bare-command shape to COMMAND POSITION must not lose a write that reaches
    # it through a runner. These three are the forms the fleet really uses, and the first was
    # genuinely lost by this fix's first draft — caught by diffing the new verdicts against
    # the old shape's over 3,934 commands rather than by reading the pattern.
    pytest.param("find $M -name '*.json' -print0 | xargs -0 -r rm -f", id="xargs-rm"),
    pytest.param("cd /srv/x && ls; rm -rf build", id="rm-after-a-semicolon"),
    pytest.param("find . -name '*.pyc' -exec rm -f {} +", id="find-exec-rm"),
    pytest.param("sudo chown mark:mark /srv/x", id="sudo-chown"),
    pytest.param("cp a.json b.json", id="cp-at-the-start"),
]


@pytest.mark.parametrize("command", READS)
def test_a_read_or_a_probe_is_not_marked(command: str) -> None:
    assert marks_a_write(command) is False, (
        "a marker that fires on reads is one an auditor learns to discount")


@pytest.mark.parametrize("command", WRITES)
def test_a_write_shaped_command_is_marked(command: str) -> None:
    assert marks_a_write(command) is True, "this command writes and no file-action gate saw it"


def test_the_interpreter_clause_is_reachable_at_all() -> None:
    """The regression that hid F730: a clause that can never match is worse than a missing one."""
    command = "python3 -c \"open('x','w')\""
    assert ">" not in command, "no redirect here: only the interpreter clause can match"
    assert marks_a_write(command) is True


def test_a_read_chain_is_not_joined_across_and_into_one_apparent_write() -> None:
    """`&&` ENDS a command, so a shape's scan must not reach past it (measured 2026-11-07)."""
    command = (
        'cd /home/mark/git-repos/routine-scheduler && echo "=== HEAD ===" && '
        'git log --oneline -8 && echo "=== STATUS ===" && git status --porcelain=v1 | head -30'
        ' && echo "=== UNPUSHED ===" && git log --oneline origin/main..HEAD && '
        'echo "=== WORKTREES ===" && git worktree list && echo "=== STASH ===" && '
        'git stash list'
    )
    assert marks_a_write(command) is False


@pytest.mark.parametrize("command", [
    "git stash push -u -m wip",                      # the write this shape exists to catch
    "git worktree list && echo done && git add -- src/x.py",   # a real write AFTER a read
    "cd /tmp && git apply fix.diff && echo ok",       # a write mid-chain, after a `cd`
    'wc -l f && sed -i "s/a/b/" f',                   # the in-place sed, after a read
])
def test_a_real_write_still_flags_wherever_it_sits_in_the_chain(command: str) -> None:
    """The guard on the narrowing: ending a shape's scan at `&` must not let a genuine write
    through just because a read came first. Each subcommand is judged on its own merits."""
    assert marks_a_write(command) is True


@pytest.mark.parametrize(("command", "writes"), [
    ("git stash list", False),           # inspects the stack
    ("git stash show stash@{0}", False),  # ditto — and `stash@{0}` is a REF, not a verb
    ("git stash", True),                 # the bare form STASHES: it writes
    ("git stash push -u -m wip", True),
    ("git stash pop", True),
    ("git stash apply stash@{1}", True),  # a write form carrying a ref still flags
    ("git stash drop stash@{2}", True),   # ditto: the ref guard must not excuse the verb
])
def test_only_the_writing_forms_of_git_stash_are_marked(command: str, writes: bool) -> None:
    """`stash` is the one verb in this shape's set with read-only subcommands of its own, and
    `git stash list` is how a run is supposed to inspect the stack before popping anything —
    so marking it taught exactly the wrong lesson about a command the house rules encourage."""
    assert marks_a_write(command) is writes


def test_a_filtering_sed_beside_a_grep_flag_is_not_a_write() -> None:
    """The same `&&` bug in the in-place-sed shape, and the commonest way to trip it: `\\bsed\\b`
    followed by anything up to ` -i` matched `sed -n …  && grep -i …`, where the `-i` belongs to
    grep. A filtering sed is a read, which is exactly what that shape's comment says."""
    assert marks_a_write("sed -n '1,5p' notes.md && grep -i error log.txt") is False


def test_the_marker_stays_conservative_where_it_cannot_parse() -> None:
    """A bare `>` outside quotes still flags, even when it means "greater than"."""
    assert marks_a_write('python3 -c "\nif a > b:\n    pass"') is True
