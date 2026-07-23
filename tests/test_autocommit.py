"""engine/autocommit: the engine owns version control of a routine's working dir — a run's
end commits it with the neutral identity (routines have no shell and never run git), and a
dir without .git (a conversation) is left untouched."""



from conftest import finish, git_in, write_file
from rsched.config import ServerConfig
from rsched.engine.autocommit import autocommit
from rsched.engine.runtime import run_routine

TS = "20260708-070000"


def _git(d, *args):
    return git_in(d, *args, check=False)   # asserted via returncode where it matters


def test_run_end_commits_the_working_dir(make_routine, scripted):
    d = make_routine(slug="gitr")
    assert _git(d, "init", "-q").returncode == 0
    scripted([write_file("state/out.txt", content="artifact"),
              finish(summary="wrote the artifact")])
    server = ServerConfig()
    server.routines_home = d.parent
    server.libraries_home = d.parent.parent / "lib"
    status, _run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"

    log = _git(d, "log", "--format=%an|%s")
    assert log.returncode == 0 and log.stdout.strip(), "no commit landed at run end"
    author, subject = log.stdout.strip().splitlines()[0].split("|", 1)
    assert author == "routine-scheduler"          # the neutral engine identity
    assert f"gitr:{TS}" in subject and "ok" in subject
    committed = _git(d, "show", "--name-only", "--format=", "HEAD").stdout
    assert "state/out.txt" in committed           # the run's output is IN the commit


def test_autocommit_noops_without_a_git_dir(tmp_path):
    """A conversation dir is deliberately unversioned: no .git means no commit AND no repo
    gets created behind the user's back."""
    d = tmp_path / "conv"
    d.mkdir()
    (d / "note.txt").write_text("x", encoding="utf-8")
    autocommit(d, "should not create a repo")
    assert not (d / ".git").exists()
