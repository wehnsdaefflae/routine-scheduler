"""The NAMES of every credential this instance stores — never a value (D171, operator decision
2026-10-08, option b).

WHY IT EXISTS. `rsched export` mirrors this instance off-box and must scrub the credentials out
of what it writes. It used to build its scrub list by READING every store, which the sandbox
hides from every util and every shell by design (`sandbox.py`, the daemon HOME is subtracted and
Landlock only ever subtracts) — so the export has been failing since 2026-10-03 and the off-box
mirror went stale. The operator's answer was not to open the jail but to remove the need: the
daemon is unsandboxed and already reads the stores, and a list of NAMES leaks nothing, so a
redactor can match by field name instead of by value.

WHY IT IS NOT UNDER `/api/settings`. The routine token (`RSCHED_API_TOKEN`, injected into every
util that declares it) is refused the whole `/api/settings` subtree, a routine's own secret names
and the filesystem picker (`app.ROUTINE_TOKEN_DENIED_READS`) — and the export runs inside a util,
under exactly that token. This endpoint is deliberately a sibling of that subtree so it is an
ordinary read for a run.

WHAT IT IS SAFE TO SERVE, AND WHAT IS NOT.
- The CENTRAL store's names are already in every run's prompt by operator decision D46 ("names
  only, never a value, so a run knows which credentials exist without probing"), so serving them
  here tells a run nothing it was not already told.
- A routine's OWN scoped names (D103) are a different matter: they are "invisible to every other
  routine" by design, which is why `/api/routines/{slug}/secrets` is denied to the routine token.
  A scrub list needs the NAMES but never WHOSE they are, so they are served as one sorted UNION
  with no slug attribution — strictly less than the per-routine route reveals, and the
  attribution that D103 protects never leaves the daemon.
- The OAuth connections and the config's own credential fields are served as FIELD names (the
  keys that hold a credential), because that is what a by-name redactor matches on.

Every name comes from the module that OWNS the store, never spelled again here: a store renamed
by its owner must not leave a stale literal behind that quietly stops being redacted.
"""

from __future__ import annotations

from fastapi import APIRouter

from .. import secrets as secret_store
from ..oauth import store as conn_store

router = APIRouter(tags=["settings"])

#: Config-file keys whose value is a credential, as the exporter's own name matcher reads them
#: (`token`/`routine_token` are `bootstrap.TOKEN_KEYS`; an endpoint's inline key is
#: `endpoints.<name>.api_key`). Served so a redactor need not hardcode this instance's shape.
CONFIG_CREDENTIAL_FIELDS = ("token", "routine_token", "api_key", "password", "secret")


def _connection_credential_fields() -> list[str]:
    """The `Connection` fields that hold a credential, taken from the dataclass itself — so a
    new token field added to the store is redacted the day it exists, not the day someone
    remembers this list.
    """
    hint = ("token", "secret", "password", "key")
    return sorted(name for name in conn_store.Connection.__dataclass_fields__
                  if any(h in name for h in hint))


def _scoped_names() -> list[str]:
    """The UNION of every routine's own scoped secret names, unattributed (D103 — see the module
    docstring). A store whose file cannot be read contributes nothing rather than failing the
    whole list: a redactor that gets no list redacts nothing, which is the one outcome worse
    than an incomplete one.
    """
    scoped_dir = secret_store.secrets_path().parent / secret_store.SCOPED_DIR
    names: set[str] = set()
    if scoped_dir.is_dir():
        for store in sorted(scoped_dir.glob("*.env")):
            names.update(secret_store.routine_secret_keys(store.stem))
    return sorted(names)


@router.get("/credential-names")
def credential_names() -> dict:
    """Names only, never a value: `central` is the shared Secrets store's names (D46, already in
    every run's prompt), `scoped` the UNION of every routine's own names with no attribution
    (D103), `connection_fields` the OAuth store's credential-bearing field names, and
    `config_fields` the config keys whose value is a credential.

    A by-name redactor needs nothing else, and nothing here identifies a value: this response is
    the same for an instance holding real credentials and one holding placeholders.
    """
    return {
        "central": secret_store.secret_keys(),
        "scoped": _scoped_names(),
        "connection_fields": _connection_credential_fields(),
        "config_fields": sorted(CONFIG_CREDENTIAL_FIELDS),
    }
