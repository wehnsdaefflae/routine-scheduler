"""ChatEndpoint protocol: one stateless completion in, text or natively-parsed JSON out.

Adapters return complete responses (no token streaming — the engine streams whole transcript
events). Retryable transport errors are raised as EndpointError(retryable=True); the shared
`with_retries` helper gives HTTP adapters a uniform 3-try exponential backoff.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

Message = dict  # {"role": "system"|"user"|"assistant", "content": str}

DEFAULT_TIMEOUT = 600
DEFAULT_MAX_TOKENS = 8192


class EndpointError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, auth: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.auth = auth


@dataclass
class Completion:
    text: str                     # raw reply text ("" when only parsed content came back)
    parsed: dict | None = None    # object from the endpoint's native schema mode, if any
    usage: dict = field(default_factory=lambda: {"in": 0, "out": 0})


class ChatEndpoint(Protocol):
    name: str
    context_chars: int
    supports_schema: bool

    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        schema: dict | None = None,
        effort: str | None = None,
        max_tokens: int | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> Completion: ...


def key_from_env_file(path: str, var: str) -> str | None:
    """Read VAR=value from a ~/.credentials/*.env style file (comments/quotes tolerated)."""
    from ..paths import expand

    p = expand(path)
    if not p.exists():
        return None
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() == var:
                return v.strip().strip('"').strip("'")
    return None


def split_system(messages: list[Message]) -> tuple[str, list[Message]]:
    """Pull leading system message(s) out; most APIs want them separated."""
    system_parts, rest = [], []
    for m in messages:
        if m["role"] == "system" and not rest:
            system_parts.append(m["content"])
        else:
            rest.append(m)
    return "\n\n".join(system_parts), rest


def with_retries(fn, *, tries: int = 3, base_delay: float = 1.0):
    """Run fn(); on EndpointError(retryable=True) back off 1s/2s/4s and retry."""
    last: EndpointError | None = None
    for attempt in range(tries):
        try:
            return fn()
        except EndpointError as exc:
            if not exc.retryable:
                raise
            last = exc
            if attempt < tries - 1:
                time.sleep(base_delay * (2**attempt))
    assert last is not None
    raise last
