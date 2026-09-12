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


def test_oversize_file_is_left_out_of_the_commit_and_reported(make_routine, tmp_path,
                                                              monkeypatch):
    """A file over the ceiling never becomes a blob in the routine's repo — the repo is
    mirrored into the library and pushed, and one 223 MB inventory once blocked every
    push for days. The file stays on disk; a health event names it; the rest commits."""
    import json

    from rsched.engine import autocommit as ac
    d = make_routine(slug="bigstate")
    assert _git(d, "init", "-q").returncode == 0
    monkeypatch.setattr(ac, "OVERSIZE_BYTES", 64)
    (d / "state").mkdir(exist_ok=True)
    (d / "state" / "small.json").write_text("{}", encoding="utf-8")
    (d / "state" / "inventory.jsonl").write_text("x" * 200, encoding="utf-8")
    home = d.parent
    autocommit(d, "run end", routines_home=home, run_id="bigstate:20260912-000000")
    committed = _git(d, "show", "--name-only", "--format=", "HEAD").stdout
    assert "state/small.json" in committed
    assert "inventory.jsonl" not in committed
    assert (d / "state" / "inventory.jsonl").read_text(encoding="utf-8") == "x" * 200
    rows = [json.loads(x) for x in
            (home / ".control" / "health-events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {r["event"] for r in rows} == {"oversize_state_file"}   # the fixture's own files too
    assert all(r["routine"] == "bigstate" for r in rows)
    assert any("state/inventory.jsonl" in r["detail"] for r in rows)
    assert not any("small.json" in r["detail"] for r in rows)
    # ignored trees are never scanned: git's own view of the stage decides what counts
    (d / ".gitignore").write_text("mnt/\n", encoding="utf-8")
    (d / "mnt").mkdir()
    (d / "mnt" / "huge.bin").write_text("y" * 500, encoding="utf-8")
    big_now = [rel for rel, _ in ac.oversize_files(d)]
    assert "state/inventory.jsonl" in big_now and "mnt/huge.bin" not in big_now

