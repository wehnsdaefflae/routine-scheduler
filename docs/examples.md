# Examples: four complete routines

Four realistic setups, end to end: the draft you type, what the clarifier asks, what to
pick on the create page, and what daily operation looks like. They are deliberately
different in cadence, permissions, and human involvement — together they exercise most of
the system. Treat them as templates: swap the domain, keep the shape.

All four follow the same skeleton — **files are the memory, runs are increments, you are
the gate for anything irreversible** — but each stresses a different feature:

| routine | cadence | showcases |
|---|---|---|
| [Freelance radar](#1-freelance-radar) | weekday mornings | multi-source scanning, sub-workflows per source, autonomous util repair, a send-gate |
| [Grants radar](#2-grants-radar) | weekly | a long pipeline with sign-off gates, full run history, document utils |
| [Project steward](#3-project-steward) | weekdays | the `shell` permission, real build/test cycles, standing-project state |
| [Birthday planner](#4-birthday-planner) | twice a week | iterative convergence on taste, Discord decisions, learning from feedback |

---

## 1 · Freelance radar

**Goal:** every weekday morning, a fresh shortlist of freelance AI/ML project postings
scored against your profile — and, for the ones you pick, a drafted application that goes
out only after your explicit go.

### The draft you type into the wizard

> Scan the freelance platforms (freelancermap, freelance.de, GULP, LinkedIn/Indeed via
> jobs-scrape) and my inboxes (gmail, fau-mail) for new AI/ML/LLM project postings.
> Score each against `state/profile.md` (I'll keep it updated; start by asking me for
> rate floor, stack, remote preference). Keep `state/shortlist.md`: top 10 by fit,
> newest first, with rate, link, one-line fit rationale, and predicted pros/cons.
> When I mark a posting as "pursue" (I'll answer your question), draft a tailored
> application into `state/drafts/<id>.md`, run it through pangram to keep the tone
> human, and ask me for the go before ANYTHING is sent. Never send without my approval.
> Learn from my rejections: when I say no to a posting type twice, stop shortlisting it.

### Clarifier exchange (typical)

- *"What counts as 'new' — since the last run, or a rolling window?"* → since the last
  run, with a 7-day window on the first run.
- *"Where should the profile's hard limits live?"* → `state/profile.md`; ask blocking
  questions for the initial values on run 1.
- *"Sending: via the platform's form or by email?"* → whichever the posting offers;
  platform-form utils exist (`freelancermap-apply`, `freelance-de-apply`, `gulp-apply`).

### Create page

| setting | pick | why |
|---|---|---|
| workflow | `general-task` | scan → score → draft → gate is ordinary tool work |
| traits | ask-policy, global-utils, web-research, ledger-discipline | the routine-improver meta routine handles improvement passes for every routine |
| permissions | **util-authoring-autonomous**, memory, self-modification, **communication** | scrapers break at 6 a.m. — autonomous *revisions* fix them without waking you (new utils still ask); communication mirrors the send-gate to Discord |
| budgets | 60 turns · 45 min · defaults; **ask_timeout_min 240** | a send-gate that waits longer than half a day is stale anyway |
| schedule | weekdays 06:30 | the shortlist is ready with your coffee |

### A typical run

1. Reads the state digest: profile, yesterday's shortlist, LEDGER tail, `.memory/` index
   (portal quirks live here — "GULP paginates after 20", "freelance.de needs the CDP
   browser").
2. **Spawns one sub-workflow per platform** (`spawn`, four children in parallel, each with
   a self-contained scrape-and-normalize prompt) while it reads the inboxes itself.
3. Merges, dedupes, scores; rewrites `state/shortlist.md`; compares against your past
   pursue/reject answers and drops the types you've rejected twice.
4. For each posting you marked "pursue" (your answers to earlier deferred questions):
   drafts the application, runs `pangram`, then asks a **blocking question** — the draft's
   head, options `send / hold / revise`, default `hold` — which also lands on **Discord**.
   Reply "send" from your phone and the platform-apply util fires; ignore it and the run
   continues after 4 h with the draft safely held.
5. Improvement passes: if a scraper errored, it reads the util source, fixes it, and the
   selftest-gated revision is committed for every other routine too. LEDGER entry, finish
   summary, done.

**Where you come in:** one deferred question per interesting posting ("pursue?"), one
blocking (mirrored) question per send. Everything else is autonomous.

---

## 2 · Grants radar

**Goal:** a weekly sweep of public funding programs matched to three hard constraints,
tiered and briefed — and an application pipeline that composes complete packets which are
submitted only after your sign-off.

### The draft

> Every week, find funding programs matching ALL of: solo/freelancer eligible, software
> deliverable, no letters of recommendation required. Sources: the ted util (EU
> procurement), websearch + page-fetch over the funder databases in `state/sources.md`
> (start it with the obvious ones, extend it as you find better lists), and my gmail for
> calls forwarded to me. Verify status/deadline/amount on the funder's own page before
> listing anything. Keep `state/programs.md` tiered (A: apply, B: watch, C: logged) by
> fit and deadline. For programs I mark "apply", build the full packet under
> `state/applications/<id>/` from my dossier in `state/applicant/` (ask me once for CV
> and boilerplate), check the prose with pangram, and ask for my sign-off before any
> submission — by form, email, or portal. Track every submitted application to a
> decision and keep statistics across the year.

### Create page

| setting | pick | why |
|---|---|---|
| workflow | `general-task` (or generate a pipeline pattern once the library has traffic) | |
| traits | ask-policy, global-utils, web-research, ledger-discipline | source tuning and pipeline growth come from the routine-improver's research/features lenses |
| permissions | util-authoring, memory, self-modification, **run-history-full**, communication | **full run history** is the point: "did we already see this program in March?", "what did the run that submitted X actually do?" — longitudinal questions the LEDGER alone can't answer |
| budgets | **80 turns · 60 min** · ask_timeout_min 480 | a weekly run may verify dozens of pages; give it room |
| schedule | Mondays 07:00 | deadlines are usually weekday-anchored |

### A typical run

1. Drains your answers (pursue/reject/sign-off) from the week; checks `.memory/` for
   funder-portal quirks.
2. Discovery sweep: `ted` with the CPV codes from `state/sources.md`, websearch over the
   query bank (rotating 5 of 20 queries per run — the routine-improver's research lens retunes the
   bank by yield), gmail for forwarded calls.
3. **Re-verifies every tracked program** on its funder page — deadlines move; a dead link
   demotes the program with a note instead of silently keeping stale data.
4. Advances each active application one stage: research → compose (packet fields,
   attachments via `xlsx-pdf` / `pdf-stamp` where forms are involved) → pangram pass →
   **sign-off gate** (blocking, default `hold`, mirrored to Discord) → submit → monitor
   the inbox for the funder's reply.
5. Cross-run statistics from `run-history-full`: submissions per month, tier→outcome
   rates — into `state/stats.md`. LEDGER, finish.

**Where you come in:** tier-A programs arrive as deferred "apply?" questions; each
submission is one blocking sign-off. Expect ~10 quiet minutes a week.

---

## 3 · Project steward

**Goal:** an autonomous weekday dev/PM routine advancing a real software project — code,
tests, docs, obligations — one focused increment per run. (Modeled on stewarding a
grant-funded open-source project: deliverables register, worklog, public repo hygiene.)

### The draft

> Advance the project in ~/projects/llmsectest every weekday. Sources of truth:
> `STATE.md` (current milestone + health), `BACKLOG.md` (prioritized work),
> `DELIVERABLES.md` (obligations with dates — nothing here may silently slip). Each
> run: fold in new signals (my gmail for the project alias; the project's Zulip
> stream), confirm nothing due is slipping, then pick ONE highest-value action and
> finish it — code with tests green, or a doc, or an obligation. If ahead of schedule,
> spend the run on quality (tests, refactors, edge cases), never on pulling future
> milestones forward. Weekly (Mondays) do a fresh-eyes audit of the public surfaces
> (README, docs site) as a first-time reader; "functional but bad" is a real finding.
> Draft outbound messages (Zulip status updates, emails) but post NOTHING without my
> go. Commit your work; keep `WORKLOG.md` append-only.

### Create page

| setting | pick | why |
|---|---|---|
| workflow | `general-task` | orient → decide one thing → execute → record |
| traits | ask-policy, global-utils, ledger-discipline, web-research | engineering improvement arrives via the routine-improver's lenses |
| permissions | **shell**, util-authoring, memory, self-modification, run-history | **this is the routine the shell permission exists for**: `gu shell "cd ~/projects/llmsectest && uv run pytest -q"` — builds, test suites, linters. Repeatable operations still get promoted to utils (`pytest-run`, `git-sync` already exist) |
| fs roots | read+write: `~/projects/llmsectest` | the project lives outside the routine dir |
| budgets | 80 turns · **90 min** · ask_timeout_min 480 | test suites take wall-clock time |
| schedule | weekdays 07:00 | |

### A typical run

1. Orients: `STATE.md`, `BACKLOG.md`, last WORKLOG entries, `.memory/` ("the docs build
   needs node 20", "Zulip posts need the topic prefix").
2. Signals: gmail scan for the alias, `zulip read` for the cohort stream — deadlines and
   requests land in `BACKLOG.md`; things only you can do land as deferred questions.
3. Guard pass over `DELIVERABLES.md`: anything due ≤ 14 days gets priority regardless of
   the backlog.
4. **One thing**: implements the next backlog item — edits code (fs write root), runs the
   suite via `shell`/`pytest-run`, iterates to green, commits via `git-sync`. If ahead of
   schedule: a quality increment instead (a flaky test, a type-hole, a doc gap).
5. Mondays: the fresh-eyes audit — reads the public README/docs as a stranger, fixes
   "functional but bad" findings in the same run.
6. Drafts (never posts) the weekly Zulip status update → blocking question with the text,
   default `don't post`. LEDGER + WORKLOG, finish.

**Where you come in:** the Monday "post this update?" gate, occasional scope questions,
and the deliverables it flags as needing a human signature.

---

## 4 · Birthday planner

**Goal:** converge on a real, costed plan for a milestone birthday — venue, dates,
logistics, guest coordination — by proposing concretely, collecting your reactions, and
learning your taste run over run.

### The draft

> Plan my 44th birthday (March 12–14, 2027, ~20 guests, budget ceiling 3000 €). Each
> run: improve the top 3 proposals in `state/proposals.md` — each with venue, dates,
> rough cost, travel notes (use db-link for train connections), and pros/cons — based
> on my feedback since the last run. Research real venues and providers with websearch
> and page-fetch; cache findings with sources under `state/research/`. Ask me a FEW
> pivotal taste questions (max 2 per run) with concrete options. Track guest
> availability in `state/guests.md` from what I relay. Never contact anyone — venues,
> guests, vendors — yourself: prepare drafts and shortlists, I do the outreach.

### Create page

| setting | pick | why |
|---|---|---|
| workflow | `general-task` | propose → feedback → learn → propose is steady-state work |
| traits | ask-policy, web-research, ledger-discipline | the routine-improver's UI lens keeps `proposals.md` readable as it grows |
| permissions | memory, **communication**, **run-history** | taste questions belong on your phone (Discord), not buried in a console; run-history lets it diff proposals against exactly what you saw last time |
| budgets | 40 turns · 30 min · **ask_timeout_min 1440** | taste questions can wait a day; the run continues on its stated default and folds your late answer into the next one |
| schedule | Tuesdays + Fridays 18:00 | often enough to converge, rare enough to have news |

### A typical run

1. Drains your feedback (answers, and anything you injected mid-week), diffs it against
   the taste notes in `.memory/` ("prefers long tables over standing receptions",
   "trains over driving, always").
2. Research pass on the current gaps: two venue candidates verified on their own sites,
   prices cached with source links, `db-link` deep links for the guests coming by train.
3. Rewrites `state/proposals.md`: top 3, each fully costed, with what changed since last
   time and why (your feedback, quoted).
4. Asks its ≤2 pivotal questions — deferred, with options, mirrored to Discord: *"Venue
   direction: (1) rented cabin, self-catered · (2) restaurant back room · (3) mixed —
   cabin + one restaurant evening. Default if unanswered: keep developing 1 and 3."*
5. Updates the taste model in `.memory/` from your previous answers (what you chose AND
   what you rejected), LEDGER entry, finish summary you can read in one minute.

**Where you come in:** two option-taps on your phone twice a week. The plan converges
because rejections are recorded as firmly as choices — the LEDGER keeps it from
re-proposing what you already declined.

---

## Patterns worth stealing

- **State the default on every ask.** All four set `default` on blocking questions — the
  run continues usefully when you're busy, and you can still answer later.
- **Reject-memory beats accept-memory.** Freelance radar and Birthday planner both record
  *rejections* (in LEDGER/state) so nothing is re-proposed. Cheap and transformative.
- **Sub-workflows per source, not one mega-loop.** Parallel children with disjoint outputs
  (Freelance radar step 2) keep the main conversation short and the failures isolated.
- **Repair, don't route around.** A broken util fixed under `util-authoring-autonomous`
  is fixed for every routine. A silent workaround breaks everyone tomorrow.
- **Give history only where history is the point.** Only Grants radar carries
  `run-history-full`; the others get by on the last summary + LEDGER — smaller prompts,
  fewer places to wander.
