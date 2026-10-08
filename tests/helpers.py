"""Builders the fast suite's files used to carry a copy of each.

A server over tmp homes, a RunContext over a scaffolded routine, a routine's capabilities
rewritten by hand: each was copied into every file that needed one, so a constructor or a
layout that changed had to be found and answered in every copy. These are plain functions —
none holds state or needs a teardown; the fixtures that do live in conftest.py, beside the
config and client builders they share a home with. A helper with one caller stays in its test
file.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import yaml

from rsched import reminders as reminder_store
from rsched.config import ServerConfig, load_routine
from rsched.endpoints import cliproxy_mgmt
from rsched.engine.budgets_config import Budgets
from rsched.engine.run_context import RunContext
from rsched.engine.transcript import Transcript, read_events
from rsched.grantpolicy import GrantPolicy
from rsched.reminders import Reminder

# ---- servers ----------------------------------------------------------------------------


def server_config(**attrs: object) -> ServerConfig:
    """`ServerConfig()` with `attrs` assigned after construction — the way every hand-built
    test server is made. A home it does not name stays the hermetic default
    (conftest.py's `_hermetic_home`)."""
    server = ServerConfig()
    for name, value in attrs.items():
        setattr(server, name, value)
    return server


def server_for(routine_dir: Path, **attrs: object) -> ServerConfig:
    """The server a routine made by `make_routine` runs under: its parent is the routines home,
    so `.control` logs land in tmp, and the library is a sibling `test-library` that holds only
    what a test puts there."""
    return server_config(**{"routines_home": routine_dir.parent,
                            "libraries_home": routine_dir.parent.parent / "test-library",
                            **attrs})


def tmp_server(tmp_path: Path, *, create: bool = True, **attrs: object) -> ServerConfig:
    """A server whose routines home is `tmp_path/routines`, created unless `create=False`."""
    server = server_config(routines_home=tmp_path / "routines", **attrs)
    if create:
        server.routines_home.mkdir(parents=True, exist_ok=True)
    return server


def stats_server(tmp_path: Path) -> ServerConfig:
    """Tmp routines, conversations and library homes, with the `.control` dir the run-usage
    stream lands in already there."""
    server = server_config(routines_home=tmp_path / "routines",
                           conversations_home=tmp_path / "conversations",
                           libraries_home=tmp_path / "library")
    (server.routines_home / ".control").mkdir(parents=True, exist_ok=True)
    return server


# ---- a run's context ----------------------------------------------------------------------


def run_context(routine_dir: Path, ts: str, *, server: ServerConfig | None = None,
                registry: object = None, **fields: object) -> RunContext:
    """A real root RunContext over a scaffolded routine dir: the routine's own config,
    `runs/<ts>/` with its transcript, the budgets the routine declares. `fields` are further
    RunContext fields (`grants`, `turn`, `depth`, …)."""
    cfg, _problems = load_routine(routine_dir)
    assert cfg is not None
    run_dir = routine_dir / "runs" / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    return RunContext(routine=cfg, server=server if server is not None else ServerConfig(),
                      registry=registry, run_ts=ts, run_dir=run_dir,
                      transcript=Transcript(run_dir / "transcript.jsonl"),
                      budgets=Budgets.from_config(cfg.budgets), **fields)


def action_ctx(routine_dir: Path, tmp_path: Path, ts: str = "20260716-070000") -> RunContext:
    """The RunContext the file-action tests dispatch against: an empty library under
    `tmp_path`, and an empty GrantPolicy where a live run holds its own."""
    return run_context(routine_dir, ts, grants=GrantPolicy(),
                       server=server_config(libraries_home=tmp_path / "libraries"))


def set_capabilities(routine_dir: Path, **caps: object) -> None:
    """Merge `caps` into the routine.yaml `capabilities:` mapping, as a hand edit would."""
    path = routine_dir / "routine.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw["capabilities"] = {**(raw.get("capabilities") or {}), **caps}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def prompt_text(ep) -> str:
    """Everything the model was shown on its last call — the observations live in the message
    list, not the transcript, so this is where a rendered hold or an engine note is asserted."""
    return json.dumps(ep.calls[-1]["messages"], ensure_ascii=False)


def reminder(rid: str = "rem-1", regex: str = "^util:danger", desc: str = "it deletes the target",
             scope: str = "local", reach: str = "", **stats: int) -> Reminder:
    """A consequence reminder as a store holds one. A curated (`global`) one reaches every
    routine unless `reach` says otherwise; `stats` override its four-way tally."""
    return Reminder(id=rid, regex=regex, description=desc, scope=scope,
                    created_run="r:1", stats={**reminder_store.blank_stats(), **stats},
                    reach=reach or ("universal" if scope == "global" else ""))


# ---- on disk ------------------------------------------------------------------------------


def routine_yaml(routine_dir: Path) -> dict:
    """A routine's routine.yaml as it stands on disk."""
    return yaml.safe_load((routine_dir / "routine.yaml").read_text(encoding="utf-8"))


def bare_routine(home: Path, slug: str) -> Path:
    """A routine dir whose routine.yaml names nothing but its slug."""
    d = home / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "routine.yaml").write_text(f"slug: {slug}\n", encoding="utf-8")
    return d


def util_src(name: str, *, secrets: str = "(none)", fs: str = "none", calls: str = "(none)",
             net: str = "none") -> str:
    """A library util's main.py that is nothing but its header: what it calls, and the
    secrets, network and filesystem reach it declares."""
    return (f'"""{name} — t.\n\nusage: gu {name}\ncalls: {calls}\ntags: t\n'
            f'secrets: {secrets}\nnet: {net}\nfs: {fs}\n"""\n')


def write_util(server, name: str, **header: str) -> None:
    """Put the util `util_src(name, **header)` into the library of `server` (anything with a
    `libraries_home`)."""
    d = server.libraries_home / "utils" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text(util_src(name, **header), encoding="utf-8")


def util_library(home: Path, name: str, body: str) -> Path:
    """A library at `home` holding one util, `name`, whose main.py is `body`; returns `home`."""
    d = home / "utils" / name
    d.mkdir(parents=True)
    (d / "main.py").write_text(body, encoding="utf-8")
    return home


def write_usage_stream(routines_home: Path, records: list[dict]) -> None:
    """The run-usage stream (`.control/workflow-usage.jsonl`) holding exactly `records`."""
    control = routines_home / ".control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "workflow-usage.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def measured_run(i: int, *, slug: str = "digest", recipe: str = "c1",
                 model: str = "Opus high", effort: str = "high", engine: str = "0.396.0",
                 rules: dict | None = None, status: str = "ok", tokens: int = 40_000,
                 met: int = 2, owed: int = 2, trial: str = "") -> dict:
    """One depth-0 usage record carrying the `fingerprint` and `quality` the change read models
    measure (engine/runrecord.py), run `i` on 2026-10-<i>. The fast suite's change tests and the
    browser suite's Changes pages read the same shape."""
    return {"ts": f"2026-10-{i:02d}T07:00:00+02:00", "routine": slug, "run_id": f"{slug}:{i}",
            "depth": 0, "status": status, "turns": 20, "tokens": tokens,
            "recipe_commit": recipe, "utils": {"websearch": {"ok": 3}},
            "fingerprint": {"engine": engine, "model": model, "effort": effort,
                            "deliberation": "standard", "config": "cfg1",
                            "rules": rules or {"web-research": "r1"},
                            **({"trial": trial} if trial else {})},
            "quality": {"owed": owed, "met": met, "unmet": owed - met, "not_due": 0,
                        "stages_skipped": 0, "challenged": 0, "disputed": 0, "holds": 0,
                        "schema_retries": 0, "interventions": 0, "elapsed_s": 300,
                        "cache_read": 900, "cache_write": 100}}


def health_events(routines_home: Path, **match: object) -> list[dict]:
    """The health stream's rows, oldest first — none while it does not exist yet — keeping
    those whose fields equal `match` (`event="run_failed"`, `routine="r"`)."""
    path = routines_home / ".control" / "health-events.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [row for row in rows if all(row[k] == v for k, v in match.items())]


def transcript_events(run_dir: Path) -> list[dict]:
    """A run's transcript events, read back the way the engine reads them."""
    return read_events(run_dir / "transcript.jsonl")[0]


def write_executable(path: Path, text: str) -> None:
    """`text` at `path` (parents created), mode 0755 — a stub the code under test runs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


# ---- processes and clients ----------------------------------------------------------------


def pid_alive(pid: int) -> bool:
    """Whether `pid` still runs — a zombie (exited, not yet collected by its init) does not."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    return not stat.rsplit(") ", 1)[1].startswith("Z")


def mock_proxy(monkeypatch, handler) -> None:
    """Send every request the CLIProxy management client makes to `handler` instead
    (an httpx MockTransport handler)."""
    factory = httpx.Client
    monkeypatch.setattr(cliproxy_mgmt.httpx, "Client", lambda **kw: factory(
        transport=httpx.MockTransport(handler), **kw))
