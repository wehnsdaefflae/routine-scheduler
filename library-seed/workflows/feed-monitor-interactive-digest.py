"""Periodic feed monitor and interactive digest renderer.

Consume periodic incoming items from a monitored source (e.g., an inbox or feed), extract and
deduplicate their sub-items, auto-categorize them, and render a self-contained interactive HTML
digest that is PUBLISHED as a static public page. The page's vote buttons write to a shared
feedback store that the next run reads back — a documented, symmetric feedback loop with no hosted
server. This file is a PATTERN, not a program: the orchestrator never executes it — it *acts it
out*, one engine action per turn, following the control flow below (its branches, loops, and error
handling). The dummy imports name the parameters this routine works with; the clarifier pins them
down for the concrete task, and `decompose` turns this pattern into the routine's own markdown
state-machine (main.md + stages/).

Design note: nothing here hosts a long-lived web server — a run ends, and with it anything it
started — so this pattern does NOT stand up a `POST /vote` server. It needs exactly two outside
capabilities, and names neither tool — the run finds both in its CAPABILITIES catalog and records
in its own memory which one worked:

1. A SITE PUBLISHER: uploads a single HTML file and returns a public URL. Pick ONE and stay with
   it — one retry policy, never a paste-host lottery across services.
2. A SHARED-STATE / FEEDBACK STORE reachable from the browser: a small REST-addressable JSON
   document that permits cross-origin reads and writes without a key. A store instance is
   registered once as the vote sink, its URL is embedded in the published page so the browser can
   write votes to it, and the next run reads the same instance back before ranking.
"""

# --- Parameter contract -------------------------------------------------------------------------
# These imports do not resolve to anything at run time. Each names one piece of information the
# clarifier must fix for THIS routine — the type, and what it means, live in the comment.
from routine.params import (
    SOURCE,               # str       — the monitored source and how "new" is recognized there (e.g. an inbox label, a sender set, a feed URL); the run picks the tool that reads it from its catalog
    LAST_PROCESSED_STATE, # str       — path to the state file tracking the marker (timestamp/id) of the last processed item
    CATEGORIES_HINT,      # list[str] — initial/preferred categories for extracted sub-items
    VOTES_STATE,          # str       — path to the state file mirroring the last-read vote tally (a local cache of the shared vote store)
    VOTE_SINK_STATE,      # str       — path to the state file holding the shared vote store's {id, url} (created at bootstrap)
    OUTPUT_DIGEST,        # str       — path where the rendered self-contained HTML digest is written before publishing
    PUBLISH_TARGET,       # str       — where the digest is published: the ONE sanctioned public destination whose publisher uploads OUTPUT_DIGEST and returns a public URL
)

# The engine actions the orchestrator may take — exactly one per turn, each answered by an
# OBSERVATION the next turn reasons about. Shown as ordinary calls for readability.
from routine.actions import read_file, write_file, util, write_util, llm, spawn, wait, ask_user, finish
from routine.state import phase, ledger    # state/phase.json helper, LEDGER.md append helper

META = {
    "name": "Feed monitor & interactive digest",
    "slug": "feed-monitor-interactive-digest",
    "description": "Periodically read a source for new items, extract/deduplicate/categorize "
                   "sub-items, PUBLISH a self-contained interactive HTML digest as a static page, "
                   "and weight future digests by votes the page collects into a shared feedback "
                   "store.",
    "when_to_use": "The routine monitors an inbox or feed on a schedule, extracts links/content "
                   "from incoming items, categorizes them, renders a self-contained HTML page with "
                   "upvote/downvote buttons, and PUBLISHES it to a public URL. Votes are written by "
                   "the browser into a shared feedback store and read back on the next run to "
                   "influence ranking. Use when the instruction involves processing new periodic "
                   "inputs into a categorized, published, vote-weighted digest whose deliverable "
                   "is a STATIC page — nothing here hosts a server, so the feedback loop runs "
                   "through a shared store the browser writes to. Needs two capabilities: a site "
                   "publisher, and a browser-writable shared-state store.",
    "version": 7,
    "tags": ["monitor", "digest", "publishing", "feedback-loop", "categorization", "ranking"],
    "includes": ["decision-record", "ask-policy", "engagement-accountability", "feedback-implementation-gate"],
    "tools": None,          # None = every action kind is allowed
}

PHASES = ["bootstrap", "steady"]     # tracked in state/phase.json

class ExternalBlocker(Exception):
    """This run can't proceed right now (the source is down, a util failed)."""


def main():
    """One run of the routine — the top-level control flow."""
    orient()

    if phase.current() == "bootstrap":
        bootstrap()
        # fall through — a bootstrap run still processes whatever the source already holds, so
        # the first published digest has real items in it.

    votes = collect_feedback()          # read back what the last published page's readers wrote
    new_items = fetch_new_items()
    if not new_items:
        return finish("ok", "No new items this run; consumed latest votes, nothing to publish.")

    sub_items = []
    for item in new_items:
        sub_items.extend(extract_sub_items(item))

    sub_items = deduplicate(sub_items)
    categorize(sub_items)                   # ONE judgment over the batch, not one call per item

    ranked_items = rank_items(sub_items, votes)

    html = render_html(ranked_items, vote_sink_url())
    write_file(OUTPUT_DIGEST, html)
    url = publish_digest(OUTPUT_DIGEST)

    update_last_processed(new_items)
    record(url)
    return finish("ok", f"Processed {len(new_items)} new items, published digest at {url}.")


def orient():
    """Consume the state digest (phase, last result, LEDGER tail, user messages/answers) before
    exploring anything new — so you never re-try a known dead end. The digest already carries the
    LEDGER tail and says when there is more; read the file only if it says so."""


def bootstrap():
    """First run: find the tools for the three outside jobs (read SOURCE, publish a page, hold
    shared state) in your catalog — the `util` action with name `list` gives their exact usage —
    and NOTE which ones you settled on, so later runs don't repeat the search. Then initialize
    LAST_PROCESSED_STATE, register the vote store (storing its {id, url} in VOTE_SINK_STATE),
    render an EMPTY digest, publish it to PUBLISH_TARGET, and advance state/phase.json to
    'steady'. No local server is started — the deliverable is a published static page, and
    feedback flows through the shared store."""
    try:
        util(...)   # confirm you can actually read SOURCE before building anything on it
    except Exception:
        ask_user("Could not read the monitored source. Please verify the source and access.",
                 mode="deferred")

    write_file(LAST_PROCESSED_STATE, {"last_timestamp": None})
    sink = util(...)                           # create the shared vote store -> {id, url}
    write_file(VOTE_SINK_STATE, sink)
    write_file(VOTES_STATE, [])
    html = render_html([], sink["url"])        # embed the sink URL so the page can write votes
    write_file(OUTPUT_DIGEST, html)
    publish_digest(OUTPUT_DIGEST)


def collect_feedback():
    """Read the current vote tally BACK from the shared store registered as the vote sink
    (VOTE_SINK_STATE holds its {id, url}). The published page's buttons write votes into this
    store, so reading it at the start of the run closes the feedback loop. Mirror the result into
    VOTES_STATE for ranking; a missing/empty store means no votes yet."""
    if not file_exists(VOTE_SINK_STATE):
        return []
    sink = read_file(VOTE_SINK_STATE)
    votes = util(...)                # read the shared store back by its id (sink["id"])
    write_file(VOTES_STATE, votes)
    return votes


def fetch_new_items():
    """Read SOURCE to get the items still to process. The LAST_PROCESSED_STATE marker is a
    LOW-WATER guard against re-processing, NOT the sole selector: advance it only to the newest
    item this run actually PROCESSED (extracted + folded into the digest), never merely to the
    newest item SEEN. Otherwise an item that arrived but was skipped this run (source truncation,
    an error, a same-timestamp collision) is silently lost forever once the marker jumps past it.
    So the fetch should SWEEP all recognized-but-unprocessed items from the source (e.g. all unread
    mail from known senders), not just those strictly newer than the marker, and reconcile against
    the marker to avoid duplicates."""
    marker = read_file(LAST_PROCESSED_STATE) if file_exists(LAST_PROCESSED_STATE) else {}
    # Fetch recognized-but-unprocessed items from the source (sweep, don't trust the marker alone);
    # after the digest is built, advance the marker only to the newest item actually processed.
    # This pattern represents the fetch operation.
    pass


def extract_sub_items(item):
    """From a single new item (e.g., a newsletter), extract the individual sub-items
    (e.g., articles with title, summary, source, date, url)."""
    pass


def deduplicate(sub_items):
    """Drop duplicates: exact URL match first, then near-identical titles (normalized to
    lowercase, whitespace collapsed).

    This is judgment-free and runs identically every run, so it belongs in this routine's own
    persistent tooling rather than being re-derived by hand each time — write it once through
    whichever authoring capability your CAPABILITIES list offers, and call it thereafter."""


def categorize(sub_items):
    """Assign every sub-item a category from CATEGORIES_HINT, or a new one where none fits.

    ONE scoped `llm` judgment over the WHOLE batch, not one per item: the categories are chosen
    against each other, and a per-item call spends a turn apiece to make a worse decision with
    less context."""
    return llm(f"Categorize each of these titles into {CATEGORIES_HINT} or a new category: "
               f"{[s['title'] for s in sub_items]}")


def rank_items(sub_items, votes):
    """Apply vote weights (from the shared store, mirrored in VOTES_STATE) to sub_items and
    categories to surface upvoted topics higher and sink downvoted ones."""
    pass


def render_html(ranked_items, vote_sink_url):
    """Generate a SELF-CONTAINED HTML page (embedded CSS/JS), items grouped by category with
    timestamps and original links, plus upvote/downvote buttons. The buttons maintain a local
    tally and write it to `vote_sink_url` — the shared store's REST endpoint, which must permit
    cross-origin writes from the browser — so the next run reads the votes back. There is NO
    server and NO POST /vote endpoint; the page is a static artifact that talks only to the
    shared store."""
    pass


def publish_digest(path):
    """Publish the rendered HTML at `path` to PUBLISH_TARGET and return the public URL, then
    inform the user of it. Use ONE sanctioned publishing capability with one retry policy — never
    improvise a paste-host lottery across many services. Which tool provides it comes from your
    catalog, and from this routine's own memory once a run has found one that works."""
    result = util(...)       # the site-publishing capability, uploading `path` to PUBLISH_TARGET
    return result["url"]


def vote_sink_url():
    """The vote store's REST URL (from VOTE_SINK_STATE), embedded into the rendered page so
    the browser can write votes to it. None before the store is registered."""
    return read_file(VOTE_SINK_STATE)["url"] if file_exists(VOTE_SINK_STATE) else None


def update_last_processed(new_items):
    """Update LAST_PROCESSED_STATE with the timestamp of the most recent processed item."""
    if new_items:
        latest = max(i['timestamp'] for i in new_items)
        write_file(LAST_PROCESSED_STATE, {"last_timestamp": latest})


def file_exists(path):
    """Helper to check if a state file exists."""
    pass


def record(url):
    """Update state/phase.json and any state files; append exactly one LEDGER entry for the run."""
    ledger.append(f"Processed N new items, published digest to {url}, consumed prior votes from "
                  "the shared vote store.")


if __name__ == "__main__":
    main()
