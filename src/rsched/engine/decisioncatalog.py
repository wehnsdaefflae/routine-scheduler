"""CAPABILITIES' one line naming the DECISION models a run reaches.

Prompt prose, so it lives beside the section that prints it rather than beside the transport
(`endpoints/decisions.py`, which owns resolution). It was part of the `decide` ACTION's module
until D288 (2026-11-25) retired that kind: the capability travels by util now, and what the run
is told — which models exist, which take images, which answers by default — is unchanged by
how the call is spelled.
"""

from __future__ import annotations


def catalog_line(server, roles: dict | None = None) -> str:
    """The line, or "" when this instance has no decision model configured.

    "" is the whole behaviour for an unconfigured instance: a run is never told about a
    capability it cannot reach (`endpoints/decisions.decision_catalog` reports a row per
    configured model, and a row carrying an `error` is one that failed to resolve now).
    """
    from ..endpoints.decisions import decision_catalog

    rows = [r for r in decision_catalog(server, roles) if "error" not in r]
    if not rows:
        return ""
    parts = [f"{r['name']} ({'text + images' if r['images'] else 'text'}"
             + (f", {r['default']}" if r.get("default") else "") + ")" for r in rows]
    return "Decision models: " + ", ".join(parts)
