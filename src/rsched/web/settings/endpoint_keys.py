"""An endpoint's CREDENTIAL, on the way in and on the way out: which rung the card shows,
what a submitted `api_key` field means, and the save that is refused (D208).

Kept beside the CRUD handler rather than inside it because the three-state read below is the
subtle part of an endpoints save and deserves to be read on its own — and because
`settings/endpoints.py` sits at the house bar, where one more responsibility would have to
push something else out anyway.
"""

from __future__ import annotations

from fastapi import HTTPException

from ...config import KEY_VAR_DEFAULTS, EndpointConfig
from ...endpoints.base import api_key_source


def key_source(ep: EndpointConfig) -> dict:
    """Which rung of the credential ladder serves this endpoint RIGHT NOW — labels only,
    never values. Computed by the transport layer itself (endpoints.base), beside the
    resolvers it mirrors, so the indicator can't drift from what a run does.
    """
    src = api_key_source(api_key=ep.api_key, key_var=ep.key_var,
                         key_env_file=ep.key_env_file)
    if src["source"] == "none" and ep.kind == "openai":
        src["keyless_ok"] = True   # local backends (Ollama, vLLM) legitimately run keyless
    return src


def resolve_inline_key(spec: dict, prev: dict, *, submitted: set[str],
                       api_key: str | None) -> None:
    """Apply the submitted `api_key` field to `spec` in place — the three-state read.

    The Settings editor submits the key field blank on every unrelated edit, so BLANK and
    ABSENT must both keep the stored key: a save that wipes a credential nobody meant to touch
    is the worst outcome this route has. An EXPLICIT null is the operator saying "remove the
    inline key" (so a `key_var` in the Secrets store takes over, which an inline key shadows).

    Pydantic cannot tell those apart by value — `model_dump()` hands back `""` for an absent
    field and a null is dropped by any `exclude_none` on the way through — so the decision is
    taken from `model_fields_set` (`submitted`) plus the attribute, and never from a dump.
    """
    if "api_key" in submitted and api_key is None:
        spec.pop("api_key", None)                 # explicit null: the inline key goes
        return
    if not spec.get("api_key") and prev.get("api_key"):
        spec["api_key"] = prev["api_key"]         # blank or absent: keep what is stored


def _ladder(spec: dict) -> tuple[str, str, str]:
    """The credential triple as the LOADER will read it: an unset `key_var` falls to the
    endpoint kind's default (`EndpointConfig._kind_default_key_var`), so a spec that omits the
    field and one that spells the default out are the same endpoint and must compare equal.
    """
    kind = spec.get("kind", "")
    return (spec.get("api_key") or "",
            spec.get("key_var") or KEY_VAR_DEFAULTS.get(kind, ""),
            spec.get("key_env_file") or "")


def refuse_a_credential_nobody_holds(name: str, spec: dict, prev: dict) -> None:
    """Refuse a save that leaves this endpoint with no credential it can actually resolve.

    THE CONDITION, and why it is this one. The refusal fires when BOTH hold:
      1. after the save no rung of the ladder resolves — no inline key, and `key_var` is in
         neither the Secrets store nor the configured env file; and
      2. this save is what put it there: the credential triple (api_key, key_var,
         key_env_file) CHANGED, compared at the value the loader will read, so spelling the
         kind default out is not a change.

    Clause 2 is load-bearing, not caution. An endpoint already in that state is legitimate and
    common: a keyless local backend (Ollama, vLLM — `key_source.keyless_ok`), and an endpoint
    added before its key exists, which is the sequence the console's add form and
    docs/endpoints.md both follow. Refusing on clause 1 alone would make every one of them
    unsavable — an operator could no longer fix an Ollama endpoint's base_url. A brand-new
    endpoint has no `prev` at all, so the comparison fills in the kind default for it: adding
    one with no credential field is "not yet given a key" and saves, while NAMING a `key_var`
    nobody holds is the state the operator asked to have refused, and is.

    And clause 1 reads the RESULT, not the submitted `key_var`, because of the shape this
    instance actually carries: an endpoint whose inline key is present while its `key_var` is
    in no store (`anthropic metered`, whose key_var is ANTHROPIC_API_KEY). Its credential
    resolves — inline wins — so every ordinary edit of it saves. What is refused is clearing
    that inline key, because there would then be nothing left, which is exactly the save the
    routines riding that endpoint cannot survive.
    """
    inline, var, env_file = _ladder(spec)
    after = api_key_source(api_key=inline, key_var=var, key_env_file=env_file)
    # `prev` at its OWN kind: a kind switch moves the key_var default under the endpoint
    # (openai's OPENAI_API_KEY → anthropic's ANTHROPIC_API_KEY), and that IS a change of where
    # the credential comes from. A brand-new endpoint has no kind to be read at, so it is
    # compared at the submitted one — an add with no credential field is "not given a key yet".
    unchanged = _ladder(spec) == _ladder(prev or {"kind": spec.get("kind", "")})
    if after["source"] != "none" or unchanged:
        return
    where = "is not in the Secrets store"
    if after.get("env_file_miss"):
        where = f"is in neither the Secrets store nor {after['env_file']}"
    raise HTTPException(400, f"{name!r} would be left with no credential: no inline api_key, "
                             f"and {var!r} {where}. Add the secret in "
                             f"Settings → Secrets first, or leave the inline key in place.")
