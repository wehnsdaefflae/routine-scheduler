"""Cumulative feedback-driven research site — a stateful per-run pipeline that compounds.

Each run: load prior durable state, INGEST FEEDBACK FIRST from a private (non-public) fetch and
update a persistent preference model, do CUMULATIVE research over everything genuinely due (never
from scratch), fan out per-item to evaluate + render stable-id subpages, then GUARDED-publish the
whole site to a live webroot without ever clobbering the endpoint-written feedback data file.

This file is a PATTERN, not a program: the orchestrator never executes it — it *acts it out*, one
engine action per turn, following the control flow below (its branches, loops, error handling).
The dummy imports name the parameters this routine works with; the clarifier pins them down for the
concrete task, and `decompose` turns this pattern into the routine's own markdown state-machine.
"""

# --- Parameter contract -------------------------------------------------------------------------
# These imports do not resolve at run time. Each names one piece of information the clarifier must
# fix for THIS routine — the type, and what it means, live in the comment.
from routine.params import (
    SITE_URL,           # str       — the live public site the run republishes (the webroot)
    PUBLISH_TARGET,     # dict      — how to push the site (e.g. FTP host + source folder = webroot)
    FEEDBACK_PRIVATE,   # str       — PRIVATE path to the endpoint-written feedback data file (FTP/auth GET); NEVER a public URL
    FEEDBACK_ENDPOINT,  # dict      — the server-side receiver (PHP/CGI) controls POST to: token + input cap
    GROUND_TRUTH,       # dict      — read-only source of truth about the subject (e.g. Notion) — synthesize, never republish verbatim
    ENRICH_SOURCES,     # list[str] — open-web inputs the cumulative research draws candidates from
    ITEM_SECTIONS,      # list[str] — the fixed per-item sections each subpage renders (each item gets its own controls)
    BATCH_FLOOR,        # int       — the MINIMUM new/refreshed items a run works (e.g. 3). A FLOOR, never a ceiling: a run takes everything genuinely due
)

# The engine actions the orchestrator may take — exactly one per turn, each answered by an
# OBSERVATION the next turn reasons about. Shown as ordinary calls for readability.
from routine.actions import read_file, write_file, util, write_util, llm, spawn, wait, ask_user, finish
from routine.state import phase, ledger    # state/phase.json helper, LEDGER.md append helper

META = {
    "name": "Cumulative feedback-driven research site",
    "slug": "cumulative-feedback-research-site",
    "description": "Compound a persistent research corpus, evaluate everything due each run, and "
                   "republish a stable-id feedback-collecting site — learning from prior feedback first.",
    "when_to_use": "Use when a recurring instruction maintains a CUMULATIVE research corpus + "
                   "per-item evaluations and republishes a multi-page site every run, where user "
                   "feedback captured on the site (via a server-side endpoint) must be ingested "
                   "before new work and stable ids must survive regeneration. Fits FTP-webroot "
                   "publishing with a guarded, never-clobber-the-feedback-file upload. Not for "
                   "one-off builds, no-server/static-only sites, or non-cumulative research.",
    "version": 4,
    "tags": ["cumulative-research", "feedback-loop", "publishing", "stateful-pipeline", "evaluation"],
    "includes": ["ask-policy", "web-research", "decision-record", "engagement-accountability", "feedback-implementation-gate"],
    "tools": None,          # None = every action kind is allowed
}

PHASES = ["bootstrap", "steady"]        # bootstrap stands up state/ + endpoint; steady compounds forever


class NeedsDecision(Exception):
    """A choice only the user can make — raised to file a deferred question and carry on."""


class ExternalBlocker(Exception):
    """This item can't proceed right now (a source is down, ground-truth unreachable)."""


def main():
    """One run of the pipeline — strict order: state -> feedback -> research -> evaluate -> publish."""
    orient()                                    # consume state digest + LEDGER before anything new

    if phase.current() == "bootstrap":
        bootstrap()                             # first run: stand up state/, endpoint, first thin site
        # fall through — a bootstrap run still ingests and publishes a real first batch, so the
        # site is worth looking at after its FIRST fire rather than after its second.

    # 1. INGEST FEEDBACK FIRST — private fetch, cursor-guarded, feeds the preference model.
    prefs = ingest_feedback()

    # 2. CUMULATIVE research — extend the corpus by everything due, never start from scratch.
    batch = select_batch(prefs)                 # new candidates + every record whose refresh came round
    if not batch:
        if not prefs_moved(prefs):
            # Nothing arrived and nothing is due. Establish that — the sources were actually
            # checked — then say it plainly and finish. Republishing an unchanged site is not
            # evidence a run happened.
            return record("Nothing due: no new feedback, no candidate, no refresh; site unchanged.")
        # Feedback moved the model even though no research is due → the site must show it.
        publish_site()
        return record("ingested feedback; nothing new to research; site republished")

    # 3. Per-item fan-out: evaluate (fit + acceptance probability) and render a stable-id subpage.
    for item in batch:
        try:
            evaluation = evaluate(item, prefs)  # grounded in ground-truth + web, never verbatim
            verify(persist_item(item, evaluation))
            render_subpage(item, evaluation, prefs)
        except NeedsDecision as decision:
            ask_user(decision, mode="deferred")     # → Decisions page; this item waits
        except ExternalBlocker:
            continue                            # source down now; leave prior record intact, move on
        if not room_to_finish_cleanly():
            break                               # stop at a clean boundary — never a half-evaluated item

    # 4. GUARDED publish — regenerate the whole site, assets/subpages before the landing page.
    publish_site()

    return record("feedback ingested, everything due evaluated, site republished, feedback file untouched")


def orient():
    """Consume the state digest (phase, last result, LEDGER tail, user messages/answers) before
    exploring — recall the corpus, evaluations, preference model, and feedback cursor so no prior
    work is redone and the stable-id convention stays consistent. The digest already carries the
    LEDGER tail and says when there is more; read the file only if it says so."""


def bootstrap():
    """First run: create state/ (empty corpus, evaluations, preference model, feedback cursor at 0);
    deploy the server-side feedback endpoint (token + capped input) with its data file OUT of public
    HTTP view; file deferred questions for genuinely pivotal unknowns (ask-policy); research a first
    real batch and publish a thin but real site. Advance phase.json to 'steady'."""


def ingest_feedback():
    """Fetch the feedback data file via FEEDBACK_PRIVATE (private FTP path or authenticated GET —
    NEVER a public URL). Read only entries past the state/ cursor, then advance the cursor. Fold new
    signals into the persistent preference model and return it:
      - per-item upvotes/downvotes are WEIGHTED EVIDENCE reconciled against research, NOT hard
        overrides (a stale/mistaken vote must not force a factually wrong claim);
      - the single general free-text direction field may AUTONOMOUSLY apply layout/design/style/scope
        changes — honor it without asking.
    Persist the updated model to state/."""


def select_batch(prefs):
    """Return everything genuinely due this run — newly discovered candidates plus every existing
    record whose refresh has come round — steered by the preference model. Draw new candidates from
    ENRICH_SOURCES; treat GROUND_TRUTH as read-only truth about the subject.

    BATCH_FLOOR is a FLOOR, not a ceiling: at least that many per run, with no upper bound. An
    earlier version of this pattern capped the batch "so no run tries everything", and the corpus
    stopped moving — one holder logged 30 consecutive silent checks while every run reported
    success. Take the whole due list and let the run work it to a clean boundary. If fewer than
    BATCH_FLOOR are due, WIDEN rather than return short: deepen a thin record, refresh the oldest,
    push the candidate search further out.

    Return [] only when the corpus is genuinely current and nothing at all is due."""


def prefs_moved(prefs):
    """True when this run's feedback ingest actually changed the preference model — a vote, a note,
    or the direction field past the cursor. False means the user said nothing since the last run,
    which is the case where an idle run is the honest outcome rather than a republish."""


def room_to_finish_cleanly():
    """True while the remaining budget can still evaluate, persist AND render the next item, and
    then publish. A BOUNDARY check, never a ration: it exists so a run never starts an item it
    cannot finish and leaves a half-written record behind — not so a run does less than it could.
    When it goes false, stop at the boundary and name the untouched items in the LEDGER entry so
    the next run knows they are still due."""


def evaluate(item, prefs):
    """Evaluate one item on (a) fit with the subject and (b) probability of acceptance under the best
    realistic framing. Ground every claim in GROUND_TRUTH (read-only) plus open-web verification
    (web-research), reconciled with weighted feedback from `prefs`. Use `llm` for scoped judgment;
    run code through a CAPABILITY your CAPABILITIES list offers — a judgment-free step you repeat
    identically every run belongs in this routine's own persistent tooling, a capability other
    routines would share too belongs in the shared library. Return the evaluation."""


def persist_item(item, evaluation):
    """Write the item's corpus record + evaluation (fit, acceptance probability, reasoning) into
    state/ under a stable slug. These compound across runs — return the path so it can be read back."""


def render_subpage(item, evaluation, prefs):
    """Regenerate this item's subpage with EXACTLY the ITEM_SECTIONS, carrying ONLY synthesized
    analysis (never GROUND_TRUTH verbatim). Under EVERY individual point inside each section render
    three controls — upvote, downvote, free-text — each POSTing to FEEDBACK_ENDPOINT with a STABLE
    id identical across runs for the same logical point (e.g. `item-slug · section · point`) so
    feedback maps back after regeneration."""


def publish_site():
    """Regenerate the full multi-page site (overview/landing with navigation and filter/sort by fit,
    acceptance chance, deadline, status; the ONE general site-wide direction field; all subpages) and
    push to SITE_URL via PUBLISH_TARGET. The target folder is the LIVE webroot, so upload assets and
    subpages BEFORE the landing page (or upload-then-rename) to avoid a half-published site.
    HARD RULE: NEVER upload over / overwrite the endpoint-written feedback data file — verify it is
    excluded from the upload set before pushing."""


def verify(path):
    """Read back what was persisted — confirm the record/evaluation exists and the stable-id
    convention holds. A claimed-but-unverified outcome is the worst failure this system knows."""


def record(summary):
    """Update state/phase.json and any state files; append exactly one LEDGER entry (feedback
    consumed + cursor moved, items added/refreshed, anything left due at the boundary, preference-
    model shifts, publish result, decisions, candidates rejected + why)."""
    ledger.append(summary)
    return finish("ok", summary)


if __name__ == "__main__":
    main()
