"""`rsched` CLI: daemon | engine-run (internal) | run-once | validate | lint | suggest |
scaffold | abort. M1 ships run-once + validate + engine-run; the rest arrive with their
milestones."""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from .config import MODEL_KINDS, ModelRef, load_server_config
from .paths import expand


def _render_event(obj: dict) -> str | None:
    t = obj.get("type")
    p = obj.get("payload", {})
    if t == "header":
        o = obj.get("orchestrator", {})
        return f"── run {obj.get('run_id')} · {o.get('endpoint')}:{o.get('model')} ──"
    if t == "assistant_action":
        say = p.get("say", "")
        brief = {"util": f"{p.get('name')} {' '.join(p.get('args') or [])}".strip(),
                 "write_util": p.get("name"), "read_file": p.get("path"),
                 "write_file": p.get("path"),
                 "memory_read": p.get("name"),
                 "memory_write": f"{p.get('name')}{' (delete)' if p.get('delete') else ''}",
                 "llm": (p.get("prompt") or "")[:60],
                 "spawn": f"{p.get('label') or ''} [{p.get('workflow') or 'general-task'}]",
                 "kill": f"#{p.get('n')}", "wait": "all" if p.get("all") else
                 (f"#{p.get('n')}" if p.get("n") else "any"),
                 "ask_user": (p.get("question") or "")[:60],
                 "finish": f"{p.get('status')}", }.get(p.get("kind"), "")
        return f"[{obj.get('turn')}] {say}\n    → {p.get('kind')}: {brief}"
    if t == "observation":
        kind = p.get("kind")
        if kind == "util":
            return f"    ← util {p.get('name')}: " + ("missing" if p.get("missing")
                                                      else f"exit {p.get('exit')}")
        if kind == "write_util":
            state = ("pending approval" if p.get("pending_approval") else "declined"
                     if p.get("declined") else "selftest ok" if p.get("selftest_ok")
                     else "selftest failed")
            return f"    ← write_util {p.get('name')}: {state}"
        if kind == "llm":
            return "    ← llm reply" + (" (error)" if p.get("error") else "")
        if kind == "spawn":
            return (f"    ← spawn REJECTED: {p.get('reason')}" if p.get("rejected")
                    else f"    ← sub-workflow #{p.get('n')} started")
        if kind == "wait":
            done = ", ".join(f"#{f['n']}:{f['status']}" for f in p.get("finished", []))
            return f"    ← wait → {done or ('timeout' if p.get('timed_out') else 'nothing new')}"
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


def _parse_model_overrides(values: list[str]) -> dict[str, ModelRef]:
    """--model main=ollama-local:gemma4:latest (model may itself contain colons)."""
    out: dict[str, ModelRef] = {}
    for val in values or []:
        kind, _, rest = val.partition("=")
        endpoint, _, model = rest.partition(":")
        if not (kind and endpoint and model):
            raise SystemExit(f"--model expects kind=endpoint:model, got {val!r}")
        if kind not in MODEL_KINDS:
            raise SystemExit(f"--model kind must be one of {MODEL_KINDS}, got {kind!r}")
        out[kind] = ModelRef(endpoint=endpoint, model=model)
    return out


def _routine_dir(server, slug_or_path: str) -> Path:
    p = expand(slug_or_path)
    if p.is_dir() and (p / "routine.yaml").exists():
        return p
    return server.routines_home / slug_or_path


def cmd_run_once(args) -> int:
    from .engine.control import request_abort
    from .engine.runtime import run_routine

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
                                      model_overrides=_parse_model_overrides(args.model),
                                      on_event=None if args.quiet else on_event)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"run dir: {run_dir}", file=sys.stderr)
    return {"ok": 0, "partial": 0, "failed": 1, "aborted": 130}.get(status, 1)


def cmd_engine_run(args) -> int:
    """Internal: spawned by the daemon. Same as run-once but quiet, with a fixed run_ts."""
    from .engine.control import request_abort
    from .engine.runtime import run_routine

    server, _ = load_server_config()
    routine_dir = _routine_dir(server, args.routine)
    signal.signal(signal.SIGTERM, lambda *a: request_abort())
    try:
        status, _ = run_routine(routine_dir, server, run_ts=args.run_ts,
                                resume_from=args.run_ts if getattr(args, "resume", False) else None)
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
    import logging
    import os

    import uvicorn

    from .web.app import create_app

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from .bootstrap import (adopt_permissions, adopt_unlimited_tokens, ensure_config,
                            migrate_fragments_split, migrate_improvement_split,
                            retire_self_modification, seed_routines,
                            sync_seed_library_docs, sync_seed_utils)
    ensure_config()                       # fresh deploy: generate config+token so the API isn't open
    server, problems = load_server_config()
    seed_routines(server.routines_home)   # fresh deploy: install the (disabled) bundled meta routines
    migrate_fragments_split(server.routines_home, server.libraries_home)  # pre-split instances
    migrate_improvement_split(server.routines_home, server.libraries_home)  # pre-improver instances
    adopt_permissions(server.routines_home, server.permissions_home)  # new defaults → existing routines
    adopt_unlimited_tokens(server.routines_home, server.conversations_home)  # tokens: -1 everywhere (once)
    retire_self_modification(server.routines_home, server.conversations_home,
                             server.permissions_home)  # the permission no longer exists
    sync_seed_utils(server.libraries_home)    # utils added to util-seed since this instance bootstrapped
    sync_seed_library_docs(server.libraries_home)  # workflows/traits/permissions added since, too
    for pr in problems:
        logging.getLogger("rsched").warning("config: %s", pr)
    app = create_app(server)
    # env overrides so a container can bind the LAN (RSCHED_BIND=0.0.0.0) and remap the port
    # without editing the mounted config; unset → the config's bind/port as before.
    host = os.environ.get("RSCHED_BIND") or server.bind
    port = int(os.environ.get("RSCHED_PORT") or server.port)
    # Bound graceful shutdown: the web UI holds long-lived SSE streams that never close on
    # their own, so an unbounded graceful shutdown hangs (a manual `systemctl restart` waited
    # the full TimeoutStopSec; the self-update restart, which SIGTERMs itself, would hang with
    # no systemd timeout at all). 10s force-closes idle streams while letting real requests finish.
    uvicorn.run(app, host=host, port=port, log_level="warning",
                timeout_graceful_shutdown=10)
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


def cmd_lint(args) -> int:
    from .workflows.lint import lint_all

    server, _ = load_server_config()
    results = lint_all(server.libraries_home)
    bad = 0
    for name, problems in sorted(results.items()):
        if args.target and args.target not in name:
            continue
        print(f"{name}: {'ok' if not problems else 'PROBLEMS'}")
        for p in problems:
            print(f"  - {p}")
            bad += 1
    return 1 if bad else 0


def cmd_suggest(args) -> int:
    from .workflows.suggest import suggest

    server, _ = load_server_config()
    result = suggest(server, args.instruction)
    for s in result["suggestions"]:
        print(f"{s['confidence']:.2f}  {s['slug']}  — {s['reason']}")
    if result.get("none_fit"):
        print(f"none fit — hint: {result.get('new_workflow_hint', '')}", file=sys.stderr)
    return 0


def cmd_scaffold(args) -> int:
    from .workflows.scaffold import scaffold

    server, _ = load_server_config()
    try:
        path = scaffold(
            server, slug=args.slug, name=args.name or args.slug,
            instruction=Path(args.instruction_file).read_text(encoding="utf-8")
            if args.instruction_file else f"# Instruction\n\n(fill in) — scaffolded for {args.slug}",
            workflow_slug=args.workflow, cron=args.cron or "", tz=args.tz,
            description=args.description or "",
            tags=args.tag or None,
            fs_read_roots=args.read_root or None, fs_write_roots=args.write_root or None,
        )
    except (ValueError, KeyError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"scaffolded: {path}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rsched", description="LLM agent routine scheduler")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run-once", help="execute one routine run now, streaming events")
    r.add_argument("routine", help="routine slug (under routines_home) or a directory path")
    r.add_argument("--model", action="append",
                   help="override a routine model: kind=endpoint:model (kind: main|subroutine|tool_call, repeatable)")
    r.add_argument("--quiet", action="store_true", help="no event stream on stdout")
    r.set_defaults(fn=cmd_run_once)

    e = sub.add_parser("engine-run", help="internal: run a routine (spawned by the daemon)")
    e.add_argument("routine")
    e.add_argument("--run-ts", required=True)
    e.add_argument("--resume", action="store_true", help="rehydrate the run's transcript and continue it")
    e.set_defaults(fn=cmd_engine_run)

    v = sub.add_parser("validate", help="validate server config and routine.yaml files")
    v.add_argument("routine", nargs="?", help="one routine (default: all)")
    v.set_defaults(fn=cmd_validate)

    d = sub.add_parser("daemon", help="run the scheduler (systemd runs this)")
    d.set_defaults(fn=cmd_daemon)

    a = sub.add_parser("abort", help="abort a run: rsched abort <slug>[:<ts>]")
    a.add_argument("run_id")
    a.set_defaults(fn=cmd_abort)

    li = sub.add_parser("lint", help="lint the workflow library + materialized workflows")
    li.add_argument("target", nargs="?", help="limit to entries containing this string")
    li.set_defaults(fn=cmd_lint)

    su = sub.add_parser("suggest", help="rank library workflows for an instruction")
    su.add_argument("--instruction", required=True)
    su.set_defaults(fn=cmd_suggest)

    sc = sub.add_parser("scaffold", help="create a routine dir from a library workflow")
    sc.add_argument("slug")
    sc.add_argument("--workflow", required=True, help="library workflow slug")
    sc.add_argument("--cron", default="")
    sc.add_argument("--tz", default="Europe/Berlin")
    sc.add_argument("--name", default="")
    sc.add_argument("--description", default="", help="one-line description shown in the UI (defaults to name)")
    sc.add_argument("--instruction-file", help="file whose content becomes instruction.md")
    sc.add_argument("--tag", action="append", help="tag for filtering, e.g. meta (repeatable)")
    sc.add_argument("--read-root", action="append", help="extra fs read root (repeatable)")
    sc.add_argument("--write-root", action="append", help="extra fs write root (repeatable)")
    sc.set_defaults(fn=cmd_scaffold)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
