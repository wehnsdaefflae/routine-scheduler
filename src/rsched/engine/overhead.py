"""How much of a run went to its TASK, and how much to its own PROCESS — counted, every run.

A routine whose runs are mostly its own bookkeeping is a critical failure (P4). This is the
detector: a deterministic reading of the run's LANDED actions and their observations, so it
measures a live run and a kept transcript the same.

Every landed action is one of three:

- FRICTION — a turn the harness took: a HELD action, a deferred finish, an `error`
  observation, a `script`/`util` exiting non-zero (a `shell`'s non-zero may be grep's "no
  match"), every schema-rejected reply and every chain element NOT EXECUTED.
- PROCESS — the run working on itself: `memory_*`, `read_rule`, `goal`, `job`, the finish, the
  outcome script; a file action whose every target is a process artefact (recipe at any depth,
  notebook, inbox, past runs, `.util_outputs/`, the engine's `state/` files, anything NAMED as
  a record by a whole word); a script/util whose every named path, or whose PROGRAM, is one;
  and a `shell` over nothing but such artefacts and the own `state/` (hand-keeping records).
  Outside the own dir only a ledger/backlog/journal/worklog counts.
- TASK — everything else, including the current run's children's work (`runs/<ts>/sub/`,
  `artifacts/from-…`), product data a file action or script writes under `state/`, and
  `report`/`ask_user` (for some routines they ARE the product).

`process_share` is (process + friction) / all — the share NOT spent on the task.

**Calibration** against a hand classification of the same runs (median of per-run shares, as
the invariant reads it):

    judged bookkeeping-dominated (hand task %)   median   |  judged healthy (hand task %)  median
    library-sync (25)                             0.583   |  voice-model-trainer (66)       0.263
    token-lab (49)                                0.467   |  funscript-trainer (65)         0.255
    rules-review (43)                             0.411   |  global-utils-review (65)       0.232
    moltbook-heartbeat (48)                       0.409   |  deficit-coach (77)             0.216
    vodafone-outage-claims (43)                   0.396   |  scheduler-improvement-res (61) 0.204
    predatory-publisher-tarpit (38)               0.380   |  scheduler-builder (68)         0.160
    llmsectest-weekday (42)                       0.350   |  folder-reorg (80)              0.155
    config-optimizer (48)                         0.344   |  routine-improver (71)          0.136
    uncensored-model-radar (43)                   0.335   |
    fau (42)                                      0.308   |  borderline (not scored):
    tv-show-tracker-seedbox-manager (39)          0.277   |  doppelcheck 0.267, freelance 0.263,
    self-audit (45)                               0.206   |  grant-hunter 0.244, website 0.223

(library-sync's row predates a fix; its runs now land four actions — see `MIN_ACTIONS`.)

`THRESHOLD` = 0.27, the midpoint of the gap between the highest healthy median (0.263) and the
lowest flagged one (0.277): 11 of 12 flagged, 0 of 8 healthy. Never move it without re-running
the calibration. The miss (self-audit) is a known LIMIT: bookkeeping on a DOMAIN-named store,
through the routine's own tooling or as reports, reads as task here; the outcome series
(readmodels/outcome_series.py) is the complementary net. Measured and rejected: counting
`report`/`ask_user` as process, counting every `state/` read, not counting the finish, and
`changelog` outside the own dir — each put a healthy routine over the bar. Treating
`.util_outputs/` reads as the producing call's purpose is held back pending re-calibration.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from . import hold
from .frictions import _lines, in_task

TASK, PROCESS, FRICTION = "task", "process", "friction"

#: The fleet invariant: a routine whose MEDIAN `process_share` over its last `WINDOW` depth-0
#: runs UNDER ITS CURRENT RECIPE reaches this is dominated by its own process (calibration in the
#: module docstring; the recipe window is readmodels/overhead_reading.py's).
THRESHOLD = 0.27
#: How many of a routine's newest measured depth-0 runs the invariant reads, and how many it
#: needs before it judges at all — one long orientation run is not a routine's habit.
WINDOW = 5
MIN_RUNS = 3
#: How many LANDED ACTIONS a run needs before its share is evidence of anything. Two actions of
#: a correct run are process BY RULE (the outcome script and the finish), so the floor under
#: `process_share` is 2/total, and the bar is unreachable from below only once total ≥ 8
#: (2/8 = 0.25 < 0.27 ≤ 2/7). Derived, not chosen: smaller flags a correct short run, larger
#: stops judging real runs.
MIN_ACTIONS = 8

#: Kinds that ARE the run working on its own process, whatever they name: its notebook, the
#: general rules that bind it, the goals it keeps, the jobs it opens and checkpoints.
PROCESS_KINDS = frozenset({"memory_read", "memory_write", "read_rule", "goal", "job"})
#: Kinds whose targets decide: a file action on the routine's own process artefacts is process.
FILE_KINDS = frozenset({"read_file", "view_image", "write_file", "edit_file", "delete", "move",
                        "mkdir"})
#: Kinds whose ARGUMENTS decide, conservatively: every path they name must be a process
#: artefact, and at least one must be named, or the call is the task's.
CALL_KINDS = frozenset({"shell", "script", "util"})

#: Files that are a recipe or its config — the routine's own, or a JOB's inside its workspace.
_RECIPE_FILES = frozenset({"main.md", "tuning.yaml", "routine.yaml"})
#: Directories that hold nothing but process: a recipe's stages (the routine's or a job's) and
#: its notebook, at any depth of the own dir.
_PROCESS_ANYWHERE = frozenset({"stages", ".memory"})
#: Directories directly under the routine's own dir that hold nothing but its process — except
#: the CURRENT run's children under `runs/<ts>/sub/` (`_bucket`).
_PROCESS_DIRS = frozenset({"inbox", "questions", "runs", ".util_outputs"})
#: Engine- and recipe-owned process files in a `state/` dir — everything else there is product.
_PROCESS_STATE = frozenset({"phase.json", "plan.md", "notes.md", "obligations.json",
                            "finish-line.json", "jobs.json", "reminders.json"})
#: Words that NAME a record of the process — matched as whole WORDS of a path component or
#: program name (`mail-log.jsonl`, `reviewLedger`), never as a substring ("journalhub" is a
#: product). `log` takes no plural: a `logs/` folder is usually a product's.
_RECORD_WORDS = frozenset({"ledger", "backlog", "journal", "worklog", "changelog", "tracker",
                           "log"})
#: Outside the own dir a CHANGELOG is a project's product and a "tracker" a product's name; a
#: ledger, backlog, journal or worklog there is still the run's own record of its work.
_OUTSIDE_WORDS = frozenset({"ledger", "backlog", "journal", "worklog"})
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
#: A child's delivered work, landed in the parent's `artifacts/` (engine/child.handback_dirname).
_HANDBACK = re.compile(r"^from-(sub|bg|branch)-")
#: A token that looks like a path: it has a slash or ends in a data/document/code file
#: extension, and is not a flag or a URL. The extension list is closed on purpose — a shell
#: heredoc's `json.load` or `d.get` is code, not a path.
_PATHISH = re.compile(r"^[^-]\S*(/\S*|\.(md|json|jsonl|ya?ml|toml|txt|csv|tsv|py|sh|html?|log|out"
                      r"|js|css|php|xml|ini|cfg|db|sqlite|gz))$", re.IGNORECASE)
_SPLIT = re.compile(r"[;&|<>()`$\"'=,]+")


def _relative(path: str, routine_dir: str) -> str | None:
    """`path` relative to the routine's own dir, or None when it lies outside it. A relative
    path is the run's own (it resolves in the routine dir, or in a job's workspace there).
    """
    p = str(path).strip()
    if not p:
        return None
    if not p.startswith(("/", "~")):
        return str(PurePosixPath(p))
    root = routine_dir.rstrip("/") + "/"
    return str(PurePosixPath(p[len(root):])) if p.startswith(root) else None


def is_record_name(name: str, words: frozenset[str] = _RECORD_WORDS) -> bool:
    """Does a path component or program name carry a record WORD — `ledger`, `mail-log`,
    `reviewLedger`, `WORKLOG.md` — whole, a plural `s` allowed except on `log`?
    """
    tokens = {w.lower() for w in _WORD.findall(name)}
    return bool(words & (tokens | {t[:-1] for t in tokens if t.endswith("s") and t != "logs"}))


def is_process_path(path: str, routine_dir: str) -> bool:
    """Is `path` one of the routine's own PROCESS artefacts (its recipe, notebook, phase, plan,
    notes, inbox, past runs, or a file named as a ledger/backlog/journal)?
    """
    rel = _relative(path, routine_dir)
    if rel is None:     # outside the own dir: only a file or folder NAMED as a run's record
        return any(is_record_name(part, _OUTSIDE_WORDS) for part in PurePosixPath(path).parts[-2:])
    if rel in (".", ""):
        return False
    parts = PurePosixPath(rel).parts
    if (rel == "scripts/outcome.py" or parts[0] in _PROCESS_DIRS or parts[-1] in _RECIPE_FILES
            or _PROCESS_ANYWHERE.intersection(parts)):
        return True
    if parts[-2:-1] == ("state",) and parts[-1] in _PROCESS_STATE:
        return True
    return any(is_record_name(part) for part in parts)


def _bucket(path: str, routine_dir: str, run_ts: str) -> str:
    """PROCESS or TASK for one named path. A child's work is the task's delegated product,
    whatever it is called: under this run's own `runs/<run_ts>/sub/`, or handed back into
    `artifacts/from-…` (engine/child.handback_dirname).
    """
    rel = _relative(path, routine_dir)
    parts = PurePosixPath(rel).parts if rel is not None else ()
    if ((parts[:1] == ("artifacts",) and len(parts) > 1 and _HANDBACK.match(parts[1]))
            or (run_ts and parts[:3] == ("runs", run_ts, "sub"))):
        return TASK
    return PROCESS if is_process_path(path, routine_dir) else TASK


def _in_state(path: str, routine_dir: str) -> bool:
    rel = _relative(path, routine_dir)
    return rel is not None and PurePosixPath(rel).parts[:1] == ("state",)


def _file_targets(action: dict) -> list[str]:
    paths = action.get("paths")
    if isinstance(paths, list) and paths:
        return [str(p) for p in paths if p]
    return [str(action[f]) for f in ("path", "src", "dest") if action.get(f)]


def _call_tokens(action: dict) -> list[str]:
    """The path-like tokens a shell command, script or util call names."""
    kind = action.get("kind")
    if kind == "shell":
        text = str(action.get("command") or "")
    else:
        text = " ".join(str(a) for a in (action.get("args") or []))
    try:
        words = shlex.split(text, posix=True)
    except ValueError:
        words = text.split()
    tokens: list[str] = []
    for word in words:
        tokens.extend(t for t in _SPLIT.split(word) if t)
    return [t for t in tokens if _PATHISH.match(t) and "://" not in t]


def _call_is_process(action: dict, routine_dir: str, run_ts: str) -> bool:
    name = str(action.get("name") or "")
    if (action.get("kind") == "script" and name == "outcome") or is_record_name(name):
        return True
    named = [t for t in _call_tokens(action) if _relative(t, routine_dir) not in (".", "")]
    if not named:
        return False
    # a script or util handed a record-keeping PROGRAM first (`bin/ledger.py add …`) is keeping
    # that record, whatever else it names; otherwise every path it names must be the process's
    if action.get("kind") != "shell" and is_record_name(PurePosixPath(named[0]).name):
        return True
    if action.get("kind") == "shell":
        # the ONE-OFF action over nothing but the routine's own state: the run hand-keeping its
        # records field by field, not doing its task (a script over the same files is tooling)
        return all(_bucket(t, routine_dir, run_ts) == PROCESS or _in_state(t, routine_dir)
                   for t in named)
    return all(_bucket(t, routine_dir, run_ts) == PROCESS for t in named)


def classify(action: dict, obs: dict | None, routine_dir: str, run_ts: str = "") -> str:
    """One landed action, with the observation it came back with: TASK, PROCESS or FRICTION.

    FRICTION first, whatever the action was aimed at. `run_ts` names the run whose children's
    work under `runs/<run_ts>/sub/` is its task's.
    """
    kind = str(action.get("kind") or "")
    if obs is not None and (hold.is_hold(obs) or obs.get("rejected") or obs.get("error")):
        return FRICTION
    exit_code = obs.get("exit") if obs is not None else None
    if kind in ("script", "util") and isinstance(exit_code, int) and exit_code != 0:
        return FRICTION     # a tool call that failed (a shell's non-zero is grep saying "no match")
    if kind in PROCESS_KINDS or kind == "finish":
        return PROCESS
    if kind in FILE_KINDS:
        targets = _file_targets(action)
        process = bool(targets) and all(_bucket(t, routine_dir, run_ts) == PROCESS for t in targets)
        return PROCESS if process else TASK
    if kind in CALL_KINDS:
        return PROCESS if _call_is_process(action, routine_dir, run_ts) else TASK
    return TASK


def from_events(events: Iterable[dict], routine_dir: str | Path) -> dict | None:
    """The run's three counts and its `process_share` — the share of its counted actions that
    were NOT its task — or None when it landed no action at all.
    """
    root = str(routine_dir)
    counts = {TASK: 0, PROCESS: 0, FRICTION: 0}
    run_ts = ""
    pending: dict | None = None
    for ev in in_task(events):
        if ev.get("type") == "header" and ev.get("run_id"):
            run_ts = str(ev["run_id"]).rpartition(":")[2]
        payload = ev.get("payload")
        if not isinstance(payload, dict):
            continue
        kind = ev.get("type")
        if kind == "assistant_action":
            if pending is not None:
                counts[classify(pending, None, root, run_ts)] += 1
            pending = payload
        elif kind == "observation":
            if pending is not None:
                counts[classify(pending, payload, root, run_ts)] += 1
                pending = None
            # a chain element the engine never ran is a turn's worth of writing lost
            not_run = payload.get("not_executed")
            counts[FRICTION] += len(not_run) if isinstance(not_run, list) else 0
        elif kind == "error" and payload.get("where") == "schema":
            counts[FRICTION] += 1
    if pending is not None:
        counts[classify(pending, None, root, run_ts)] += 1
    total = sum(counts.values())
    if not total:
        return None
    return {**counts, "process_share": round((counts[PROCESS] + counts[FRICTION]) / total, 3)}


def of_run(run_dir: Path, routine_dir: str | Path) -> dict | None:
    """One run's overhead from its transcript (plain or rotated `.gz`), or None when there is
    no transcript or no action in it — UNKNOWN, never a zero share.
    """
    path = Path(run_dir) / "transcript.jsonl"
    if not path.exists() and not path.with_suffix(".jsonl.gz").exists():
        return None
    return from_events(_lines(path), routine_dir)
