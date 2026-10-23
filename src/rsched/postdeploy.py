"""Is the instance that is NOW RUNNING actually healthy, and is what it serves what we built?

The question `rsched validate` and `rsched drain` bracket but neither asks. Validate asks whether
the config is flyable; drain quiesces the box so the restart interrupts nothing. Both run BEFORE.
Nothing ran after — and a deploy session is this project's highest-yield defect detector:

> Read 2026-10-01's releases as a dataset: only 0.374.0 came from a scheduled detector; everything
> else came from a human mid-deploy. `rsched-backup.service` had failed 19 nights unnoticed
> (0.374.3); a deploy script drove the WRONG HOST's Docker daemon (0.374.0, ~5.2 GB built
> locally); the library export published three live credentials (0.373.0); nightly backups failed
> on a root-owned proxy file (0.373.1).

The hypothesis "the gate ignores deploy" is FALSE — there are 10+ deploy test modules, and more of
them would have caught none of the four, because each was a property of THE LIVE INSTANCE and not
of a fixture. The gap is DETECTION: the highest-yield inspection in this project was unwritten, had
no checklist, and produced no record. `deploy/DOCKER.md` §1 verifies a BUILD ARTEFACT (the image
answers, node is v24) — not the instance that is serving afterwards.

So this is a CHECKLIST, fixed and named, each entry derived from one of those four specimens, and
a RECORD, so the session leaves a trace instead of living in the operator's head:

* `served-identity`   — WHICH box answered, printed before anything else (the wrong-Docker-daemon
                        specimen: "I was looking at the wrong machine" must be impossible to do
                        silently). Unreachable is a FAIL, and then nothing below is a verdict.
* `build-skew`        — the version the daemon SERVES against the checkout it was deployed from.
                        A deploy that did not actually relaunch is the commonest silent failure,
                        and `docker compose up -d` NEVER reloads code (CLAUDE.md, "Deploy").
* `backup-freshness`  — how long since `rsched-backup.service` last COMPLETED (F602's 19 nights).
* `state-path-ownership` — the homes the daemon must write, owned by a uid it can write as (the
                        root-owned-proxy-file specimen: one root 0600 file fails its whole home,
                        and a failed home keeps no snapshot that night).

EVERY FUNCTION HERE IS PURE, over inputs the caller passes in — a parsed status payload, a
`staleness()` verdict, a list of stat results. The HTTP call, the config read and the `os.stat`
live in `cli_postdeploy.py`. That is the split `conformance.py` + `cli_validate.py` already use,
and it is what makes the checklist testable at all: a test can build the 19-nights shape, or a
root-owned home, without a live instance and without `tests/production_guard.py` having anything
to object to.

WHAT IS DELIBERATELY NOT RE-DERIVED. `daemon/backup_watch.staleness` is already the ONE reader of
backup state and `web/assetskew` already publishes the build verdict on `/api/status`; both are
consumed here as given. A second implementation of either would be a second thing to keep true.

FATAL vs REPORTED — the choice a post-deploy check lives or dies by. A command that exits non-zero
on something benign is run once and then never again, so the exit code means exactly one thing:
**the operator must act before he walks away from this deploy.** FAIL (exit 1) is for that; NOTE
(exit 0) is for what he should see and need not act on — a home a fresh install has not created,
a deployment with no git stamp to compare. No check here is advisory about THIS deploy: skew means
the deploy did not land, and a backup that has not completed means the instance is unprotected
right now, whichever night it broke.

And the one deliberate divergence from `backup_watch`: a backup record that cannot be READ AT ALL
is a FAIL here, where the daemon's watch calls it `unknown` and claims nothing. The watch is
right for a fleet-wide health event filed off a 5-second tick — a share the daemon cannot see is
not evidence that a backup failed. This command is a person standing at the box he just deployed,
and F602's lesson is the opposite one: the indicator was ABSENT, a grep returned zero hits, and
nineteen nights passed. An absence a human is looking at is a finding, not a silence.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The three verdicts a line can carry. A human reads this output, so the words are fixed and
#: unambiguous — never "warn", never a bare exit code, never a count he has to interpret.
PASS, NOTE, FAIL = "PASS", "NOTE", "FAIL"

#: The checklist, in the order a session reads it: what answered, then whether it is the build we
#: deployed, then the two standing conditions. Fixed and named, because the complaint this exists
#: to answer is that the highest-yield inspection in the project had no checklist at all. A check
#: added here is added to `cli_postdeploy.run_checks` and to `deploy/DOCKER.md`'s list in the same
#: change, or the session and the command disagree about what was looked at.
CHECK_NAMES = ("served-identity", "build-skew", "backup-freshness", "state-path-ownership")


@dataclass(frozen=True)
class Check:
    """One checklist entry's outcome: its fixed name, its verdict, and why in the operator's words.

    `detail` is written to be read by someone who did not write the check and will not open the
    source: it names the value it saw, the path or URL it saw it at, and what to do.
    """

    name: str
    verdict: str
    detail: str

    def line(self) -> str:
        """The one operator-visible line. The verdict leads, so a column of them scans."""
        return f"{self.verdict:<4}  {self.name:<21} {self.detail}"


@dataclass(frozen=True)
class PathStat:
    """One state path as the caller's `os.stat` saw it — the whole impure part, passed in.

    `uid` is -1 when the path does not exist, so a missing home can never read as owned by root.
    """

    path: str
    exists: bool
    uid: int = -1


def served_identity(url: str, status: dict | None, source: str) -> Check:
    """WHICH instance answered, and from where the address came.

    0.374.0: a deploy script drove the wrong host's Docker daemon and built ~5.2 GB locally. The
    cure is not cleverness, it is printing the address and its provenance before any verdict —
    and refusing to pretend the rest of the checklist means anything when nothing answered.
    """
    if status is None:
        return Check(CHECK_NAMES[0], FAIL,
                     f"no answer from {url} ({source}) — nothing below is a verdict about the "
                     "instance you deployed; check you are on the right host and that the "
                     "container is up (`docker compose ps`)")
    version = str(status.get("version") or "")
    build = str(status.get("build") or "")
    where = f"{url} ({source})"
    if not version:
        return Check(CHECK_NAMES[0], NOTE,
                     f"{where} answered, but its payload carries no `version` — this is not an "
                     "rsched /api/status, so you may be talking to a different service")
    tz = str(status.get("server_tz") or "?")
    runs = status.get("active_runs")
    return Check(CHECK_NAMES[0], PASS,
                 f"{where} — rsched {version}, build {build or '(no git stamp)'}, tz {tz}"
                 + (f", {runs} run(s) active" if runs is not None else ""))


def build_skew(status: dict | None, checkout_version: str, checkout: str = "") -> Check:
    """Does the daemon SERVE the build it was deployed from? Two independent axes, either fatal.

    `docker compose up -d` compares the CONFIG and no-ops when only the bind-mounted source
    changed, so a green `compose config` and a `Container rsched Running` both look like success
    and mean nothing about what is live (CLAUDE.md, "Deploy"). Two things can say so:

    * the VERSION the process serves against `__version__` in the checkout it was deployed from —
      readable from outside, and it still works on a deployment with no usable git;
    * `/api/status`'s own `build_skew` (`web/assetskew`, F696), the daemon comparing the stamp it
      booted with against the checkout NOW. That one is deliberately false whenever either stamp
      is unknown — "no stamp must never read as a skew" — which is the hole the version axis
      closes, so both are read and neither is trusted alone.

    A payload that never arrived is NOTE, not PASS and not FAIL: `served-identity` already holds
    that finding, and one defect reported twice teaches nobody anything (the rule
    `conformance.chain_problems` follows for an uncatalogued model).
    """
    if status is None:
        return Check(CHECK_NAMES[1], NOTE,
                     "not determined — /api/status did not answer, so the served build is "
                     "unknown (see served-identity above)")
    served = str(status.get("version") or "")
    booted = str(status.get("build") or "")
    disk = str(status.get("disk_build") or "")
    at = f" at {checkout}" if checkout else ""
    if served and checkout_version and served != checkout_version:
        return Check(CHECK_NAMES[1], FAIL,
                     f"the daemon serves {served} and the checkout{at} is {checkout_version} — "
                     "the deploy did NOT relaunch the process (or it relaunched from another "
                     "tree). `docker compose up -d` never reloads bind-mounted code")
    if status.get("build_skew"):
        return Check(CHECK_NAMES[1], FAIL,
                     f"the process booted at build {booted or '(unknown)'} and its checkout is "
                     f"now at {disk or '(unknown)'} — /static is served live from the checkout "
                     "while every Python object stays at the boot build (F696). Replace the "
                     "process: `rsched drain && docker compose up -d`")
    if not checkout_version:
        return Check(CHECK_NAMES[1], NOTE,
                     f"the daemon serves {served or '(unknown)'}, but `__version__` could not be "
                     f"read from the checkout{at} — only the daemon's own stamp comparison was "
                     "available, and it claims nothing when a stamp is missing")
    if not booted or not disk:
        return Check(CHECK_NAMES[1], NOTE,
                     f"the daemon serves {served}, which matches the checkout{at}; it has no git "
                     "stamp, so a checkout that moved SINCE the boot cannot be seen from here")
    return Check(CHECK_NAMES[1], PASS,
                 f"the daemon serves {served} at build {booted}, and its checkout{at} is the same "
                 "build — the deploy landed")


def backup_freshness(state: str, detail: str) -> Check:
    """`daemon/backup_watch.staleness`'s verdict, read as a post-deploy finding.

    Taken as given, never recomputed: that module is the one reader of backup state, and the
    stamp's name, the window and the wording all belong to it. What this adds is the reading an
    operator standing at the box needs — see the module docstring on why `unknown` is a FAIL here
    and an `unknown` there.
    """
    if state == "ok":
        return Check(CHECK_NAMES[2], PASS, detail)
    if state == "stale":
        return Check(CHECK_NAMES[2], FAIL,
                     f"{detail} — this instance is UNPROTECTED right now, whichever night it "
                     "broke (F602: 19 nights, 2026-09-12 to 10-01, nobody saw it)")
    if state == "unknown":
        return Check(CHECK_NAMES[2], FAIL,
                     f"{detail} — the ABSENCE of the record is itself the finding: F602's "
                     "nineteen silent nights were not a broken indicator, they were a missing "
                     "one. Mount the backup root (or point `RSCHED_MIRROR` at it) and read "
                     "`journalctl --user -u rsched-backup`")
    return Check(CHECK_NAMES[2], NOTE,
                 f"backup_watch returned an unrecognised state {state!r}: {detail}")


def state_path_ownership(stats: list[PathStat], daemon_uid: int,
                         truncated: bool = False) -> Check:
    """Are the homes the daemon must write owned by a uid it can write as?

    0.373.1: the root-run `cliproxy` sidecar wrote one file as root 0600 in a home the backup
    reads as the host user, and `deploy/backup.sh` reads every home as that user — so one
    unreadable file fails its home, and a failed home keeps NO SNAPSHOT at all. It had done that
    every night since its 2026-09-14 login. `deploy/state-paths.sh` states the invariant ("every
    container that writes one of these homes writes as the same uid") and
    `tests/test_deploy_state.py` holds the compose file to it — but nothing ever looked at the
    FILES on a running box, which is where the defect actually was.

    A path that does not exist is a NOTE: a home only created once a feature has been used is a
    fresh install, not a broken one (`STATE_PATHS_OPTIONAL`'s whole reason for being a separate
    list). Only a wrong OWNER is a FAIL.
    """
    if daemon_uid < 0:
        return Check(CHECK_NAMES[3], NOTE,
                     "the uid the daemon writes as could not be determined, so ownership was "
                     "not checked")
    wrong = [s for s in stats if s.exists and s.uid != daemon_uid]
    missing = [s for s in stats if not s.exists]
    if wrong:
        named = "; ".join(f"{s.path} is owned by uid {s.uid}" for s in wrong)
        return Check(CHECK_NAMES[3], FAIL,
                     f"{named} — the daemon writes as uid {daemon_uid}. One file it cannot write, "
                     "or that the backup (running as that user) cannot read, fails its whole "
                     f"home and that home keeps no snapshot: `sudo chown -R {daemon_uid} …`")
    cut = (" — the walk stopped at its file cap, so this covers only what it reached"
           if truncated else "")
    if missing:
        named = ", ".join(s.path for s in missing)
        return Check(CHECK_NAMES[3], NOTE,
                     f"{len(stats) - len(missing)} path(s) owned by uid {daemon_uid}; absent "
                     f"here: {named} (a home only exists once its feature has been used){cut}")
    if truncated:
        return Check(CHECK_NAMES[3], NOTE,
                     f"the {len(stats)} path(s) reached are owned by uid {daemon_uid}{cut}")
    return Check(CHECK_NAMES[3], PASS,
                 f"all {len(stats)} state path(s) are owned by uid {daemon_uid}, the uid the "
                 "daemon writes as")


def exit_code(checks: list[Check]) -> int:
    """0 when the operator may walk away, 1 when he may not. See the module docstring.

    Only FAIL counts. A NOTE never moves it, because a command that goes red over a home a fresh
    install has not created is one nobody runs twice.
    """
    return 1 if any(c.verdict == FAIL for c in checks) else 0


def summary_line(checks: list[Check]) -> str:
    """The one line the record keeps and the operator remembers."""
    counts = [f"{sum(1 for c in checks if c.verdict == v)} {v}"
              for v in (PASS, NOTE, FAIL) if any(c.verdict == v for c in checks)]
    failed = [c.name for c in checks if c.verdict == FAIL]
    head = f"{len(checks)} checks: {' · '.join(counts)}"
    return f"{head} — ACT: {', '.join(failed)}" if failed else f"{head} — nothing to act on"


def record_row(ts: str, url: str, status: dict | None, checks: list[Check]) -> dict:
    """The JSONL row one session appends — the trace the session used to leave nowhere.

    Flat and small on purpose: it is read by a human with `tail`, and by any later reader that
    wants the history of what a deploy session found. The verdicts go in BY NAME, so a row stays
    readable when the checklist grows.
    """
    return {"ts": ts, "url": url,
            "version": str((status or {}).get("version") or ""),
            "build": str((status or {}).get("build") or ""),
            "verdicts": {c.name: c.verdict for c in checks},
            "details": {c.name: c.detail for c in checks},
            "summary": summary_line(checks), "exit": exit_code(checks)}
