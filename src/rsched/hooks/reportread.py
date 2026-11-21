"""The report ledger READ FROM THE HOOK STREAM — D152 campaign step 9, "replace last".

`hooks/store.py` states the design as *wrap first, replace last*: every mechanism mirrors into
the stream, then each one in turn stops reading its own projection. Step 4 did the write half
for reports — the hook is written first and `reports.jsonl` is derived from it. This module is
the READ half: `reports.read_reports` answers from `.control/hooks.jsonl` and nothing else.

**Why this is one module and not twenty.** Roughly twenty modules answer a question from the
report ledger — the open-thread cap, the item read models, the orphan sweep, the Messages page,
the priorities fold, the finish hold. Not one of them is touched here, because every one of them
goes through `reports.read_reports`. The seam was already in the right place; what was behind it
was the wrong file.

**The stream is the ONLY read source.** Until the comparison was retired the read also folded
`reports.jsonl` on every cache miss and served it whenever it named a report the stream lacked.
Measured on the live instance before that was removed: the stream held exactly as many report
rows as the ledger, the shortfall event had not fired since 2026-11-07, and every miss parsed
both stores (~21 MB) only to find them equal. So a read never opens `reports.jsonl` now, and the
memo fingerprint covers the stream alone.

**What the ledger file is FOR, now that nothing here reads it.** Readers outside this engine:
the operator reads it by eye and routine scripts fold it. That is why the WRITE stays
(`hooks/reportproj`) — one derivation, the old file's bytes unchanged.
"""

from __future__ import annotations

from pathlib import Path

from .reportproj import rebuild
from .store import hooks_path


def read_reports(path: Path) -> list[dict]:
    """Every report, folded into one row per id, in filing order — THE read, for every caller.

    Re-exported as `reports.read_reports`, which is the name its ~20 callers hold; it lives
    here because this module owns what is read. `path` still names the LEDGER: it identifies
    the instance, every caller already holds it, and the stream is found beside it.

    A `delivered`, `retracted` or `superseded` event is merged into its report as a
    `delivered: {ts, run_id}` / `retracted: {ts}` / `superseded: {ts, by, to}` key; an event
    naming no report is dropped rather than becoming a phantom item. The fold is
    `reports.fold_rows` (through `reportproj.rebuild`), never a second implementation of it.

    Folded ONCE per append, behind the read-models' stat fingerprint over the STREAM: it is
    append-only and the fold was re-run by every routine-page open (the Messages panel), every
    orphans read and every outbox retraction — a per-request cost that grows with how much the
    fleet talks. The returned rows are SHARED and must be treated as immutable; every caller
    folds them into new dicts.
    """
    from ..readmodels import memo

    home = path.parent.parent
    return memo.memoized_shared(f"reports:{path}", [hooks_path(home)], lambda: rebuild(home))
