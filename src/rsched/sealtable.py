"""THE ONE TABLE of what a run may not touch inside a routine dir, and what it is told when it
tries.

Every layer that enforces a seal reads its names, its flags and its wording HERE:

- the RESOLVED file gate (`engine/fileops._write_gate` / `_memory_gate`, its matching in
  `engine/storeseal.py`), which judges the resolved path against every routine dir the run can
  reach, and its removal half (`storeseal.removal_refusal`);
- the Landlock jail (`ownseal.sealed_names`), which withholds every TOP-LEVEL entry here from a
  subprocess — a nested one (`state/jobs.json`) cannot be withheld without sealing its whole
  parent, so the jail derives from the table and the gate covers the rest;
- the LEXICAL action gates — `GrantPolicy.deny` for `runs/`, `routine.yaml` and the recipe,
  `engine/actions.py` for `.memory/` — a cheap early correction on the relative path as the run
  wrote it, never the seal itself (a traversal walks past a lexical test).

The layers stay separate on purpose: each catches what the others cannot. What merged is their
source of truth — before this table the same seals were spelled in five modules, and the
`runs/` and recipe refusals were each worded twice. Those two now say ONE thing in both layers
(the more specific wording of each pair was kept). Two wordings remain per-layer, each pinned by
a test on its own layer: `routine.yaml`'s lexical refusal names the action's path while the
resolved one opens "routine.yaml is config", and `.memory/`'s lexical one is a validator problem
naming the action kind. Both live on their row (`lexical`), so even those have one home.

ORDER IS PRECEDENCE: the resolved gate refuses with the first row that matches, the rows
flagged `precedes_removal` before a shared-store note or a removal is judged, the rest after.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import runledger
from .engine import finishline, obligations
from .engine.outputs import OUTPUTS_DIR
from .grants import CONFIG_FILE, RECIPE_PREFIXES
from .jobs import JOBS_FILE
from .rundepth import RUNS_DIR

#: `docread.CACHE_DIR`, spelled here because `docread` imports the sandbox, which imports this
#: table through `ownseal`; `tests/test_seal_table.py` pins the two equal.
DOC_CACHE_DIR = ".doc_cache"
#: The entity a recipe refusal routes an access request to.
RECIPE_ENTITY = "action:write_recipe"


@dataclass(frozen=True)
class Seal:
    """One sealed path under a routine dir. `refusal` may carry `{path}`, `{route}` or `{kind}`,
    filled by `worded`; a row with a `lexical` wording says that instead in the action gate.
    """

    name: str                       # relative to a routine dir
    kind: str                       # "dir" | "file"
    refusal: str
    lexical: str = ""
    precedes_removal: bool = False  # judged before a note / removal is (see the docstring)
    reads: bool = False             # a READ is refused too (`.memory/`)
    nested: bool = False            # any same-named entry deeper under a routine dir as well
    exact: bool = False             # the path itself only, nothing beneath it
    child_workspace_exempt: bool = False   # a child's own workspace tree is where it works
    by_name: bool = False           # refused by file name ANYWHERE, and in any removed tree
    recipe: bool = False            # lifted by the recipe-authoring permission
    removal: bool = False           # a removal may not take it with it
    jobs_dial: bool = False         # sealed only while the routine keeps jobs

    @property
    def jailed(self) -> bool:
        """Can the jail withhold it? Only a top-level entry: the jail mounts whole children."""
        return "/" not in self.name

    def worded(self, *, path: str = "", route: str = "", kind: str = "",
               lexical: bool = False) -> str:
        """The refusal a run reads, its placeholders filled."""
        text = self.lexical if lexical and self.lexical else self.refusal
        return text.format(path=path, route=route, kind=kind)


MEMORY_REFUSAL = (".memory/ is reachable only through memory_read / memory_write — the engine "
                  "owns .memory/INDEX.md (built from each note's 'about' line), which a generic "
                  "file action would bypass")
FINISH_LINE_REFUSAL = ("the finish line is the operator's — a run reports against it in its "
                       "finish `accounting` (a distance, or met for an outcome the run proves) "
                       "and never edits it; if you believe it is wrong, file a report saying why")
JOBS_REFUSAL = ("state/jobs.json is the engine's job store — change a job with the `job` "
                "action (create / update / checkpoint / delete), which keeps the run's ledger "
                "and the gate that holds your finish in step with it")
RUNS_REFUSAL = ("runs/ is engine-owned and read-only — transcripts and results are written by "
                "the engine, never by the run.")
CONFIG_REFUSAL = ("routine.yaml is config (permissions, capabilities, budgets, roots) — no run "
                  "edits it, not even the routine-improver (machine-tunable knobs live in "
                  "tuning.yaml); file a deferred ask_user instead")
RECIPE_REFUSAL = ("writing {path!r} would modify this routine's own recipe "
                  "(main.md / stages/ / tuning.yaml), which needs the recipe-authoring "
                  "permission — this routine does not hold it, so its instructions are the "
                  "user's. Describe the change you need in a deferred ask_user (or a report). "
                  "{route}")

MEMORY = Seal(".memory", "dir", MEMORY_REFUSAL, precedes_removal=True, reads=True, nested=True,
              lexical=("kind={kind} may not touch .memory/ — use memory_read / memory_write "
                       "(the engine maintains .memory/INDEX.md for you)"))
RUNS = Seal(RUNS_DIR, "dir", RUNS_REFUSAL, child_workspace_exempt=True)
CONFIG = Seal(CONFIG_FILE, "file", CONFIG_REFUSAL, by_name=True,
              lexical=("writing {path!r} would change routine config (routine.yaml — "
                       "permissions, capabilities, budgets, roots). Config is the user's: NO "
                       "run edits it, not even the routine-improver (machine-tunable knobs like "
                       "deliberation live in tuning.yaml). File a deferred ask_user describing "
                       "the change you need."))

#: The recipe's top-level entries, from the prefixes `grantpolicy.is_recipe_path` tests — which
#: is also how the resolved gate matches them (a job's own recipe included).
RECIPE = tuple(Seal(p.strip("/"), "dir" if p.endswith("/") else "file", RECIPE_REFUSAL,
                    recipe=True) for p in RECIPE_PREFIXES)

SEALS: tuple[Seal, ...] = (
    MEMORY,
    Seal(str(finishline.FILE), "file", FINISH_LINE_REFUSAL, precedes_removal=True, exact=True,
         removal=True),
    Seal(str(obligations.FILE), "file", obligations.REFUSAL, precedes_removal=True,
         removal=True),
    Seal(runledger.FILE, "file", runledger.REFUSAL, precedes_removal=True, removal=True),
    Seal(runledger.ARCHIVE_DIR, "dir", runledger.ARCHIVE_REFUSAL, precedes_removal=True,
         removal=True),
    Seal(JOBS_FILE, "file", JOBS_REFUSAL, precedes_removal=True, removal=True, jobs_dial=True),
    RUNS,
    Seal(OUTPUTS_DIR, "dir",
         f"{OUTPUTS_DIR}/ is engine-owned and read-only for the run — it is the saved full "
         "text of util output too large for its observation (read_file it); a run does not "
         "rewrite the record of what a util returned"),
    Seal(DOC_CACHE_DIR, "dir",
         f"{DOC_CACHE_DIR}/ is engine-owned and read-only for the run — it holds the Markdown "
         "read_file converted documents to; read_file the document itself, and write what you "
         "derive from it elsewhere"),
    CONFIG,
    *RECIPE,
)


def recipe_refusal(path: str, route: str) -> str:
    """What a write into the routine's own recipe is told, in either layer."""
    return RECIPE_REFUSAL.format(path=path, route=route)


def jail_names(*, recipe_unlocked: bool, kind: str = "") -> tuple[str, ...]:
    """The top-level names the jail withholds — every jailable row, the recipe's only while it
    is locked — optionally of one `kind`, in table order.
    """
    return tuple(s.name for s in SEALS if s.jailed and (not kind or s.kind == kind)
                 and not (s.recipe and recipe_unlocked))
