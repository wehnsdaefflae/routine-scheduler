"""Collaborative application-coaching steward — an expert coach that regenerates a shared,
editable review surface for an iterating grant/tender/proposal application.

Each run acts out ONE coach session over a piece of shared state that two (or more) human
collaborators also edit through a published, self-contained page. The coach ingests the humans'
latest edits FIRST, does real professional homework, scores every piece of the application for
AI-vs-human authorship, gives per-segment actionable feedback, regenerates the required
attachments as one visually-consistent set of compiled PDFs, maintains a pre-submission
checklist, rates the whole application, and republishes the page. The page and its shared state
ARE the deliverable; this routine is the coach that tends them.

This file is a PATTERN, not a program: the orchestrator never executes it — it *acts it out*,
one engine action per turn, following the control flow below. The dummy imports name the
parameters the clarifier fixes for the concrete application (which grant, which people, which
store, which deadline); the routine is shown the live tool catalogue at run time and chooses the
capability that fits each step.
"""

# --- Parameter contract -------------------------------------------------------------------------
# None of these resolve at run time. Each names one thing the clarifier must pin down for THIS
# application; the comment gives its type and meaning.
from routine.params import (
    COACH_PERSONA,      # str        — the expert role the coach embodies, and the language ALL feedback is written in
    COLLABORATORS,      # list[dict] — the humans who edit (name, role/bio) and share the single-editor lock
    APPLICATION_SPEC,   # dict       — the programme: funder, focus, ceiling, eligibility nexus, deadline, jury/evaluators
    SEED_DRAFT,         # str        — path to the current concept draft the segments are first seeded from
    ATTACHMENTS_SPEC,   # list[dict] — the required documents (name, constraints e.g. page limits) to generate as PDFs
    SHARED_STATE_REF,   # str        — handle for the one SHARED PERSISTENT STATE both sides read/write.
                        # Name the persistence CAPABILITY, not a specific service: when the deliverable
                        # publishes to a steward/status hub, PREFER that hub's OWN native persistence
                        # (a server-side store behind its page); reach for an external cloud-JSON store
                        # ONLY when no native persistence exists — and note that a public JSON-blob
                        # service may be unreachable from the host (e.g. a Cloudflare 403), which
                        # silently breaks a routine born on it.
    STEWARD_PAGE,       # str        — the public URL the self-contained page is published to
    DEADLINE,           # str        — the submission deadline the page counts down to
    RUN_TRIGGER,        # str        — the on-demand trigger the page's "run now" button calls (the exception to cadence)
)

# Exactly one engine action per turn; each is answered by an OBSERVATION the next turn reasons
# about. Shown as ordinary calls for readability.
from routine.actions import read_file, write_file, util, write_util, llm, spawn, wait, ask_user, finish
from routine.state import phase, ledger

META = {
    "name": "Application coaching steward",
    "slug": "application-coaching-steward",
    "description": "An expert coach that iteratively perfects a multi-author grant/proposal "
                   "application over shared cloud state and a published, editable steward page.",
    "when_to_use": "Use when the instruction asks you to act as a domain coach who repeatedly "
                   "improves a structured application/proposal WITH the applicants over time: "
                   "shared state that humans also edit, per-section feedback and authorship "
                   "scoring, generated attachments, a submission checklist, and a beautiful "
                   "collaborative page as the deliverable. Prefer this over general-task whenever "
                   "the human edits between runs are authoritative inputs and the routine is the "
                   "coach that regenerates the surface.",
    "version": 2,
    "tags": ["coaching", "collaboration", "grant-application", "status-page", "authorship-scoring"],
    "includes": ["web-research", "status-page", "interface-design", "interface-copy",
                 "feedback-implementation-gate", "ai-writing-tells", "evidence-discipline",
                 "decision-record"],
    "tools": None,          # None = every action kind is allowed
}

PHASES = ["seed", "steady", "final-push"]   # tracked in state/phase.json; drives emphasis, not structure


class NeedsDecision(Exception):
    """A choice only the applicants can make (a strategy fork, a factual gap only they can fill) —
    raised to surface it on the page's comments/decisions channel and carry on."""


class EditorLocked(Exception):
    """A human holds the advisory single-editor lock right now — persist nothing that would clobber
    their in-flight edits; publish read-only and defer writes."""


def main():
    """One coach session — the top-level control flow, in the order the instruction fixes."""
    state = orient_and_ingest()                 # STEP 1 — load shared state; the humans' edits come first

    if phase.current() == "seed":
        seed(state)                             # first run: build segments from SEED_DRAFT, attachments from ATTACHMENTS_SPEC
        # fall through — a seed run still produces a full first pass so the page is useful immediately

    grow_research_corpus(state)                 # STEP 2 — real homework; knowledge compounds across runs

    for segment in state.segments:              # STEPS 3+4 — score, then coach each segment
        segment.pangram = score_authorship(segment.text)
        try:
            segment.feedback = coach_segment(segment, state)
        except NeedsDecision as d:
            ask_user(d, mode="deferred")        # surfaced to the collaborators; segment keeps prior feedback

    for attachment in state.attachments:        # STEP 5 — regenerate each attachment as a beautiful PDF
        try:
            attachment.pdf = compile_attachment(attachment, state)
            attachment.pangram = score_authorship(attachment.text)
        except ExternalBlocker:
            continue                            # e.g. compile toolchain unavailable this turn; leave prior PDF, note it

    refresh_checklist(state)                    # STEP 6 — pivot the pre-submission checklist to what "perfect" needs now
    rate_application(state)                     # STEP 7 — total authorship score + brutally-honest-but-fair 1–10

    publish_page(state)                         # STEP 8 — republish the self-contained collaborative page

    try:
        persist(state)                          # STEP 9 — write everything back to the shared store
    except EditorLocked:
        return finish("ok", "Published read-only; a collaborator is mid-edit, so shared state "
                            "was left untouched this run.")

    record()
    return finish("ok", "what advanced this session: rating delta, per-segment moves, attachments "
                        "recompiled, checklist changes, open decisions, days to deadline")


def orient_and_ingest():
    """STEP 1 — ORIENT + INGEST, before anything new. Read the state digest and LEDGER, then load
    the whole shared state from SHARED_STATE_REF. The collaborators' edited segment texts, their
    checklist ticks, and the free-form comments field are AUTHORITATIVE: treat them as this run's
    brief and let them drive the work. Detect whether this session was invoked by the page's
    RUN_TRIGGER ("run now") versus the scheduled cadence, and note the editor-lock / login /
    inactivity-countdown state so publishing and persistence respect a human who is mid-edit.
    Return the live state object the rest of the run mutates. The digest already carries the
    LEDGER tail and says when there is more; read the file only if it says so."""


def seed(state):
    """PHASE 'seed' (first run) — build the shared state from scratch: split SEED_DRAFT into ordered,
    individually-editable segments (one paragraph/section each); seed the attachments list from
    ATTACHMENTS_SPEC with their constraints (page limits, required sub-sections); start an empty
    research corpus, an empty comments field, and a first-cut checklist; establish the visual
    identity the page and all attachments will share. Advance state/phase.json to 'steady' once the
    state is populated (or to 'final-push' when little time remains before DEADLINE)."""


def grow_research_corpus(state):
    """STEP 2 — RESEARCH, as a seasoned professional does homework. Verify facts by searching, not
    from memory: the programme and funder, the jury/evaluators and their stated criteria, previously
    funded projects, the focus areas, the competitive landscape for this specific idea, and recent
    developments that would strengthen the case. Append findings to the persistent corpus in the
    shared state so knowledge compounds across runs; cite sources so later feedback can lean on
    them. Let what the applicants asked in the comments field steer where you dig."""


def score_authorship(text):
    """STEPS 3/5 — compute the AI-vs-human authorship score for a piece of text (a segment, or a
    fully-assembled attachment). Return a score the page renders with AI weight in red and human
    weight in green. The same capability scores segments, complete attachments, and the whole
    application total, so per-part and overall numbers stay comparable."""


def coach_segment(segment, state):
    """STEP 4 — PER-SEGMENT feedback, in the coach's language (COACH_PERSONA fixes it). Every note
    MUST (a) quote literal substrings from THIS segment and (b) give a concrete rewrite example, not
    a vague direction. It must first judge whether the PRIOR run's feedback for this segment was
    actually implemented in the edited text (feedback-implementation-gate) and say so, then judge
    how close the current text is to getting the application accepted beyond doubt. Keep it
    ACTIONABLE and bounded by reality — the time left to DEADLINE, the collaborators' actual CVs and
    references, the required eligibility nexus, and the known jury. Raise NeedsDecision only for a
    fork the applicants alone can resolve."""


def compile_attachment(attachment, state):
    """STEP 5 — regenerate this required attachment as a stunning, compiled PDF. Research the best
    layout, typography and style for a document of this kind, and hold ALL attachments to ONE shared
    visual identity. Rewrite the content to what the coach would consider perfect given the current
    application state, honouring the attachment's constraints (page limits, mandatory sections).
    Land the compiled PDF in the run's artifacts so the page can link and preview it, and hand the
    assembled text back for authorship scoring. If the compile toolchain is missing a piece, write
    and self-test a small script that supplies it rather than skipping the attachment."""


def refresh_checklist(state):
    """STEP 6 — maintain a realistic pre-submission checklist of what the applicants must DO to
    maximise acceptance, from the coach's vantage. Preserve the humans' done/not-done ticks ingested
    in STEP 1, and pivot the item set each run toward what "perfect" now requires: eligibility proof
    of the required nexus, budget / own-contribution handling, references and letters, jury-fit
    tactics, consultation and pitch logistics, and deadline mechanics. Fewer, sharper items beat a
    long stale list."""


def rate_application(state):
    """STEP 7 — OVERALL evaluation. Compute the whole-application authorship total, then give a
    brutally-honest-but-fair 1–10 rating grounded in the per-segment feedback, the attachments, the
    checklist progress, and the application as a whole. The written verdict stays actionable and
    bounded by the time left, the CVs, and the location — no praise that doesn't help, no despair
    that doesn't point somewhere."""


def publish_page(state):
    """STEP 8 — republish the self-contained, beautifully-designed steward page to STEWARD_PAGE
    (status-page / interface-design / interface-copy). It carries:
      • Header: the total authorship score (AI red / human green), the 1–10 rating with the coach's
        written verdict, and a LIVE countdown to DEADLINE.
      • Per segment: LEFT an editable multi-line field holding that paragraph; RIGHT the coach's
        feedback; plus that segment's authorship score.
      • An attachments section: each required document with a link/preview of its PDF and its
        complete-document authorship score.
      • The checklist as checkable, persisted items.
      • A free-form comments field where either collaborator addresses the coach (ingested next run).
      • Collaboration, all client-side against the shared store: lightweight login as one of the
        COLLABORATORS; an advisory single-editor lock so the page is read-only to everyone else
        while one edits (polled every few seconds); auto-logout after inactivity with a VISIBLE
        countdown and copy that plainly explains the rule; edits persisted to the shared store on
        save/logout so the other side sees identical state; and a "run now" button that fires
        RUN_TRIGGER for the exceptional on-demand session (the norm is the daily cadence, each
        person contributing across the day and picking up the next with fresh progress).
    Publishing is always safe to do; it must never clobber a human who currently holds the lock."""


def persist(state):
    """STEP 9 — write the updated segments, feedback, scores, checklist, rating, research corpus and
    bookkeeping (last-run feedback markers, timestamps, lock and countdown state) back to
    SHARED_STATE_REF as the single source of truth. If a collaborator holds the editor lock, raise
    EditorLocked rather than overwrite their in-flight work — the page was already republished
    read-only, and the next session will reconcile from their saved edits."""


def record():
    """Update state/phase.json (roll 'steady' → 'final-push' as DEADLINE nears) and append exactly
    one LEDGER entry: the rating delta, which segments moved and whether prior feedback landed, which
    attachments recompiled, checklist changes, decisions raised, and candidates considered and
    dropped with why. Then sweep the run once for machinery friction you merely worked around — an
    action or tool that failed or misled you — and file each real hitch with the `report` action
    before finishing. When LEDGER.md grows past ~400 lines or ~40 entries, rotate it THAT run:
    archive the older entries behind a one-line rollup and keep only the recent tail."""
    ledger.append("rating delta, segment moves, attachments, checklist, decisions, rejected candidates")


class ExternalBlocker(Exception):
    """A step can't proceed this turn (a source is down, a compile dependency is missing) — caught so
    the run keeps the rest of the surface fresh instead of failing whole."""


if __name__ == "__main__":
    main()
