"""`rsched` CLI: daemon | engine-run (internal) | run-once | validate | lint | suggest |
scaffold | abort. M1 ships run-once + validate + engine-run; the rest arrive with their
milestones."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

from .config import RoleRef, load_server_config
from .paths import expand


def _render_event(obj: dict) -> str | None:
    t = obj.get("type")
    p = obj.get("payload", {})
    if t == "header":
        o = obj.get("orchestrator", {})
        return f"── run {obj.get('run_id')} · {o.get('endpoint')}:{o.get('model')} ──"
    if t == "assistant_action":
        say = p.get("say", "")
        brief = {"shell": p.get("command"), "read_file": p.get("path"), "write_file": p.get("path"),
                 "llm": (p.get("prompt") or "")[:60], "subinstruction": p.get("label") or "",
                 "ask_user": (p.get("question") or "")[:60],
                 "finish": f"{p.get('status')}", }.get(p.get("kind"), "")
        return f"[{obj.get('turn')}] {say}\n    → {p.get('kind')}: {brief}"
    if t == "observation":
        kind = p.get("kind")
        if kind == "shell":
            tag = "REJECTED" if p.get("rejected") else f"exit {p.get('exit')}"
            return f"    ← shell {tag}"
        if kind == "llm":
            return "    ← llm reply" + (" (error)" if p.get("error") else "")
        if kind == "subinstruction":
            return f"    ← subrun {p.get('label')!r}: {p.get('status')} ({p.get('turns')} turns)"
        return f"    ← {kind}"
    if t == "question":
        return f"    ? [{p.get('mode')}] {p.get('question')}"
    if t == "answer":
        return f"    ! answered: {p.get('text', '')[:80]}"
    if t == "user_injection":
        return f"    + injected: {p.get('text', '')[:80]}"
    if t == "error":
        return f"    ✗ error ({p.get('where')}): {p.get('message', '')[:120]}"
    if t == "compaction":
        return f"    ⇣ compacted context ({p.get('before_chars')} → {p.get('after_chars')} chars)"
    if t == "finish":
        return f"── finish: {p.get('status')} ──\n{p.get('summary', '')}"
    return None


def _parse_role_overrides(values: list[str]) -> dict[str, RoleRef]:
    """--role orchestrator=ollama-local:gemma4:latest (model may itself contain colons)."""
    out: dict[str, RoleRef] = {}
    for val in values or []:
        role, _, rest = val.partition("=")
        endpoint, _, model = rest.partition(":")
        if not (role and endpoint and model):
            raise SystemExit(f"--role expects role=endpoint:model, got {val!r}")
        out[role] = RoleRef(endpoint=endpoint, model=model)
    return out


def _routine_dir(server, slug_or_path: str) -> Path:
    p = expand(slug_or_path)
    if p.is_dir() and (p / "routine.yaml").exists():
        return p
    return server.routines_home / slug_or_path


def cmd_run_once(args) -> int:
    from .engine.loop import request_abort, run_routine

    server, problems = load_server_config()
    for pr in problems:
        print(f"config: {pr}", file=sys.stderr)
    routine_dir = _routine_dir(server, args.routine)
    if not (routine_dir / "routine.yaml").exists():
        print(f"no routine at {routine_dir} (missing routine.yaml)", file=sys.stderr)
        return 2

    signal.signal(signal.SIGTERM, lambda *a: request_abort())
    signal.signal(signal.SIGINT, lambda *a: request_abort())

    def on_event(obj: dict) -> None:
        line = _render_event(obj)
        if line:
            print(line, flush=True)

    try:
        status, run_dir = run_routine(routine_dir, server,
                                      role_overrides=_parse_role_overrides(args.role),
                                      on_event=None if args.quiet else on_event)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"run dir: {run_dir}", file=sys.stderr)
    return {"ok": 0, "partial": 0, "failed": 1, "aborted": 130}.get(status, 1)


def cmd_engine_run(args) -> int:
    """Internal: spawned by the daemon. Same as run-once but quiet, with a fixed run_ts."""
    from .engine.loop import request_abort, run_routine

    server, _ = load_server_config()
    routine_dir = _routine_dir(server, args.routine)
    signal.signal(signal.SIGTERM, lambda *a: request_abort())
    try:
        status, _ = run_routine(routine_dir, server, run_ts=args.run_ts)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return {"ok": 0, "partial": 0, "failed": 1, "aborted": 130}.get(status, 1)


def cmd_validate(args) -> int:
    from .config import load_routine

    server, sproblems = load_server_config()
    total = list(sproblems)
    for line in total:
        print(f"server config: {line}")
    targets = ([_routine_dir(server, args.routine)] if args.routine else
               sorted(p for p in server.routines_home.iterdir()
                      if p.is_dir() and not p.name.startswith(".")) if server.routines_home.is_dir() else [])
    for d in targets:
        cfg, problems = load_routine(d)
        status = "ok" if cfg and not problems else "PROBLEMS"
        print(f"{d.name}: {status}")
        for pr in problems:
            print(f"  - {pr}")
        total.extend(problems)
    return 1 if total else 0


def cmd_daemon(_args) -> int:
    import asyncio
    import logging

    from .daemon.events import EventBus
    from .daemon.runner import Runner
    from .daemon.scheduler import Scheduler

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    server, problems = load_server_config()
    for pr in problems:
        logging.getLogger("rsched").warning("config: %s", pr)

    async def main() -> None:
        bus = EventBus()
        runner = Runner(server, bus)
        scheduler = Scheduler(server, runner, bus)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        task = asyncio.create_task(scheduler.run_forever())
        await stop.wait()
        logging.getLogger("rsched").info("shutting down (%d active runs keep running "
                                         "and will be recovered at next boot)", len(runner.active))
        task.cancel()

    asyncio.run(main())
    return 0


def cmd_abort(args) -> int:
    import asyncio

    from .daemon import registry
    from .daemon.runner import abort_process
    from .ids import parse_run_id
    from .paths import read_json

    server, _ = load_server_config()
    target = args.run_id
    if ":" in target:
        slug, ts = parse_run_id(target)
    else:
        slug = target
        runs = registry.run_index(_routine_dir(server, slug), slug)
        alive = [r for r in runs if r.state in ("running", "waiting_user", "paused", "starting")]
        if not alive:
            print(f"no active run for {slug}", file=sys.stderr)
            return 1
        ts = alive[0].ts
    run_dir = _routine_dir(server, slug) / "runs" / ts
    st = read_json(run_dir / "status.json")
    pid = st.get("pid") if isinstance(st, dict) else None
    ok = asyncio.run(abort_process(pid, run_dir, f"{slug}:{ts}"))
    print(f"abort {'sent' if ok else 'failed — process not found'} for {slug}:{ts}",
          file=sys.stderr)
    return 0 if ok else 1


def _not_yet(milestone: str):
    def cmd(_args) -> int:
        print(f"not implemented yet (arrives with {milestone})", file=sys.stderr)
        return 2

    return cmd


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rsched", description="LLM agent routine scheduler")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run-once", help="execute one routine run now, streaming events")
    r.add_argument("routine", help="routine slug (under routines_home) or a directory path")
    r.add_argument("--role", action="append",
                   help="override a model role: role=endpoint:model (repeatable)")
    r.add_argument("--quiet", action="store_true", help="no event stream on stdout")
    r.set_defaults(fn=cmd_run_once)

    e = sub.add_parser("engine-run", help="internal: run a routine (spawned by the daemon)")
    e.add_argument("routine")
    e.add_argument("--run-ts", required=True)
    e.set_defaults(fn=cmd_engine_run)

    v = sub.add_parser("validate", help="validate server config and routine.yaml files")
    v.add_argument("routine", nargs="?", help="one routine (default: all)")
    v.set_defaults(fn=cmd_validate)

    d = sub.add_parser("daemon", help="run the scheduler (systemd runs this)")
    d.set_defaults(fn=cmd_daemon)

    a = sub.add_parser("abort", help="abort a run: rsched abort <slug>[:<ts>]")
    a.add_argument("run_id")
    a.set_defaults(fn=cmd_abort)

    for name, milestone in (("lint", "M4"), ("suggest", "M4"), ("scaffold", "M4")):
        s = sub.add_parser(name)
        s.add_argument("args", nargs="*")
        s.set_defaults(fn=_not_yet(milestone))

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
