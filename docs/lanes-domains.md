# Lanes, domains and tags — how routines relate to each other

A routine sits in three independent structures. They answer three different questions, have
three different cardinalities and three different owners; keeping them apart is what stops one
decision from silently making another.

## The three objects

| | what it decides | cardinality | lives in | owner |
|---|---|---|---|---|
| **lane** | when a set of routines fires and in what order | at most one per routine, **enforced** | `.control/lanes.json` | daemon-owned; the web RECORDS, the daemon FIRES |
| **domain** | the shared config block, the shared store, the notes boundary | at most one per routine | the record in `.control/domains.json`; MEMBERSHIP in the routine's OWN `domain:` key | user-only config, like every other key there |
| **tags** | what it is about | any number | `tags:` in routine.yaml, plus whatever its domain contributes | "a label, not behaviour" (`configflow.py`) |

A lane's chain fires each member ONCE, in order. A flow with an inbound and an outbound end
BRACKETS the lane — a dedicated inbound-router member placed first and a dedicated
outbound-sender member placed last — rather than running one member twice.

**The third axis crosses the second.** `tags` is one of a domain's `CONFIG_KEYS` and one of its
list keys, so a domain UNIONS its tags onto every member: a routine's effective tags are its own
plus its domain's. That is deliberate — a domain is a set of routines that belong together, so
naming the set once beats repeating it per member — but it means a tag on a routine is no proof
that routine's own file names it. Anything reading tags reads the EFFECTIVE set.

**Config clustering and the trust boundary are ONE object.** They answer the same question —
which routines are close enough to share? — and they have the same cardinality. Splitting them
would dissolve the argument that makes a domain note approval-free: a note cannot leave the
domain because the domain's store is in its members' fs roots and nobody else's. The boundary IS
the safety model.

### What a domain shares

ELEVEN routine.yaml keys (`domains.CONFIG_KEYS`). The domain is a DEFAULT, never an override —
the two halves of that set are answered differently because their shapes differ:

| | keys | how a member's own file combines with it |
|---|---|---|
| **lists** | `permissions`, `rules`, `machines`, `tags`, `fs_read_roots`, `fs_write_roots` | UNION — the domain is a floor a member adds to; a member cannot subtract an entry |
| **mappings** | `models`, `connections`, `grants`, `budgets` | PER KEY, the member's own value winning — a shared budget fills in only what a member leaves unset, a shared model binds only a role the member has not bound itself |
| **both at once** | `capabilities` | its list members (actions, utils, util tags) UNION; its dials — the approval levels, `runs`, `workflows`, `reminders` — take the member's value wherever it sets one |

A key the domain does not set is left entirely to each member. The merge happens at LOAD
(`config/domainconfig.apply_shared_config`) and writes nothing back, so clearing `domain:`
returns a routine to exactly what its own file says. It runs BEFORE validation, which is what
makes "the member set it" mean *the key is in its file* rather than *the model has a default* —
every one of these fields has a non-empty default, budgets especially, so a merge over the
validated model could never tell the two apart.

The same list fixes what a domain may NOT share: slug, name, description, enabled, schedule,
workflow, retention, triggers and improve say WHICH routine this is and when it runs, so sharing
them is meaningless or destructive. Nor does it reach `tuning.yaml`: `deliberation` is a
machine-tunable handle that lives there rather than in routine.yaml, so it is each routine's
own however close its neighbours are.

## Where each axis is edited

A lane is edited on the Routines page: its row carries run-now, pause and edit; the toolbar
above the list creates lanes and holds the instance-wide failure default. From a conversation the
same store is reached by the `manage_lane` action, which covers the temporal axis and nothing
else — no verb there touches a config block, a store or a notes boundary. That kind is
conversation-INITIATED rather than conversation-only (F328): every depth-0 run is offered it,
`list` answers anywhere because it writes nothing, a root conversation applies a changing verb
directly, and any other depth-0 run queues a proposal for the Decisions page instead of applying
it. A within-reply child (depth > 0) is refused outright.

Which domain a routine is IN is on that routine's own page, beside every other per-routine
setting, because joining one is an ordinary config save. What a domain SHARES is one block,
edited once in the Routines page's domains section rather than once per member. There is
deliberately no `manage_domain`: `domain:` is user-only config in the routine's own file, so a
run that wants to move a routine proposes it as a config patch (`ask_user` with `config_patch`)
for the user to approve and the web to write.

## Why a lane is instance state and a domain's membership is routine config

A lane is about the ORDER several routines fire in and belongs to no single one of them, so it
lives where triggers and one-shot schedules live: a daemon-owned file under `.control/` that
the web writes and the daemon reads.

A domain splits down the middle, which is the point. The RECORD — its name and its
shared block — describes several routines at once, so it sits beside the lanes in
`.control/domains.json`. MEMBERSHIP does not: which surface a routine shares is an ordinary
per-routine setting; putting it in the routine's own file is what makes "at most one" a
**fact of the file** rather than a rule someone has to enforce across a list. It also means
membership lives in exactly one place and cannot disagree with itself — `domains.members()`
reads the routines, so a routine deleted from disk is out of the domain by construction.

Membership pointing that way is why deleting a domain is REFUSED (409) while any routine still
names it: the delete cannot un-name itself from member files; going through with it would
silently narrow what every member may do. Empty it first, one routine config save at a time.
The store is left on disk regardless — a config record disappearing is not consent to delete
the files members wrote — and `rsched validate` names a routine whose `domain:` answers to
nothing, because that routine inherits an empty block rather than failing.

## Why these are three objects and not one

**The temporal axis demands exclusivity.** A routine in two scheduled lanes fires twice, so lane
membership must be effectively single — and one record carrying timing, config and a boundary at
once quantizes all three to that cardinality. Under such a record you cannot say "these five
routines share a permission surface" without also saying "and they fire together".

The predecessor record proved what that costs: 14 of them carried 31 memberships between them,
with **zero** routines in more than one. Exclusivity had eaten the whole model. Four `Instance ·`
records held byte-identical 294-char config blocks and two `Professional ·` ones held another —
precisely the failure mode D82 exists to prevent (N copies of one policy surface drifting
apart), recreated inside it by the cadence split. Two records carried a config block and no
members at all. `Instance · Weekly · Utils` fired an empty chain at `0 3 * * 2` for weeks,
logging a chain-done event that reads exactly like a chain whose members all completed. The
dimensions with nowhere of their own to live were hand-encoded in the NAMES:
`Instance · Weekly · Research` is a domain, a cadence and a topic crammed into one string.

And because the shared config rode on the same membership, moving a routine from the nightly
record to the weekly one silently changed what it was allowed to do. **A timing decision was a
permissions decision**, with nothing anywhere to say so.

## Three things this shape rules out

- **A config merge across records.** `config/domainconfig.domain_config_for` looks ONE block up
  by id out of the routine's own key and combines nothing across records. Its predecessor
  scanned a membership list and merged whatever it found with "first record wins the whole key"
  while unioning WITHIN one, so what a routine inherited depended on the order rows happened to
  sit in a JSON file — an order no caller could have stated. With one domain there is no order
  to depend on.
- **An unanswerable store-root count.** `domains.member_store_roots` returns zero or one — the
  cardinality is why — and it stays a list only because every caller splices it into the run's
  fs roots, where `[]` keeps a concatenation from becoming a branch.
- **A config change hidden inside a timing change.** A lane owns no store and no config, so
  deleting one returns its members to their own crons and changes nothing else about them.

## The conversion (`migrate_group_split.py`)

The one-shot that reads `.control/groups.json` and writes `.control/lanes.json`,
`.control/domains.json` and a `domain:` key into each member's routine.yaml.

Three rules decide what becomes what:

1. **Identical config blocks become ONE domain.** Clustering is by the block's exact content
   after `domains.clean_config` — what every read of the store returns — so the four `Instance ·`
   copies collapse to one; two blocks differing only in a key the store drops collapse with them.
   Nothing has to be guessed about intent. The domain's name is the leading ` · ` segment the
   contributing names share — `Instance`, `Professional` — which is exactly the dimension that
   had nowhere else to live; a cluster of one keeps that group's whole name (`FAU`).
2. **A domain inherits the id of whichever contributing group has FILES in its store** (the
   first contributor when none has any), so no directory of shared files moves. This is not
   tidiness: routines address these paths in their own memory. One live routine carries
   `READ /control/group-stores/grp-8bfd2aa6/…` as a standing prevention rule it wrote for
   itself after an incident; several more name a store id in a ledger. A moved store would
   silently falsify agent-authored notes instead of failing loudly, which is why the store
   directory is named `group-stores` and every migrated id is kept as it stands.

   **An id names neither a kind of object nor a store.** A migrated LANE keeps its source id
   exactly as a migrated domain does, since an id is an opaque handle nothing parses — so five
   ids on the live instance name a lane AND a domain, two unrelated records that share nothing
   but a string. Every migrated object of either kind carries the `grp-` prefix; a newly created
   lane gets `lane-` and a newly created domain `dom-` (`lanes.new_id`, `domains.new_id`). Which
   store an id was read from is what says what it is — never the prefix.
3. **Nothing that holds anything is dropped.** A group with members but no cron was never a
   lane — nothing fired it — so it becomes a TAG on its members: the user's own categorization
   survives on the axis meant to carry it. That branch reads the CLOCK ALONE, so a group with
   members, a config block and no cron becomes BOTH — its block still joins a cluster and its
   members are stamped `domain:` as well as tagged. Config outlives membership
   too — a block with no members still joins its cluster and can still be the contributor whose
   id the domain takes, if its store is the one holding files. Only a group with no members and
   no config is dropped outright.

Two more things it does, neither of them a rule about what becomes what:

- **It enforces the lane cardinality as it writes.** A slug two groups both listed stays in the
  first lane in file order and is dropped from the later one, which is left with its remaining
  members or with no lane at all. Nothing on this instance is in that state; it is written out
  anyway — a document the store would refuse to WRITE is a document `lanes.update` would refuse
  to EDIT, which would turn a topology nobody has into a lane nobody could ever save again.
  Every drop is named in the migration's log line.
- **It reads the source defensively.** It runs on the daemon's upgrade boot, before the app
  exists and with nothing catching what it raises, so a record it cannot read is SKIPPED and
  named rather than thrown on. One document must never be the place a stale reference takes the
  instance down.

Everything else carries across untouched: each lane keeps its `on_failure`, `tz`, `paused` and
`created`; the instance-wide `default_on_failure` moves with the store. The conversion is
one-shot and self-retiring — it runs only while `.control/groups.json` exists and unlinks it at
the end, so a second boot does nothing.

### One consequence worth stating out loud

Collapsing four `Instance ·` groups into one domain **widens a trust boundary**: the store that
belonged to `Instance · Nightly · Maintenance` becomes readable and writable by every Instance
member, not just `library-sync` and `self-audit`. Same for `Professional`. That is the point —
routines sharing a policy surface share a store — but a widening is never something to discover
later.
