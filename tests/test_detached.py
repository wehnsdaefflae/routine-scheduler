"""DetachedManager lifecycle: intake→fire, deliver (idempotent), wake race, reconcile, gc.

Uses a FakeRunner (records fire/resume, writes a real status.json on fire — no subprocess)
and on-disk fixtures, mirroring tests/test_scheduler.py. asyncio_mode=auto is set, so the
async tests run directly."""

import os

import yaml

from conftest import FakeRunner
from rsched.config import ServerConfig
from rsched.daemon.detached import DetachedManager
from rsched.daemon.runner import Runner
from rsched.ids import now_iso
from rsched.paths import atomic_write_json, read_json


def _server(tmp_path) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.conversations_home = tmp_path / "conversations"
    s.background_home = tmp_path / "background"
    s.libraries_home = tmp_path / "lib"
    for h in (s.routines_home, s.conversations_home, s.background_home, s.libraries_home):
        h.mkdir(parents=True, exist_ok=True)
    return s


class DetachedFakeRunner(FakeRunner):
    """The shared double, specialized for the detached manager: fire writes a REAL running
    status.json (the manager polls disk), resume refuses while draining or already active
    (the manager's wake path relies on that)."""

    async def fire(self, cfg, *, reason="schedule") -> str:
        ts = "20260715-120000"
        run_dir = cfg.dir / "runs" / ts
        run_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(run_dir / "status.json",
                          {"run_id": f"{cfg.slug}:{ts}", "state": "running",
                           "pid": os.getpid(), "updated": now_iso()})
        self.active[cfg.slug] = ts
        self.fired.append((cfg.slug, reason))
        return f"{cfg.slug}:{ts}"

    async def resume(self, cfg, ts, *, reason="resume") -> str | None:
        if self.draining or cfg.slug in self.active:
            return None
        self.resumed.append((cfg.slug, ts, reason))
        return f"{cfg.slug}:{ts}"

    # the real terminal-gated resume (it only touches registry + self.resume, so it binds
    # cleanly onto the fake) — what _wake_owner calls
    resume_terminal = Runner.resume_terminal

def _owner(server, slug="c-1", *, last_state="finished",
           permissions=("util-authoring", "memory")) -> "os.PathLike":
    """A minimal conversation dir with one (terminal, by default) run — an idle owner."""
    d = server.conversations_home / slug
    for sub in ("inbox", "state", "artifacts"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    (d / "routine.yaml").write_text(yaml.safe_dump({
        "slug": slug, "name": "Owner", "enabled": True,
        "schedule": {"cron": "", "tz": "Europe/Berlin", "catchup": "skip"},
        "workflow": {"library_slug": "converse", "library_commit": ""},
        "permissions": list(permissions),
        "capabilities": {"actions": ["write_util", "memory_read", "memory_write"],
                         "utils": [], "confirm": "creations", "runs": "last"},
        "models": {"main": "scripted-model"},
        "budgets": {"max_turns": 10},
        "fs_read_roots": ["/proj"], "fs_write_roots": ["/proj"],
    }), encoding="utf-8")
    ts = "20260715-110000"
    rd = d / "runs" / ts
    rd.mkdir(parents=True)
    atomic_write_json(rd / "status.json", {"run_id": f"{slug}:{ts}", "state": last_state,
                                           "pid": os.getpid(), "updated": now_iso()})
    return d


def _task(server, taskid, owner_dir, *, state="finished", summary="scrape done",
          artifact=False, pid=None) -> "os.PathLike":
    """A background task dir (already materialized+fired) with a status of the given state."""
    d = server.background_home / taskid
    (d / "state").mkdir(parents=True, exist_ok=True)
    (d / "routine.yaml").write_text(yaml.safe_dump({
        "slug": taskid, "name": "scrape", "enabled": True,
        "schedule": {"cron": "", "tz": "Europe/Berlin", "catchup": "skip"},
        "workflow": {"library_slug": "general-task", "library_commit": ""},
        "owner": {"slug": owner_dir.name, "dir": str(owner_dir)},
        "budgets": {"max_turns": 60},
    }), encoding="utf-8")
    ts = "20260715-120000"
    rd = d / "runs" / ts
    rd.mkdir(parents=True)
    atomic_write_json(rd / "status.json", {"run_id": f"{taskid}:{ts}", "state": state,
                                           "pid": pid if pid is not None else os.getpid(),
                                           "updated": now_iso()})
    (rd / "result.md").write_text(summary, encoding="utf-8")
    if artifact:
        (d / "artifacts").mkdir(exist_ok=True)
        (d / "artifacts" / "report.md").write_text("# report\n", encoding="utf-8")
    return d


def _request(server, taskid, owner_dir, *, workflow="general-task", label="scrape", prompt="do it"):
    atomic_write_json(server.background_home / ".requests" / f"{taskid}.json",
                      {"taskid": taskid, "prompt": prompt, "workflow": workflow,
                       "label": label, "owner": {"slug": owner_dir.name, "dir": str(owner_dir)}})


# -- intake ---------------------------------------------------------------------------------

async def test_intake_materializes_and_fires(tmp_path):
    server = _server(tmp_path)
    owner = _owner(server)
    fr = DetachedFakeRunner()
    mgr = DetachedManager(server, fr)
    _request(server, "bg-1", owner)
    await mgr.tick()
    assert ("bg-1", "detached") in fr.fired
    task_dir = server.background_home / "bg-1"
    assert (task_dir / "main.md").exists() and (task_dir / "routine.yaml").exists()
    ry = yaml.safe_load((task_dir / "routine.yaml").read_text())
    assert ry["owner"] == {"slug": "c-1", "dir": str(owner)}
    assert ry["budgets"]["max_turns"] == 60          # background budget, not the owner's 10
    assert not (server.background_home / ".requests" / "bg-1.json").exists()  # consumed


async def test_intake_copies_and_strips_owner_capabilities(tmp_path):
    server = _server(tmp_path)
    owner = _owner(server)
    mgr = DetachedManager(server, DetachedFakeRunner())
    _request(server, "bg-1", owner)
    await mgr.tick()
    ry = yaml.safe_load((server.background_home / "bg-1" / "routine.yaml").read_text())
    assert ry["fs_write_roots"] == ["/proj"]                       # project access copied
    assert "write_util" not in ry["capabilities"]["actions"]       # gated kinds stripped
    assert "memory_read" not in ry["capabilities"]["actions"]


async def test_intake_idempotent_when_run_exists(tmp_path):
    server = _server(tmp_path)
    owner = _owner(server)
    _task(server, "bg-1", owner, state="running")   # a run already exists on disk
    fr = DetachedFakeRunner()
    mgr = DetachedManager(server, fr)
    _request(server, "bg-1", owner)
    await mgr.tick()
    assert fr.fired == []                            # not re-fired
    assert not (server.background_home / ".requests" / "bg-1.json").exists()  # request consumed


# -- deliver --------------------------------------------------------------------------------

async def test_deliver_writes_message_and_artifacts(tmp_path):
    server = _server(tmp_path)
    owner = _owner(server)
    task = _task(server, "bg-1", owner, summary="found 42 rows", artifact=True)
    mgr = DetachedManager(server, DetachedFakeRunner())
    await mgr.tick()
    msgs = list((owner / "inbox").glob("msg-bg-*.json"))
    assert len(msgs) == 1
    body = read_json(msgs[0])
    assert body["via"] == "background" and "found 42 rows" in body["text"]
    assert (owner / "artifacts" / "from-bg-bg-1" / "report.md").exists()
    assert (task / "delivered.json").exists()


async def test_deliver_is_idempotent(tmp_path):
    server = _server(tmp_path)
    owner = _owner(server)
    _task(server, "bg-1", owner, artifact=True)
    mgr = DetachedManager(server, DetachedFakeRunner())
    await mgr.tick()
    # simulate the owner draining the delivery, then re-tick: delivered.json must prevent a redo
    for m in (owner / "inbox").glob("msg-bg-*.json"):
        m.unlink()
    await mgr.tick()
    assert list((owner / "inbox").glob("msg-bg-*.json")) == []


async def test_deliver_drops_when_owner_missing(tmp_path):
    server = _server(tmp_path)
    owner = server.conversations_home / "gone"   # never created
    task = _task(server, "bg-1", owner)
    mgr = DetachedManager(server, DetachedFakeRunner())
    await mgr.tick()
    assert read_json(task / "delivered.json")["owner"] == "missing"


async def test_crashed_task_delivers_failure(tmp_path):
    server = _server(tmp_path)
    owner = _owner(server)
    _task(server, "bg-1", owner, state="running", pid=999999)  # dead pid, non-terminal, not in active
    mgr = DetachedManager(server, DetachedFakeRunner())
    await mgr.tick()
    body = read_json(next((owner / "inbox").glob("msg-bg-*.json")))
    assert "failed" in body["text"]


# -- wake -----------------------------------------------------------------------------------

async def test_discord_ping_gated_on_communication(tmp_path, monkeypatch):
    from rsched import utils_lib
    sent = []
    monkeypatch.setattr(utils_lib, "run_util",
                        lambda home, name, args, **kw: sent.append((name, args)) or (0, "", ""))
    monkeypatch.setattr(utils_lib, "exists", lambda home, name: True)

    server = _server(tmp_path)
    # owner WITHOUT communication → no ping
    owner_a = _owner(server, "c-no", permissions=("memory",))
    _task(server, "bg-a", owner_a)
    await DetachedManager(server, DetachedFakeRunner()).tick()
    assert sent == []
    # owner WITH communication → a discord ping fires
    owner_b = _owner(server, "c-yes", permissions=("memory", "communication"))
    _task(server, "bg-b", owner_b, summary="scrape ok")
    await DetachedManager(server, DetachedFakeRunner()).tick()
    assert sent and sent[0][0] == "discord" and sent[0][1][0] == "send"


async def test_wake_resumes_idle_owner(tmp_path):
    server = _server(tmp_path)
    owner = _owner(server, last_state="finished")
    _task(server, "bg-1", owner)
    fr = DetachedFakeRunner()
    await DetachedManager(server, fr).tick()
    assert fr.resumed and fr.resumed[0][0] == "c-1" and fr.resumed[0][2] == "detached"


async def test_wake_skips_live_owner(tmp_path):
    server = _server(tmp_path)
    owner = _owner(server, last_state="running")   # a reply is live
    _task(server, "bg-1", owner)
    fr = DetachedFakeRunner()
    fr.active["c-1"] = "20260715-110000"            # owner is active → resume must be skipped
    await DetachedManager(server, fr).tick()
    assert fr.resumed == []
    assert list((owner / "inbox").glob("msg-bg-*.json"))   # message left for the live reply


async def test_reconcile_delivers_after_restart(tmp_path):
    server = _server(tmp_path)
    owner = _owner(server)
    _task(server, "bg-1", owner, summary="done post-restart")
    fr = DetachedFakeRunner()
    await DetachedManager(server, fr).reconcile()
    assert list((owner / "inbox").glob("msg-bg-*.json"))
    assert fr.resumed                                # idle owner woken


# -- digest + gc ----------------------------------------------------------------------------

async def test_digest_lists_tasks(tmp_path):
    server = _server(tmp_path)
    owner = _owner(server)
    _task(server, "bg-1", owner)
    await DetachedManager(server, DetachedFakeRunner()).tick()
    rows = read_json(owner / "state" / "background.json")
    assert rows and rows[0]["taskid"] == "bg-1" and rows[0]["state"] == "finished"


async def test_gc_removes_aged_delivered_task(tmp_path):
    server = _server(tmp_path)
    owner = _owner(server)
    task = _task(server, "bg-1", owner)
    mgr = DetachedManager(server, DetachedFakeRunner())
    await mgr.tick()                                  # delivers
    # owner drains the message, then the delivered marker ages past the grace window
    for m in (owner / "inbox").glob("msg-bg-*.json"):
        m.unlink()
    old = (task / "delivered.json").stat().st_mtime - 10_000
    os.utime(task / "delivered.json", (old, old))
    await mgr.tick()
    assert not task.exists()                          # gc'd
    assert read_json(owner / "state" / "background.json") == []   # digest emptied
