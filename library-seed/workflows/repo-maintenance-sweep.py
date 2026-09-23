"""Full-sweep maintenance of a deployed repository — audit everything, fix what is safe,
publish straight to the live branch.

The shape: a repo that IS the product (a site, a docs set, a static artifact) where every run
performs a COMPLETE maintenance sweep rather than a slice of one — cruft, bugs, SEO/metadata,
performance and accessibility, content staleness — fixes what it finds, proves the fixes before
publishing, and pushes to the branch that auto-deploys. Code-shaped fixes ship in the same run.
Content-shaped changes (claims, dates, credentials, prose about the user) are researched and then
gated on the user's confirmation before they ever go live.

This file is a PATTERN, not a program: the orchestrator never executes it — it ACTS IT OUT, one
engine action per turn, following the branches, loops and error handling below. The dummy imports
name the parameters the clarifier pins down for the concrete repo; the instruction itself stays
separate from this file.
"""

# --- Parameter contract -------------------------------------------------------------------------
# None of these resolve at run time. Each names one thing the clarifier fixes for THIS routine.
from routine.params import (
    REPO,               # str       — the repository under maintenance (where the working copy comes from)
    PUBLISH_TARGET,     # str       — the branch that auto-deploys; the standing decision is direct pushes to it
    LIVE_URL,           # str       — the public URL the published branch serves, checked after every push
    CONVENTION_DOCS,    # list[str] — in-repo docs stating the binding house rules; authoritative, re-read every run
    AUDIT_PASSES,       # list[str] — the named passes one FULL sweep covers (cruft, bugs, metadata, perf, content, …)
    PROTECTED_PATHS,    # list[str] — paths never edited unless the user names one explicitly
    ARCHITECTURE_RULES, # list[str] — structural invariants a fix may not break (file layout, single-bundle rules, …)
    CONTENT_GATE,       # str       — what counts as substantive content needing research + user confirmation
    FINDINGS_RECORD,    # str       — durable state file where findings and settled decisions accumulate
)

from routine.actions import (read_file, write_file, util, write_util, llm, spawn, subruns,
                             wait, ask_user, finish)
from routine.state import phase, ledger

META = {
    "name": "Repo maintenance sweep",
    "slug": "repo-maintenance-sweep",
    "description": "Full audit sweep of a deployed repo each run: fix code/meta/cruft directly, "
                   "gate content on confirmation, verify, push to the live branch, report.",
    "when_to_use": "Use for recurring 'maintain this repo / keep this site healthy' instructions "
                   "where the repo deploys itself on push and the run is trusted to fix and "
                   "publish without review. Fits audits that must cover EVERYTHING every run "
                   "(dead code, broken links, metadata, performance, accessibility, stale facts) "
                   "and that distinguish safe technical fixes from claims needing user sign-off.",
    "version": 1,
    "tags": ["maintenance", "website", "audit", "refactor", "seo", "deploy"],
    "includes": ["change-restraint", "evidence-discipline", "independent-verification",
                 "unexamined-is-not-clean", "git-checkpoint", "ask-policy", "web-research"],
    "tools": None,
}

PHASES = ["bootstrap", "steady", "stand-down"]      # tracked in state/phase.json


class ConventionViolation(Exception):
    """A planned edit would break a house rule or an architecture invariant."""


class VerificationFailed(Exception):
    """An edit did not survive its own check — it gets fixed or reverted, never published."""


class NeedsConfirmation(Exception):
    """A content change about the user: research it, propose exact wording, wait for an answer."""


def main():
    """One run: orient, sync, sweep everything, fix what is provable, verify, publish, report.

    STEP SIZE — a run attempts as much as it can finish WELL. The sweep is always complete: all
    of AUDIT_PASSES, every run, because a partial audit reports a clean bill it has not earned.
    The fix loop then keeps taking findings while findings remain and the remaining budget can
    still apply AND verify the next one cleanly; it stops at a clean boundary — a verified,
    publishable tree — rather than half-applied. The turn budget is a runaway BACKSTOP, not a
    ration: do not stop because turns are being spent, and do not invent work because they remain.

    EXIT EARLY — a run with nothing to do is a normal outcome. If the sweep comes back with no
    actionable finding (or every finding is already recorded as known-and-accepted in
    FINDINGS_RECORD), the run says so plainly, names what it audited as evidence, publishes
    nothing, and finishes. Manufacturing a cosmetic diff to justify a run is the failure mode
    this branch exists to prevent.
    """
    orient()

    if phase.current() == "stand-down":
        return stand_down()

    if phase.current() == "bootstrap":
        bootstrap()
        # fall through: a bootstrap run still performs a real sweep, so the routine is useful
        # after its FIRST fire.

    requests = user_requests()                  # inbox items outrank self-directed sweep work
    start_sha = sync_working_copy()
    rules = read_conventions()                  # CONVENTION_DOCS are authoritative over memory

    findings = []
    for pass_name in AUDIT_PASSES:
        findings += sweep_pass(pass_name, rules)

    todo = triage(requests, findings)

    if not todo.fix_now and not todo.content and not requests:
        record(start_sha, applied=[], deferred=findings, pushed=None)
        return finish("ok", "Full sweep completed; passes audited and no actionable finding — "
                            "nothing published this run.")

    applied, rejected = [], []
    while todo.fix_now and budget_can_apply_and_verify():
        item = todo.fix_now.pop(0)
        try:
            change = apply_fix(item, rules)
            verify_edit(change, rules)
            applied.append(change)
        except ConventionViolation as v:
            rejected.append((item, v))          # the rule wins; record why, do not "improve" it
        except VerificationFailed:
            revert_edit(item)
            rejected.append((item, "reverted: failed its own check"))
        except NeedsConfirmation as q:
            todo.content.append(q)

    for proposal in todo.content:
        research(proposal)                      # sources first, never memory
        ask_user(proposal, mode="deferred")     # unanswered → leave the live text untouched

    pushed = None
    if applied:
        try:
            verify_tree(applied, rules)         # whole-tree gate BEFORE anything reaches the branch
            pushed = publish(applied)
            confirm_live(pushed, applied)
        except VerificationFailed:
            pushed = None
            rejected.append(("tree", "held back: pre-publish verification failed"))

    recheck(rules)
    record(start_sha, applied, todo.deferred + [r[0] for r in rejected], pushed)
    return report(applied, rejected, todo, pushed)


# --- steps ---------------------------------------------------------------------------------------

def orient():
    """Consume the state digest first: phase, last run's result, LEDGER tail, pending user answers,
    and FINDINGS_RECORD. The findings record is what stops this routine re-litigating settled
    decisions — an issue already examined and deliberately accepted is reported as such, not
    rediscovered and refixed. Read the record file itself when the digest says there is more."""


def bootstrap():
    """First fire: create state/, write the initial FINDINGS_RECORD skeleton (known-and-accepted
    issues, open content proposals, architecture invariants restated from CONVENTION_DOCS), and
    file deferred questions only for genuinely pivotal unknowns. Advance phase.json to 'steady'
    and continue into this run's real sweep."""


def user_requests():
    """Read the inbox for this routine. User instructions take priority over self-directed sweep
    work and are executed in the SAME run wherever feasible; a request that cannot be done now is
    answered with why and carried forward explicitly rather than silently dropped."""


def sync_working_copy():
    """Take a FRESH working copy of REPO at the current tip of PUBLISH_TARGET — clone it or update
    an existing one, never edit a stale tree. Record the starting commit id: it is what a revert
    would return to and what the report quotes. Use whichever version-control capability the run's
    catalog offers."""


def read_conventions():
    """Read every doc in CONVENTION_DOCS this run, before any edit. They state the binding house
    rules (property and styling constraints, required inline behaviours, path conventions, index
    and metadata files that must be updated alongside new content). They outrank habit, outrank
    what worked in another repo, and outrank what this pattern's author assumed. Return them as the
    rule set every later step checks against, together with ARCHITECTURE_RULES."""


def sweep_pass(pass_name, rules):
    """Run ONE named pass of the audit over the whole tree and return findings, each carrying
    file:line evidence, a severity, and a proposed fix.

    Every pass in AUDIT_PASSES runs every run — a pass skipped for time is a false clean bill.
    Typical passes and what 'evidence' means in each:
      - cruft: unreferenced files, dead rules/functions, duplicated blocks, commented-out code,
        leftover dev artifacts. Cross-check EVERY reference path before proposing a deletion —
        config, index/manifest files, server rules and inline markup all reference files that a
        text search of the pages alone will miss. Deliberately retained archives are not cruft.
      - correctness: broken internal links and anchors across ALL language or locale trees,
        cross-tree switch links whose targets are named differently, wrong asset paths, malformed
        markup, script syntax/runtime errors, layout rules that violate the house rules, parity
        gaps where a section exists in one tree and not its mirror, broken external links.
      - discoverability/metadata: per page — title, description, canonical, cross-language
        pairing, social card tags, heading hierarchy, image alternative text, structured data,
        and the site index/crawler files' accuracy against the pages that ACTUALLY exist.
      - performance and quality: oversized or non-responsive assets, duplicated payload,
        render-blocking resources, font loading, cache and compression headers, accessibility
        basics (landmarks, focus states, aria on interactive widgets, contrast in EVERY theme).
      - content staleness: dead project links, outdated titles or affiliations, expired dates,
        ended engagements still advertised, year stamps.

    Use the run's checking capabilities for what a machine can settle (markup/script parsing,
    link reachability, page fetching) and read the source for what it cannot. A finding asserted
    without a file:line or a fetched status code is not a finding."""


def triage(requests, findings):
    """Split the work into three buckets and return them.

    fix_now   — technical fixes the user trusts this routine to apply unreviewed: dead code,
                broken links and paths, malformed markup, metadata, index files, performance and
                accessibility defects, plus every user request that is technical in shape.
    content   — anything matching CONTENT_GATE: substantive prose, claims, dates, credentials,
                facts about the user. These are never edited on the run's own authority.
    deferred  — findings deliberately left alone: already recorded as accepted, inside
                PROTECTED_PATHS, or a fix that would cost more than the defect. Each carries its
                reason, because the report names them and the next run must not rediscover them.

    Order fix_now by risk-first: things that are broken for a visitor before things that are
    merely untidy."""


def budget_can_apply_and_turn():   # noqa — see budget_can_apply_and_verify
    ...


def budget_can_apply_and_verify():
    """True while the remaining turn budget can apply the NEXT fix and prove it. This is a
    finish-what-you-start judgement, not a quota: the loop keeps taking findings while findings
    remain, and stops only at a boundary where the tree is verified and publishable."""


def apply_fix(item, rules):
    """Apply one fix and return the change (files touched, what, why).

    Edits are surgical and anchored — targeted replacements in place, never a wholesale rewrite of
    a large shared stylesheet or script. Refuse and raise ConventionViolation if the fix would
    break a house rule or an ARCHITECTURE_RULES invariant (splitting a deliberately single bundle,
    restructuring a layout, changing load behaviour of an inline bootstrap script, switching to
    absolute paths). Refuse and raise NeedsConfirmation the moment a 'technical' fix turns out to
    change what the site CLAIMS.

    Mirror every change across all language or locale trees unless it is genuinely
    language-specific — translate rather than leave a gap. Touch nothing under PROTECTED_PATHS
    unless the user named that exact file this run."""


def verify_edit(change, rules):
    """Prove the single change immediately: re-read the edited region, re-parse the file, re-run
    the syntax/runtime check on any script or page with inline script, and re-check the links the
    edit touched. Raise VerificationFailed on anything unresolved — a claimed-but-unchecked fix is
    worse than the defect it replaced."""


def revert_edit(item):
    """Undo exactly that edit and leave the rest of the tree intact. Reverting one failed fix is
    normal; abandoning the run's verified work because one item failed is not."""


def research(proposal):
    """Before proposing any wording about the user, check the sources: the linked pages, the
    upstream profiles, the public record. Bring back what is actually true and cite where it came
    from, then draft the exact replacement wording for BOTH (all) language trees so the user can
    answer yes or no rather than compose it themselves. Unanswered proposals stay in
    FINDINGS_RECORD and are re-offered next run, not quietly published."""


def verify_tree(applied, rules):
    """The pre-publish gate, run once over the whole working tree before anything reaches
    PUBLISH_TARGET. Confirm: every edited file parses; scripts and inline scripts pass their
    check; the internal link graph resolves across all trees; no file under PROTECTED_PATHS is
    modified; the diff introduces no house-rule violation; no secret, credential or private
    recipient configuration appears in the diff. Raise VerificationFailed on any miss — the
    branch deploys itself, so an unverified push is live within a minute and the only remedy is
    another push."""


def publish(applied):
    """Commit with a message naming the categories touched and push to PUBLISH_TARGET. The
    standing decision is direct-to-branch: no side branch, no review request. Return the published
    commit id — the report quotes it and it is the anchor for any later revert."""


def confirm_live(pushed, applied):
    """After publishing, check the deployed site itself: fetch LIVE_URL and each changed page,
    confirm they respond and render the change. Verification against the working copy is not
    verification of the deployment. If the live check fails, say so loudly in the report and
    propose or apply the revert to the recorded starting commit."""


def recheck(rules):
    """Ask once more whether more is due. A landed fix routinely exposes the next one — a deleted
    file orphans another, a corrected path reveals a second, a user answer arrives mid-run. Re-run
    the affected passes; if they return items and the budget can still apply AND verify them
    cleanly, do them now and ask again. Stop when the passes come back empty or at a clean,
    published boundary."""


def record(start_sha, applied, deferred, pushed):
    """Update FINDINGS_RECORD so later runs accumulate instead of repeating: what was checked,
    what was fixed, what was examined and deliberately accepted (with the reason), and which
    content proposals are awaiting an answer. Keep it a STABLE file overwritten in place — a
    per-date filename is a file no future run opens.

    Append exactly one LEDGER entry: starting commit, published commit, categories touched,
    decisions taken, candidates rejected and why. Rotate LEDGER.md in this same step, as part of
    recording rather than deferrable housekeeping, whenever it exceeds the size IN BYTES the
    recipe names — archive the older entries behind a one-line rollup and keep the recent tail.
    Derive that threshold from this routine's own entries (roughly ten to fifteen of them); if a
    rotation leaves the file still over it, the number is wrong and gets fixed along with the
    measurement that justifies it.

    Advance phase.json to 'stand-down' only when the GOAL-scoped stopping conditions in
    state/stopping.json are met — the user's own words for when this maintenance ends (the site
    retired, handed over, or the mandate withdrawn). Then sweep the run for machinery friction you
    merely worked around and file each real hitch with the `report` action."""
    ledger.append("start sha, pushed sha, categories, decisions, rejected candidates")


def report(applied, rejected, todo, pushed):
    """The finish summary is the deliverable the user reads. It states, in plain prose:
    what was audited (every pass, so a clean pass is evidence and not silence); every change made
    as file + what + why; everything found and deliberately NOT changed, with the reason; any
    content proposal awaiting confirmation, quoted as the exact wording offered; the commit
    published; and the live-site verification result. If nothing was published, say that first and
    say why. Report failures as prominently as fixes."""
    return finish("ok", "Sweep, fixes, publication and live verification — as listed.")


def stand_down():
    """Terminal phase — the maintenance mandate is complete. Do three things and nothing else:
    verify the live deliverable one final time against LIVE_URL itself (never against state
    files), tell the user in plain words where the repo and the site stand, what remains open in
    FINDINGS_RECORD, and what the last published commit was — and finish, accounting the stopping
    conditions as met. Start no sweep, apply no fix, open no new question."""
    return finish("ok", "Maintenance closed out: live site verified, final commit and open items named.")


if __name__ == "__main__":
    main()
