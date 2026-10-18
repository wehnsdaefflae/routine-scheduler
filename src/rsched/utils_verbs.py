"""VERB-SCOPED secrets — which of a util's declared credentials the RUNNING VERB may see.

R2622, the operator's decision of 2026-10-16: `secrets: DEEPL_API@credentials` injects
`DEEPL_API` only when `credentials` is the verb being run, so a verb-dispatched util can
absorb a credential-holding sibling without handing that credential to every other verb's
callers. Before this, a util's credential surface was the UNION of all its verbs' needs —
measured across all 54 live utils (R2620): three service credentials would have reached every
one of `remote`'s ~3,900 `exec` calls to serve one verb almost nobody calls, which is why that
fold was reverted and a credentialed sibling was permanently unfoldable. Reservation already
follows a verb (`<util>:<verb>` in a permission's `requires:`); secrets had the coarse
treatment, and this is the same axis.

Unsuffixed names keep today's meaning exactly, so the grammar is purely additive: no existing
util, caller or routine config moves, and the util's own code still just reads `os.environ`.

TWO RULES KEEP THE ENGINE AND THE UTIL'S OWN DISPATCHER FROM DISAGREEING — the failure that
would turn a sandbox decision into a mystery missing credential, after which the obvious move
is provisioning a secret that is already provisioned (R2621):

1. The verb VOCABULARY is the util's own `usage:` block. `declared_verbs` reads the invocation
   table its author wrote (`gu <name> <verb> …`), so the engine never reimplements a
   dispatcher. A bound verb absent from that table is a HEADER PROBLEM
   (`utils_header.header_problems`), caught when the util is saved rather than as an empty env
   at call time.
2. The RUNNING verb is argv's FIRST token, and only when it is in the vocabulary
   (`running_verb`). Every verb-dispatched util in the library puts its verb there — the
   survey of all 54 read each one's own usage table — so nothing later in argv is consulted,
   and a flag VALUE that happens to spell a verb (`gu remote exec m --cwd credentials`) can
   never be read as one. That also disposes of the two shapes R2621 named as traps:
   `gu web-request https://… --out f` and `gu code-search "needle" src/` lead with a
   positional that is not a declared verb, so neither names a verb and both withhold.

FAIL CLOSED, AND SAY SO. When no verb can be determined — a default-path call, `--selftest`,
an empty argv — every verb-bound secret is WITHHELD rather than injected, and the withholding
is reported to the run (`engine/secretgate.withheld_verb_scoped` → the observation's
`withheld_verb_scoped`). A silent withholding is indistinguishable from a credential nobody
provisioned, which is the whole reason this module reports instead of merely deciding.

The scope is read over the WHOLE call tree, not just the root header: a `calls:` edge hands a
callee's credentials to its caller (`utils_run.util_needs`), so a verb-bound name reached that
way is withheld unless the ROOT call's verb matches it. Fail-closed there too — otherwise
`calls:` would be a second door around the scope.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from . import utils_lib
from .utils_header import parse_header

#: `gu <util> <verb>` inside a usage block — the verb shape (a lowercase word, deliberately
#: what `is_slug` accepts for a util name) is the capture group here, so the grammar lives in
#: ONE pattern. The util's name is not pinned: a usage block shows its own invocations, so this
#: reads the table without being told which util it belongs to.
_USAGE_VERB_RE = re.compile(r"\bgu\s+[a-z0-9][a-z0-9-]*\s+([a-z][a-z0-9-]*)")


def declared_verbs(src: str) -> tuple[str, ...]:
    """The verb vocabulary a util's `usage:` BLOCK declares, in the order its author listed
    them. Empty for a util with no verbs (20 of the 54 live ones) — and an empty vocabulary
    means no argv can name a verb, so every verb-bound secret such a header declared is
    withheld on every call.

    Reads the whole block (`utils_header.usage_block`), which is the same surface every
    discovery path shows a caller: a verb the author did not list is a verb nobody can call
    on purpose.
    """
    out: list[str] = []
    for line in parse_header(src)["usage"].splitlines():
        m = _USAGE_VERB_RE.search(line)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return tuple(out)


def running_verb(src: str, args: Sequence[str]) -> str | None:
    """The verb THIS argv runs, or None when none can be determined.

    argv's FIRST token decides, and only when it is in the vocabulary: otherwise this call
    runs the util's default path (or a flag, like `--selftest`) and names no verb. Nothing
    later in argv is consulted — a flag's VALUE that spells a verb (`--cwd credentials`) must
    never be read as one, which would widen the scope instead of narrowing it.
    """
    vocabulary = set(declared_verbs(src))
    if not vocabulary or not args:
        return None
    token = str(args[0])
    return token if token in vocabulary else None


def verb_scope(home: Path, name: str) -> dict[str, set[str]]:
    """Every verb-BOUND secret in util `name`'s call tree → the verbs it is bound to.

    The union over the tree (`utils_run.util_needs().tree`), because one call tree runs inside
    one env: a binding on a callee still governs what the caller's subprocess receives.
    """
    from .utils_run import util_needs

    out: dict[str, set[str]] = {}
    for reached in util_needs(home, name).tree:
        src = utils_lib.read_util(home, reached)
        if src is None:
            continue
        for secret, verbs in parse_header(src)["verb_secrets"].items():
            out.setdefault(secret.upper(), set()).update(verbs)
    return out


def withheld_for_verb(home: Path, name: str,
                      args: Sequence[str]) -> tuple[list[str], str | None]:
    """`(the verb-bound secrets THIS call may not see, the verb it runs)`.

    Empty list and None when the call tree binds no secret to a verb — the state of every util
    in the library the day this shipped, which is what makes the change additive.
    """
    scope = verb_scope(home, name)
    if not scope:
        return [], None
    src = utils_lib.read_util(home, name) or ""
    verb = running_verb(src, args)
    return sorted(s for s, verbs in scope.items() if verb not in verbs), verb


def child_env(home: Path, name: str, args: Sequence[str],
              extra_secrets: Mapping[str, str] | None = None,
              withhold: set[str] | None = None) -> dict:
    """A util subprocess's environment: `utils_run.scoped_env` over the util's transitive
    declarations (`calls:` siblings included — one jail, one env), minus the verb-bound names
    this argv may not see.

    Was `utils_run._child_env` until R2622; it moved here because the verb decision and the
    env it produces are one thing, and because the runner module is at its size ceiling.
    """
    from .utils_run import scoped_env, util_needs

    verb_withheld, _verb = withheld_for_verb(home, name, args)
    return scoped_env(util_needs(home, name).secrets, dict(extra_secrets or {}),
                      set(withhold or set()) | set(verb_withheld))
