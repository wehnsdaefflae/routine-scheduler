"""Conversation lifecycle on disk — the Conversations tab's backend core.

A **conversation** is a routine-shaped dir under `conversations_home` (default
`~/conversations/<slug>`): schedule-less, marked `kind: conversation`, and — unlike a
routine — NEVER git-versioned (no `.git`, so the engine's autocommit no-ops; delete means
gone). The user's first message IS instruction.md; every later message resumes the same
run in place (converse semantics), so one conversation = one continuous run with a fresh
budget window per reply. Creation is instant: the `converse` library workflow is
materialized verbatim (no LLM in the path) and the standard rules are bound; a title and
editable tags are generated off-path by `autolabel` via the system model.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from .config import (
    CONVERSATION_DELIBERATION,
    DEFAULT_BUDGETS,
    DEFAULT_PERMISSIONS,
    DEFAULT_RULES,
    ServerConfig,
    write_tuning,
)
from .ids import run_ts
from .paths import atomic_write
from .schedule import server_tz

log = logging.getLogger("rsched.conversations")

CONVERSE_WORKFLOW = "converse"
# Stock rule set for a conversation: the routine defaults plus git-checkpoint (undo points
# in external project repos the conversation edits — the conversation dir itself is
# unversioned). intent-inference earns its place most here: a conversation is where the
# user intervenes constantly, so every reply is evidence about what they actually want.
CONVERSATION_RULES = [*DEFAULT_RULES, "git-checkpoint"]
# Same default permission surface as routines, plus background-tasks (the `detach` action):
# launching long fire-and-forget jobs that outlive a reply is a conversation-shaped capability
# (the finished task reports back into the chat). Shell stays a one-click opt-in.
CONVERSATION_PERMISSIONS = [*DEFAULT_PERMISSIONS, "background-tasks"]
# Per-REPLY ceilings (each user message resumes the run with a fresh window — turns, wall
# clock, tokens and subruns all reset), and deliberately a BACKSTOP rather than a pace. The
# old 10-turn cap was the pace: the model read it at turn 1 and never attempted anything
# that would not fit, so replies came out short by PLANNING, not by truncation — and turn 11
# force-finished with an engine string the user read as the reply. What bounds a reply now
# is the work reaching a point worth handing over (see the converse pattern's checkpoint
# rule); this only stops a runaway. Tokens ride the default (-1 = unlimited); max_subruns
# rides the default too — decomposing a heavy step is a normal move, not a rationed one.
CONVERSATION_BUDGETS = {**DEFAULT_BUDGETS, "max_turns": 40, "max_wall_clock_min": 60}
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
    the user's turn.
    """
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
    multimodal, else described by an image-describing util the engine falls back to). Images
    are auto-shown to a multimodal model already, so view_image is mainly for another look.

    This block is prose the model reads, so it names the CAPABILITY, never the util behind it
    — the fallback tool is the engine's choice, not the run's, and naming it here put a util
    name into every conversation that ever carried an attachment.
    """
    if not paths:
        return ""
    lines = "\n".join(f"- {p}" for p in paths)
    return ("\n\n[attached files — read text with read_file; SEE images/PDFs with the "
            "view_image action (shown to you directly when this model is multimodal, else "
            "described for you automatically); spreadsheets via a fitting util]\n"
            f"{lines}")


def _seed_instruction(pb: dict | None, first_message: str, conv_dir: Path) -> str:
    """instruction.md for a conversation. Without a playbook it IS the first message. With one, the
    playbook's brief (MAIN.md body) leads as the working brief and the first message SPECIALIZES it;
    on-demand detail files are copied into `<conv>/playbook/` so the run can read them with
    read_file (the use-instruction analog: MAIN always loaded, details pulled in on demand).
    """
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


def create_conversation(server: ServerConfig, *, slug: str, first_message: str,  # noqa: PLR0913 — the parameter list IS the composer's config surface (scaffold's twin)
                        workdir: str = "", models: dict[str, str] | None = None,
                        permissions: list[str] | None = None,
                        capabilities: dict | None = None, deliberation: str = "",
                        playbook_slug: str = "",
                        budgets: dict | None = None,
                        fs_read_roots: list[str] | None = None,
                        fs_write_roots: list[str] | None = None,
                        rules: list[str] | None = None,
                        connections: dict[str, str] | None = None) -> Path:
    """Create <conversations_home>/<slug> ready to run: materialized converse main.md with
    a Standing-practices tail naming the rules it holds, instruction.md = the first message,
    and a schedule-less routine.yaml marked `kind: conversation`. NO git init — a
    conversation is deliberately unversioned (the engine's autocommit no-ops without .git).

    A `playbook_slug` seeds instruction.md from that library playbook's brief (the first message
    specializes it) and records a `playbook: {slug, commit}` binding — the Update-playbook button
    later revises that source playbook from this conversation's deltas.

    `rules` (slugs) and `connections` ({provider: account}) are likewise chosen PRE-START
    (F339). Rules must be: a rule reaches the prompt through main.md's Standing-practices
    tail, materialized here — one added afterwards never governs reply #1, which has already
    fired. `rules=None` keeps the CONVERSATION_RULES default; an explicit list replaces it.

    `fs_read_roots` / `fs_write_roots` are extra folder grants applied at CREATE time (D70):
    they land in routine.yaml's native root lists — the same keys an allow-forever fs grant
    is written to (web.grants_apply) — BEFORE the engine boots, so reply #1 already runs
    with the access (the workdir stays the first root: the project directory).
    """
    from . import library_docs, playbooks
    from . import rules as rules_mod
    from .rules import with_practices_tail
    from .workflows.adapt import dump_markdown
    from .workflows.library import head_commit, read_workflow
    from .workflows.pyworkflow import render_markdown

    conv_dir = server.conversations_home / slug
    if conv_dir.exists():
        raise ValueError(f"conversation dir {conv_dir} already exists")
    meta, raw = read_workflow(server.libraries_home, CONVERSE_WORKFLOW)
    pb = playbooks.read_playbook(server.libraries_home, playbook_slug) if playbook_slug else None
    title = fallback_title(first_message if first_message.strip()
                           else (str(pb["meta"].get("title")) if pb else "conversation"))

    for sub in ("state", "inbox", "attachments", "artifacts"):
        (conv_dir / sub).mkdir(parents=True)
    # the general rules a conversation holds: slugs only — the prose stays in the library.
    # F339: the composer may choose them PRE-START, because a rule reaches the prompt through
    # main.md's Standing-practices tail, which is materialized right here — a rule added after
    # creation does not govern reply #1, which has already fired.
    wanted = CONVERSATION_RULES if rules is None else rules
    active_rules = [r for r in wanted if r in set(library_docs.slugs(server.rules_home))]
    rule_summaries = rules_mod.summaries(server.rules_home, active_rules)
    commit = head_commit(server.libraries_home)
    main_meta = {"name": title, "slug": slug,
                 "materialized_from": {"slug": CONVERSE_WORKFLOW, "commit": commit,
                                       "version": meta.get("version", 0)},
                 **({"tools": list(meta["tools"])} if meta.get("tools") is not None else {})}
    body = with_practices_tail(render_markdown(raw, meta), rule_summaries)
    (conv_dir / "main.md").write_text(dump_markdown(main_meta, body), encoding="utf-8")
    (conv_dir / "instruction.md").write_text(
        _seed_instruction(pb, first_message, conv_dir) + "\n", encoding="utf-8")
    (conv_dir / "LEDGER.md").write_text(_LEDGER_SEED, encoding="utf-8")

    available_perms = set(library_docs.slugs(server.permissions_home))
    active_perms = [p for p in (permissions if permissions is not None
                                else CONVERSATION_PERMISSIONS) if p in available_perms]
    from .grants import capabilities_for, floor_capabilities, read_library_requires

    # a caller-resolved mapping (the composer's ⚙ panel, already validated + floored by
    # resolve_permission_layers) wins; otherwise derive it from the active docs — under
    # the SAME raise-then-floor discipline every save applies (decided 2026-07-23: a
    # mapping is floored from BIRTH on every path, so no orphan capability ever persists)
    if capabilities is None:
        lib = read_library_requires(server.permissions_home)
        capabilities = floor_capabilities(active_perms, lib,
                                          capabilities_for(active_perms, lib))
    cfg = {
        "name": title,
        "slug": slug,
        "kind": "conversation",
        "description": title,
        "enabled": True,
        "schedule": {"cron": "", "tz": server_tz(), "catchup": "skip"},
        "workflow": {"library_slug": CONVERSE_WORKFLOW, "library_commit": commit,
                     "version": meta.get("version", 0)},
        **({"playbook": {"slug": playbook_slug, "commit": commit}} if pb else {}),
        **({"models": models} if models else {}),
        "permissions": active_perms,
        "rules": active_rules,
        # D55/F339: OAuth bindings chosen on the composer, so reply #1 already acts as the
        # account instead of failing on an unbound connection and asking mid-run.
        **({"connections": dict(connections)} if connections else {}),
        "capabilities": capabilities,
        "budgets": {**CONVERSATION_BUDGETS,
                    **{k: int(v) for k, v in (budgets or {}).items() if k in DEFAULT_BUDGETS}},
        "retention": {"keep_runs": 1000},   # one continuous run — retention never prunes it
    }
    # workdir first (the project directory — detail/PATCH treat the first write root as it),
    # then the composer's extra folder grants, deduped, in the canonical native-key form.
    # A read+write folder lands in BOTH lists (the workdir convention: the engine's own
    # read_file resolves against fs_read_roots only, so a write-only root would be
    # util-writable yet unreadable to the file actions).
    wd = [workdir.strip()] if workdir.strip() else []
    write_roots = wd + [r for r in (fs_write_roots or []) if r not in wd]
    read_roots = list(write_roots)
    read_roots += [r for r in (fs_read_roots or []) if r not in read_roots]
    if read_roots:
        cfg["fs_read_roots"] = read_roots
    if write_roots:
        cfg["fs_write_roots"] = write_roots
    atomic_write(conv_dir / "routine.yaml",
                 yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
    # tuning.yaml: chat is judgment-heavy — context-on-paper by default (composer +
    # header-panel slider; a pre-start pick governs reply #1 already)
    write_tuning(conv_dir, {"deliberation": deliberation or CONVERSATION_DELIBERATION})
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
    """Best-effort title + tags from the first message via the CONVERSATION'S OWN model —
    the same model its replies use (`for_model("main", …)`, with the system model as the
    fallback when the conversation pins no model), so a conversation set to an uncensored
    model gets its title from that model too, not a default model that might refuse. Runs
    OFF the reply path (the API fires it in a thread). Falls back to the first-line title
    already written at creation; never raises. Only touches name/description/tags — keys the
    engine never writes, so a live run is safe.
    """
    try:
        from .endpoints import EndpointRegistry

        models: dict = {}
        try:
            conv_cfg = yaml.safe_load((conv_dir / "routine.yaml").read_text(encoding="utf-8")) or {}
            models = conv_cfg.get("models") or {}
        except Exception:
            models = {}
        endpoint, ref = EndpointRegistry(server).for_model("main", models)
        comp = endpoint.complete(
            [{"role": "user", "content":
              "Title this new conversation with an agent, and tag it. First message:\n---\n"
              + text[:2000] + "\n---\nReturn ONLY the JSON object {title, tags}."}],
            model=ref.model, schema=_LABEL_SCHEMA, effort=ref.effort,
            temperature=ref.temperature, max_tokens=ref.max_tokens, timeout=60,
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
        # atomic: the daemon scans routine.yaml between replies — never let it read a torn file
        atomic_write(conv_dir / "routine.yaml",
                     yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    except Exception as exc:
        log.info("autolabel skipped for %s: %s", conv_dir.name, exc)
