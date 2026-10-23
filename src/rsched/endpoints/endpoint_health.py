"""Is the CREDENTIAL behind this endpoint still alive? The record, and the one judgement.

The daemon has kept user CONNECTIONS alive since `daemon/oauth_refresh.py` — it refreshes an
expiring provider token 5 minutes early and flags the connection `needs_reauth` when the
provider refuses the grant, "the console record IS the notification". Nothing did the same for
the MODEL credentials every run depends on. On 2026-09-14 06:18 a claude-proxy subscription
token expired and eight routines failed at turn 0 within two minutes, each `503
auth_unavailable`: the credential was dead for as long as nobody happened to read a run page.

So an endpoint gets the flag a connection already has. Two things live here, and only these two:

- **the RECORD** — `<routines_home>/.control/endpoint-health.json`, keyed by endpoint name.
  Derived state, never config: the web layer remains the only writer of `config.yaml`, a run
  writes no config, and deleting this file costs one probe. The pattern is `endpoints/limits.py`
  and `daemon/library_watch.py`, which say the same thing about the same directory;
- **the VERDICT** (`verdict`) — what a probe's answer MEANS, as a pure function of that answer.

WHEN to probe and what to do about a dead credential is `daemon/endpoint_watch.py`'s business,
not this module's. Nothing here reaches the network, and `EndpointRegistry.resolve` is
deliberately offline — it sits on the per-turn path — so no reader of this record may fetch
either: a miss is a miss.

## Four states, because a rate limit is not a dead credential

`oauth_refresh` only flags `needs_reauth` on the statuses that mean the provider REFUSED the
grant (400/401/403), and treats a 429 or a 5xx as transient, because a flagged connection is
withheld from every run — so flagging an outage would cost a re-authorization by hand for a
provider hiccup. The same trap is live here in a different dialect: CLIProxyAPI marks an account
`unavailable` both for a dead refresh token AND for a usage limit it expects back, with
`next_retry_after` set on the second. Hence:

- `ok` — at least one account serving this endpoint's models is available;
- `needs_reauth` — every one of them is unavailable and NONE is waiting on a retry time. Only
  this state is the flag, and only a person can clear it;
- `cooling` — all unavailable, but the proxy expects at least one back (`next_retry_after`);
- `unknown` — nothing is claimed: the proxy could not be read, or it lists no account for this
  endpoint's models at all. `daemon/backup_watch`'s rule, for its reason — claiming a credential
  is dead because we could not see its proxy would be an invention, and a fresh install that has
  never signed an account in is not a broken one.

The detail strings carry the proxy's OWN one-line account messages, which
`endpoints/cliproxy_login.py` has already cut to one line and stripped of any token-shaped word.
Nothing here adds a field of its own to them.
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
    """Every endpoint's record, keyed by endpoint name. Empty when nothing has probed yet."""
    data = read_json(path(routines_home))
    if not isinstance(data, dict):
        return {}
    return {name: row for name, row in data.items() if isinstance(row, dict)}


def get(routines_home: Path, endpoint: str) -> dict | None:
    """One endpoint's record, or None. Read-only and never probes — see the module docstring."""
    return load(routines_home).get(endpoint)


def verdict(reply: dict) -> tuple[str, str]:
    """What a proxy account listing says about this endpoint's credential, as (state, detail).

    `reply` is `endpoints/cliproxy_login.accounts()`'s answer — already filtered to the
    auth-file providers the endpoint's catalog models belong to, so every row in it is an
    account that serves this endpoint.
    """
    if not reply.get("ok"):
        why = str(reply.get("error") or "the proxy's account list could not be read")
        return "unknown", f"nothing claimed about this endpoint's credential: {why}"
    rows = [row for row in (reply.get("accounts") or []) if isinstance(row, dict)]
    usable = [row for row in rows if not row.get("disabled")]
    if not usable:
        return "unknown", ("the proxy lists no enabled account for this endpoint's models — "
                           "nothing is claimed about a credential that was never signed in")
    alive = [row for row in usable if not row.get("unavailable")]
    if alive:
        return "ok", (f"{len(alive)} of {len(usable)} proxy account(s) serving this endpoint "
                      f"are available: {_labels(alive)}")
    cooling = [row for row in usable if row.get("next_retry_after")]
    if cooling:
        return "cooling", (f"all {len(usable)} proxy account(s) serving this endpoint are "
                           f"unavailable, and the proxy expects {len(cooling)} back: "
                           f"{_reasons(cooling)}")
    return "needs_reauth", (f"all {len(usable)} proxy account(s) serving this endpoint are "
                            f"unavailable with no retry expected, so every run whose model "
                            f"resolves here fails at turn 0: {_reasons(usable)}")


def remember(routines_home: Path, endpoint: str, *, state: str, detail: str, probe: str,
             filed_at: str = "") -> dict:
    """Persist one endpoint's verdict, merged into the record file, and return the row written.

    `filed_at` is the stamp of the health event this state last produced — carried in the
    RECORD rather than in the prober's memory so a daemon restart cannot re-file a standing
    condition (`daemon/backup_watch`'s latch is process memory and does exactly that).
    """
    rows = load(routines_home)
    row = {"state": state, "needs_reauth": state == "needs_reauth", "detail": detail,
           "probe": probe, "checked_at": now_iso(), "filed_at": filed_at}
    rows[endpoint] = row
    atomic_write_json(path(routines_home), rows)
    return row


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
