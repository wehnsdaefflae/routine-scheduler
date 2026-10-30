"""`rsched` CLI: `daemon` (scheduler + web UI, what systemd runs), `run-once` (execute one
run now, streaming events), `validate` (server config + routine.yaml checks), `lint`
(workflow library), `suggest` (rank library workflows for an instruction), `scaffold`
(create a routine dir from a library workflow), `abort` (stop a run), `drain` (quiesce before a
deploy) and `postdeploy` (check the LIVE instance after one, and record the session) — plus the
internal `engine-run` the daemon spawns for each run. `rsched <cmd> --help` lists per-command
flags.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

from .cli_daemon import LOG_FORMAT, cmd_daemon
from .cli_overrides import parse_effort_overrides, parse_model_overrides
from .cli_postdeploy import add_parser as add_postdeploy_parser
from .cli_render import _render_event
from .cli_runctl import cmd_abort, cmd_drain
from .cli_validate import cmd_validate
from .config import EFFORT_LEVELS, MODEL_KINDS, ROUTINE_MODEL_ROLES, load_server_config
from .paths import expand

#: A finished run's status → the process exit code (`run-once` and the daemon's `engine-run`).
#: A partial run did its job as far as it went — 0; an abort is the shell's SIGINT 130.
RUN_EXIT_CODES = {"ok": 0, "partial": 0, "failed": 1, "aborted": 130}


def _routine_dir(server, slug_or_path: str) -> Path:
    p = expand(slug_or_path)
    if p.is_dir() and (p / "routine.yaml").exists():
        return p
    return server.routines_home / slug_or_path


def cmd_run_once(args) -> int:
    from . import abortmark
    from .engine.runtime import run_routine

    server, problems = load_server_config()
    for pr in problems:
        print(f"config: {pr}", file=sys.stderr)
    routine_dir = _routine_dir(server, args.routine)
    if not (routine_dir / "routine.yaml").exists():
        print(f"no routine at {routine_dir} (missing routine.yaml)", file=sys.stderr)
        return 2

    # Ctrl-C is the abort, never a KeyboardInterrupt: the flag ends a util, script or shell
    # command in flight through `procgroup.terminate` (utils_run.run_jailed) — the terminal's
    # SIGINT itself never reaches one, since it runs in a session of its own — and the run then
    # writes its own `aborted` close-out.
    # F751: each handler records WHO it is before raising the flag — a signal carries no
    # sender, so the handler is the only witness and the finish text names it.
    signal.signal(signal.SIGTERM,
                  lambda *_: abortmark.signalled("signal", "SIGTERM to `rsched run-once`"))
    signal.signal(signal.SIGINT,
                  lambda *_: abortmark.signalled("user", "interrupted at the terminal (Ctrl-C)"))

    def on_event(obj: dict) -> None:
        print(_render_event(obj), flush=True)

    try:
        status, run_dir = run_routine(routine_dir, server,
                                      model_overrides=parse_model_overrides(args.model),
                                      effort_overrides=parse_effort_overrides(args.effort),
                                      on_event=None if args.quiet else on_event)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"run dir: {run_dir}", file=sys.stderr)
    return RUN_EXIT_CODES.get(status, 1)


def cmd_engine_run(args) -> int:
    """Internal: spawned by the daemon. Same as run-once but quiet, with a fixed run_ts — and
    `--model` overrides only when the daemon names a model trial's (rsched/trials.py).

    `--config` and `--homes` are REQUIRED and have NO default. This process inherits nothing
    from its spawner, so a default would mean silently adopting
    `~/.config/routine-scheduler/config.yaml` — the production instance — whenever the
    spawner meant somewhere else (F394). The homes the named config resolves to must be the
    ones the spawner is using; a disagreement is refused, never reconciled.

    Logging is configured FIRST, in the daemon's line shape (`LOG_FORMAT`), at WARNING. A fresh
    interpreter has no handler, so Python's last-resort one printed the bare message — no level,
    no logger name — and the daemon's re-emit of a cleanly finishing run's warnings (F97,
    `runner_state._notable_stderr`) matches on the level name: it could never fire. WARNING,
    because the daemon re-emits nothing below it and the stderr pipe is held in its memory.

    The OOM rank is raised SECOND, before anything this process spawns can inherit the old one:
    under memory pressure the kernel then takes a run, never the daemon (rsched/oomrank.py).
    """
    logging.basicConfig(level=logging.WARNING, format=LOG_FORMAT)
    from . import abortmark, oomrank
    if problem := oomrank.mark_run_expendable():
        logging.getLogger("rsched.engine").warning(problem)
    from .endpoints.instrument import FileSink, set_sink
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
    # The daemon's abort (runner_state.abort_process): the flag ends the run at its next turn
    # boundary and a util, script or shell command in flight at once (utils_run.run_jailed).
    # Neither of the daemon's signals reaches that command — it leads a group of its own.
    # F751: the handler records what it KNOWS — that a signal arrived — and a door that wrote
    # a breadcrumb into the run dir first (a console cancel, `rsched abort`) outranks it, since
    # that one knows who asked. An unmarked SIGTERM is a supervisor stop or a deploy.
    signal.signal(signal.SIGTERM,
                  lambda *_: abortmark.signalled("signal", "SIGTERM to the engine process"))
    try:
        status, _ = run_routine(routine_dir, server, run_ts=args.run_ts,
                                model_overrides=parse_model_overrides(args.model),
                                effort_overrides=parse_effort_overrides(args.effort),
                                resume_from=args.run_ts if getattr(args, "resume", False) else None)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return RUN_EXIT_CODES.get(status, 1)


def cmd_lint(args) -> int:
    from .workflows.lint import lint_all

    if getattr(args, "libraries_home", None):
        libraries_home = expand(args.libraries_home)   # sandboxed caller: skip ~/.config read
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
            tz=args.tz,               # empty: scaffold's default, the server's zone
            description=args.description or "",
            tags=args.tag or None,
            fs_read_roots=args.read_root or None, fs_write_roots=args.write_root or None,
        )
    except (ValueError, KeyError, OSError) as exc:     # OSError: an unreadable instruction file
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
                   help="override a routine model role: kind=name (a catalog model — a "
                        "decision model for decision/decision_media; kind: "
                        f"{'|'.join(ROUTINE_MODEL_ROLES)}, repeatable)")
    r.add_argument("--effort", action="append",
                   help=f"override a role's reasoning effort: kind=level (kind: "
                        f"{'|'.join(MODEL_KINDS)}; level: {'|'.join(EFFORT_LEVELS)}; repeatable)")
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
    e.add_argument("--model", action="append",
                   help="a MODEL TRIAL's role override, kind=name (a catalog model; "
                        "repeatable) — the daemon names a trial run's models here "
                        "(rsched/trials.py)")
    e.add_argument("--effort", action="append",
                   help="a MODEL TRIAL's role effort override, kind=level (repeatable)")
    e.set_defaults(fn=cmd_engine_run)

    v = sub.add_parser("validate", help="validate server config and routine.yaml files")
    v.add_argument("routine", nargs="?", help="one routine (default: all)")
    v.set_defaults(fn=cmd_validate)

    d = sub.add_parser("daemon", help="run the scheduler (systemd runs this)")
    d.set_defaults(fn=cmd_daemon)

    a = sub.add_parser("abort", help="abort a run: rsched abort <slug>[:<ts>]")
    a.add_argument("run_id")
    a.set_defaults(fn=cmd_abort)

    dr = sub.add_parser("drain", help="pause scheduling and wait for in-flight runs, so a "
                                      "deploy restart interrupts nothing (F746)")
    dr.add_argument("--timeout", type=int, default=1800,
                    help="seconds to wait before giving up (default 1800); exit 1 names what "
                         "is still running, so `rsched drain && docker compose up -d` stops")
    dr.add_argument("--poll", type=int, default=15, help="seconds between checks (default 15)")
    dr.add_argument("--resume", action="store_true",
                    help="lift the pause this command left in place, after the deploy")
    dr.set_defaults(fn=cmd_drain)

    # …and the AFTER half of the same deploy (R2428): the instance is running the new code — is
    # it healthy, and is what it serves what we built? Its flags live with its body.
    add_postdeploy_parser(sub)

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
