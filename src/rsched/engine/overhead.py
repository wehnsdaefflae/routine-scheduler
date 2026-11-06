"""How much of a run went to its TASK, and how much to its own PROCESS — counted, every run.

The operator's standard (2026-11-06): a routine whose runs are mostly about bookkeeping — its
own ledger, state, records, reconciliation — rather than its task is a CRITICAL failure, and it
must never again happen unnoticed. A hand classification of ~10,500 turns that day found 12 of
24 routines bookkeeping-dominated, and nothing on the instance had said so. This is the
detector: a deterministic reading of the run's LANDED actions and the observations the
transcript records for them, so it runs the same over a live run and over a kept transcript.

Every landed action is one of three:

- FRICTION — a turn the harness took: a HELD action (`hold.is_hold`), a deferred finish, an
  action refused or failed in the engine (an `error` observation — an anchor miss, the memory
  cap, a gate), a `script`/`util` that exited non-zero (a `shell`'s non-zero is grep saying "no
  match", as engine/frictions.py rules), plus every schema-rejected reply and every chain
  element reported NOT EXECUTED.
- PROCESS — the run working on itself: `memory_*`, `read_rule`, `goal`, `job`, the finish; a
  file action whose every target is a process artefact (its recipe — `main.md`, `stages/`, at
  any depth so a job's recipe counts — its notebook, inbox, past runs, `.util_outputs/`, the
  engine's own `state/` files, and anything NAMED as a record: ledger, backlog, journal,
  worklog, changelog, tracker, `log`); the outcome script; a script or util whose every named
  path is such an artefact, or whose PROGRAM is one (`bin/ledger.py add …`); and a `shell` —
  the one-off action — over nothing but such artefacts and the routine's own `state/`, which is
  the run hand-keeping its records field by field. Outside the own dir only a
  ledger/backlog/journal/worklog counts: a project's CHANGELOG there is its product.
- TASK — everything else. Product data under `state/` is the task's when a file action or a
  script writes it; `report` and `ask_user` are the task's too, because for some routines a
  report or a question IS the product and nothing here can tell which.

`process_share` is (process + friction) / all — the share NOT spent on the task.

**Calibration** (2026-11-06, against the hand classification of the same runs — the last ≤4
runs since 2026-10-23 of each routine; median of the per-run shares, as the invariant reads it):

    judged bookkeeping-dominated (hand task %)   median   |  judged healthy (hand task %)  median
    library-sync (25)                             0.583   |  voice-model-trainer (66)       0.263
    token-lab (49)                                0.467   |  funscript-trainer (65)         0.255
    rules-review (43)                             0.411   |  global-utils-review (65)       0.232
    moltbook-heartbeat (48)                       0.409   |  deficit-coach (77)             0.216
    vodafone-outage-claims (43)                   0.396   |  scheduler-improvement-res (61) 0.204
    predatory-publisher-tarpit (38)               0.380   |  scheduler-builder (68)         0.160
    llmsectest-weekday (42)                       0.352   |  folder-reorg (80)              0.155
    config-optimizer (48)                         0.344   |  routine-improver (71)          0.136
    uncensored-model-radar (43)                   0.335   |
    fau (42)                                      0.308   |  borderline (not scored):
    tv-show-tracker-seedbox-manager (39)          0.277   |  doppelcheck 0.267, freelance 0.263,
    self-audit (45)                               0.206   |  grant-hunter 0.247, website 0.223

`THRESHOLD` = 0.27, the midpoint of the gap between the highest healthy median (0.263) and the
lowest flagged one (0.277): 11 of 12 flagged, 0 of 8 healthy. The miss is self-audit, whose
bookkeeping is its item ledger (`audit/report.json`, named as nothing) maintained through its
own scripts and 31 reports — both TASK by the rules above, which stay conservative rather than
learn one routine's file names. That is a LIMIT, not a verdict: bookkeeping kept on a DOMAIN
ledger — a store named for what it tracks rather than as a ledger, worked through the routine's
own tooling, or reports filed as work — is outside what this classifier sees, and a routine doing
only that reads healthy here. The complementary net is routine-improver's outcome review, which
reads each routine's OUTCOME series (readmodels/outcome_series.py) before any health claim: a
routine whose measures stay flat while its runs keep finishing `ok` is caught there, whatever
its turns were spent on. Four rules were MEASURED and rejected: counting `report` /
`ask_user` as process (one healthy routine over the bar — scheduler-improvement-research files
reports as its product), counting every read of `state/` (deficit-coach's product reads tie the
lowest flagged routine), not counting the finish (3 healthy routines over at any bar that flags
the 12), and `changelog` outside the own dir (scheduler-improvement-research reads the
scheduler's CHANGELOG as its input). The two scales differ — the hand's bar is task < 50%, this
one's is 27% — because the hand also judged ORIENT reads of product state and reports as
bookkeeping, which a deterministic rule cannot.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from . import hold
from .frictions import _lines

TASK, PROCESS, FRICTION = "task", "process", "friction"

#: The fleet invariant: a routine whose MEDIAN `process_share` over its last `WINDOW` depth-0
#: runs UNDER ITS CURRENT RECIPE reaches this is dominated by its own process (calibration in the
#: module docstring; the recipe window is readmodels/overhead_reading.py's).
THRESHOLD = 0.27
#: How many of a routine's newest measured depth-0 runs the invariant reads, and how many it
#: needs before it judges at all — one long orientation run is not a routine's habit.
WINDOW = 5
MIN_RUNS = 3

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
#: Directories directly under the routine's own dir that hold nothing but its process.
_PROCESS_DIRS = frozenset({"inbox", "questions", "runs", ".util_outputs"})
#: Engine- and recipe-owned process files in a `state/` dir — everything else there is product.
_PROCESS_STATE = frozenset({"phase.json", "plan.md", "notes.md", "obligations.json",
                            "finish-line.json", "jobs.json", "reminders.json"})
#: A file or folder NAMED as a record of the process, wherever in the routine's own dir it sits
#: (`LEDGER.md`, `state/backlog.md`, `journal/`, `mail-log.jsonl` — `log` only as a whole word).
_PROCESS_NAME = re.compile(r"ledger|backlog|journal|worklog|changelog|tracker"
                           r"|(^|[-_.])log([-_.]|$)", re.IGNORECASE)
#: Outside the own dir a CHANGELOG is a project's product and a "tracker" a product's name; a
#: ledger, backlog, journal or worklog there is still the run's own record of its work.
_OUTSIDE_NAME = re.compile(r"ledger|backlog|journal|worklog", re.IGNORECASE)
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


def is_process_path(path: str, routine_dir: str) -> bool:
    """Is `path` one of the routine's own PROCESS artefacts (its recipe, notebook, phase, plan,
    notes, inbox, past runs, or a file named as a ledger/backlog/journal)?
    """
    rel = _relative(path, routine_dir)
    if rel is None:     # outside the own dir: only a file or folder NAMED as a run's record
        return any(_OUTSIDE_NAME.search(part) for part in PurePosixPath(path).parts[-2:])
    if rel in (".", ""):
        return False
    parts = PurePosixPath(rel).parts
    if (rel == "scripts/outcome.py" or parts[0] in _PROCESS_DIRS or parts[-1] in _RECIPE_FILES
            or _PROCESS_ANYWHERE.intersection(parts)):
        return True
    if parts[-2:-1] == ("state",) and parts[-1] in _PROCESS_STATE:
        return True
    return any(_PROCESS_NAME.search(part) for part in parts)


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


def _call_is_process(action: dict, routine_dir: str) -> bool:
    name = str(action.get("name") or "")
    if (action.get("kind") == "script" and name == "outcome") or _PROCESS_NAME.search(name):
        return True
    named = [t for t in _call_tokens(action) if _relative(t, routine_dir) not in (".", "")]
    if not named:
        return False
    # a script or util handed a record-keeping PROGRAM first (`bin/ledger.py add …`) is keeping
    # that record, whatever else it names; otherwise every path it names must be the process's
    if action.get("kind") != "shell" and _PROCESS_NAME.search(PurePosixPath(named[0]).name):
        return True
    if action.get("kind") == "shell":
        # the ONE-OFF action over nothing but the routine's own state: the run hand-keeping its
        # records field by field, not doing its task (a script over the same files is tooling)
        return all(is_process_path(t, routine_dir) or _in_state(t, routine_dir) for t in named)
    return all(is_process_path(t, routine_dir) for t in named)


def classify(action: dict, obs: dict | None, routine_dir: str) -> str:
    """One landed action, with the observation it came back with: TASK, PROCESS or FRICTION.

    FRICTION first — a held action, a deferred finish and a refused or failed action are turns
    the harness took, whatever they were aimed at. `report` and `ask_user` are the TASK's: for
    some routines a report or a question IS the product, and a classifier cannot tell which.
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
        process = bool(targets) and all(is_process_path(t, routine_dir) for t in targets)
        return PROCESS if process else TASK
    if kind in CALL_KINDS:
        return PROCESS if _call_is_process(action, routine_dir) else TASK
    return TASK


def from_events(events: Iterable[dict], routine_dir: str | Path) -> dict | None:
    """The run's three counts and its `process_share` — the share of its counted actions that
    were NOT its task — or None when it landed no action at all.
    """
    root = str(routine_dir)
    counts = {TASK: 0, PROCESS: 0, FRICTION: 0}
    pending: dict | None = None
    for ev in events:
        payload = ev.get("payload")
        if not isinstance(payload, dict):
            continue
        kind = ev.get("type")
        if kind == "assistant_action":
            if pending is not None:
                counts[classify(pending, None, root)] += 1
            pending = payload
        elif kind == "observation":
            if pending is not None:
                counts[classify(pending, payload, root)] += 1
                pending = None
            # a chain element the engine never ran is a turn's worth of writing lost
            not_run = payload.get("not_executed")
            counts[FRICTION] += len(not_run) if isinstance(not_run, list) else 0
        elif kind == "error" and payload.get("where") == "schema":
            counts[FRICTION] += 1
    if pending is not None:
        counts[classify(pending, None, root)] += 1
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
