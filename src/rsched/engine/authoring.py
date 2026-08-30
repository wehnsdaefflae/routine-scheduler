"""Library AUTHORING handlers — `write_util`, `write_rule`, `remove_util`.

Split out of `interact.py` (F393). What these share is not "they ask the user" — most kinds
can do that — it is that they WRITE THE SHARED LIBRARY, so each is a ladder of refusals with
its own approval dial (`confirm` for utils, `rule_confirm` for rules, because a rule revision
lands on every holder at its next run and that is a different decision from creating a util).
The `# noqa: PLR0911` on the two big handlers is the point of the shape: every rung of the
ladder is its own teaching exit, and collapsing them would make the refusals interchangeable.

There is deliberately no `remove_rule`: deleting a rule silently un-binds every holder with
nothing to catch it, so a run reports it and the user deletes it.
"""

from __future__ import annotations

from .. import sandbox, utils_header, utils_lib, utils_run
from ..ids import is_slug
from ..paths import resolve_rel
from .interact import handle_ask, is_approval
from .observations import truncate


def recreate_denial(loop, action: dict) -> list[str]:
    """The never-recreate rule, checked INSIDE the schema-retry cycle (a denied call is
    corrected and never becomes a turn, like every permission gate): a write_util for a
    slug that once existed and was deleted from the util library — the user's deliberate
    act, per the library's git history — must not proceed silently. The unlock is the
    grant entity `recreate:<slug>` (entities.py): an allow-now decision THIS run opens
    it (the routine's normal write_util approval level still applies afterwards), and a
    never-decision tombstones it. `recreate:` has no allow-forever on purpose — a fresh
    deletion must always outrank an old grant.
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
        return [f"util {name!r} was DELETED from the util library by the user, and "
                f"{g.request_route(eid)}"]
    return [f"util {name!r} existed before and was DELETED from the util library by the "
            f"user — a user-deleted util is never recreated without asking. First ask_user "
            f'with request: "{eid}", mode "blocking", and a question saying why it is '
            f"needed; recreate only after the user allows it (the grant covers this run). "
            f"On a deny or a timeout, work without it and note the gap in your finish "
            f"summary."]


def handle_write_util(loop, action: dict, poll_s: float) -> dict:  # noqa: PLR0911 — gate ladder: every refusal is its own teaching exit
    ctx = loop.ctx
    name, raw_content = action["name"], action.get("content")
    if ctx.depth > 0:
        return {"kind": "write_util", "name": name, "declined": True,
                "reason": "sub-workflows cannot create/revise utils — use existing ones"}
    home = ctx.server.libraries_home
    utils_lib.ensure_library(home, remote=ctx.server.libraries_remote)
    src_path = str(action.get("path") or "")
    edit_mode = raw_content is None and not src_path
    if edit_mode:
        # Edit mode (D42-B / F187): anchor-patch the EXISTING source ENGINE-side, so a
        # 3-line fix to a 50KB util never requires re-emitting the whole script through
        # one reply (the re-emit exceeded the output cap and made big utils unfixable
        # for shell-less routines, observed 2026-07-24). The synthesized result rides
        # the exact same approval + selftest + rollback gate as a full rewrite.
        anchor = str(action.get("anchor") or "")
        source = utils_lib.read_util(home, name)
        if source is None:
            return {"kind": "write_util", "name": name, "edit_failed": True,
                    "reason": f"no util {name!r} exists to edit — edit mode patches an "
                              "existing script; pass 'content' (the complete script) to "
                              "create a new one"}
        count = source.count(anchor)
        if count == 0:
            return {"kind": "write_util", "name": name, "edit_failed": True,
                    "reason": "anchor not found in the util's current source — copy it "
                              "VERBATIM (whitespace included) from "
                              f'{{"kind": "util", "name": "show", "args": ["{name}", '
                              '"--full"]}'}
        if count > 1 and not action.get("all"):
            return {"kind": "write_util", "name": name, "edit_failed": True,
                    "reason": f"anchor occurs {count}× in the source — extend it until "
                              "unique, or set all: true to replace every occurrence"}
        content = source.replace(anchor, str(action.get("replacement") or ""))
    elif src_path:
        # Content-from-file (F280): install the script from a file's EXACT bytes — a
        # large pre-built util (a subtask's tested draft, a consolidation) must not be
        # re-typed through one reply, which caps out and is never guaranteed faithful.
        # Reads under the run's own readable roots only; the result rides the SAME
        # header + approval + selftest + rollback gate as inline content.
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
    # Approval policy is the routine's write_util capability level (always: every change;
    # creations: new utils only; never). No grants on the ctx = confirm everything.
    if ctx.grants is None or ctx.grants.needs_confirm(creating):
        verb = "create" if creating else "revise"
        excerpt = (f"anchor:\n{str(action.get('anchor'))[:180]}\nreplacement:\n"
                   f"{str(action.get('replacement') or '')[:180]}" if edit_mode
                   else f"First lines:\n{content.strip()[:400]}")
        ask = handle_ask(loop, {
            "question": f"Approve {verb} of global util '{name}'?"
                        f"{_impact_note(ctx, 'util', name, content)} "
                        f"{'In-place patch — ' if edit_mode else ''}{excerpt}",
            "mode": "blocking", "options": ["approve", "decline"],
            "default": "the util is NOT applied until approved"}, poll_s,
            qtype="util-approval")
        if not ask.get("answered"):
            return {"kind": "write_util", "name": name, "pending_approval": True,
                    "qid": ask.get("qid")}
        if not is_approval(ask["answer"]):
            # carry the verbatim answer: a decline that hides WHAT was said reads as a
            # contradiction when the user meant to approve in other words (F161)
            return {"kind": "write_util", "name": name, "declined": True,
                    "answer": str(ask["answer"])[:200]}
    # Selftest gates the LIBRARY, not just the observation: on failure the write is rolled
    # back — a new util's dir removed, a revision restored to the previous working text —
    # so a broken script is never left live for concurrent `gu` callers.
    previous = None if creating else utils_lib.read_util(home, name)
    utils_lib.write_util_file(home, name, content)
    ok, output = utils_run.selftest(home, name, policy=sandbox.base_policy(ctx.server))
    if not ok:
        if previous is None:
            utils_lib.remove_util_file(home, name)
        else:
            utils_lib.write_util_file(home, name, previous)
        # Head+tail, never a head slice: a traceback's END is the repair material, and a
        # long selftest log sliced at its head hid exactly the AssertionError that
        # explained the failure (R93).
        output, _ = truncate(output, cap=2000)
        return {"kind": "write_util", "name": name, "created": creating,
                "selftest_ok": False, "reverted": True, "output": output}
    utils_lib.git_commit(home, f"{'create' if creating else 'revise'} {name}",
                         paths=[f"utils/{name}"])
    return {"kind": "write_util", "name": name, "created": creating, "selftest_ok": True}



def _impact_note(ctx, kind: str, name: str, content: str | None) -> str:
    """The blast radius, for the approval question. Advisory and best-effort: an impact that
    could REFUSE a write would make a diagnostic the reason authoring fails, and the write gate
    is the selftest and the linter, not this.
    """
    from ..library_impact import impact, impact_lines

    try:
        return " " + " | ".join(impact_lines(impact(ctx.server, kind, name, content)))
    except (OSError, ValueError, AttributeError):
        return ""


def handle_write_rule(loop, action: dict, poll_s: float) -> dict:  # noqa: PLR0911 — gate ladder: every refusal is its own teaching exit
    """Author or revise a GENERAL RULE in the shared library — the write_util shape, applied
    to prose instead of code, gated by the rule-authoring permission.

    Two differences from a util, both from blast radius. A rule is held by many routines, so
    the approval dial is its OWN (`rule_confirm`) rather than write_util's: a routine trusted
    to author its own tools is not thereby trusted to reword what every other routine follows.
    And the gate is the library LINTER instead of a selftest — prose has nothing to execute,
    so conformance (a `# rule:` heading, tags, no capabilities smuggled into frontmatter) is
    the only mechanical check there is; it runs BEFORE the approval ask, so a malformed draft
    never reaches the user.

    DELETION is deliberately not an action. Removing a rule silently un-binds every routine
    holding it, and unlike a util there is no callers check that can catch it — a run that
    believes a rule should go says so in a report or a deferred ask_user, and the user
    deletes it on the Library tab.
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
        # Edit mode, the normal shape for a revision: anchor-patch the current prose so a
        # one-clause fix costs one clause, and so the change is legible as a diff to the
        # user approving it.
        anchor = str(action.get("anchor") or "")
        if existing is None:
            return {"kind": "write_rule", "name": name, "edit_failed": True,
                    "reason": f"no rule {name!r} exists to revise — pass 'content' (the "
                              "complete rule markdown) to author a new one"}
        if not anchor:
            return {"kind": "write_rule", "name": name, "edit_failed": True,
                    "reason": "pass either 'content' (the whole rule) or 'anchor' + "
                              "'replacement' (an in-place revision)"}
        count = existing.count(anchor)
        if count == 0:
            return {"kind": "write_rule", "name": name, "edit_failed": True,
                    "reason": "anchor not found in the rule's current text — copy it VERBATIM "
                              f'from {{"kind": "read_rule", "name": "{name}"}}'}
        if count > 1 and not action.get("all"):
            return {"kind": "write_rule", "name": name, "edit_failed": True,
                    "reason": f"anchor occurs {count}× in the rule — extend it until unique, "
                              "or set all: true to replace every occurrence"}
        content = existing.replace(anchor, str(action.get("replacement") or ""))
    else:
        content = str(raw_content)
    if problems := lint_rule_text(content, filename=f"{name}.md"):
        return {"kind": "write_rule", "name": name, "lint_ok": False, "problems": problems}
    creating = existing is None
    # WHO ELSE this lands on: a revision reaches every holder at its next run, so the
    # approval question names them. This is the fact that makes the decision reviewable.
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
            qtype="rule-approval")
        if not ask.get("answered"):
            return {"kind": "write_rule", "name": name, "pending_approval": True,
                    "qid": ask.get("qid")}
        if not is_approval(ask["answer"]):
            return {"kind": "write_rule", "name": name, "declined": True,
                    "answer": str(ask["answer"])[:200]}
    library_docs.write_doc(home, name, content)
    library_docs.git_commit(ctx.server.libraries_home,
                            f"{'author' if creating else 'revise'} rule {name}",
                            paths=[f"rules/{name}.md"])
    return {"kind": "write_rule", "name": name, "created": creating, "written": True,
            "holders": holders}


def _rule_holders(ctx, slug: str) -> list[str]:
    """Every routine/conversation whose config binds this rule — who a revision reaches.

    Read straight off the config files rather than the registry: a rule is authored from a
    maintenance routine that may not hold run-history access, and this is a fact about the
    instance, not about any run.
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
    util-authoring capability. Refuses if any sibling still declares it on a `calls:` line
    (mirrors the `gu remove` no-callers refusal); asks for approval unless the routine's
    write_util policy is 'never'; the removal itself runs un-sandboxed engine-side (like
    write_util's library write), committed so it is recoverable from git history.
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
            qtype="util-approval")
        if not ask.get("answered"):
            return {"kind": "remove_util", "name": name, "pending_approval": True,
                    "qid": ask.get("qid")}
        if not is_approval(ask["answer"]):
            return {"kind": "remove_util", "name": name, "declined": True}
    utils_lib.remove_util_file(home, name)
    utils_lib.git_commit(home, f"remove {name}", paths=[f"utils/{name}"])
    return {"kind": "remove_util", "name": name, "removed": True}
