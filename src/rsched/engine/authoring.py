"""Library AUTHORING handlers — `write_util`, `write_rule`, `remove_util`.

These WRITE THE SHARED LIBRARY, so each is a ladder of refusals with its own approval dial
(`confirm` for utils, `rule_confirm` for rules — a rule revision lands on every holder at its
next run). Every rung is its own teaching exit; the one rung they share, EDIT MODE's anchor
patch, is one helper (`anchor_edit`, also used by `memory_write`'s edit mode) so a guard taught
to one kind cannot be missing from another.

There is deliberately no `remove_rule`: deleting a rule silently un-binds every holder with
nothing to catch it, so a run reports it and the user deletes it.
"""

from __future__ import annotations

from .. import libgit, sandbox, utils_header, utils_lib, utils_stage
from ..ids import is_slug
from ..paths import resolve_rel
from .askback import still_pending
from .interact import handle_ask, is_approval
from .observations import truncate


def recreate_denial(loop, action: dict) -> list[str]:
    """The never-recreate rule, checked INSIDE the schema-retry cycle: a write_util for a
    slug the user deleted from the util library (per its git history) must not proceed
    silently. The unlock is the grant entity `recreate:<slug>` — allow-now opens it for THIS
    run, a never-decision tombstones it. No allow-forever on purpose: a fresh deletion must
    always outrank an old grant.
    """
    if action.get("kind") != "write_util":
        return []
    ctx = loop.ctx
    name = str(action.get("name") or "")
    if ctx.depth > 0 or not name or not is_slug(name):
        return []   # subruns can't write utils at all — handle_write_util declines them
    home = ctx.server.libraries_home
    if utils_lib.exists(home, name) or not utils_lib.was_deleted(home, name):
        return []   # a revision, or a slug that never existed — no recreation involved
    eid = f"recreate:{name}"
    g = loop.grants
    state = g.entity_state(eid) if g is not None else "undecided"
    if state == "granted_now":
        return []   # the user explicitly allowed this recreate, this run
    if state in ("denied_forever", "denied_now"):
        return [(f"util {name!r} was DELETED from the util library by the user, and "
                 f"{g.request_route(eid)}")]
    return [(f"util {name!r} existed before and was DELETED from the util library by the "
             f"user — a user-deleted util is never recreated without asking. First ask_user "
             f'with request: "{eid}", mode "blocking", and a question saying why it is '
             f"needed; recreate only after the user allows it (the grant covers this run). "
             f"On a deny or a timeout, work without it and note the gap in your finish "
             f"summary.")]


def anchor_edit(text: str, action: dict, *, current: str, verbatim_from: str,
                 whole: str) -> tuple[str, str]:
    """EDIT MODE's one rule for both library kinds: replace the action's verbatim `anchor` in
    `text` — every occurrence with `all: true`. Returns `(patched, "")`, or `("", reason)`,
    the teaching exit naming where to copy the anchor from.

    An EMPTY anchor is refused before it is counted: "" occurs between every two characters.
    """
    anchor = str(action.get("anchor") or "")
    if not anchor:
        return "", (f"pass either {whole} or a non-empty 'anchor' + 'replacement' (an "
                    "in-place revision)")
    count = text.count(anchor)
    if count == 0:
        return "", (f"anchor not found in {current} — copy it VERBATIM (whitespace included) "
                    f"from {verbatim_from}")
    if count > 1 and not action.get("all"):
        return "", (f"anchor occurs {count}× in {current} — extend it until unique, or set "
                    "all: true to replace every occurrence")
    return text.replace(anchor, str(action.get("replacement") or "")), ""


def handle_write_util(loop, action: dict, poll_s: float) -> dict:  # noqa: PLR0911 — gate ladder: every refusal is its own teaching exit
    ctx = loop.ctx
    name, raw_content = action["name"], action.get("content")
    if ctx.depth > 0:
        return {"kind": "write_util", "name": name, "declined": True,
                "reason": "sub-workflows cannot create/revise utils — use existing ones"}
    if name.startswith(utils_lib.STAGING_PREFIX):
        # The selftest staging prefix is hidden from the catalog and swept as stale.
        return {"kind": "write_util", "name": name, "declined": True,
                "reason": f"{utils_lib.STAGING_PREFIX!r} is reserved for utils being "
                          "selftested before install — pick a name without that prefix"}
    home = ctx.server.libraries_home
    utils_lib.ensure_library(home, remote=ctx.server.libraries_remote)
    src_path = str(action.get("path") or "")
    edit_mode = raw_content is None and not src_path
    if edit_mode:
        # Edit mode (D42-B): anchor-patch the EXISTING source engine-side, so a small fix
        # never re-emits a big script through one reply. Same gate as a full rewrite.
        source = utils_lib.read_util(home, name)
        if source is None:
            return {"kind": "write_util", "name": name, "edit_failed": True,
                    "reason": f"no util {name!r} exists to edit — edit mode patches an "
                              "existing script; pass 'content' (the complete script) to "
                              "create a new one"}
        content, problem = anchor_edit(
            source, action, current="the util's current source",
            verbatim_from=f'{{"kind": "util", "name": "show", "args": ["{name}", "--full"]}}',
            whole="'content' (the complete script), 'path' (a file holding it)")
        if problem:
            return {"kind": "write_util", "name": name, "edit_failed": True, "reason": problem}
    elif src_path:
        # Content-from-file (F280): a file's EXACT bytes, read under the run's readable
        # roots only; the SAME gate as inline content.
        try:
            content = resolve_rel(ctx.routine.dir, src_path,
                                  ctx.read_roots()).read_text(encoding="utf-8")
        except (OSError, PermissionError) as exc:
            return {"kind": "write_util", "name": name, "read_failed": True,
                    "reason": str(exc)}
    else:
        content = str(raw_content)
    # Doc-standard gate BEFORE the approval ask: a util without tags or with undeclared
    # secrets never reaches the user or the library — the observation names the fix.
    problems = utils_header.header_problems(content)
    if problems:
        return {"kind": "write_util", "name": name, "header_ok": False,
                "problems": problems}
    creating = not utils_lib.exists(home, name)
    # Approval policy is the routine's write_util level (always / creations / never; no grants
    # = confirm everything). A revision that WIDENS the util's reach is always asked: it is a
    # new grant for every routine that calls it.
    widened = ([] if creating
               else utils_header.widening(utils_lib.read_util(home, name) or "", content))
    approved_for_run = ("approval:write_util" in getattr(ctx, "granted_now", set())
                        and not widened)
    if widened or (not approved_for_run
                   and (ctx.grants is None or ctx.grants.needs_confirm(creating))):
        verb = "create" if creating else "revise"
        excerpt = (f"anchor:\n{str(action.get('anchor'))[:180]}\nreplacement:\n"
                   f"{str(action.get('replacement') or '')[:180]}" if edit_mode
                   else f"First lines:\n{content.strip()[:400]}")
        reach = (f" This revision lets it reach {', '.join(widened)} — which every routine "
                 "calling it then reaches too." if widened else "")
        ask = handle_ask(loop, {
            "question": f"Approve {verb} of global util '{name}'?{reach}"
                        f"{_impact_note(ctx, 'util', name, content)} "
                        f"{'In-place patch — ' if edit_mode else ''}{excerpt}",
            "mode": "blocking", "options": ["approve", "decline",
                "approve this kind for the rest of this run"],
            "default": "the util is NOT applied until approved"}, poll_s,
            qtype="util-approval", subject=name)
        if not ask.get("answered"):
            # pending — after an ask-back the operator's words ride THIS observation
            return {"kind": "write_util", "name": name, **still_pending(ask)}
        if not is_approval(ask["answer"]):
            # carry the verbatim answer: the user may have meant approval in other words
            return {"kind": "write_util", "name": name, "declined": True,
                    "answer": str(ask["answer"])[:200]}
        if str(ask["answer"]).strip().lower() == "approve this kind for the rest of this run":
            ctx.granted_now.add("approval:write_util")
    # Selftest BEFORE the library holds the text (D161): tested under a throwaway slug,
    # `utils/<name>` written only on green, so no `gu <name>` ever reaches an untested script.
    ok, output = utils_stage.write_selftested(
        home, name, content, policy=sandbox.base_policy(ctx.server),
        message=f"{'create' if creating else 'revise'} {name}",
        routines_home=ctx.server.routines_home, aborted=ctx.aborted)
    if not ok:
        # Head+tail, never a head slice: a traceback's END is the repair material.
        output, _ = truncate(output, cap=2000)
        return {"kind": "write_util", "name": name, "created": creating,
                "selftest_ok": False, "reverted": True, "output": output}
    return {"kind": "write_util", "name": name, "created": creating, "selftest_ok": True}


def _impact_note(ctx, kind: str, name: str, content: str | None) -> str:
    """The blast radius, for the approval question. Advisory and best-effort — never a reason
    authoring fails.
    """
    from ..library_impact import impact, impact_lines

    try:
        return " " + " | ".join(impact_lines(impact(ctx.server, kind, name, content)))
    except (OSError, ValueError, AttributeError):
        return ""


def handle_write_rule(loop, action: dict, poll_s: float) -> dict:  # noqa: PLR0911 — gate ladder: every refusal is its own teaching exit
    """Author or revise a GENERAL RULE in the shared library — the write_util shape, applied
    to prose instead of code, gated by the rule-authoring permission.

    Its approval dial is its OWN (`rule_confirm`), since a rule binds many routines; its gate
    is the library LINTER (run before the approval ask) instead of a selftest. DELETION is
    deliberately not an action — it would silently un-bind every holder; the user deletes it.
    """
    from .. import library_docs
    from ..workflows.lint import lint_rule_text

    ctx = loop.ctx
    name = action["name"]
    if ctx.depth > 0:
        return {"kind": "write_rule", "name": name, "declined": True,
                "reason": "sub-workflows cannot author rules — a rule binds whole routines, "
                          "so it is a top-level decision"}
    if not is_slug(name):
        return {"kind": "write_rule", "name": name, "declined": True,
                "reason": f"{name!r} is not a kebab-case slug — a rule's slug is its filename"}
    home = ctx.server.rules_home
    home.mkdir(parents=True, exist_ok=True)
    existing = library_docs.read_doc(home, name)
    raw_content = action.get("content")
    if raw_content is None:
        # Edit mode, the normal shape for a revision: legible as a diff to the approver.
        if existing is None:
            return {"kind": "write_rule", "name": name, "edit_failed": True,
                    "reason": f"no rule {name!r} exists to revise — pass 'content' (the "
                              "complete rule markdown) to author a new one"}
        content, problem = anchor_edit(
            existing, action, current="the rule's current text",
            verbatim_from=f'{{"kind": "read_rule", "name": "{name}"}}',
            whole="'content' (the whole rule)")
        if problem:
            return {"kind": "write_rule", "name": name, "edit_failed": True, "reason": problem}
    else:
        content = str(raw_content)
    if existing is not None and content == existing:
        # A no-op revision would commit a change to every holder (R3148).
        return {"kind": "write_rule", "name": name, "declined": True,
                "reason": "this revision is identical to the rule already in the library — "
                          "nothing to change, and a commit here would reach every holder"}
    if problems := lint_rule_text(content, filename=f"{name}.md"):
        return {"kind": "write_rule", "name": name, "lint_ok": False, "problems": problems}
    creating = existing is None
    # WHO ELSE this lands on — named in the approval question.
    holders = _rule_holders(ctx, name)
    if ctx.grants is None or ctx.grants.needs_rule_confirm(creating):
        verb = "author" if creating else "revise"
        excerpt = (f"anchor:\n{str(action.get('anchor'))[:200]}\nreplacement:\n"
                   f"{str(action.get('replacement') or '')[:200]}" if raw_content is None
                   else f"First lines:\n{content.strip()[:400]}")
        ask = handle_ask(loop, {
            "question": f"Approve {verb} of general rule '{name}'?"
                        f"{_impact_note(ctx, 'rule', name, content)}. {excerpt}",
            "mode": "blocking", "options": ["approve", "decline"],
            "default": "the rule is NOT changed until approved"}, poll_s,
            qtype="rule-approval", subject=name)
        if not ask.get("answered"):
            return {"kind": "write_rule", "name": name, **still_pending(ask)}
        if not is_approval(ask["answer"]):
            return {"kind": "write_rule", "name": name, "declined": True,
                    "answer": str(ask["answer"])[:200]}
    library_docs.write_doc(home, name, content)
    libgit.commit(ctx.server.libraries_home,
                  f"{'author' if creating else 'revise'} rule {name}",
                  routines_home=ctx.server.routines_home, paths=[f"rules/{name}.md"])
    return {"kind": "write_rule", "name": name, "created": creating, "written": True,
            "holders": holders}


def _rule_holders(ctx, slug: str) -> list[str]:
    """Every routine/conversation whose config binds this rule — who a revision reaches.

    Read straight off the config files, not the registry: a fact about the instance.
    """
    from .. import rules as rules_mod

    out: list[str] = []
    for home in (ctx.server.routines_home, ctx.server.conversations_home):
        if not home.is_dir():
            continue
        out.extend(d.name
                   for d in sorted(p for p in home.iterdir()
                                   if p.is_dir() and not p.name.startswith("."))
                   if slug in rules_mod.current_rules(d))
    return out


def handle_remove_util(loop, action: dict, poll_s: float) -> dict:
    """Delete a global util (curation) — the write_util counterpart, gated by the same
    util-authoring capability. Refuses while any sibling declares it on a `calls:` line;
    asks for approval unless write_util policy is 'never'; committed, so recoverable.
    """
    ctx = loop.ctx
    name = action["name"]
    if ctx.depth > 0:
        return {"kind": "remove_util", "name": name, "declined": True,
                "reason": "sub-workflows cannot remove utils — curation is a top-level action"}
    home = ctx.server.libraries_home
    utils_lib.ensure_library(home, remote=ctx.server.libraries_remote)
    if not utils_lib.exists(home, name):
        return {"kind": "remove_util", "name": name, "missing": True}
    if callers := utils_lib.referenced_by(home, name):
        return {"kind": "remove_util", "name": name, "callers": callers}
    # Removal is destructive — approve it unless write_util is fully autonomous ('never').
    if ctx.grants is None or ctx.grants.needs_confirm(creating=True):
        ask = handle_ask(loop, {
            "question": f"Approve removal of global util '{name}'? It is deleted from the "
                        f"library (recoverable from git history).",
            "mode": "blocking", "options": ["approve", "decline"],
            "default": "the util is NOT removed until approved"}, poll_s,
            qtype="util-approval", subject=name)
        if not ask.get("answered"):
            return {"kind": "remove_util", "name": name, **still_pending(ask)}
        if not is_approval(ask["answer"]):
            return {"kind": "remove_util", "name": name, "declined": True}
    utils_lib.remove_util_file(home, name)
    libgit.commit(home, f"remove {name}", routines_home=ctx.server.routines_home,
                  paths=[f"utils/{name}"])
    return {"kind": "remove_util", "name": name, "removed": True}
