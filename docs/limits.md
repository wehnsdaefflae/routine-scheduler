# Known limits — what a release said it does NOT cover

A release entry that declares its own remainder is good engineering writing. It is not a route.
Of the nine such remainders declared between 0.444.0 and 0.445.2, the ONE given a report id —
R2844, `_OPENING_STOP` splitting on ASCII sentence stops only — was fixed **the next day**. Two
more named a route (a `docs/designs.md` entry; the question id `q-20260912-121159-40`). Six
carried no route at all, and two of those went wrong *silently*: a credential watch covering one
provider of three looks like a credential watch, and a command "never run against a live
instance" is the one artefact whose whole purpose is to be run against a live instance.

The mechanism that turns a known limit into a fix is **being addressed to someone**, and the
CHANGELOG addresses nobody — it is a record, not an inbox, by design and rightly. This file is
the inbox for the one class that had none.

### What belongs here, and what does not

| the thing | where it goes | why |
|---|---|---|
| a defect someone owns | a **report** — say so in the clause: `Not fixed, reported as R2844` | the path that worked in 24 hours |
| a judgement, not a defect | a **decision or question** — `raised as D291`, `Still open: q-20260912-121159-40` | a judgement call does not belong in a defect queue |
| decided but unbuilt | a **`docs/designs.md`** entry (`is NOT built, so the entry stays in docs/designs.md`) | that file is an ORDER, and it has its own reader |
| known, **undecided**, unbuilt | **an entry here** | nothing else holds this class |
| nobody will ever build it | `Accepted permanently: <reason>` **in the clause itself** | an honest outcome, stated once, where it was declared |

`docs/designs.md` holds what is DECIDED and unbuilt, and an entry there is deleted when it ships.
This file holds the opposite contract: **known, undecided, unbuilt**, with the evidence, the
release that declared it, and one line on what would make it matter. An entry **graduates** to
`docs/designs.md` when somebody decides it, and is **deleted** when it stops being true.

### The declaration vocabulary

A release declares a limit with one of: `Known limit`, `Known limits`, `Still open:`,
`Not fixed`, `Not measured`, `is NOT built`, or a bullet under a `Known gaps in this release`
heading. `tests/test_limits_md.py` reads the CHANGELOG for exactly those and requires a
disposition for each — so using one of them is how you declare a limit, and a clause written in
words the check does not know is not caught. A marker quoted as a `code token` is prose ABOUT
this convention, not a declaration (R2596's lesson: a tombstone comment quoting its own marker
once reddened `main` itself).

### The shape of an entry

Each entry is a `##` heading and six fields, every one required:
**Not covered** (what the code does not do) · **Evidence** (how that is known) ·
**Declared** (the release and date whose entry said so) · **Clause** (a verbatim fragment of
that entry, which is the join — at least 25 characters) · **Would matter when** (the one line
that tells a future reader whether to care) · **Disposition** (what is being done, or that
nothing is).

The check keeps the join honest in both directions: an entry whose fragment no longer appears in
its release's clauses is reported, because the limit was reworded, shipped or deleted — and then
the entry must be re-pointed, graduated or removed rather than left to rot.

---

## The fast stage and a live run cannot both be guaranteed in the 5 GiB cgroup

- **Not covered:** 0.535.1 priced a serial selection correctly but left the genuine collision: a release gate's FAST stage and a live routine run cannot both be guaranteed inside this container's cgroup, so a gate was refused rather than run smaller.
- **Evidence:** the fast stage holds 3,584 MB of 5,120 MB; the report measured a live run holding 1,717 MB (3,271 MB left) while this instance measured 1,086 MB (4,033 MB left). Fitting SOMETIMES is worse than never, because the refusal then reads as a flaky failure.
- **Declared:** 0.535.1 (2026-11-13)
- **Clause:** "the fast stage's 3,584 MB and a live routine run cannot both be guaranteed"
- **Would matter when:** any release gate runs while a routine is live — i.e. most of the day.
- **Disposition:** raised as **D291** and ANSWERED by the operator ("reduce FAST_WORKERS while a routine run is active, trading gate wall-clock for coexistence" — not the cgroup raise); built and shipped in 0.542.0. This entry stays only until the next release confirms the gate coexists; it is then deleted, which is what a graduated entry looks like.

## F790's guard is green-only, never seen to fail

- **Not covered:** the assertion that stops `apply it` staying live while the edit box is open was never observed RED, so it is a guard that has only ever passed.
- **Evidence:** three attempts to see it fail died on the shared gate lock (`/tmp/rsched-gates.lock`), which the release gate holds per stage.
- **Declared:** 0.471.0 (2026-10-28)
- **Clause:** "is green-only, not red-first"
- **Would matter when:** somebody edits that control's lifecycle — a guard that cannot fail protects nothing, and nobody would learn it from a green suite.
- **Disposition:** undecided. Cheap to settle whenever the box is quiet (break the production line deliberately, watch the test go red, restore) and not worth its own release.

## D219 asserts the frame-ancestors header outside a loaded page

- **Not covered:** no browser test asserts the security header's VALUE inside a page the browser has actually loaded.
- **Evidence:** `frame-ancestors 'self'` is a claim about the outside world; nothing in this suite embeds the console elsewhere, so the assertion is made at the response level only.
- **Declared:** 0.471.0 (2026-10-28)
- **Clause:** "D219 ships no browser test asserting the header VALUE inside a loaded page"
- **Would matter when:** an operator frames the console from another host and sees it blank, or a future change relaxes the header and no test notices.
- **Disposition:** undecided. Testing it needs a second origin in the browser harness, which is a harness change rather than a fix.

## The read-model memo is per-process

- **Not covered:** the memo that makes a read model cheap lives in the serving process, so a resumed leg starts with an empty one.
- **Evidence:** stated when the memo shipped; it is a module-level cache by construction.
- **Declared:** 0.455.0 (2026-10-25)
- **Clause:** "the memo is per-process, so a resumed leg starts empty"
- **Would matter when:** a resumed leg's first read of a large stream becomes slow enough to notice, or a measurement attributes that cost to the wrong thing.
- **Disposition:** accepted for now, deliberately: a cold read is correct, only slower, and a shared cache would add a second writer to a derived value.

## Thirteen recommender fields are judged only on the accept route

- **Not covered:** the 13 `GAP —` fields (`capabilities`, `deliberation`, `schedule`, `run_gate`, the fs roots) are validated where a proposal is ACCEPTED, not where it is composed.
- **Evidence:** named in the release that shipped the accept-route checks.
- **Declared:** 0.455.0 (2026-10-25)
- **Clause:** "are still judged only on the accept route"
- **Would matter when:** a surface other than the accept route starts proposing those fields — the proposal would be offered unchecked, which is the shape F884 already cost once.
- **Disposition:** undecided; it becomes a defect the moment a second writer exists.

## Namespace-double detection is by name, and covers four classes

- **Not covered:** ~126 of ~208 namespace doubles are unchecked; only four classes are covered, and `Completion` is not among them.
- **Evidence:** that is how F754's stand-in got through — measured when the check shipped.
- **Declared:** 0.448.0 (2026-10-24)
- **Clause:** "detection is by NAME, so ~126 of ~208 namespace doubles are unchecked"
- **Would matter when:** a stand-in of an uncovered class drifts from the real one; the suite then proves the double correct and says nothing about the code.
- **Disposition:** undecided. Widening it is mechanical but not free — each class needs its own spec — so the decision is which classes earn it, not whether the check works.

## Two curly-quote marker mappings are untested

- **Not covered:** the U+201C/U+201D mappings in the marker table change no outcome today and have no test.
- **Evidence:** no marker in the table contains a double quote, so the mapping is unreachable; it is kept so a marker carrying a quoted phrase cannot reintroduce the bug.
- **Declared:** 0.445.1 (2026-10-23)
- **Clause:** "no marker contains a double quote, so the two U+201C/U+201D mappings change no outcome today"
- **Would matter when:** somebody adds a marker containing a quoted phrase — exactly the case the mapping exists for.
- **Disposition:** **Accepted permanently** in substance: an unreachable branch kept as insurance, whose cost is two table rows. It is listed here rather than in the clause because the next marker with a quote makes it live, and then it needs a test.

## `rsched postdeploy` has never run against a live instance

- **Not covered:** the real HTTP call to `/api/status` is exercised nowhere, and the command has never been run against a live instance.
- **Evidence:** `_status` is stubbed in all 42 tests, because a run's sandbox cannot read the live config (Errno 13) or `/api/settings` (403). The checks' logic is tested; their one real I/O path is not.
- **Declared:** 0.445.0 (2026-10-23)
- **Clause:** "the real HTTP call to `/api/status` is exercised nowhere"
- **Would matter when:** it already does. This is the one artefact whose whole purpose is to be run against a live instance, so a command that looks tested and has never run is the worst case in this file.
- **Disposition:** undecided, and the strongest candidate here for being raised as a decision: it needs either a deploy-time invocation somebody reads, or a grant that lets a run reach the live status route.

## The questions client cannot see future schedules

- **Not covered:** the client cannot see FUTURE SCHEDULES, so an ordinary deferred ask from a finished run reads as a proposal even when its routine fires within the hour; the open card's kind chip still reads `deferred`, rendering the record's own `mode`.
- **Evidence:** asserted in a test rather than glossed, when the distinction shipped.
- **Declared:** 0.445.0 (2026-10-23)
- **Clause:** "the client cannot see FUTURE SCHEDULES"
- **Would matter when:** the operator sorts his Decisions page by urgency and a question that will be answered by its own routine in an hour sits beside one that waits on him indefinitely.
- **Disposition:** undecided. Serving the next fire time to the client is a small read-model addition; whether the distinction is worth a payload field is the judgement.

## The credential watch covers one provider of three

- **Not covered:** it watches only CLIProxy-bound endpoints. OpenRouter and Nano-GPT are not covered.
- **Evidence:** stated in the release that shipped the watch.
- **Declared:** 0.445.0 (2026-10-23)
- **Clause:** "it watches only CLIProxy-bound endpoints"
- **Would matter when:** an OpenRouter or Nano-GPT credential expires. A credential watch covering one provider of three LOOKS like a credential watch, which is why this one goes wrong silently.
- **Disposition:** undecided. Each provider needs its own liveness probe, so this is three small pieces of work rather than one.

## A declined blocking ask is settled by matching a head word

- **Not covered:** `interact._held_not_settled` decides a declined BLOCKING ask by matching the phrase's head word, not by the value.
- **Evidence:** widening D38's settle rule needs `interact.py`, which is at the house size bar; a test pins the head word, so a reword goes red first.
- **Declared:** 0.444.0 (2026-10-23)
- **Clause:** "a declined BLOCKING ask is settled by `interact._held_not_settled` matching the phrase's head word"
- **Would matter when:** the decline vocabulary is reworded — the pinned test catches it, so the failure mode is a red gate rather than a silent one.
- **Disposition:** undecided, and blocked on a size budget rather than on a judgement: the fix wants `interact.py` to shrink or to hand the rule to a module that owns it.

## The conformance findings were never measured against the live fleet

- **Not covered:** the number of routines each conformance finding affects was never counted on the live fleet.
- **Evidence:** a run cannot read the server config (Errno 13), so an in-run count is vacuous — and it was not claimed, which is the right call.
- **Declared:** 0.444.0 (2026-10-23)
- **Clause:** "a run cannot read the server config (Errno 13), so an in-run count of affected routines is vacuous"
- **Would matter when:** somebody prioritises conformance work by impact and has no impact figure to sort by.
- **Disposition:** undecided; it is the same missing capability as the postdeploy entry above (a run that may read live instance state), so one decision probably settles both.
