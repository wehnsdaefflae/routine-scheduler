"""The credential VALUES this instance stores, as the bytes a file would hold them in — the
needle set a daemon-side screen matches with, and the coverage statement that says which
sources it could actually read (D296, operator decision 2026-11-19, option a).

WHY IT IS HERE AND NOT IN THE EXPORT. `rsched export` mirrors this instance off-box and must
scrub the instance's credentials out of what it writes. It runs inside a util, in a Landlock
jail that hides the whole instance config dir (`sandbox.py`, and Landlock only ever subtracts),
so it cannot read `secrets.env` or `connections.json` — and a credential it cannot read is a
credential it cannot match. Since D171 it asks the daemon for NAMES instead and reports
`withheld_coverage: partial`, which is honest but means the export can exit 0 having compared
nothing for two of its sources. The daemon is unsandboxed and already reads every store, so the
MATCHING can happen here, where the values already are, and only the findings leave.

WHAT NEVER LEAVES. A needle is bytes the caller never sees: `Needles.names_for` answers with the
NAMES a matched value is stored under, which is exactly what D171 already serves by name
(`web/api_credential_names.py`), and nothing here is serializable to a response. `secret_names`
below is the only outward-facing shape, and it is names and counts.

THE FLOOR AND WHY IT MATTERS TO THE THREAT MODEL. A value shorter than
`captured_output.REDACT_MIN_CHARS` (8) is not a needle — the engine's own redaction floor, for
the same reason: masking every "true" or "8080" corrupts output without protecting anything a
guess could not reach anyway. The floor is also what keeps a screen from being a search oracle:
a needle matches a WHOLE stored value and never a prefix, so a caller learns nothing by
submitting a partial guess, and cannot submit bytes at all (`credscreen` opens the file itself).

Every name and every store path comes from the module that OWNS it — `secrets`, `oauth.store`,
`triggers` — never spelled again here, so a store renamed by its owner cannot leave a stale
literal behind that quietly stops being matched.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import secrets as secret_store
from .captured_output import REDACT_MIN_CHARS
from .oauth import store as conn_store
from .triggers import webhook_tokens

#: A config-file (or connections.json) key whose VALUE is a credential: `token`,
#: `routine_token`, an endpoint's `api_key`, and by the same suffix rule any `*_secret` or
#: `*_password` a later config grows. The suffix rule is deliberate: an exact-name set
#: {token, api_key} once let `routine_token` through into a mirror that gets pushed.
SECRET_KEY_RE = re.compile(r"(?:^|_)(?:token|api_key|secret|password)$")
#: A Secrets-store NAME (or a field inside a JSON-map value) that holds a credential. The
#: store keeps plain settings beside credentials — GMAIL_ADDRESS, NNTP_USER, ZULIP_SITE sit in
#: hundreds of routine files by design — so the NAME is what says which value grants access.
#: Broader than SECRET_KEY_RE because operators name credentials `OPENROUTER_UTIL_KEY`,
#: `NNTP_PASS`, `TELEGRAM_API_HASH`, `DEEPL_API`.
CREDENTIAL_NAME_RE = re.compile(
    r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS|PWD|HASH|API|AUTH|CREDENTIAL)S?$", re.IGNORECASE)
#: The password inside a `scheme://user:password@host` value.
URL_PASSWORD_RE = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s/:@]+:)([^\s/@]+)(@)", re.IGNORECASE)
#: What the config's redactor writes in place of a credential — never a needle.
REDACTED = "REDACTED"
#: The sources a coverage statement accounts for. A screen is `full` only when every one of
#: them was read BY VALUE; any other state is `partial`, and a partial screen may never say
#: a file is clean (`credscreen`).
SOURCES = ("config", "central_store", "scoped_stores", "oauth_connections", "webhook_tokens")


@dataclass
class Needles:
    """Credential bytes to match, the names behind each, and what could be read.

    `by_bytes` maps the exact bytes a file would carry a credential in — verbatim UTF-8, and
    the JSON-escaped spelling when that differs — to the names it is stored under. Values are
    never exposed: a caller reads `names_for`, `coverage` and `longest`.
    """

    by_bytes: dict[bytes, list[str]] = field(default_factory=dict)
    #: source -> True (read by value) | "unreadable" (counted, never matched)
    read: dict[str, Any] = field(default_factory=dict)
    #: source -> the OSError that denied it, for the operator's own diagnosis
    denied: dict[str, str] = field(default_factory=dict)

    def add(self, value: str, name: str) -> None:
        """Record one stored credential under the name it is stored as. Short values and the
        redaction marker are skipped — see the module docstring on the floor.
        """
        if not isinstance(value, str) or len(value) < REDACT_MIN_CHARS or value == REDACTED:
            return
        for raw in self._spellings(value):
            self.by_bytes.setdefault(raw, [])
            if name not in self.by_bytes[raw]:
                self.by_bytes[raw].append(name)

    @staticmethod
    def _spellings(value: str) -> list[bytes]:
        """Verbatim, plus the JSON-escaped form when it differs — a multi-line private key
        inside a .json or .jsonl file is stored escaped, and a byte-substring match over the
        raw spelling alone would never see it.
        """
        out = [value.encode("utf-8")]
        escaped = json.dumps(value)[1:-1].encode("utf-8")
        if escaped != out[0]:
            out.append(escaped)
        return out

    @property
    def longest(self) -> int:
        """The longest needle in bytes — the overlap a chunked read must carry, so a
        credential straddling a chunk boundary is still found.
        """
        return max((len(b) for b in self.by_bytes), default=0)

    def names_for(self, raw: bytes) -> list[str]:
        return list(self.by_bytes.get(raw, ()))

    @property
    def coverage(self) -> str:
        """`full` only when every source in SOURCES was read by value."""
        return "full" if all(self.read.get(s) is True for s in SOURCES) else "partial"

    def unreadable(self) -> list[str]:
        return sorted(s for s in SOURCES if self.read.get(s) is not True)


def _keyed_values(data: object, name_re: re.Pattern[str],
                  path: str = "") -> list[tuple[str, str]]:
    """(dotted key path, value) for every scalar whose KEY name_re names a credential, plus
    the password of any `scheme://user:password@host` value anywhere in the tree.
    """
    out: list[tuple[str, str]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            here = f"{path}.{key}" if path else str(key)
            if isinstance(value, str):
                if name_re.search(str(key)):
                    out.append((here, value))
                found = URL_PASSWORD_RE.search(value)
                if found:
                    out.append((f"{here} (URL password)", found.group(2)))
            else:
                out += _keyed_values(value, name_re, here)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            out += _keyed_values(item, name_re, f"{path}[{i}]")
    return out


def _store_credentials(store: dict[str, str], label: str) -> list[tuple[str, str]]:
    """Which values of a Secrets store are credentials: the ones whose NAME says so, the ones
    whose JSON-map FIELD name says so, and any value spanning lines (a private key).
    """
    out: list[tuple[str, str]] = []
    for name, value in store.items():
        if value.strip().startswith("{"):
            try:
                inner = json.loads(value)
            except ValueError:
                inner = None
            if isinstance(inner, dict):
                out += [(f"{label} {name}.{k}", v)
                        for k, v in _keyed_values(inner, CREDENTIAL_NAME_RE)]
                continue
        if CREDENTIAL_NAME_RE.search(name) or "\n" in value:
            out.append((f"{label} {name}", value))
    return out


def _read_by_value(needles: Needles, source: str, read) -> None:
    """Read one source, or record it as unreadable. An OSError is the EXPECTED denial (a store
    the daemon's own user cannot read); a parse error is a real fault and is not swallowed into
    a coverage label, because a malformed store is a defect, not a permission.
    """
    try:
        pairs = read()
    except OSError as exc:
        needles.read[source] = "unreadable"
        needles.denied[source] = f"{type(exc).__name__}: {exc}"
        return
    needles.read[source] = True
    for name, value in pairs:
        needles.add(value, name)


def collect(config_path: Path | None = None,
            routines_homes: tuple[Path, ...] = ()) -> Needles:
    """Every credential this instance stores, as needles, with a coverage statement.

    Nothing here raises on an unreadable store: a screen that cannot read one source must say
    `partial` and go on matching the rest, because the alternative — refusing — leaves the
    caller comparing nothing at all, which is the outcome D296 exists to end.
    """
    from .paths import config_file

    needles = Needles()
    cfg_path = Path(config_path) if config_path else config_file()
    base = cfg_path.parent

    def config_pairs() -> list[tuple[str, str]]:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
        if not isinstance(data, dict):
            return []
        return [(f"config {key}", value) for key, value in _keyed_values(data, SECRET_KEY_RE)]

    _read_by_value(needles, "config", config_pairs)

    def central_pairs() -> list[tuple[str, str]]:
        return _store_credentials(secret_store.load_secrets(), "secret")

    _read_by_value(needles, "central_store", central_pairs)

    def scoped_pairs() -> list[tuple[str, str]]:
        scoped = secret_store.secrets_path().parent / secret_store.SCOPED_DIR
        out: list[tuple[str, str]] = []
        # `iterdir`, never `glob`: on a directory access is DENIED for, glob swallows the
        # PermissionError and yields nothing, so a store nobody could look at reads as an
        # EMPTY one and coverage says `full` — a wrong answer in the reassuring direction,
        # and the reproducible defect the export was found to have. `iterdir` raises, and
        # `_read_by_value` turns that into `unreadable`. (Measured 2026-11-20: pathlib's
        # exists()/is_dir() do NOT hide EACCES — only ENOENT, ENOTDIR, EBADF and ELOOP — so
        # the guard needed here is the LISTING call, not a stat before it.)
        for entry in sorted(scoped.iterdir()) if scoped.is_dir() else []:
            if entry.suffix == ".env":
                out += _store_credentials(secret_store.load_routine_secrets(entry.stem),
                                          f"routine {entry.stem}'s secret")
        return out

    _read_by_value(needles, "scoped_stores", scoped_pairs)

    def connection_pairs() -> list[tuple[str, str]]:
        path = conn_store.connections_path()
        if not path.exists():
            return []
        grants = json.loads(path.read_text(encoding="utf-8"))
        return [(f"connection {key}", value)
                for key, value in _keyed_values(grants, SECRET_KEY_RE)]

    _read_by_value(needles, "oauth_connections", connection_pairs)

    def webhook_pairs() -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for home in routines_homes or (base,):
            if not Path(home).is_dir():
                continue
            for routine_yaml in sorted(Path(home).glob("*/routine.yaml")):
                slug = routine_yaml.parent.name
                if slug.startswith("."):
                    continue
                try:
                    data = yaml.safe_load(routine_yaml.read_text(encoding="utf-8"))
                except (UnicodeDecodeError, yaml.YAMLError):
                    continue        # a routine.yaml nobody can parse holds no token to match
                out += [(f"routine {slug}'s webhook token", token)
                        for token in webhook_tokens(data)]
        return out

    _read_by_value(needles, "webhook_tokens", webhook_pairs)
    return needles


def secret_names(needles: Needles) -> dict[str, object]:
    """The outward-facing shape: coverage, the sources that could not be read, and how many
    needles the screen is matching with — never a value, and never which name holds which.
    """
    return {
        "coverage": needles.coverage,
        "unreadable": needles.unreadable(),
        "denied": dict(needles.denied),
        "credentials_matched_against": len(needles.by_bytes),
    }
