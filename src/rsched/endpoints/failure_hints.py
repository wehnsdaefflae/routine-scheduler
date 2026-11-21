"""What a proxy's failure MEANS, in words — the line under a failed endpoint test.

A subscription proxy relays upstream errors that read like a scheduler defect when they are
not (a listed-but-not-enabled model, an id no signed-in account serves, every account cooling
down), so the console explains them instead of showing a bare HTTP status.
"""

from __future__ import annotations

#: (substring of the error text, what it means and what to do) — first match wins.
HINTS: tuple[tuple[str, str], ...] = (
    ("Requested entity was not found", (
        "The proxy lists this model, but its upstream provider answered NOT FOUND: the model is "
        "not enabled for this account (yet). Providers publish a model before they switch it "
        "on, and a proxy's own list can run ahead of that. Nothing here is misconfigured — try "
        "again later, or pick a model the provider does serve.")),
    ("unknown provider for model", (
        "No signed-in account on the proxy serves this model id. Sign in (or re-authenticate) "
        "the provider that serves it from this card, or choose an id from the list.")),
    ("auth_unavailable", (
        "Every account that could serve this model is unavailable right now — cooling down "
        "after an error or out of quota. The accounts listed on this card say which, and when "
        "it retries.")),
)


def explain(error: str) -> str:
    """The plain-words meaning of a failed call's error text, or "" when none is known."""
    return next((meaning for needle, meaning in HINTS if needle in error), "")
