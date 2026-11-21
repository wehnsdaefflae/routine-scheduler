"""Is each SUBSCRIPTION LOGIN behind the model gateway still alive? The record, and the one
judgement.

The model-credential twin of `daemon/oauth_refresh.py`'s `needs_reauth` flag on connections:
an expired proxy token otherwise fails every run at turn 0 with nothing saying so. Two things live
here, and only these two:

- **the RECORD** — `<routines_home>/.control/endpoint-health.json`, keyed by LOGIN
  (`<gateway>/<login provider>`, e.g. `otari/claude` — `daemon/endpoint_watch.logins`). Derived
  state, never config: deleting it costs one probe, and a login no gateway names any more is
  dropped by the next pass (`keep_only`);
- **the VERDICT** (`verdict`) — what a probe's answer MEANS, as a pure function of that answer.

WHEN to probe and what to do is `daemon/endpoint_watch.py`'s business. Nothing here reaches the
network, and no reader of this record may fetch either (`EndpointRegistry.resolve` is on the
per-turn path): a miss is a miss.

## Four states, because a rate limit is not a dead credential

CLIProxyAPI marks an account `unavailable` both for a dead refresh token AND for a usage limit it
expects back (with `next_retry_after` set on the second). Hence:

- `ok` — at least one account of this login's provider is available;
- `needs_reauth` — every one is unavailable and NONE is waiting on a retry time. Only this state
  is the flag, and only a person can clear it;
- `cooling` — all unavailable, but the proxy expects at least one back (`next_retry_after`);
- `unknown` — nothing is claimed: the proxy could not be read, or lists no account of this
  login's provider (a fresh install that never signed in is not a broken one).

`web/operator_conditions` reads the record back for the blocked-work surface: an
`endpoint_needs_reauth` row counts only while some login here is still `needs_reauth`.

The detail strings carry the proxy's OWN one-line account messages, already redacted by
`endpoints/cliproxy_login.py`.
"""

from __future__ import annotations

from pathlib import Path

from ..ids import now_iso
from ..paths import atomic_write_json, read_json

#: Daemon-owned derived state, beside the other `.control` caches. Never config.
HEALTH_FILE = Path(".control") / "endpoint-health.json"

#: The states `verdict` answers with — see the module docstring for why there are four.
STATES = ("ok", "needs_reauth", "cooling", "unknown")


def path(routines_home: Path) -> Path:
    return Path(routines_home) / HEALTH_FILE


def load(routines_home: Path) -> dict[str, dict]:
    """Every login's record, keyed by login. Empty when nothing has probed yet."""
    data = read_json(path(routines_home))
    if not isinstance(data, dict):
        return {}
    return {name: row for name, row in data.items() if isinstance(row, dict)}


def get(routines_home: Path, login: str) -> dict | None:
    """One login's record, or None. Read-only and never probes — see the module docstring."""
    return load(routines_home).get(login)


def any_needs_reauth(routines_home: Path) -> bool | None:
    """Is some login still `needs_reauth`? None when nothing was ever recorded — unknown, which
    a standing-condition reader must not turn into "resolved".
    """
    rows = load(routines_home)
    if not rows:
        return None
    return any(row.get("state") == "needs_reauth" for row in rows.values())


def verdict(reply: dict) -> tuple[str, str]:
    """What a proxy account listing says about one login's credential, as (state, detail).

    `reply` is `endpoints/cliproxy_login.accounts()`'s answer, filtered to the one auth-file
    provider the login is (`daemon/endpoint_watch`), so every row in it is an account of it.
    """
    if not reply.get("ok"):
        why = str(reply.get("error") or "the proxy's account list could not be read")
        return "unknown", f"nothing claimed about this login's credential: {why}"
    rows = [row for row in (reply.get("accounts") or []) if isinstance(row, dict)]
    usable = [row for row in rows if not row.get("disabled")]
    if not usable:
        return "unknown", ("the proxy lists no enabled account for this login — nothing is "
                           "claimed about a credential that was never signed in")
    alive = [row for row in usable if not row.get("unavailable")]
    if alive:
        return "ok", (f"{len(alive)} of {len(usable)} proxy account(s) of this login are "
                      f"available: {_labels(alive)}")
    cooling = [row for row in usable if row.get("next_retry_after")]
    if cooling:
        return "cooling", (f"all {len(usable)} proxy account(s) of this login are "
                           f"unavailable, and the proxy expects {len(cooling)} back: "
                           f"{_reasons(cooling)}")
    return "needs_reauth", (f"all {len(usable)} proxy account(s) of this login are "
                            f"unavailable with no retry expected, so every run whose model "
                            f"routes to it fails at turn 0: {_reasons(usable)}")


def remember(routines_home: Path, login: str, *, state: str, detail: str, probe: str,
             filed_at: str = "") -> dict:
    """Persist one login's verdict, merged into the record file, and return the row written.

    `filed_at` is the stamp of the health event this state last produced — carried in the
    RECORD, not process memory, so a daemon restart cannot re-file a standing condition.
    """
    rows = load(routines_home)
    row = {"state": state, "needs_reauth": state == "needs_reauth", "detail": detail,
           "probe": probe, "checked_at": now_iso(), "filed_at": filed_at}
    rows[login] = row
    atomic_write_json(path(routines_home), rows)
    return row


def keep_only(routines_home: Path, logins: set[str]) -> None:
    """Drop every record whose login is not in `logins` — the pass's own set. Rewrites the file
    only when something goes, so a quiet instance does not touch it every ten minutes.
    """
    rows = load(routines_home)
    if set(rows) - logins:
        atomic_write_json(path(routines_home),
                          {name: row for name, row in rows.items() if name in logins})


def _labels(rows: list[dict]) -> str:
    return ", ".join(sorted(_name(row) for row in rows))


def _reasons(rows: list[dict]) -> str:
    """Each account named with the proxy's own words for its state, already redacted."""
    out = []
    for row in sorted(rows, key=_name):
        said = str(row.get("status_message") or row.get("status") or "unavailable")
        out.append(f"{_name(row)} ({said})")
    return "; ".join(out)


def _name(row: dict) -> str:
    return str(row.get("label") or row.get("name") or "an unnamed account")
