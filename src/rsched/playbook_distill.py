"""Distil a conversation into a reusable playbook (and revise one) via the system model.

The Save-as-playbook / Update-playbook buttons in a conversation call these: read the intent
(instruction.md — the first message — plus the run transcript) and the PROCEDURE that satisfied
it, then ask the system model for a GENERALIZED playbook a future conversation can be seeded with.
Mirrors workflows/adapt.decompose (a structured LLM inference with a graceful failure) and its
refuse-to-degrade discipline (a revise never overwrites the current playbook with an empty result).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import ids

PLAYBOOK_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["slug", "title", "when", "tags", "axis", "main"],
    "properties": {
        "slug": {"type": "string", "description": "kebab-case identifier for the playbook"},
        "title": {"type": "string", "description": "short imperative title"},
        "when": {"type": "string",
                 "description": "ONE line: when to reuse this playbook (this is the "
                                "catalog entry)"},
        "tags": {"type": "array", "items": {"type": "string"},
                 "description": "2-5 short lowercase topic tags (domain or project words)"},
        "axis": {"type": "string",
                 "description": "the generalization axis — what varies between uses (one line)"},
        "main": {"type": "string",
                 "description": "MAIN.md BODY markdown, NO front matter: a '## Parameters' list "
                                "using {{named}} placeholders for what varies, '## Instructions' "
                                "(imperative steps for a future agent), and optional '## Detailed "
                                "references' / '## Notes' sections"},
        "details": {"type": "array", "description": "optional on-demand detail files for long "
                    "material (omit for a short playbook)", "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["name", "body"],
                        "properties": {
                            "name": {"type": "string",
                                     "description": "kebab-case detail file name, no extension"},
                            "body": {"type": "string",
                                     "description": "the detail file's markdown"}}}},
    },
}

_DISTILL_PROMPT = """You are capturing a finished piece of work as a REUSABLE PLAYBOOK — a \
generalized brief that will seed FUTURE conversations so they one-shot the same KIND of result.

Below is a conversation: the user's initial request, the steps the agent took, and its replies.
Infer (a) the user's TRUE underlying intent and (b) the PROCEDURE that actually satisfied it, then
generalize BOTH into a playbook that applies to similar-but-different cases.

CONVERSATION:
---
{digest}
---

Generalize well:
- Choose the generalization AXIS — what should VARY between uses (parameterize it with {{named}} \
placeholders under '## Parameters') vs. what stays FIXED (keep concrete). State it in "axis".
- Capture the user's CORRECTED final intent: where they intervened or redirected mid-conversation, \
keep the corrected instruction and drop the superseded one.
- Phrase '## Instructions' as imperative steps to a future agent — not a narrative of what happened.
- Keep "main" lean (aim <=150 lines). Push long examples/references into "details" files, each \
referenced from main by a one-line "read `<name>.md` when you need X"; omit details for a short one.
- "when" is ONE line: when to reach for this playbook.

Return ONLY the JSON playbook object."""

_REVISE_PROMPT = """You are REVISING an existing playbook to reflect what a NEW conversation \
taught you. The user re-ran this playbook and adjusted course; fold those adjustments — corrected \
intent, better or added steps, dropped steps — back into it. Keep everything that still holds; \
refine and tighten, never blindly rewrite from scratch.

EXISTING PLAYBOOK (its full MAIN.md):
---
{existing}
---

THE NEW CONVERSATION (where the user adjusted the procedure or intent):
---
{digest}
---

Keep the SAME slug "{slug}". Preserve the parts that still hold, change only what this \
conversation showed should change, keep "main" lean (push long material into "details"), and \
state the (possibly updated) generalization axis.

Return ONLY the JSON playbook object."""


def conversation_digest(conv_dir: Path, *, max_chars: int = 24_000) -> str:
    """A compact intent+procedure digest: the first message (instruction.md — NOT in the
    transcript) followed by each user message, assistant action (the procedure), and reply from the
    conversation's one continuous run.
    """
    from . import registry
    from .engine.transcript import read_events

    lines: list[str] = []
    instr = conv_dir / "instruction.md"
    if instr.is_file():
        lines.append("INITIAL REQUEST (the first message / brief):\n"
                     + instr.read_text(encoding="utf-8").strip())
    runs = registry.run_index(conv_dir, conv_dir.name)
    tpath = (runs[0].dir / "transcript.jsonl") if runs else None
    if tpath and tpath.exists():
        events, _ = read_events(tpath, 0)
        for ev in events:
            t, p = ev.get("type"), (ev.get("payload") or {})
            if t == "user_injection" and p.get("source") != "engine":
                lines.append("USER: " + str(p.get("text", "")).strip())
            elif t == "assistant_action" and p.get("kind") != "finish":
                label = p.get("kind") if p.get("kind") != "util" else f"util:{p.get('name', '')}"
                target = p.get("path") or ""
                lines.append(f"  · [{label}] {str(p.get('say', '')).strip()}"
                             + (f" ({target})" if target else ""))
            elif t == "finish":
                lines.append("ASSISTANT REPLY: " + str(p.get("summary", "")).strip())
            elif t == "answer":
                lines.append("USER (answer): " + str(p.get("text", "")).strip())
    text = "\n".join(ln for ln in lines if ln)
    if len(text) <= max_chars:
        return text
    # keep the TAIL: the newest exchanges are exactly what an Update-playbook distils
    return "[earlier conversation truncated]\n" + text[-max_chars:]


def _oneline(v: object) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _slugify(v: object) -> str:
    """The ONE slug rule, capped at a filename-sane length. `ids.slugify` owns the alphabet
    so a name a routine gets and a name a playbook gets cannot round-trip two different
    ways; only the default and the cap are this module's.
    """
    return ids.slugify(str(v or ""), default="playbook")[:60]


def _normalize(data: dict) -> dict:
    """Validate + clean the model's playbook into the shape materialize() consumes. Raises
    ValueError on an empty body (the refuse-to-degrade guard).
    """
    main_body = str(data.get("main") or "").strip()
    if not main_body:
        raise ValueError("the model returned an empty playbook body")
    tags = [re.sub(r"[^a-z0-9-]+", "-", str(t).lower()).strip("-")
            for t in (data.get("tags") or [])]
    tags = [t for t in tags if t][:6] or ["general"]
    details = {}
    for d in (data.get("details") or []):
        name, body = str(d.get("name") or "").strip(), str(d.get("body") or "").strip()
        if name and body:
            details[name] = body
    slug = _slugify(data.get("slug") or data.get("title"))
    return {"slug": slug, "title": _oneline(data.get("title")) or slug,
            "when": _oneline(data.get("when")), "axis": _oneline(data.get("axis")),
            "tags": tags, "main_body": main_body, "details": details}


def _infer(server, prompt: str, *, purpose: str, kind: str) -> dict:
    """One schema'd system-model call, advancing the CONFIGURED chain when a rung fails hard.

    F907: the operator's save-as-playbook on `c-20261014-134208` died on an upstream
    credential-pool HTTP 429. `for_system()` resolves the whole chain and `failover.pick` hands
    back the member SERVING it — resolve-time avoidance — but a failure that happens during the
    CALL had nowhere to go here: the exhausted `EndpointError` escaped to the route's 502 while
    the `fallbacks:` the catalog already declares went untried. The engine's turn loop has had
    call-time failover since `engine/degrade.py`; this button never ran through it.

    So the same primitive is used, not a new policy: `failover.next_after` returns the next
    usable member strictly after the one that just failed (which `InstrumentedEndpoint` has
    already marked cooling), skipping members already cooling, and marks it as serving so the
    rest of the process starts there. Deliberately narrow:

    - only a RETRYABLE error fails over — a 429, a 5xx, a timeout, after the adapter's own
      retries are spent. An auth failure or a bad request is the same on every rung, so walking
      the chain would multiply one real error into several;
    - every rung keeps ITS OWN parameters (`effort`, `temperature`, `max_tokens`), as
      `resolve_chain` resolved them, and the schema and the 180 s timeout are unchanged. Nothing
      is truncated to fit a smaller rung: a member that cannot hold the prompt is a failure to
      report, not a digest to silently cut;
    - a MALFORMED or EMPTY reply is NOT a transport failure and stays outside this loop — it
      raises from `_normalize` exactly as before, so a model that answered badly is not retried
      as though it had not answered;
    - the chain is resolved LAZILY, on the first retryable failure only. The happy path keeps
      the single resolution it always had, and a registry that cannot produce a chain (a test
      double, a server with no catalog) propagates the original error rather than raising a
      second one on top of it.
    """
    from .endpoints import EndpointRegistry, failover
    from .endpoints.base import EndpointError

    registry = EndpointRegistry(server)
    endpoint, ref = registry.for_system()
    chain: list | None = None
    attempted: list[str] = []
    while True:
        attempted.append(ref.name or ref.model)
        try:
            comp = endpoint.complete([{"role": "user", "content": prompt}], model=ref.model,
                                     schema=PLAYBOOK_SCHEMA, effort=ref.effort,
                                     temperature=ref.temperature, max_tokens=ref.max_tokens,
                                     timeout=180, purpose=purpose, kind=kind)
            break
        except EndpointError as exc:
            if not exc.retryable:
                raise
            if chain is None:
                try:
                    chain = registry.resolve_chain(server.system_model, server.system_effort)
                except Exception:
                    raise exc from None
            nxt = failover.next_after(chain, ref, has_media=False, prompt_tokens=0)
            if nxt is None:
                raise EndpointError(
                    f"{exc} — the system-model chain is exhausted "
                    f"(tried {', '.join(attempted)})",
                    retryable=exc.retryable, auth=exc.auth) from exc
            endpoint, ref = nxt
    data = comp.parsed if comp.parsed is not None else json.loads(comp.text)
    return _normalize(data)


def distill_playbook(server, conv_dir: Path) -> dict:
    """Infer a NEW generalized playbook from a conversation. Raises on no endpoint / bad output —
    the caller (a user-initiated button) surfaces the error rather than silently degrading.
    """
    digest = conversation_digest(conv_dir)
    return _infer(server, _DISTILL_PROMPT.format(digest=digest),
                  purpose="Distil conversation → playbook", kind="save-playbook")


def revise_playbook(server, conv_dir: Path, existing_main: str, slug: str) -> dict:
    """Revise the bound playbook from this conversation's deltas. Never renames (keeps `slug`)."""
    digest = conversation_digest(conv_dir)
    out = _infer(server, _REVISE_PROMPT.format(existing=existing_main, digest=digest, slug=slug),
                 purpose=f"Revise playbook → {slug}", kind="update-playbook")
    out["slug"] = slug
    return out


def materialize(pb: dict) -> tuple[str, dict]:
    """(full MAIN.md text, details dict) from a distilled/revised playbook — stamps today's date."""
    from . import playbooks

    meta = {"slug": pb["slug"], "title": pb["title"], "when": pb["when"],
            "tags": pb["tags"], "axis": pb["axis"],
            # user-facing doc metadata: the LOCAL calendar date, made timezone-explicit
            "updated": datetime.now().astimezone().date().isoformat()}
    return playbooks.compose_main(meta, pb["main_body"]), pb["details"]
