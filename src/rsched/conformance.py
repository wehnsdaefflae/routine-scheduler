"""Is THIS INSTANCE's config flyable? — the question the hermetic suite cannot ask (R2196).

The test suite proves the CODE correct against fixtures, and that discipline is right and must
not change: `tests/production_guard.py` is a session-wide floor refusing any write into the live
instance's data homes, written after F394, where an escaped subprocess ran eleven turns on a paid
endpoint against production. Every `load_server_config` call in `tests/` but two passes an
explicit fixture path.

So nothing ever asks whether the config this instance actually runs on is flyable. The defect
class that falls through the gap is a CONFIG defect, not a code defect, and the project has paid
for it:

* **2026-09-14 06:18** — eight routines dead at turn 0 within two minutes, each `503
  auth_unavailable`, because `Astra high`'s only fallback was `Opus high` and both sat on the
  `cliproxy` endpoint: ONE failure domain, no way out. 0.344.0 then fixed chain DEPTH
  (breadth-first transitive resolution) and said the fix "is that the reachable set finally
  matches what a reader of `config.yaml` would already assume" — i.e. the defect was a gap
  between the config as written and the config as resolved.
* **R1492** — a subtask killed on a classifier refusal: `_handle_refusal` advances the chain, and
  a model with nowhere to go turns a refusal into a dead run.

**The property that failed on 2026-09-14 was checkable with no network call at all.** That is
what this module checks, and why it is deliberately not "more live tests": live calls cost money
and quota, which is why `tests/test_live.py` is gated behind `RSCHED_LIVE_TESTS=1`. Everything
here is offline, free and reads only the resolved config:

1. every routine role resolves to a non-empty chain;
2. no role's chain has all its members in one failure domain;
3. the server's `system_model` resolves (it is the fallback for every unset role).

Checked where the live config is already read — `rsched validate`, beside `trials.roles_problem`
(the same shape: a question the loader cannot answer because it cannot see the catalog) and
`cli_validate._instance_problems` (coherence belonging to no single routine). `load_server_config`
already returns a `problems` list the daemon surfaces; nothing asserted it was empty for THIS
instance, and no problem was ever raised for a chain whose rungs share a failure domain.

A FAILURE DOMAIN has TWO INDEPENDENT AXES, and sharing either one is fatal: the endpoint's
`base_url` (one host, one outage) and the environment variable its key is read from (one
credential, one expiry). A chain is unsafe when EVERY member shares a host, or when every member
reads one credential — not only when it shares both. 2026-09-14 was the credential axis, so a
check that demanded both would have passed a chain spread over two endpoint names and two URLs
behind one expiring key, which is the incident itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import ServerConfig

#: A chain of one is single-domain by definition and is reported as a chain of one, not as a
#: shared domain — the two readings want different fixes (add a fallback vs move one elsewhere).
_MIN_CHAIN = 2


def _domain(server: ServerConfig, model_name: str) -> tuple[str, str] | None:
    """The failure domain of one catalog model: its endpoint's (base_url, key_var).

    None when the model or its endpoint is not in the catalog — an unresolvable name is
    `roles_problem`'s finding, not this one, and reporting it twice helps nobody.
    """
    mc = server.models.get(model_name)
    if mc is None:
        return None
    ep = server.endpoints.get(mc.endpoint)
    if ep is None:
        return None
    return (ep.base_url or f"(endpoint {ep.name})", ep.key_var or f"(kind {ep.kind})")


def _chain_names(server: ServerConfig, name: str) -> list[str]:
    """The model plus its fallbacks TRANSITIVELY, breadth-first in declaration order — the same
    walk `EndpointRegistry.resolve_chain` does, over NAMES alone.

    Re-walked here rather than calling `resolve_chain` because that returns resolved endpoints
    and raises for a name the catalog lacks: this check must survive a broken config and report
    on it, and it must not need a registry (so `rsched validate` can call it with no endpoints
    constructed). Unresolvable and duplicate entries are skipped exactly as there.
    """
    if name not in server.models:
        return []
    chain = [name]
    seen = {name}
    queue = list(server.models[name].fallbacks)
    while queue:
        fb = queue.pop(0)
        if fb in seen or fb not in server.models:
            continue
        seen.add(fb)
        chain.append(fb)
        queue.extend(f for f in server.models[fb].fallbacks if f not in seen)
    return chain


def chain_problems(server: ServerConfig, models: dict[str, str] | None) -> list[str]:
    """Every failure-domain problem in a routine's standing model roles.

    One line per role, naming the shared domain and the chain that shares it — a problem a
    reader cannot act on is a problem nobody fixes. A role whose name is not in the catalog is
    skipped (that is `trials.roles_problem`'s line).
    """
    out: list[str] = []
    for role, name in sorted((models or {}).items()):
        if not isinstance(name, str) or name not in server.models:
            continue
        chain = _chain_names(server, name)
        if len(chain) < _MIN_CHAIN:
            out.append(
                f"models.{role} {name!r} has no reachable fallback — a hard provider failure or "
                "a classifier refusal on it ends the run (2026-09-14: eight routines dead at "
                "turn 0; R1492: a refusal with nowhere to go kills the subtask)")
            continue
        domains = [d for m in chain if (d := _domain(server, m)) is not None]
        if not domains:
            continue
        hosts = {url for url, _key in domains}
        keys = {key for _url, key in domains}
        # EITHER axis alone is fatal, and that is why the check is two sets and not one pair of
        # them: all members behind one host die together when the host does, all members reading
        # one credential die together when it expires. 2026-09-14 was the second — two endpoint
        # names, two base_urls would not have saved it, because the KEY was shared.
        shared = (f"host {next(iter(hosts))}" if len(hosts) == 1 else "") or ""
        if len(keys) == 1:
            shared = f"{shared} and " if shared else ""
            shared += f"credential {next(iter(keys))}"
        if shared:
            out.append(
                f"models.{role} {name!r}: all {len(chain)} chain members "
                f"({', '.join(chain)}) share ONE failure domain ({shared}) — one host outage or "
                "one credential expiry takes the whole chain out at once, which is exactly what "
                "killed eight routines at turn 0 on 2026-09-14")
    return out


def system_model_problems(server: ServerConfig) -> list[str]:
    """The server's own `system_model` — the fallback behind every role a routine leaves unset.

    Checked separately from the routines because it belongs to no routine: a routine naming no
    `main` model runs on it, so an unresolvable or single-domain system model is a fleet-wide
    condition that no per-routine surface can see.
    """
    # `getattr`, not an attribute read: `rsched validate`'s own tests hand `_instance_problems`
    # a minimal stand-in carrying only the fields that test needs (tests/test_lanes.py), and a
    # hard read there fails the whole command for a question it was not asked. Same shape as the
    # four-attribute server the fresh-container boot test hands `cmd_daemon`.
    name = getattr(server, "system_model", "")
    if not name or not getattr(server, "models", None):
        return []
    if name not in server.models:
        return [(f"system_model {name!r} is not in the model catalog — every routine that "
                 "names no model of its own dies resolving it")]
    # One role, so one line at most; `chain_problems` words it as `models.<role>` because that
    # is where a routine's roles live, and the server's own model is not one of those.
    return [line.replace("models.system_model", "system_model", 1)
            for line in chain_problems(server, {"system_model": name})]
