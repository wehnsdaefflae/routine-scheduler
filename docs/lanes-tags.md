# Lanes, shared stores and tags — how routines relate to each other

A routine sits in three independent structures. They answer three different questions, have
three different cardinalities and three different owners; keeping them apart is what stops one
decision from silently making another.

## The three objects

| | what it decides | cardinality | lives in | owner |
|---|---|---|---|---|
| **lane** | when a set of routines fires and in what order | at most one per routine, **enforced** | `.control/lanes.json` | daemon-owned; the web RECORDS, the daemon FIRES |
| **shared store** | which files a set of routines reads and writes together, plus the notes channel between them | any number per routine | a directory directly under `.control/group-stores/`; SHARING is the routine's OWN `fs_write_roots` | user-only config, like every other key there |
| **tags** | what it is about | any number | `tags:` in routine.yaml | "a label, not behaviour" (`configflow.py`) |

What a routine may DO — its permissions, capabilities, rules, budgets, models — is none of the
three. Settings patterns (docs/patterns.md) replace settings templates and a domain's shared
config block: a routine's settings are its own routine.yaml, read against the pattern it follows.

## Lanes

A lane's chain fires each member ONCE, in order. The boundary between two members is also the
only quiet gap a back-to-back chain leaves, so a pending self-update restart takes it: with
nothing else active the next member is HELD, the daemon restarts, and the member fires on the new
code at the first tick after boot. Nothing a person starts is refused — the hold is the daemon's
own next member; a parked or background run keeps the chain advancing.

A flow with an inbound and an outbound end BRACKETS the lane — a dedicated inbound-router member
placed first and a dedicated outbound-sender member placed last — rather than running one member
twice.

A scheduled lane also carries a **catch-up policy** (`catchup: run_once`, the default, or
`skip`): a fire that came due while the daemon was not running to arm it is made up ONCE at the
next boot — never a backlog — because a lane's fire table is process memory and its members'
own crons are suppressed, so a lost lane fire has no other path (docs/architecture.md, "Lane
catch-up"). What boot compares is a WATERMARK on disk (`.control/lane-fires.json`); every
path that HANDLES a due fire moves it: an arm and a fire the operator's global pause skipped
on purpose. A deliberate skip is handled, not missed — the next boot does not fire it. Lifting
the pause decides once per lane (D156, `lane_catchup.resume_catchup`): a skipped fire still
EARLY in the lane's own cadence (less than a quarter of it has passed — six hours for a daily
lane, forty-two for a weekly one) is made up as ONE chain; a later one waits for the next fire.
Never a backlog.

What a lane FAILED to do is read at `GET /api/health/blocked` (docs/run-analytics.md): a
`lane_fire_refused` (the previous chain is still in flight), a `lane_chain_stopped` (the
chain ended early, so its remaining members never ran) and a `lane_chain_member_skipped` (a
member slug that is no routine in any home) each produce NO run, so a lane that is quietly
not firing looks exactly like a lane with nothing to do. `subject` on those rows is the
LANE ID, which is opaque by contract — resolve it against the lane store, never by reading
a prefix.

## Shared stores

A shared store is a directory directly under `<routines_home>/.control/group-stores/`. A routine
SHARES it by naming that directory among its own `fs_write_roots` (`rsched/sharedstores.py`);
nothing else records the sharing: which routines share a store is read back out of their
routine.yaml files, so it lives in exactly one place and cannot disagree with itself. A store
reaches a run as an ORDINARY read-write root — the file actions and the util sandbox already
honour it; nothing injects it. A root ABOVE the stores (a meta routine that may write the
whole routines home) covers every store and shares none: sharing is naming the store itself,
which keeps the set of routines a note can reach the set the operator put there.

The directory name `group-stores` is FROZEN: routines address these paths in their own memory.
One live routine carries `READ …/group-stores/grp-8bfd2aa6/…` as a standing prevention rule it
wrote for itself after an incident; several more name a store in a ledger. A moved store would
silently falsify agent-authored notes instead of failing loudly. A store's own name is an opaque
handle nothing parses.

Every run of a routine that shares a store reads a `SHARED STORES` paragraph in its harness
contract (docs/prompt-anatomy.md): each store it shares, the OTHER routines sharing it, and the
collision contract — writes are whole-file and last write wins per file, so the routines sharing
a store write per-routine filenames (`<slug>-<topic>.md`) and treat shared files as read-mostly.

### Notes between the routines sharing a store

A NOTE is coordination; a `report` is work an owner must act on, tracked until answered. For
routines coordinating over one store a report is heavyweight — it turns "here is the file I
staged for you" into a ledger row and a Messages-page item somebody has to close. So:

    <store>/notes/<to-slug>/note-<anything>.json     {"from": slug, "ts": iso, "text": …}

A routine WRITES one with an ordinary file write. The engine READS them at the addressee's boot
from every store it shares, renders them into the state digest as `NOTES FROM ROUTINES YOU SHARE
A STORE WITH`, and DELETES them once read. No approval, no ledger row, no Messages-page item:
the store is in its sharers' write roots and nobody else's.

**A note is NOT an inbox message — the differences are the design.** The two look alike —
both are a JSON file a run finds at boot, delivered exactly once. A note is READ AND DROPPED where
an inbox message is MOVED to `runs/<ts>/consumed/`, because a note that survived would be
re-shown every run until someone deleted it by hand — the tracked-work-item shape this channel
exists to avoid. A note is CAPPED at twenty per boot (the newest shown, the count of older ones
dropped said out loud) where the inbox has no cap, because a note is a nudge, not a mailbox. Its
text is capped on the read, because the writer is another routine's unvalidated file write,
straight into this prompt. And a note appears on no Messages folder, because the Messages page's
write contract gives the operator full write access to every waiting `msg-*` — extending it to
notes would make a person the author of a message another routine signed.

**A note nobody would read is refused where it is written.** A routine reads notes only from the
stores among its own write roots, so a note addressed to a routine that does NOT share the store
would sit there unread forever while its writer believed it delivered. The engine's write gate
(`engine/fileops._write_gate` → `sharedstores.note_refusal`) therefore refuses a `write_file`,
`edit_file`, `mkdir` or `move` destination inside `<store>/notes/<x>/` when `x` does not share
that store, naming the routines that do and the channel that reaches any routine: an addressed
`report` with target `x`. Deleting a stranded note, or moving one out, is never refused — that is
the repair. A shell command, a script or a util writes past the action layer, which is why the
contract says who shares each store.

## Tags

`tags:` says what a routine is about: any number, no behaviour, matched by the dashboard's search.
The Steward-hub heading a routine's card sits under is not a grouping either — it is the
routine's own `hub_tab` identity field (docs/status-pages.md).

## Where each is edited

A lane is edited on the Routines page: its row carries run-now, pause and edit; the toolbar
above the list creates lanes and holds the instance-wide failure default. From a conversation the
same store is reached by the `manage_lane` action, which covers the temporal axis and nothing
else — no verb there touches a routine's settings, its roots or a store. That kind is
conversation-INITIATED rather than conversation-only (F328): every depth-0 run is offered it,
`list` answers anywhere because it writes nothing, a root conversation applies a changing verb
directly, and any other depth-0 run queues a proposal for the Decisions page instead of applying
it. A within-reply child (depth > 0) is refused outright.

Which stores a routine shares is on that routine's own page — its Filesystem roots panel, a
read-write root naming the store — because sharing one is an ordinary config save. A run that
wants a routine to share a store (or stop sharing one) proposes it as a config patch (`ask_user`
with `config_patch` on `fs_write_roots`) for the user to approve and the web to write. Tags are
edited in the routine page's identity group.

## Why a lane is instance state and sharing is routine config

A lane is about the ORDER several routines fire in and belongs to no single one of them, so it
lives where triggers and one-shot schedules live: a daemon-owned file under `.control/` that
the web writes and the daemon reads.

Sharing a store is the opposite: which stores a routine reads and writes is an ordinary
per-routine setting — a write root — so it lives in the routine's own file, like every other
setting there; no list anywhere has to agree with the routines. A routine deleted from disk
stops sharing by construction. Taking a store out of every routine's roots leaves the directory
on disk: a config change is not consent to delete the files routines wrote there.

## Why a lane owns nothing but timing

**The temporal axis demands exclusivity.** A routine in two scheduled lanes fires twice, so lane
membership must be effectively single — and a record carrying timing AND a shared surface would
quantize that surface to the same cardinality: nobody could say "these five routines share a
store" without also saying "and they fire together". Worse, moving a routine from one lane to
another would then silently change what it may do. **A timing decision must never be a
permissions decision**, so a lane owns no store and no config: deleting one returns its members
to their own crons and changes nothing else about them.
