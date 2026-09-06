"""`rsched` CLI: `daemon` (scheduler + web UI, what systemd runs), `run-once` (execute one
run now, streaming events), `validate` (server config + routine.yaml checks), `lint`
(workflow library), `suggest` (rank library workflows for an instruction), `scaffold`
(create a routine dir from a library workflow), `abort` (stop a run) — plus the internal
`engine-run` the daemon spawns for each run. `rsched <cmd> --help` lists per-command flags.
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from .cli_daemon import cmd_daemon
from .cli_render import _render_event, _server_tz
from .config import MODEL_KINDS, load_server_config
from .paths import expand


def _parse_model_overrides(values: list[str]) -> dict[str, str]:
    """--model main=gpt-4o (a catalog model NAME; repeatable per role)."""
    out: dict[str, str] = {}
    for val in values or []:
        kind, _, name = val.partition("=")
        if not (kind and name):
            raise SystemExit(f"--model expects kind=name (a catalog model), got {val!r}")
        if kind not in MODEL_KINDS:
            raise SystemExit(f"--model kind must be one of {MODEL_KINDS}, got {kind!r}")
        out[kind] = name
    return out


def _dir_across_homes(server, slug: str):
    """Resolve a slug across routines, conversations, and background homes — `rsched
    abort` applies to any of them (conversations and detached tasks are runs too).
    """
    from . import registry

    for home in registry.all_homes(server):
        if (home / slug / "routine.yaml").is_file():
            return home / slug
    return server.routines_home / slug   # let downstream produce the not-found error


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

    signal.signal(signal.SIGTERM, lambda *_: request_abort())
    signal.signal(signal.SIGINT, lambda *_: request_abort())

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
    """Internal: spawned by the daemon. Same as run-once but quiet, with a fixed run_ts.

    `--config` and `--homes` are REQUIRED and have NO default. This process inherits nothing
    from its spawner, so a default would mean silently adopting
    `~/.config/routine-scheduler/config.yaml` — the production instance — whenever the
    spawner meant somewhere else (F394). The homes the named config resolves to must be the
    ones the spawner is using; a disagreement is refused, never reconciled.
    """
    from .endpoints.instrument import FileSink, set_sink
    from .engine.control import request_abort
    from .engine.runtime import run_routine
    from .registry import homes_fingerprint

    config_path = expand(args.config)
    if not config_path.is_file():
        print(f"error: --config {config_path}: no such file — the spawning process named a "
              "config this process cannot read", file=sys.stderr)
        return 2
    server, _ = load_server_config(config_path)
    homes = homes_fingerprint(server)
    if homes != args.homes:
        print(f"error: --homes mismatch — {config_path} does not resolve to the run homes "
              f"the spawning process is using; refusing to run.\n"
              f"  spawner: {args.homes}\n  config:  {homes}", file=sys.stderr)
        return 2
    routine_dir = _routine_dir(server, args.routine)
    # LLM task manager: this subprocess can't reach the daemon bus, so every instrumented
    # complete() appends a lifecycle record to a sidecar the daemon tails and republishes.
    if args.run_ts:
        set_sink(FileSink(routine_dir / "runs" / args.run_ts / "llm-tasks.jsonl"))
    signal.signal(signal.SIGTERM, lambda *_: request_abort())
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
                      if p.is_dir() and not p.name.startswith("."))
               if server.routines_home.is_dir() else [])
    from .readmodels.remedies import surface_lines
    from .readmodels.surface import routine_surface

    for d in targets:
        cfg, problems = load_routine(d)
        # Setup COHERENCE is a second, independent question from "is the file well-formed":
        # a routine can parse perfectly and still hold a rule that tells it to publish into a
        # directory it cannot write. Only a blocking row fails the command — an interrupt or a
        # note is reported and does not turn the exit code red.
        lines: list[str] = []
        if cfg:
            try:
                lines = surface_lines(routine_surface(server, cfg))
            except (OSError, ValueError) as exc:      # a broken library must not fail validate
                lines = [f"NOTE  setup surface unavailable: {exc}"]
        blocking = [ln for ln in lines if ln.startswith("FAIL")]
        status = "ok" if cfg and not problems and not blocking else "PROBLEMS"
        print(f"{d.name}: {status}")
        for pr in problems:
            print(f"  - {pr}")
        for ln in lines:
            print(f"  {ln}")
        total.extend(problems)
        total.extend(blocking)
    for line in _instance_problems(server):
        print(f"instance: {line}")
    return 1 if total else 0


def _instance_problems(server) -> list[str]:
    """Coherence checks that belong to no single routine, so no routine's surface can see them.

    A DANGLING REFERENCE is the shape all three share: one side of a join names the other by
    id or slug; nothing cascades when the other side goes away.

    A LANE is the case twice over. A scheduled lane with no members fires nothing on every tick
    of its cron, forever, leaving a `lane_chain_done: 0 member runs` in the health stream that
    reads exactly like a lane whose members all completed. Two of them had been running empty
    for weeks. And a lane naming a slug that is not a routine is nobody's either: routines are
    deleted out of band, so no cascade removes them from the store, the chain logs a warning
    and skips them, and the only other trace is an `unknown routine(s)` the console used to
    raise over the WHOLE membership (F442).

    A DOMAIN is the third. Membership points the other way — the routine names the domain in
    its own routine.yaml — so a deleted domain leaves every member pointing at nothing.
    `config/domainconfig.domain_config_for` deliberately returns an EMPTY block instead of
    raising: a stale reference must not be what stops a run from booting. The cost of that
    choice is total silence. The routine keeps running, keeps whatever its own file says, and
    inherits none of the shared permissions, rules or budgets it was put in the domain for —
    with nothing anywhere to say so. This check is the "reported" half of that bargain.

    Reported, never fatal — an empty lane is a normal intermediate state while you are building
    one, a phantom member is stale bookkeeping, and a routine whose domain was deleted is
    running on exactly its own config, which is a real configuration and not a broken instance.
    """
    from . import lanes as lanes_mod

    try:
        all_lanes = lanes_mod.list_lanes(server.routines_home)
    except OSError:
        all_lanes = []      # an unreadable lane store must not hide the domain check below
    routine_dirs = (sorted(p for p in server.routines_home.iterdir()
                           if (p / "routine.yaml").is_file())
                    if server.routines_home.is_dir() else [])
    live = {p.name for p in routine_dirs}
    empty = [f"lane {ln['name']!r} ({ln['id']}) has a schedule ({ln['cron']!r}) but no members "
             "— it fires nothing on every tick"
             for ln in all_lanes if ln.get("cron") and not lanes_mod.member_slugs(ln)]
    phantom = [f"lane {ln['name']!r} ({ln['id']}) names {slug!r}, which is not a routine — "
               "the chain skips it every fire; remove it from the lane"
               for ln in all_lanes for slug in lanes_mod.member_slugs(ln) if slug not in live]
    return empty + phantom + _dangling_domains(server, routine_dirs)


def _dangling_domains(server, routine_dirs: list[Path]) -> list[str]:
    """Routines naming a `domain:` that no domain record answers to — see `_instance_problems`.

    Read from the RAW routine.yaml, not the loaded config: `load_routine` merges the domain's
    block and hands back an empty one for a missing domain, so by the time a routine is loaded
    the dangling reference is indistinguishable from no reference at all. An unparseable file
    is skipped rather than reported — `cmd_validate` already prints that file's own parse
    problem; a second line about a key nobody could read would point away from the fault.
    """
    import yaml

    from . import domains as domains_mod
    from .paths import read_yaml

    known = {str(d.get("id") or "") for d in domains_mod.list_domains(server.routines_home)}
    out: list[str] = []
    for p in routine_dirs:
        try:
            raw = read_yaml(p / "routine.yaml", {})
        except (OSError, yaml.YAMLError):
            continue
        did = str(raw.get("domain") or "").strip() if isinstance(raw, dict) else ""
        if did and did not in known:
            out.append(f"routine {p.name!r} names domain {did!r}, which does not exist — it "
                       "inherits no shared config, permissions or store, silently; set its "
                       "domain to a real one or clear the key")
    return out


def cmd_abort(args) -> int:
    import asyncio

    from . import registry
    from .daemon.runner_state import abort_process
    from .ids import parse_run_id
    from .paths import read_json

    server, _ = load_server_config()
    target = args.run_id
    if ":" in target:
        slug, ts = parse_run_id(target)
    else:
        slug = target
        runs = registry.run_index(_dir_across_homes(server, slug), slug)
        alive = [r for r in runs if r.state in registry.ACTIVE_STATES]
        if not alive:
            print(f"no active run for {slug}", file=sys.stderr)
            return 1
        ts = alive[0].ts
    run_dir = _dir_across_homes(server, slug) / "runs" / ts
    st = read_json(run_dir / "status.json")
    pid = st.get("pid") if isinstance(st, dict) else None
    ok = asyncio.run(abort_process(pid))
    print(f"abort {'sent' if ok else 'failed — process not found'} for {slug}:{ts}",
          file=sys.stderr)
    return 0 if ok else 1


def cmd_lint(args) -> int:
    from .workflows.lint import lint_all

    if getattr(args, "libraries_home", None):
        libraries_home = Path(args.libraries_home)   # sandboxed caller: skip ~/.config read
    else:
        server, _ = load_server_config()
        libraries_home = server.libraries_home
    results = lint_all(libraries_home)
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
            if args.instruction_file
            else f"# Instruction\n\n(fill in) — scaffolded for {args.slug}",
            workflow_slug=args.workflow, cron=args.cron or "",
            tz=args.tz or _server_tz(),
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
                   help="override a routine model role: kind=name (a catalog model; kind: "
                        f"{'|'.join(MODEL_KINDS)}, repeatable)")
    r.add_argument("--quiet", action="store_true", help="no event stream on stdout")
    r.set_defaults(fn=cmd_run_once)

    e = sub.add_parser("engine-run", help="internal: run a routine (spawned by the daemon)")
    e.add_argument("routine")
    e.add_argument("--run-ts", required=True)
    e.add_argument("--config", required=True,
                   help="the server config the SPAWNING process loaded — no default, so "
                        "this process can never adopt a config nobody pointed it at")
    e.add_argument("--homes", required=True,
                   help="the spawner's run homes (registry.homes_fingerprint); a config "
                        "that resolves to different homes is refused")
    e.add_argument("--resume", action="store_true",
                   help="rehydrate the run's transcript and continue it")
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
    li.add_argument("--libraries-home", help="lint this library dir directly, skipping the "
                    "server-config load (lets sandboxed callers lint without ~/.config access)")
    li.set_defaults(fn=cmd_lint)

    su = sub.add_parser("suggest", help="rank library workflows for an instruction")
    su.add_argument("--instruction", required=True)
    su.set_defaults(fn=cmd_suggest)

    sc = sub.add_parser("scaffold", help="create a routine dir from a library workflow")
    sc.add_argument("slug")
    sc.add_argument("--workflow", required=True, help="library workflow slug")
    sc.add_argument("--cron", default="")
    sc.add_argument("--tz", default="")   # empty → the server's own zone at scaffold time
    sc.add_argument("--name", default="")
    sc.add_argument("--description", default="",
                    help="one-line description shown in the UI (defaults to name)")
    sc.add_argument("--instruction-file",
                    help="file whose content is the compile SEED "
                         "(decomposed into the stages, not persisted)")
    sc.add_argument("--tag", action="append", help="tag for filtering, e.g. meta (repeatable)")
    sc.add_argument("--read-root", action="append", help="extra fs read root (repeatable)")
    sc.add_argument("--write-root", action="append", help="extra fs write root (repeatable)")
    sc.set_defaults(fn=cmd_scaffold)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
