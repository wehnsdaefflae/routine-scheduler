r"""The OUTBOX declaration — the operator's IDENTITY on each channel, and the read every send owes.

The operator's standing order (2026-10-02, repeated 2026-10-09): every outbound message is
first verified not to have gone out already — he may have sent it himself, or an earlier run
did — and that holds at the util or engine level, for every channel, including channels whose
utils do not exist yet. One routine had built it for itself as a script; this makes it the
engine's (`engine/sendgate.py`).

What makes it hold for a util nobody has written yet is the CREDENTIAL. To speak as the
operator, code must hold his identity on that channel — a mailbox password, an API key, a
linked messenger session — and the sandbox gives a util or a script only the secrets and the
stores its header declares. So the library names those credentials, and the engine treats
anything that reaches one as able to send unless the library says otherwise. A permission doc
tagged `as-operator` declares, per channel:

    outbox:
      gmail:                                # the channel — the gate's ledger key
        identity:
          secrets: [GMAIL_APP_PASSWORD]     # what acts as him; `paths:` for session stores
        utils:                              # the utils allowed to hold it, each classified
          gmail:
            never_sends: [list, read, draft]   # the verbs that cannot send
            send_if: '--yes\b'              # optional: a call is a send only when it matches
        check: '^util:gmail list .*--mailbox[ =](sent|all)\b'
        run: gu gmail list --mailbox sent --query "to:<recipient>"

- `identity` — secret names and session-store paths; a call whose credentials include one
  REACHES the channel.
- `utils` — every util permitted to reach it. A verb outside `never_sends` is a SEND, so a
  new verb is gated until it is declared a read; `send_if` narrows a verb whose plain form is
  a dry run (`gmail send` without `--yes`, `usenet post` without `--go`). A util that reaches
  the identity and is NOT listed here is refused outright — a util created tomorrow cannot
  speak as him until someone writes down how its outbox is read.
- `check` — a regex over the canonical action string (`actionschema.canon`) naming the calls
  that read this channel's record of what went out; `run` is the call a refusal names.

The library declares, the engine enforces; the linter holds a doc tagged `as-operator` to an
outbox channel for every send it reserves, with that send outside `never_sends`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

#: The tag a permission carries when it sends to a person in the operator's name.
AS_OPERATOR = "as-operator"

_CHANNEL_KEYS = {"identity", "utils", "check", "run"}
_UTIL_KEYS = {"never_sends", "send_if"}


@dataclass(frozen=True)
class ChannelUtil:
    """How one util permitted to hold a channel's identity is classified."""

    never_sends: frozenset[str]
    send_if: re.Pattern | None = None


@dataclass(frozen=True)
class Channel:
    """One identity of the operator's, the utils that may hold it, and the read a send owes."""

    name: str
    doc: str
    secrets: frozenset[str]
    paths: tuple[Path, ...]
    utils: dict[str, ChannelUtil] = field(hash=False)
    check: re.Pattern = field(default=re.compile(""))
    run: str = ""

    def reached(self, secrets: set[str], fs_paths: list[str]) -> list[str]:
        """The identity credentials among `secrets` and `fs_paths` — a store counts when either
        path holds the other, since mounting a parent hands over everything under it.
        """
        from .paths import expand

        hit = sorted(self.secrets & {s.upper() for s in secrets})
        for raw in fs_paths:
            p = expand(raw)
            if p.is_absolute():
                hit += [str(ip) for ip in self.paths
                        if p == ip or ip in p.parents or p in ip.parents]
        return hit


def normalize_outbox(raw: object, *, doc: str = "", reserved: list[str] | None = None,
                     ) -> tuple[list[Channel], list[str]]:
    """Validate + normalize one doc's `outbox:` block. Returns (channels, problems).

    Lenient like `assists.normalize_assists`: a bad channel is dropped and reported, so one bad
    edit degrades one channel instead of every run's boot; the linter turns the problems into
    an authoring error. `reserved` (the doc's own `requires.utils`) is checked so that no
    reserved send sits inside a `never_sends` list — it would pass the gate unchecked.
    """
    from .paths import expand

    if raw is None:
        return [], []
    if not isinstance(raw, dict):
        return [], ["outbox must be a MAPPING of channel → {identity, utils, check, run}"]
    out: list[Channel] = []
    problems: list[str] = []
    for name, spec in raw.items():
        where = f"outbox[{name}]"
        if not isinstance(spec, dict) or set(spec) != _CHANNEL_KEYS:
            problems.append(f"{where}: a channel is exactly {sorted(_CHANNEL_KEYS)}")
            continue
        ident = spec["identity"] if isinstance(spec["identity"], dict) else {}
        secrets = frozenset(str(s).upper() for s in ident.get("secrets") or [])
        paths = tuple(expand(str(p)) for p in ident.get("paths") or [])
        utils, bad = _utils(spec["utils"], where)
        try:
            check = re.compile(str(spec["check"] or ""))
        except re.error as exc:
            bad.append(f"{where}: check is not a valid regex ({exc})")
            check = re.compile("")
        run = " ".join(str(spec["run"] or "").split())
        if not secrets and not paths:
            bad.append(f"{where}: identity must name the secrets or session stores that act "
                       "as the operator — without them nothing can reach the channel")
        if not check.pattern.strip() or not run:
            bad.append(f"{where}: check must name the calls that read this outbox, and run "
                       "the call a refused send is told to make")
        problems += bad
        if not bad:
            out.append(Channel(name=str(name), doc=doc, secrets=secrets, paths=paths,
                               utils=utils, check=check, run=run))
    problems += _sends_left_open(out, reserved or [])
    return out, problems


def _utils(raw: object, where: str) -> tuple[dict[str, ChannelUtil], list[str]]:
    if not isinstance(raw, dict) or not raw:
        return {}, [(f"{where}: utils must map each util allowed to hold this identity to its "
                     "never_sends verbs")]
    out: dict[str, ChannelUtil] = {}
    problems: list[str] = []
    for util, spec in raw.items():
        verbs = spec.get("never_sends") if isinstance(spec, dict) else None
        if not isinstance(spec, dict) or not set(spec) <= _UTIL_KEYS \
                or not isinstance(verbs or [], list):
            problems.append(f"{where}.utils[{util}]: an entry is {{never_sends: [verbs], "
                            "send_if?}}")
            continue
        try:
            send_if = re.compile(str(spec["send_if"])) if spec.get("send_if") else None
        except re.error as exc:
            problems.append(f"{where}.utils[{util}]: send_if is not a valid regex ({exc})")
            continue
        out[str(util)] = ChannelUtil(never_sends=frozenset(str(v) for v in verbs or []),
                                     send_if=send_if)
    return out, problems


def _sends_left_open(channels: list[Channel], reserved: list[str]) -> list[str]:
    """A reserved `util:verb` send listed in its channel's never_sends would pass unchecked."""
    problems = []
    for send in reserved:
        util, _, verb = send.partition(":")
        if verb and any(util in c.utils and verb in c.utils[util].never_sends
                        for c in channels):
            problems.append(f"outbox: {send!r} is reserved as a send but listed in "
                            "never_sends — it would pass the gate unchecked")
    return problems


def as_operator_problems(meta: dict) -> list[str]:
    """A doc tagged `as-operator` places every send it reserves in an outbox channel."""
    from .grants import normalize_capabilities

    tags = meta.get("tags")
    if not isinstance(tags, list) or AS_OPERATOR not in {str(t).strip().lower() for t in tags}:
        return []
    req, _ = normalize_capabilities(meta.get("requires"), label="requires", requires=True)
    channels, _ = normalize_outbox(meta.get("outbox"))
    held = {u for c in channels for u in c.utils}
    return [f"tagged {AS_OPERATOR} but no outbox channel holds {send!r} — every send in the "
            "operator's name needs its identity declared and the call that reads what already "
            "went out" for send in req.get("utils") or [] if send.partition(":")[0] not in held]


def read_library_outbox(permissions_home: Path) -> list[Channel]:
    """Every outbox channel the library's permission docs declare, in doc order. Lenient: an
    unreadable doc contributes nothing rather than failing a boot.
    """
    from .grants import normalize_capabilities
    from .library_docs import parse_lenient

    home = Path(permissions_home)
    if not home.is_dir():
        return []
    out: list[Channel] = []
    for path in sorted(home.glob("*.md")):
        try:
            meta = parse_lenient(path.read_text(encoding="utf-8"))[0]
        except OSError:
            continue
        req, _ = normalize_capabilities(meta.get("requires"), label="requires", requires=True)
        channels, _ = normalize_outbox(meta.get("outbox"), doc=path.stem,
                                       reserved=list(req.get("utils") or []))
        out.extend(channels)
    return out


def identity_reach(header: dict, libraries_home: Path,
                   channels: list[Channel]) -> dict[str, list[str]]:
    """`channel → credentials` that code with this header would hold: its own `secrets:` and
    `fs:` lines plus everything the utils on its `calls:` line declare (one jail, one env over
    the whole tree). What `write_util` shows the operator before such a util is written.
    """
    from .utils_run import util_needs

    secrets = {str(s).upper() for s in header.get("secrets") or []}
    paths = [str(p) for _mode, p in header.get("fs_paths") or []]
    for callee in header.get("calls") or []:
        needs = util_needs(libraries_home, callee)
        secrets |= set(needs.secrets)
        paths += [p for _mode, p in needs.fs_paths]
    return {c.name: creds for c in channels if (creds := c.reached(secrets, paths))}
