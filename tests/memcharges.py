"""What a test selection COSTS, and which readings bound it.

Split out of `memgate.py` (2026-11-19) when the browser charge was measured: the charges, the
measurements behind them and the question "which reading may refuse this selection" are one
responsibility, and holding them beside the waiting logic put that module over the house line
bar. `memgate` imports these names, so `memgate.need_mb`, `memgate.NEED_MB` and
`memgate.prose_for` still resolve for every caller that already used them.

Every figure here is MEASURED and says where from. Two cost whole release gates by being wrong:
a charge too high refuses a stage that would have fit (the browser figure, 2026-11-19), and a
charge too low admits one that then competes with a live routine run.
"""
from __future__ import annotations

#: MB each selection needs free — of the host's MemAvailable AND of this cgroup's own headroom
#: — before it may start.
#:
#: MEASURED, not estimated (2026-11-10, goal b8). Both figures used to rest on the docstring's
#: "~250 MB each", which understates a fast worker about 2.4-fold. The gate's own sampler now
#: records `held_peak_bytes` — anon + swapped pages, the part dropping page cache cannot free
#: and the part an OOM is decided on — so the cost of a selection is readable instead of
#: guessed. From `.gate-evidence/memory-peaks.jsonl`, every stage carrying that field:
#:
#:     stage       n   held of the 5 GiB cgroup        seconds
#:     tests      27   0.79-1.20 GB (median 0.91)      1-93      controller alone, -n 0
#:     fast        7   3.08-3.29 GB (median 3.16)      205-264   controller + 4 workers
#:     ui          5   0.80-1.03 GB (median 0.99)      640-883   controller + 2 browsers
#:
#: So the fast stage's own increment over a bare controller is 2.25 GB median and **2.38 GB at
#: its worst** — about **600 MB per worker**, against the 250 MB the old figure assumed — and
#: the whole stage HOLDS at most 3,367 MB of the 5,120 MB cgroup, leaving 1,753 MB. `fast` is
#: therefore 3584 MB: that worst measured hold rounded up to the next 256 MB. It is deliberately
#: the WHOLE stage's hold rather than the increment, because the controller is part of what must
#: fit — and it is rounded on the MB figure, not on the GB one (3.29 GB rounds to 3328 MB, which
#: is BELOW the hold it has to cover; the new measured-hold test below caught exactly that).
#:
#: `browser` is 2048 MB, and is the one figure here MEASURED IN ANOTHER CGROUP — it runs in the
#: `uitest` container's own 4 GiB one, not this 5 GiB one (the `ui` rows above are merely this
#: cgroup's idle hold while that container works). Until 2026-11-19 it was 3584 MB, the OTHER
#: container's ceiling standing in for a hold, and nothing measured here bore on it.
#:
#: It is now measured the way the others are, off the number `deploy/uitest/submit.py` has been
#: printing on every job all along (`uitest memory: {"peak_bytes": …}` — the peak of the uitest
#: container's own cgroup for that job's duration, sampled inside it):
#:
#:     ui log                  peak of the uitest cgroup     % of its 4 GiB
#:     1795049048635589242     1876 MB                       43.7
#:     1795046843234129551     1831 MB                       42.6
#:     1795028073593281118     1840 MB                       42.8
#:     1795024667265460770     1811 MB                       42.2
#:     1794890611977997461     1797 MB                       41.8
#:
#: 1,797-1,876 MB over the five most recent stages that really ran — a 4 % spread, which is what
#: a fixed browser suite should look like. So `browser` is the worst of them rounded up to the
#: next 256 MB: 2048. Every reading is `bound: "lower"` (the container mounts `/sys/fs/cgroup`
#: read-only, so the kernel's mark cannot be reset per job and is sampled instead), and the
#: lifetime marks agree at 1,838-1,904 MB, so the rounding carries the gap.
#:
#: THE OVER-CHARGE WAS NOT FREE. 3584 MB against this box's afternoon `MemAvailable` of
#: 2,290-3,008 MB refused the ui stage of three release gates on 2026-11-19 — each with quality
#: and fast already GREEN, each waiting out `RSCHED_TEST_MEM_WAIT_S` and running ZERO browser
#: tests — while the stage's real hold had room to spare in the container it actually runs in.
#: Decided by the operator, who was shown the table above: *"Price `browser` from submit.py's
#: measured peak inside the uitest container, against that container's own headroom"*.
#: The second half of that answer is `READERS_FOR` below: a browser session is read against the
#: uitest cgroup alone, because the engine host's availability does not bound what that
#: container may hold inside its own ceiling.
#:
#: WHY THE WORKER COUNT DID NOT MOVE. `peak_pct_of_limit` reads `memory.current`, which counts
#: reclaimable page cache: 51 fast stages since 2026-11-01 read a median 96.6 % of the limit —
#: 14 of them exactly 100.0 % — with `oom_kill` **0** throughout, and the live counter still at
#: 0 with 197 reclaim events. On the figure that decides an OOM the same stages sit at 61.6-65.8
#: %. Duration does not track it either: the highest hold (65.8 %) took 241 s and the highest
#: peak (100.0 %) took 205 s, while the one 1,710 s stage was the `should_wait` hang below, not
#: memory pressure. Lowering `FAST_WORKERS` would have cost every release gate wall clock
#: against a ceiling the stage was never near on the deciding figure.
#: `serial` is the THIRD entry, added 2026-11-13 (R3376 from `scheduler-builder`). Until then
#: every non-ui selection was charged the four-worker `fast` figure, whatever it selected: a
#: two-file `-n 0` call was gated on 3,584 MB, and on 2026-11-12 that refused ~12 gate calls —
#: his own release gate's FAST stage (exit 3 at 1,201 s with `real_failures: []`) and three of
#: his child runs, which handed back three built items with ZERO test evidence between them.
#: The 2.25 GB the fast figure covers above a bare controller is the four xdist WORKERS, and a
#: serial selection starts none of them, so it was being charged for processes it never spawns.
#:
#: MEASURED the same way as the others, off `held_peak_bytes` in the gate's own
#: `.gate-evidence/memory-peaks.jsonl` — 119 `tests` rows carry the field, and the 23 whose
#: stage log is still on disk can be split by the worker count pytest printed:
#:
#:     selection                       n    held of the 5 GiB cgroup      seconds
#:     -n 0, under 1000 items         21    876-2700 MB (median 1965)     1-442
#:     fast-serial (the gate's own)    5    851-1317 MB (median  934)
#:     2 workers, 6927 items           2    3979-4393 MB                  441-442
#:
#: So `serial` is 2816 MB: the worst measured serial hold (2,700) rounded up to the next 256 MB.
#: It is NOT the 1280 MB R3376 proposed from this module's older n=27 table — that table read
#: 0.79-1.20 GB, and with four times the rows the serial worst case is more than twice it, so
#: the cheaper figure would have admitted a selection it had not checked for. The two-worker
#: rows are the reason the whole-suite `-n 2` workaround is not priced here: it is not serial.
#:
#: Like every figure here this is the whole CGROUP's hold while the selection ran, a live
#: routine run included — deliberately conservative, since what must fit is everything at once.
NEED_MB = {"fast": 3584, "browser": 2048, "serial": 2816}

#: The fast charge DERIVED rather than fixed, because the worker count is now conditional
#: (D291, the operator 2026-11-14: *"Reduce FAST_WORKERS while a routine run is active, trading
#: gate wall-clock for coexistence"* — explicitly not the cgroup raise). A constant charge meant
#: a live run holding the difference REFUSED the gate (exit 3 after up to
#: `RSCHED_TEST_MEM_WAIT_S`) instead of running it smaller; and because the two fit SOMETIMES,
#: that refusal read as a flaky failure rather than as a rule.
#:
#: Both terms come off the measured table above, not from a new measurement. The seven `fast`
#: rows hold 3.08-3.29 GB with four workers and the median increment over a bare controller is
#: 2.25 GB (2.38 GB at its worst), which is the WORKERS: 2.38 GB / 4 = 610 MB each, the ~600 MB
#: per worker this module already states. The controller term is then what the worst four-worker
#: hold leaves: 3,370 = 930 + 4×610 reproduces that hold's rounding exactly (3,367 → 3,584 at
#: 256 MB), which is what `test_four_workers_still_cost_the_3584_mb_the_constant_held` pins.
#: A fast selection is never charged less than the `serial` entry it contains: the 21 serial
#: rows were measured holding up to 2,700 MB, more than this arithmetic's controller term, and a
#: budget below a measured hold is a guard reporting safety it never checked.
CONTROLLER_HOLD_MB = 930
PER_WORKER_HOLD_MB = 610


def fast_need_mb(workers: int) -> int:
    """What a fast selection at `workers` xdist workers must be charged, rounded UP to 256 MB.

    Rounded on the MB figure and never on the GB one — 3.29 GB rounds to 3,328 MB, which is
    BELOW the hold it has to cover, the trap this module's own comment records.
    """
    raw = CONTROLLER_HOLD_MB + max(0, int(workers)) * PER_WORKER_HOLD_MB
    rounded = -(-raw // 256) * 256
    return max(rounded, NEED_MB["serial"])


def need_mb(selection: str, workers: int = 0) -> int:
    """The CLASS charge for one selection — the fast one derived from its worker count, the
    others their measured constants. One reading, so the wait and the refusal message cannot
    disagree.

    Since D295 this is the FALLBACK rather than the whole answer: `priced_need` below prefers
    what THIS selection has actually held, and falls back here when it has no history. The
    class figures stay because a selection nobody has measured must still be charged something,
    and the worst case of its class is the only honest figure for it.
    """
    if selection == "fast" and workers:
        return fast_need_mb(workers)
    return NEED_MB[selection]


#: What each label is called in the message a refusal prints. "the fast suite" was the only
#: phrase before, and it was printed for a two-file `-n 0` call too — which read as a memory
#: leak in the candidate and sent R3376's author looking for one (his point 2).
SELECTION_PROSE = {
    "fast": "the fast suite at 4 xdist workers",
    "browser": "the browser suite",
    "serial": "this serial selection (no xdist workers)",
}


def prose_for(selection: str, workers: int = 0) -> str:
    """How a refusal or a wait NAMES what it is waiting for — with the worker count it actually
    settled on (D291).

    "the fast suite at 4 xdist workers" became a lie the moment the count was conditional, and
    a refusal nobody can read is what sent R3376's author hunting a memory leak in a candidate
    that had none. So the fast selection's phrase is built from the count, and the others keep
    their constant prose.
    """
    if selection == "fast" and workers:
        return f"the fast suite at {workers} xdist workers"
    return SELECTION_PROSE.get(selection, f"the {selection} suite")


def selection_for(config, markexpr: str) -> str:
    """Which NEED_MB entry this session should be charged — by what it will really start.

    The browser suite is priced by its marker expression, because its cost is the Chromiums it
    drives rather than the workers pytest starts. Everything else is priced by whether xdist
    workers will exist: `-n 0` leaves `dist == "no"` and `numprocesses == 0` (measured on this
    box with pytest 9.1.1 / xdist 3.8.0), while the default `addopts` selection resolves to
    `worksteal` with 4. A worker process never reaches here at all — `should_wait` sends it
    straight through, since the controller already waited for the whole selection.
    """
    if "not ui" not in markexpr:
        return "browser"
    serial = (getattr(config.option, "dist", "no") == "no"
              or getattr(config.option, "numprocesses", None) in (0, None))
    return "serial" if serial else "fast"


#: Which readings BOUND a selection. Everything that runs in THIS container is bound by both the
#: host and this cgroup, whichever is shorter — that is `headroom_mb` above and the pair the wait
#: has always enforced.
#:
#: `browser` is the exception, and the reason is where it runs: `deploy/uitest/submit.py` hands
#: the job to the `uitest` container, so this module executes INSIDE that container for the ui
#: stage — where `/sys/fs/cgroup` is uitest's own 4 GiB one and `/proc/meminfo` is still the
#: whole host's. Charging it against the host's `MemAvailable` asked the wrong question: measured
#: 2026-11-19 inside that container, its cgroup had 4,040 MB of headroom (4 GiB ceiling, 54 MB
#: anon) while the host read 2,413 MB available — so a stage with ample room was refused by a
#: number that does not bound it. The host is not ignored because it does not matter; it is
#: ignored because `mem_limit: 4g` plus `memswap_limit: 5g` is what bounds this container, and
#: the kernel enforces that ceiling whatever the host's reclaimable total happens to be. A
#: container that outgrows its own ceiling has its worker killed, which is exactly what the
#: ceiling is for (docker-compose.override.yml: *"Its own ceiling means a suite that runs out
#: kills a test worker, never a live run"*).
READERS_FOR = {"browser": ("this container's cgroup",)}


def readings_for(selection: str, read, read_cgroup) -> tuple[tuple[str, int | None], ...]:
    """The (name, MB) readings that may refuse `selection`, in the order a refusal reports them."""
    every = (("host memory", read), ("this container's cgroup", read_cgroup))
    allowed = READERS_FOR.get(selection)
    return tuple((name, reader()) for name, reader in every
                 if allowed is None or name in allowed)
