"""Predatory Publisher Tar-Pit — a daily draft-only routine that keeps predatory-publisher and
bogus-conference solicitations out of the user's inbox while entangling each sender in a
never-ending, escalating correspondence.

This file is a PATTERN, not a program: the orchestrator never executes it — it *acts it out*,
one engine action per turn, following the control flow below (its branches, loops, and error
handling). The dummy imports name the parameters this routine works with; the clarifier pins
them down for the concrete task, and `decompose` turns this pattern into the routine's own
markdown state-machine (main.md + steps/).

Two invariants govern every run. SENDING IS DRAFT-ONLY: replies are written into the mail
account's Drafts folder and NEVER sent — the user opens webmail and sends the batch himself.
And FILTERS ARE ADDITIVE: the server-side mail-filter script is refreshed, backup-first and
validated before activation, without ever clobbering the user's own non-predatory rules.
"""

# --- Parameter contract -------------------------------------------------------------------------
# These imports do not resolve to anything at run time. Each names one piece of information the
# clarifier must fix for THIS routine — the type, and what it means, live in the comment.
from routine.params import (
    MAIL_ACCOUNT,      # str       — how the mail capability reaches the account (secret-backed identity)
    SOURCE_FOLDERS,    # list[str] — the folders each run scans for solicitations (inbox + junk)
    DRAFTS_FOLDER,     # str       — the folder new replies are saved into for the user to send
    JUNK_FOLDER,       # str       — the folder predatory senders are filtered into
    TELLS,             # list[str] — the marks of a predatory solicitation the classifier looks for
    STALL_LIBRARY,     # list[str] — the pool of plausible-but-endless asks the escalation draws from
)

# The engine actions the orchestrator may take — exactly one per turn, each answered by an
# OBSERVATION the next turn reasons about. Shown as ordinary calls for readability.
from routine.actions import (read_file, write_file, util, write_util, llm, spawn,
                             wait, ask_user, finish)
from routine.state import phase, ledger    # state/phase.json helper, LEDGER.md append helper

META = {
    "name": "Predatory Publisher Tar-Pit",
    "slug": "predatory-publisher-tar-pit",
    "description": "Filter predatory-publisher mail to junk and draft escalating, never-ending "
                   "stall replies the user sends himself.",
    "when_to_use": "Use when the instruction is a recurring mail-hygiene routine that both files "
                   "junk-mail solicitations server-side AND composes ongoing bait replies that "
                   "keep the senders busy. Fits draft-only correspondence with per-thread state, "
                   "an escalation that must stay coherent across runs, and a filter script that is "
                   "learned and refreshed without overwriting the user's own rules.",
    "version": 3,
    "tags": ["email", "spam-filtering", "draft-only", "correspondence", "state-machine"],
    "includes": ["ai-writing-tells", "email-thread-continuation", "change-restraint",
                 "decision-record", "ask-policy"],
    "tools": None,          # None = every action kind is allowed
}

PHASES = ["bootstrap", "steady"]     # tracked in state/phase.json


class NeedsDecision(Exception):
    """A choice only the user can make — raised to file a deferred question and carry on."""


class SenderStalled(Exception):
    """A thread can't advance this run (its next stall is blocked on the user) — skip and move on."""


def main():
    """One run of the routine — the top-level control flow. The two invariants (never send;
    never clobber the user's filter rules) hold at every branch below."""
    orient()                                    # consume the state digest before anything new

    if phase.current() == "bootstrap":
        bootstrap()                             # first run(s): set up state/, learn the domain
        # fall through — a bootstrap run still delivers a first real increment.

    fresh, replies = scan()                     # classify recent mail: new solicitations vs. thread replies
    if not fresh and not replies:
        return finish("ok", "No predatory solicitations or bait replies this run; filters intact.")

    refresh_filters(fresh)                      # fold new senders/patterns into the junk ruleset

    drafted = []
    for msg in fresh:
        drafted.append(bait_first_contact(msg))     # warm, credible, positive first reply

    for thread in replies:
        try:
            drafted.append(bait_escalation(thread))  # the next endless stall, one stage further
        except SenderStalled:
            continue                            # can't advance this thread now; leave it for later
        except NeedsDecision as decision:
            ask_user(decision, mode="deferred")

    record(fresh, drafted)                      # persist per-thread state + append the LEDGER entry
    return finish("ok", "senders caught, filter changes, drafts saved, threads advanced")


def orient():
    """Consume the state digest (phase, last result, LEDGER tail, prior hand-off, user messages)
    before touching the mailbox — so the escalation resumes from the right stage per thread and no
    sender is baited twice. The digest carries the LEDGER tail; read the file only if it says so."""


def bootstrap():
    """First run(s): create state/, learn the shape of the account and its existing mail-filter
    script (so the user's non-predatory rules are known and preserved), and seed the per-thread
    bait ledger. File deferred questions for genuinely pivotal unknowns (ask-policy) — e.g. any
    sender the user actually corresponds with that must never be filtered. Advance
    state/phase.json to 'steady' once the loop can run, then continue into this run's real work."""


def scan():
    """Read recent messages across SOURCE_FOLDERS with the mail-reading capability and split them
    two ways.

    FRESH — first-contact solicitations, recognised by TELLS (bogus/obscure journal or conference
    invitations, article-processing-charge come-ons, exaggerated flattery, fabricated
    indexing/impact-factor claims, manufactured urgency, sender domains that don't match the
    purported publisher). Use `llm` for the borderline judgment; a legitimate academic contact is
    left untouched.

    REPLIES — messages that land in threads this routine is already baiting, matched on
    thread/References headers and known-sender state in the ledger. Return (fresh, replies)."""


def refresh_filters(fresh):
    """Keep the server-side mail-filter script filing known predatory senders and keyword patterns
    to JUNK_FOLDER. Read the LIVE script first, add or update only the predatory rules (learning
    the new senders/domains from `fresh`), and preserve every existing non-predatory rule the user
    has — this is additive, never a blind overwrite (change-restraint). Write the script back
    backup-first, and only through a path that validates it before activation, so an invalid script
    is never made live. If validation rejects the script, keep the prior one and surface why."""


def bait_first_contact(msg):
    """Compose the opening reply to a fresh solicitation: warm, credible, genuinely-interested
    ("yes, I'd like to submit / attend"). Thread it correctly from the original message's headers
    and save it to DRAFTS_FOLDER via the draft-writing capability — NEVER send. Open this thread in
    the per-thread ledger at escalation stage one. Write it as a real, busy academic would
    (ai-writing-tells): no throat-clearing, no tidy tricolons, no AI cadence."""


def bait_escalation(thread):
    """Advance one baited thread by exactly one stage. Read where the ledger left it (offer made,
    current stage, last stall used) and pick the NEXT plausible-but-endless ask from STALL_LIBRARY
    that hasn't been used here — an official indexing certificate, a fee-waiver code, an invoice
    reissued in another currency, ethics-board or institutional sign-off, one more missing form,
    proof of DOI registration, a co-author consent letter. Tone: polite, earnest, subtly wasting
    their time — never mocking, never revealing the game, never resolving. Thread it from the
    reply's headers and save it to DRAFTS_FOLDER — NEVER send. Raise SenderStalled if the next move
    genuinely needs the user first. Return the saved draft's handle for the ledger."""


def record(fresh, drafted):
    """Persist the per-thread bait state (who each sender is, the offer made, the escalation stage
    now reached, the stall last used) so the escalation stays coherent next run. Append exactly one
    LEDGER entry: new predatory senders caught, filter rules changed, threads advanced and to which
    stall stage, and the hand-off note for what the next run should pick up. Then sweep the run for
    machinery friction — a capability that failed or misled — and file each real hitch with the
    `report` action. Rotate LEDGER.md that run whenever it exceeds the SIZE IN BYTES the
    recipe names: archive the older entries behind a one-line rollup and keep the recent
    tail. MEASURE THAT THRESHOLD IN BYTES, DERIVED FROM THIS ROUTINE'S OWN ENTRIES — a
    count of lines or entries stops tracking what a reader pays once entries grow from
    one-liners into narratives. Measured across a 33-routine instance on 2026-09-21,
    every ledger over 100 KB was comfortably INSIDE its own count-based limit; one was
    112 KB at 30 entries against a 40-entry trigger, so a fully compliant run correctly
    did nothing. Set it at roughly ten to fifteen real entries, and if a rotation leaves
    the file still over it, fix the number with the measurement that justifies it: a
    threshold you trip by complying with it is one every run learns to ignore.

    ROTATE THE LEDGER IN THE RUN THE THRESHOLD TRIPS, and measure that threshold in
    BYTES derived from this routine's own entries. A count of lines or entries cannot see
    what a reader actually pays: entries grow from one-liners into narratives, so the same
    count means a 20 KB file one month and a 130 KB file the next. Measured across a
    33-routine instance on 2026-09-21, EVERY ledger over 100 KB was comfortably inside its
    own count-based limit -- one was 112 KB at 30 entries against a 40-entry trigger, so a
    fully compliant run correctly did nothing. The LEDGER tail is in every run's context,
    so the cost is paid before any work starts.
    THE CAP AND THE KEPT TAIL ARE ONE PAIR, AND A CROSSED PAIR IS WORSE THAN NO TRIGGER.
    The cap is a byte CEILING; keeping the last N entries is a count FLOOR worth N x the
    mean entry size, and the larger of the two is the one that actually binds. A cap set at
    "about N entries" makes them equal by construction and the trigger is inert either way:
    at or just above the floor it rotates one entry, lands just under, and re-trips on the
    next append -- a rotation every run forever; below the floor it cannot be satisfied at
    all while keeping N entries, so skipping it is a correct run's only option. Both were
    live on that instance the same day. Set the cap ABOVE the floor with headroom, and if a
    rotation leaves the file still over the cap -- or lands it within one entry's size of
    the cap -- the numbers are wrong: fix them with the measurement that justifies it. A
    threshold you trip by complying with it is one every run learns to ignore."""
    ledger.append("senders caught, filter diff, threads + stall stages, next-run hand-off")


if __name__ == "__main__":
    main()
