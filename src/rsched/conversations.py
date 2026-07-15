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
ROUTINE_ONLY_PERMISSIONS = ["run-history"]

# The conversation "state diagram" the Conversations tab shows. A conversation is a LOOP,
# not a one-pass workflow, so its meaningful state is the live reply cycle — not the single
# converse workflow phase (which is never written to state/phase.json, so the generic
# routine state graph never lights a node). These two nodes are lit from the live run state.
CONVERSATION_STATES = [
    {"name": "working", "desc": "the agent is composing a reply"},
    {"name": "waiting for you", "desc": "your turn — send a message to continue"},
]
_WORKING_RUN_STATES = {"queued", "starting", "running"}


def conversation_phase(run_state: str | None) -> str:
    """Map a conversation's live RUN state to its lifecycle phase (the diagram's CURRENT
    node). Anything not actively working — finished, blocked on your answer, brand new — is
    the user's turn."""
    return "working" if run_state in _WORKING_RUN_STATES else "waiting for you"

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
    Paths are relative to the conversation dir; the model reads text with read_file and SEES
    images/PDFs with the view_image action (shown to it directly when the model is
    multimodal, else described by the vision util). Images are auto-shown to a multimodal
    model already, so view_image is mainly for when it wants another look."""
    if not paths:
        return ""
    lines = "\n".join(f"- {p}" for p in paths)
    return ("\n\n[attached files — read text with read_file; SEE images/PDFs with the "
            "view_image action (shown to you directly when this model is multimodal, else "
            f"described by the vision util); spreadsheets via a fitting util]\n{lines}")


def _seed_instruction(pb: dict | None, first_message: str, conv_dir: Path) -> str:
    """instruction.md for a conversation. Without a playbook it IS the first message. With one, the
    playbook's brief (MAIN.md body) leads as the working brief and the first message SPECIALIZES it;
    on-demand detail files are copied into `<conv>/playbook/` so the run can read them with
    read_file (the use-instruction analog: MAIN always loaded, details pulled in on demand)."""
    if not pb:
        return first_message.rstrip()
    parts = [pb["body"].strip()]
    if pb["details"]:
        (conv_dir / "playbook").mkdir(exist_ok=True)
        for name, body in pb["details"].items():
            (conv_dir / "playbook" / name).write_text(body, encoding="utf-8")
        parts.append("Detail files referenced above live under `playbook/` — read e.g. "
                     f"`playbook/{sorted(pb['details'])[0]}` with read_file when a step needs it.")
    req = first_message.strip()
    parts.append("---\n## This conversation's specific request\n"
                 + (req or "(none given — follow the playbook above; ask me for any parameters it "
                    "needs before doing work)"))
    return "\n\n".join(parts)


def create_conversation(server: ServerConfig, *, slug: str, first_message: str,
                        workdir: str = "", models: dict[str, dict] | None = None,
                        permissions: list[str] | None = None, playbook_slug: str = "") -> Path:
    """Create <conversations_home>/<slug> ready to run: materialized converse main.md with
    a Standing-practices tail, verbatim trait copies, instruction.md = the first message,
    and a schedule-less routine.yaml marked `kind: conversation`. NO git init — a
    conversation is deliberately unversioned (the engine's autocommit no-ops without .git).

    A `playbook_slug` seeds instruction.md from that library playbook's brief (the first message
    specializes it) and records a `playbook: {slug, commit}` binding — the Update-playbook button
    later revises that source playbook from this conversation's deltas."""
    from . import library_docs, playbooks
    from .workflows.adapt import dump_markdown
    from .workflows.library import head_commit, read_workflow
    from .workflows.pyworkflow import render_markdown
    from .workflows.scaffold import _with_practices_tail

    conv_dir = server.conversations_home / slug
    if conv_dir.exists():
        raise ValueError(f"conversation dir {conv_dir} already exists")
    meta, _, raw = read_workflow(server.library_home, CONVERSE_WORKFLOW)
    pb = playbooks.read_playbook(server.library_home, playbook_slug) if playbook_slug else None
    title = fallback_title(first_message if first_message.strip()
                           else (str(pb["meta"].get("title")) if pb else "conversation"))

    for sub in ("state", "inbox", "traits", "attachments", "artifacts"):
        (conv_dir / sub).mkdir(parents=True)
    # trait copies: library text verbatim — the conversation's own files from here on
    # (refined by the routine-improver meta routine, like any routine's).
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
    main_meta = {"name": title, "slug": slug,
                 "materialized_from": {"slug": CONVERSE_WORKFLOW, "commit": commit,
                                       "version": meta.get("version", 0)},
                 **({"tools": list(meta["tools"])} if meta.get("tools") is not None else {})}
    body = _with_practices_tail(render_markdown(raw, meta), trait_summaries)
    (conv_dir / "main.md").write_text(dump_markdown(main_meta, body), encoding="utf-8")
    (conv_dir / "instruction.md").write_text(
        _seed_instruction(pb, first_message, conv_dir) + "\n", encoding="utf-8")
    (conv_dir / "LEDGER.md").write_text(_LEDGER_SEED, encoding="utf-8")

    available_perms = set(library_docs.slugs(server.permissions_home))
    active_perms = [p for p in (permissions if permissions is not None
                                else CONVERSATION_PERMISSIONS) if p in available_perms]
    from .grants import capabilities_for, read_library_requires

    capabilities = capabilities_for(active_perms, read_library_requires(server.permissions_home))
    cfg = {
        "name": title,
        "slug": slug,
        "kind": "conversation",
        "description": title,
        "enabled": True,
        "schedule": {"cron": "", "tz": "Europe/Berlin", "catchup": "skip"},
        "workflow": {"library_slug": CONVERSE_WORKFLOW, "library_commit": commit},
        **({"playbook": {"slug": playbook_slug, "commit": commit}} if pb else {}),
        **({"models": models} if models else {}),
        "permissions": active_perms,
        "capabilities": capabilities,
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
            model=ref.model, schema=_LABEL_SCHEMA, effort=ref.effort, timeout=60,
            purpose="Conversation title & tags", kind="autolabel")
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
