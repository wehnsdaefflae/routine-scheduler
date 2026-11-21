# The test suites and the gates

How the fast suite, the browser suite and the quality gates run, and the floors they hold.
CLAUDE.md's Commands section is the short form.


- `uv sync` — install/refresh the venv, on Python 3.12: `.python-version` pins the host to the
  interpreter the engine image runs (`FROM python:3.12-…`, kept in step by `tests/test_policy.py`);
  the suite refuses to start on another minor version — a host run on 3.14 once tested an
  interpreter production does not use
- `uv run pytest -q` — full suite (fast, no network; PARALLEL by default via pytest-xdist
  `-n auto --dist worksteal` in addopts, `auto` decided by `browsersource.auto_workers`: cores
  minus one, capped at `FAST_WORKERS` (4), and capped again by **the cgroup headroom there IS**
  (D291 — beside a live routine run the stage runs with FEWER workers instead of being refused,
  which is the trade the operator chose; an idle box still resolves to 4 and still costs 3,584 MB,
  so no gate pays wall clock for a coexistence nobody needs) — pass `-n0` for a serial run /
  debugging with `-s`. Every session first WAITS until
  BOTH the HOST's `MemAvailable` and THIS CONTAINER's own cgroup headroom cover its selection
  (`tests/memgate.py`, the ONE memory-admission module: the fast charge is DERIVED from the
  worker count — 930 MB controller + 610 MB per worker, rounded up to 256 MB, which re-derives
  the old 3,584 MB at four workers — 2,048 MB browser (read against the `uitest` cgroup alone),
  2,816 MB serial): on 2026-11-05 a gate started on a host with ~2 GB free and the global OOM
  killed a live run. It then WATCHES the session — a watchdog thread SIGINTs it once the
  cgroup's hold passes the charge plus 512 MB — and past `RSCHED_TEST_MEM_WAIT_S` it starts
  under that watchdog rather than exiting with no verdict (D295); `RSCHED_TEST_MEM_WAIT=0`
  switches the wait and the watchdog off. Every charge is a stage's MEASURED hold —
  `held_peak_bytes`, anon + swap — not an estimate; `NEED_MB`'s comment holds the table. Two conftest
  env knobs keep it honest AND fast: `RSCHED_SKIP_DOCS_BUILD` (the app lifespan's pdoc build
  — a to_thread task shutdown can only await) and `RSCHED_RETRY_BASE_DELAY` (endpoint retry
  LOGIC runs, the 1s/2s backoff clock doesn't); test_docs_build / test_with_retries_backoff
  clear them to pin the real paths. Single test: `uv run pytest tests/test_loop.py -q`
  or `-k <name>`. Live endpoint smoke tests run only with `RSCHED_LIVE_TESTS=1`.
  **Every run NAMES ITS SLOWEST ITEMS** — addopts carry `--durations 25 --durations-min 1.0`,
  so the tail is in every gate log and the only per-test cost the project used to record was a
  per-stage aggregate. The expectation they measure against is `pyproject.toml`'s own, beside
  `timeout = 300`: the slowest legitimate test takes "well under a minute", which leaves a
  299-second blind spot the deadline cannot see. F739 lived in it — a test polling to a
  one-minute ask timeout cost ~120 s of EVERY release gate until someone noticed by hand.
  Pinned by `tests/test_suite_durations.py`.
- **The BROWSER suite is opt-in.** `tests/ui/` drives the REAL console with Playwright over a
  stub runner (no scheduler, no engine, no LLM; see `tests/ui/conftest.py`). Its conftest marks
  the whole directory `ui` AND puts it in one `xdist_group`, and addopts carry `-m "not ui"`,
  because ~276 browser tests against ~2 150 fast ones is the difference between a two-minute
  gate and a thirty-minute one — and it
  is the thirty that decides whether the inner loop gets used. Three selections:
  `uv run pytest -q` (fast), `-m ui` (browsers), `-m ""`
  (everything — **what ships a release**).
  **The browser selections run in the `uitest` container, never beside the daemon**: the
  suite launches a Chromium per xdist worker (`tests/browsersource.py`), and run in the engine
  container it shared the daemon's 5 GiB memory cgroup — the kernel's OOM killer took the DAEMON
  eight times between 2026-10-22 and 2026-10-30, each time ending the container and orphaning
  every live run. `uitest` is the engine image in a cgroup of its own, mounting the checkout and
  a read-only browser cache and nothing live: `docker compose exec uitest
  /opt/rsched-venv/bin/python -m pytest -q -m ui`. The release gate reaches it from INSIDE the
  engine container through a spool (`deploy/uitest/`: the runner is the service's only process,
  `submit.py` streams a job back as if it ran in place, under the caller's own `flock`); a
  caller that can write that spool can already write the code the daemon imports, so it grants
  nothing. Every finished job also carries the PEAK of that container's own memory cgroup for
  its duration (`deploy/uitest/cgroupmem.py` reads it inside `uitest`, because the gate is in
  the engine container and cannot read a sibling cgroup; `submit.py` prints it as
  `uitest memory: {…}` after the job's output, and `deploy/DOCKER.md` says how to set
  `mem_limit` from a week of those peaks). That number says "this stage peaked at N GB of its
  M GB ceiling" and can NEVER say "no OOM occurred" — a restart starts a fresh cgroup, so only
  the host's `journalctl -k` settles that. To WATCH the suite, attach it to the `chrome` sidecar instead — engine container,
  `RSCHED_TEST_CDP=http://172.30.7.10:9222 RSCHED_TEST_BIND=172.30.7.2 BROWSER_CDP_TOKEN=…`, one
  worker, so its memory stays small. Whenever browser tests are in the selection, `tests/conftest.py::pytest_configure`
  switches xdist to `--dist loadgroup`, so that one group runs on ONE worker while the fast suite
  keeps spreading — three workers rendering into one headful Chrome is what the rerun shield had
  been absorbing. Still serialize any browser run under
  `flock /tmp/rsched-gates.lock`: two concurrent gates is the OOM this box has already hit once.
  EVERY UI change still gets exercised there — it is the safety net that lets the frontend be
  reworked boldly, and deselecting it by default only trades the safety for speed if the absence
  is LOUD. So every fast run ends by naming the suite it skipped, and says so pointedly when
  `static/` carries uncommitted changes (`tests/conftest.py::pytest_terminal_summary`). A suite
  that silently does not run reads exactly like a suite that passes.
  `tests/production_guard.py` is the suite's SANDBOX FLOOR (session-scoped autouse): no test
  may write inside the live instance's data homes, and none may spawn this package's CLI —
  that child is a fresh interpreter that would load the production config. Never weaken it to
  make a test pass; a test that needs to write there is pointed at `tmp_path` instead.
  `tests/render_guard.py` is its RENDER FLOOR: every observation `executor.dispatch` or
  `actionroute.dispatch_action` returns in a test is RENDERED, and a renderer that raises FAILS
  the test — the engine's own floor, which keeps a live run going, raises in the suite. A test
  of the result DICT proves nothing about the words the run reads: 0.410.0 tested three refusals
  that way and the first live one ended a run. A handler test past both seams renders its own.
  `tests/test_double_conformance.py` is its DOUBLE-CONFORMANCE FLOOR: every attribute a test
  double ASSIGNS must exist on the class it impersonates (`loop` → `EngineLoop`, `ctx` →
  `RunContext`, `budgets`, `transcript`), read statically with `ast` over `tests/**.py` — never
  by importing them. F644 is why: `engine/ladder.py` dispatched through `loop.subs` where the
  real name is `loop.subruns`, and the ladder's tests passed because their fake loop defined
  `subs` to match the caller, so no rung judged anything for months. A double's attribute
  surface is authored by the test out of the same misreading that produced the caller, so the
  two agree with each other and with nothing else. `SimpleNamespace` stays the suite's double
  (410 of them) — the floor checks what they assign, bans nothing. A real attribute may be
  declared in any of FOUR places and the checker reads all four (class annotations, class
  assignments, `self.x =` in its own methods, and a COLLABORATOR's `loop.x = …` anywhere under
  `src/` — `_recipe_unlocked` exists only because `engine/loopsetup.py` assigns it), so
  `dir(cls)` is not the surface. Its `MIN_DOUBLES`/`MIN_SURFACE` floors make a detector that
  stops finding doubles red instead of passing vacuously: widen the recognised shapes, never
  lower a floor. The extraction (the `ast` reader both rules share) is `tests/double_survey.py`;
  that file is the reader, this one is the rules — the same split as
  `tests/class_survey.py` / `tests/test_static_class_contract.py`.
  Its SECOND rule runs the other way and F766 (0.448.0) is why: a `SimpleNamespace` the real
  dataclass would have CONSTRUCTED from the same keywords is itself the violation
  (`test_no_namespace_stands_in_for_a_real_dataclass`). The stand-in at
  `tests/test_util_guard.py:85` assigned `parsed` and `text` — both REAL `Completion` fields, so
  the assigns-only rule was green — and the release broke on the field it OMITTED
  (`parsed_rest`, F754), which `engine/completion.py` had just started reading. No check of
  assigned names can see a field that is not there, so the rule removes the hand-built surface
  instead of policing it: **a scripted endpoint returns a real `Completion`, never a namespace
  shaped like one**, and the same goes for any `endpoints/`/`engine/` dataclass with a required
  field. Recognition is by SHAPE, so it needs no coverage list to maintain — which is the point:
  measured on this tree, 441 keyworded `SimpleNamespace(...)` sites in `tests/` yield 4 hits, 0
  false, and it found a site the defect report's own grep had missed. Scope is `endpoints/` +
  `engine/` and a claimable class needs ≥1 required field: over all 48 dataclasses under
  `src/rsched` the same rule flags 12 sites of which 8 are false, because a dataclass with no
  required field (`GrantPolicy`, 20 fields) matches any namespace at all. Its floors are on the
  scan's MACHINERY (`MIN_SCOPED`, `MIN_NAMESPACE_SITES`), not on its hits — zero hits is the
  healthy state, so a floor on hits would be a floor on the defect.
- `uv run ruff check` + `uv run mypy` — the strict quality gates (ruff runs `select = ALL`;
  every ignore in pyproject.toml carries its house-style reason). Both MUST be green in every
  commit; `uv run pre-commit install` wires them into git.
- `uv run rsched run-once <slug>` — execute one run from the CLI (slug under `routines_home`, or a dir
  path), streaming events. `--model kind=catalog-name` overrides a model role (catalog names, not endpoint:model pairs) and `--effort kind=level` a role's reasoning effort (effort is its own setting, `routine.yaml` `efforts:` — never a catalog row per level); `--quiet` drops the stream.
- `uv run rsched daemon` — scheduler + web UI in one process (what systemd runs).
- `uv run rsched validate | lint | suggest --instruction … | scaffold <slug> --workflow … | abort <slug>[:<ts>] | drain | postdeploy | reports --wrongly-settled`
  — see `rsched --help`. `validate` checks every routine's `routine.yaml`, lints its recipe
  prose (`main.md`, `stages/`) AND reports its SETUP SURFACE (`readmodels/surface.py` joined with
  `readmodels/remedies.py`: what the held docs, bound rules and reserved utils still need, each
  unmet line ending in the remedy that settles it — a `blocks` row fails the command, a
  `warn`/`note` row is reported only — and so does a recipe CONTRACT defect, the one recipe
  finding that is not a phrasing: `FAIL  recipe ·`, see "A contract names only the task"); `lint`
  covers the library. `reports --wrongly-settled` reads the report LEDGER — the rows a
  non-owner disposed of before D182 closed that gate (`report_threads.disposed_by_non_owner`,
  R3246): id, sender, date and title, `--json` for one document. It is a READ, so no row
  re-opens and no verdict changes — the rows stay settled, deliberately; what was missing was
  the ability to ASK which ones they were. `engine-run` is internal
  (daemon-spawned). `drain` and `postdeploy` BRACKET A DEPLOY and are the only two commands that
  are about the instance rather than about its config: drain quiesces the box before the restart
  (F746), postdeploy checks the box AFTER it (R2428) — served build vs the checkout, backup
  freshness, state-path ownership, and which instance answered — printing one line per check and
  appending one row to `.control/postdeploy.jsonl`. Exit 1 means ACT; a NOTE keeps it green. It
  exists because a deploy session is this project's highest-yield defect detector (nine of
  2026-10-01's releases say "found while deploying") and was entirely unstructured: the checks are
  pure functions in `rsched/postdeploy.py` over inputs `cli_postdeploy.py` reads, so the
  checklist is testable without a live box.
