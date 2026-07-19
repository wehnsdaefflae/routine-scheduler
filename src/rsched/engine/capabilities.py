"""The CAPABILITIES prompt section — what this run can ACTUALLY do, stated up front:
model + context window, the action kinds usable this run, the held permissions with their
short conduct notes, spawnable workflow patterns, and the util catalog at name+summary
altitude (exact usage stays on-demand via `util name=list`).
"""

from __future__ import annotations

from .run_context import RunContext

_PERMISSION_NOTE_MAX_LINES = 14


def _permission_notes(ctx: RunContext, g) -> str:
    """Usage notes for the held permissions that carry one — the library permission's body,
    capped. This is the ONLY prose a permission contributes to the prompt (permissions are
    an enforcement surface, not standards); traits carry the routine's practice prose.
    """
    from .. import library_docs

    try:
        home = ctx.server.permissions_home
    except AttributeError:      # bare test contexts
        return ""
    chunks = []
    for slug in g.active:
        raw = library_docs.read_doc(home, slug)
        if not raw:
            continue
        body = library_docs.doc_body(raw).strip()
        lines = list(body.splitlines())
        if not lines:
            continue
        if len(lines) > _PERMISSION_NOTE_MAX_LINES:
            lines = [*lines[:_PERMISSION_NOTE_MAX_LINES], "[…]"]
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks)


def capabilities_digest(ctx: RunContext, allowed_kinds: set[str] | None = None) -> str:
    """What this run can ACTUALLY do, stated up front: model + context window, the action
    kinds usable this run (workflow tools ∩ grants), the held permissions with their
    capability notes, and the util catalog at one line per util. Every run — including the
    wizard's clarify session, whose tools allowlist can't even call `util name=list` —
    plans against this instead of guessing. Exact usage flags still come from
    `util name=list` (live, never stale).
    """
    from .. import utils_lib
    from .actions import ALWAYS_KINDS, KINDS

    parts: list[str] = []
    try:
        _endpoint, ref = ctx.registry.for_model("main", ctx.routine.models)
        parts.append(f"Model: {ref.endpoint}/{ref.model} — context window ≈ "
                     f"{ref.context_chars:,} chars; the engine archives the middle of "
                     "the conversation to on-disk history at ~60-80% of that, so budget your "
                     "reads (large files via read_file ranges, not whole).")
    except Exception:
        pass
    g = ctx.grants
    kinds = [k for k in KINDS
             if (allowed_kinds is None or k in allowed_kinds or k in ALWAYS_KINDS)
             and (g is None or g.allows_kind(k))]
    parts.append("Action kinds usable this run: " + ", ".join(kinds) + ". Anything else is "
                 "rejected by the engine before it becomes a turn.")
    if g is not None:
        cap_bits = []
        if g.allows_kind("write_util"):
            cap_bits.append({
                "always": "write_util (every create/revise needs the user's approval)",
                "creations": "write_util (NEW utils need approval; revisions are autonomous "
                             "once the selftest passes)",
                "never": "write_util (autonomous, selftest-gated)",
            }[g.confirm])
        if g.allows_kind("remove_util"):
            cap_bits.append("remove_util (delete a global util the library no longer needs; "
                            "refused while another util still calls it)")
        if g.allows_kind("schedule_run"):
            cap_bits.append("schedule_run (arm/cancel a one-shot future run of a routine — "
                            "self-target always; other routines via the scheduling permission)")
        cap_bits += [f"reserved util {u!r}" for u in sorted(g.utils)]
        if g.run_history != "none":
            cap_bits.append("read previous runs under runs/ "
                            + ("(the last run only)" if g.run_history == "last"
                               else "(all of them)"))
        if getattr(g, "workflows", "catalog") == "generate":
            cap_bits.append("generate a NEW workflow pattern for a subtask when none in the "
                            "catalog fits (set that subtask's workflow to 'generate')")
        parts.append("Capabilities enabled (user-set, engine-enforced): "
                     + ("; ".join(cap_bits) if cap_bits else "(none beyond the base kinds)")
                     + ". Held permissions (conduct notes below): "
                     + (", ".join(g.active) if g.active else "(none)") + ".")
        notes = _permission_notes(ctx, g)
        if notes:
            parts.append(notes)
    if "spawn" in kinds:
        try:
            from ..workflows.library import list_workflows

            patterns = list(list_workflows(ctx.server.library_home))
        except Exception:
            patterns = []
        if patterns:
            parts.append("Sub-workflow patterns for spawn — pick the one matching the CHILD's "
                         "purpose, never reflexively the default:\n"
                         + "\n".join(f"- {w['slug']} — {w['description']}" for w in patterns))
    utils = utils_lib.list_utils(ctx.server.utils_home)
    if utils:
        lines = []
        for u in utils:
            head = u["summary"] or u["name"]
            if not head.startswith(u["name"]):
                head = f"{u['name']} — {head}"
            note = ("  [reserved — not granted to this routine]"
                    if g is not None and u["name"] in g.gated_utils
                    and u["name"] not in g.utils else "")
            lines.append(f"- {head}{note}")
        header = (f'Global utils ({len(utils)}; run `util name=list args=["<name>"]` for '
                  "one's exact usage before calling it):" if "util" in kinds else
                  f"Global utils ({len(utils)} — this workflow cannot CALL utils; the list "
                  "tells you what a routine can be built to do):")
        parts.append(header + "\n" + "\n".join(lines))
    else:
        parts.append("Global utils: (none in the library yet).")
    return "\n\n".join(parts)
