"""Conversation lifecycle on disk — the Conversations tab's backend core.

A **conversation** is a routine-shaped dir under `conversations_home` (default
`~/conversations/<slug>`): schedule-less, marked `kind: conversation`, and — unlike a
routine — NEVER git-versioned (no `.git`, so the engine's autocommit no-ops; delete means
gone). The user's first message IS instruction.md; every later message resumes the same
run in place (converse semantics), so one conversation = one continuous run with a fresh
budget window per reply. Creation is instant: the `converse` library workflow is
materialized verbatim (no LLM in the path) and traits are copied stock; a title and
editable tags are generated off-path by `autolabel` via the system model.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from .config import DEFAULT_BUDGETS, DEFAULT_PERMISSIONS, ServerConfig
from .ids import run_ts

log = logging.getLogger("rsched.conversations")

CONVERSE_WORKFLOW = "converse"
# Stock practice set for a conversation: no improve-* passes (they are per-scheduled-run
# refinement lenses) but git-checkpoint (undo points in external project repos the
# conversation edits — the conversation dir itself is unversioned).
CONVERSATION_TRAITS = ["ask-policy", "global-utils", "web-research", "ledger-discipline",
                       "git-checkpoint"]
# Same default permission surface as routines — shell stays a one-click opt-in.
CONVERSATION_PERMISSIONS = list(DEFAULT_PERMISSIONS)
# ~10 turns per REPLY: each user message resumes the run with a fresh budget window, so
# these are per-reply ceilings, not per-conversation ones. The engine's 85% warning cues
# the model to wrap up with progress and offer to continue. Tokens ride the default
# (-1 = unlimited) — the tight turn cap is what bounds a reply.
CONVERSATION_BUDGETS = {**DEFAULT_BUDGETS, "max_turns": 10, "max_wall_clock_min": 30,
                        "max_subruns": 4}
# Permissions that only make sense for scheduled routines — the UI greys them out.
ROUTINE_ONLY_PERMISSIONS = ["run-history", "run-history-full"]

_LEDGER_SEED = "# LEDGER — conversation\n\n### seed — conversation created\n"


def new_slug(home: Path) -> str:
    """A fresh conversation slug: c-<run_ts>, suffixed on a same-second collision."""
    base = f"c-{run_ts()}"
    slug, n = base, 1
    while (home / slug).exists():
        n += 1
        slug = f"{base}-{n}"
    return slug


def fallback_title(text: str) -> str:
    """No-LLM title: the first non-empty line, tightened."""
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "conversation")
    line = re.sub(r"\s+", " ", line)
    return line[:60] + ("…" if len(line) > 60 else "")


def attachment_note(paths: list[str]) -> str:
    """The block appended to a message (or instruction.md) that carries file attachments.
    Paths are relative to the conversation dir; the model reads text with read_file and is
    steered to the vision util for images/PDFs by the converse workflow."""
    if not paths:
        return ""
    lines = "\n".join(f"- {p}" for p in paths)
    return ("\n\n[attached files — read text with read_file; images/PDFs via the `vision` "
            f"util; spreadsheets via a fitting util]\n{lines}")


def create_conversation(server: ServerConfig, *, slug: str, first_message: str,
                        workdir: str = "", models: dict[str, dict] | None = None,
                        permissions: list[str] | None = None) -> Path:
    """Create <conversations_home>/<slug> ready to run: materialized converse main.md with
    a Standing-practices tail, verbatim trait copies, instruction.md = the first message,
    and a schedule-less routine.yaml marked `kind: conversation`. NO git init — a
    conversation is deliberately unversioned (the engine's autocommit no-ops without .git)."""
    from . import library_docs
    from .workflows.adapt import dump_markdown
    from .workflows.library import head_commit, read_workflow
    from .workflows.pyworkflow import render_markdown
    from .workflows.scaffold import _with_practices_tail

    conv_dir = server.conversations_home / slug
    if conv_dir.exists():
        raise ValueError(f"conversation dir {conv_dir} already exists")
    meta, _, raw = read_workflow(server.library_home, CONVERSE_WORKFLOW)

    for sub in ("state", "inbox", "traits", "attachments", "artifacts"):
        (conv_dir / sub).mkdir(parents=True)
    # trait copies: library text verbatim — the conversation's own files from here on
    # (self-refined via the self-modification permission, like any routine's).
    available = set(library_docs.slugs(server.traits_home))
    trait_summaries: dict[str, str] = {}
    for t in [t for t in CONVERSATION_TRAITS if t in available]:
        raw_doc = library_docs.read_doc(server.traits_home, t)
        body = library_docs.doc_body(raw_doc).strip() if raw_doc else ""
        if not body:
            continue
        (conv_dir / "traits" / f"{t}.md").write_text(body + "\n", encoding="utf-8")
        m = library_docs.DOC_RE.search(body)
        trait_summaries[t] = m.group("summary").strip() if m else ""
    commit = head_commit(server.library_home)
    main_meta = {"name": fallback_title(first_message), "slug": slug,
                 "materialized_from": {"slug": CONVERSE_WORKFLOW, "commit": commit,
                                       "version": meta.get("version", 0)},
                 **({"tools": list(meta["tools"])} if meta.get("tools") is not None else {})}
    body = _with_practices_tail(render_markdown(raw, meta), trait_summaries)
    (conv_dir / "main.md").write_text(dump_markdown(main_meta, body), encoding="utf-8")
    (conv_dir / "instruction.md").write_text(first_message.rstrip() + "\n", encoding="utf-8")
    (conv_dir / "LEDGER.md").write_text(_LEDGER_SEED, encoding="utf-8")

    available_perms = set(library_docs.slugs(server.permissions_home))
    active_perms = [p for p in (permissions if permissions is not None
                                else CONVERSATION_PERMISSIONS) if p in available_perms]
    cfg = {
        "name": fallback_title(first_message),
        "slug": slug,
        "kind": "conversation",
        "description": fallback_title(first_message),
        "enabled": True,
        "schedule": {"cron": "", "tz": "Europe/Berlin", "catchup": "skip"},
        "workflow": {"library_slug": CONVERSE_WORKFLOW, "library_commit": commit},
        **({"models": models} if models else {}),
        "permissions": active_perms,
        "budgets": dict(CONVERSATION_BUDGETS),
        "retention": {"keep_runs": 1000},   # one continuous run — retention never prunes it
    }
    if workdir.strip():
        cfg["fs_read_roots"] = [workdir.strip()]
        cfg["fs_write_roots"] = [workdir.strip()]
    (conv_dir / "routine.yaml").write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return conv_dir


_LABEL_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["title", "tags"],
    "properties": {
        "title": {"type": "string", "description": "3-7 word title for this conversation"},
        "tags": {"type": "array", "items": {"type": "string"},
                 "description": "1-3 short lowercase topic tags (project or domain words)"},
    },
}


def autolabel(server: ServerConfig, conv_dir: Path, text: str) -> None:
    """Best-effort title + tags from the first message via the system model — runs OFF the
    reply path (the API fires it in a thread). Falls back to the first-line title already
    written at creation; never raises. Only touches name/description/tags — keys the
    engine never writes, so a live run is safe."""
    try:
        from .endpoints import EndpointRegistry

        endpoint, ref = EndpointRegistry(server).for_system()
        comp = endpoint.complete(
            [{"role": "user", "content":
              "Title this new conversation with an agent, and tag it. First message:\n---\n"
              + text[:2000] + "\n---\nReturn ONLY the JSON object {title, tags}."}],
            model=ref.model, schema=_LABEL_SCHEMA, effort=ref.effort, timeout=60)
        import json

        data = comp.parsed if comp.parsed is not None else json.loads(comp.text)
        title = re.sub(r"\s+", " ", str(data.get("title") or "")).strip()[:80]
        tags = [re.sub(r"[^a-z0-9-]+", "-", str(t).lower()).strip("-")
                for t in (data.get("tags") or [])][:3]
        tags = [t for t in tags if t]
        if not title:
            return
        raw = yaml.safe_load((conv_dir / "routine.yaml").read_text(encoding="utf-8")) or {}
        raw["name"] = title
        raw["description"] = title
        if tags:
            raw["tags"] = tags
        (conv_dir / "routine.yaml").write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — cosmetic labelling must never break a create
        log.info("autolabel skipped for %s: %s", conv_dir.name, exc)
