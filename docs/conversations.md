# Conversations

A **conversation** is an interactive, Claude-Code-like session with an agent: you type a task, it
works — running utils, reading and writing files, producing deliverables — and replies, and you
keep going turn by turn. It's the interactive counterpart to a scheduled **routine**: same engine,
same tools, same *the-workflow-is-the-harness* design, but driven by a present human instead of a
cron schedule.

Use a conversation when you want to *do a piece of work now, together* — explore a codebase, draft
and revise a document, clean a dataset, research something — rather than tend a recurring task.

## Conversation vs. routine

| | Conversation | Routine |
|---|---|---|
| Trigger | You, message by message | A cron schedule |
| Lifetime | One continuous session, resumed in place each reply | One run per fire, state carried in files |
| Instruction | Your first message (optionally seeded by a playbook) | A workflow, decomposed from your instruction at creation |
| Versioned? | **No** — the directory is not a git repo; delete means gone | Yes — the engine commits each run |
| Budget | Per **reply**, fresh each message | Per **run** |
| Spine | A **working plan** the agent writes and revises as it goes | A workflow compiled at creation |
| Where | Conversations tab (`~/conversations/<slug>`) | Dashboard (`~/routines/<slug>`) |

Everything else — the tool set, permissions, artifacts, the readable transcript — is shared.

## Starting a conversation

**Conversations → + new.** The only required field is the **first message**: the task, in your own
words. What you write becomes the conversation's working instruction, so say what you want produced
and what "done" looks like.

Optional, all on the same form:

- **Playbook** — seed the conversation from a saved **playbook** (a reusable one-shot brief). The
  brief becomes the instruction and your first message just *specializes* it — see the **Playbooks**
  guide. With a playbook picked you can even leave the message empty.
- **Project directory** — a folder the agent may read and edit. This is how a conversation works on
  a real codebase or document set.
- **Folder access** — extra folders beyond the project directory, granted **before the first reply
  fires**: read + write ones (also readable) and read-only ones, each picked with the server-side
  directory browser. They land on the conversation's config the same way an allow-forever folder
  grant does, so reply #1 already has the access instead of asking you mid-run.
- **Model** — start on a specific catalog model (picked by name), or the system default
  (switchable any time later). Every option shows the model's context window; one whose window is
  too small to run the harness at all is disabled — and the server refuses it too.
- **Shell** — off by default (the agent works through selftested utils); flip it on for the rare
  session that needs the escape hatch.
- **Admin** — arm the admin token for this browser session so the conversation starts with
  capability gating lifted (reply #1 included). The server re-checks the token on every request
  and never stores it.
- **Attachments** — drop in files (or paste a screenshot straight into the box).

## The reply cycle

A conversation is **one continuous run**, and every reply is a self-contained leg of it:

- You send a message → the agent works (each step visible in the transcript) → it **finishes with a
  reply**. That reply *is* the finish summary of this leg.
- A message can never fall between the cracks of a finishing reply (R108/F268): one that
  arrives while the model is finishing DEFERS the finish and is answered in the same leg;
  one that races past even that — the run finished in the instant between the web's
  liveness check and the file landing — is caught twice over: the message endpoint
  re-checks liveness AFTER writing (and wakes the run when it finished in between), and
  the daemon's post-finish reap sweeps the inbox for any still-unconsumed USER message and
  resumes the run itself. Report/trigger deliveries are exempt from the sweep — they keep
  their own read-on-next-run contract.
- You send another message → the same run **resumes in place** with a fresh budget window. Nothing
  is lost between messages; the files, the LEDGER, and everything the agent observed carry over.
- If you message while the agent is still working, it's delivered as an injection and **picked up at
  the next turn** rather than starting a new leg.

**A reply ends when you have something, not after N steps.** The agent works until it reaches a
point worth handing you: a finished piece of the job, a verified deliverable, a decision only you
can make, or a genuine blocker. A single message can run for many turns when the job needs it — it
is not trying to answer quickly, it is trying to answer.

The per-reply budget is a **backstop** against a runaway, not a pace. When the agent nears the
ceiling it gets a warning and converges to the nearest clean handover: it brings the working plan
and LEDGER up to date and replies with honest progress, ending with an offer to continue. Say
**continue** and it picks up right where it left off, in the same conversation, with a fresh window.
And if the budget does run out mid-work, the agent still gets a reserved final turn to write the
reply itself — you never get an engine error where an answer should be.

Because chat replies draw from a **reserved interactive pool**, a busy schedule never makes you wait
in line behind cron runs, and vice versa.

## Questions and approvals

When the agent needs a decision — a plain question, a util-change approval, a typed access
request — the question appears **twice, answering once**: pinned above the composer as the full
form (free text, ask-back, the timeout line), and inline in the chat where it was asked. For a
blocking question with options (approve/decline) or an access request, the chat bubble carries
the **one-click buttons** right there, so you can approve without scrolling to the panel or
switching to the Decisions page — all three surfaces settle each other the moment any one of
them answers.

## The working plan

A scheduled routine gets its structure from a workflow compiled when you create it. A conversation
gets it the other way round: as soon as a request needs more than a handful of steps, or will span
more than one reply, the agent writes a **working plan** into `state/plan.md` — the goal in a line,
the ordered steps with their status, the decisions still open, and what it's waiting on from you.

The plan is put in front of the agent at the top of **every** later reply, so it — not the chat
scrollback — is what keeps a long job coherent. The agent revises it as the work teaches it: ticking
off what's done, re-ordering, adding what it discovered, dropping what turned out unnecessary. When
one step needs more detail than a few lines can hold, that step gets its own file under `stages/`,
read only when the step comes up. When the job is finished, the plan is deleted.

Two things follow from this that are worth knowing:

- **Redirect it freely.** The plan serves the conversation, never the other way round. Tell the
  agent the third step is wrong and it revises the plan and says so.
- **Big steps get decomposed.** A plan step that is large and self-contained is the agent's cue to
  run it as a child task with its own fresh context and budget, rather than spend this reply's
  context on it — so a long job doesn't degrade as it goes.

## The three panes

- **Left — conversations list.** One line per conversation (state dot, title, time); hover for a
  card with the snippet, tags, and status. A tag filter narrows the list. The pane folds to a rail.
- **Center — the chat.** Your messages and the agent's replies are the conversation; the tool work
  between them folds into one expandable group per reply, so you can read the outcome and expand the
  how only when you want it. Hover any message — yours, a reply, even a single step inside a work
  fold — and a **↩ refer-to** button primes the composer with it, messenger-reply style: your next
  message leads with a quoted `> re …` line the agent reads naturally, and the sent bubble shows the
  reference as a compact quote chip (✕ on the chip drops it before sending).
- **Right — artifacts + state.** The deliverables the agent produces, a live state-graph card, and
  a **files** card — which files the run read / wrote / edited, per-path counts straight from the
  transcript (subtasks and your slash commands included). Also folds to a rail so the chat gets the
  width.

## Deliberation

The header's **⚙ capabilities & budgets** panel carries the **deliberation** slider — how much of
the model's thinking lands on paper as it works (conversations default to *deliberate*: says that
carry the context behind each step, including knowledge beyond the immediate inputs). A change saves
to the conversation and, when a reply is live, re-levels it at the next turn.

## Attachments

Attach files on the first message or any later one; paste an image directly into the message box and
it's attached automatically.

- **Text** files are read with the file tool.
- **Images and PDFs** are *seen* — shown directly to the model when it's a multimodal one, otherwise
  described via the vision util. (An attached image is usually shown to the model already; it can ask
  for another look.)
- **Spreadsheets and other binaries** are handled by a fitting util.

## Slash commands — run actions yourself

You can run the SAME effect actions and global utils the assistant uses, straight from the
message box. Type `/` and the composer autocompletes; the **/ commands** button next to the
input opens the full reference (the actions your conversation's capabilities allow, plus
every global util with its usage line).

```
/util websearch "rust web frameworks" --json
/read_file notes/draft.md
/write_file notes/todo.md - call the bank
/edit_file notes/todo.md anchor="call the bank" replacement="called ✓"
/view_image shots/screen.png what changed here?
/llm summarize the pasted text in two lines
/memory_read env-quirks
/memory_write env-quirks about="server quirks" the NAS mounts read-only after backup
```

A command executes through the engine's normal action path — the same capability
enforcement, the same working-directory rules — and costs **no model turn**. Crucially,
**the speaking turn stays with you**: a message that only runs commands does not hand the
turn to the assistant, so you can run as many as you like (fetch a page, read a file, jot
a memory note) and the assistant stays quiet. It replies only when you send a plain
message — and then it sees everything your commands produced. The result of each command
appears in the chat as a command block; a malformed or disallowed command answers with its
usage line instead of failing silently. Loop-control actions (`spawn`, `subtask`, `wait`,
`ask_user`, `finish`, …) are deliberately not commands — they steer the assistant's run;
ask for them in plain words.

The same rule holds anywhere the turn is yours: if you resume a finished run (a conversation
reply, or a completed routine) with a command, it executes and the turn stays with you. It
does **not** apply to a routine's own scheduled execution — that is the routine's turn, not
yours, so its workflow always runs (a command you inject there is context for that run).

## The goal — what DONE means

A conversation's budgets are a **runaway backstop**, not a definition of done. What actually
bounds the job is the **goal**: meaning-level conditions you write, in the rail's `goal` panel.

- A condition is prose — "the PDF is verified", "only diagnose, do not start fixing".
- Conditions live in **groups**, and a group is satisfied by **ALL** of its conditions or by
  **ANY** one of them — click the ALL/ANY chip to switch. With more than one group, the same
  chip appears at the top for how the groups combine. That is how you say "either the work is
  published *or* I call it off", which a flat checklist cannot express.
- **`after s<n>`** on a condition holds it dormant until that one is met — the sequencing case.
  A dormant condition is greyed and says what it waits for; the agent is shown it but is not
  asked to judge it yet.
- On a routine, a condition can also name a **stage**, so it is live only during that stage.

The agent sees the whole structure in its prompt and **must account for every active condition
when it finishes** — a line per condition saying met or unmet and why. A finish that skips one is
rejected and costs it a turn. Its verdict is written back, so the panel's marks (`✓` met, `○`
open, `–` dropped) are the run's own conclusions, not a list you maintain by hand. You can always
overrule one: click the mark to cycle it, then **save goal**.

When every condition the goal needs is met the panel says **goal met**, and the agent is told the
job is done and to finish now. The engine does not force it to stop — it cannot judge your
conditions, only make them impossible to ignore.

**Claims are checked.** Marking a condition met is a claim, and a second model reads the agent's
own transcript to see whether it holds up. If it does not, the finish is set aside once with the
objection, and the agent either does the missing work or restates its case. It is asked only
once: a repeated verdict stands, because a check that could veto forever would hang the job
rather than bound it. When the two disagreed, the condition carries an amber **disputed** mark —
hover it for the objection — and the call is yours.

## Artifacts — deliverables in the side panel

When the agent produces something that's more than a chat answer — a report, a generated page, a
chart, a data file — it writes it into the conversation's **artifacts** folder, and the right-hand
panel lists and renders it: HTML (sandboxed), Markdown, images, PDF, CSV, and JSON all display
inline. Re-writing the same filename updates the artifact in place. The agent names each artifact it
produced in its reply, so you always know what to look at.

## Capabilities, budgets, and model

The same **⚙ capabilities & budgets** panel is offered in two places: on the **new-conversation
composer** (open it before you hit *start* — the first reply fires on create, so a permission,
budget, or deliberation level that must govern reply #1 has to be set there) and at the top of a
running conversation, where changes apply from the next reply:

- **Budgets** are **per reply**: turns, minutes, tokens and child tasks for each message, not the
  whole session. They are a runaway backstop — raise them for a conversation doing heavy work,
  lower them if you want short exchanges.
- **Permissions** work exactly as they do for routines (see the *Traits & permissions* guide). A
  conversation starts with the default set; the **shell** action is a one-click grant. Previous-run depth is
  greyed out — a conversation is one continuous run, so it doesn't apply.
- **Model** switches from the line at the top. Change it any time; if a reply is in flight, it
  switches at its next turn boundary too. The picker labels every model with its context window
  (and flags tight ones); a model whose window minus its max output tokens leaves no room for
  input cannot complete a single turn, so the picker disables it and the server refuses it.
- **General rules** — the shared practices the conversation holds — are **picked on the composer**
  (F339). They have to be: a rule reaches the prompt through `main.md`'s Standing-practices tail,
  which is woven when the conversation is created, so one bound afterwards never governs reply #1.
  On a running conversation the picker still edits them, from the next reply onward.
- **Connections** (an OAuth account per provider) are pickable on the composer too, so the first
  reply can already act as that account instead of hitting an unbound connection and having to
  ask. Connect the accounts themselves in Settings → Connections.

Title and tags are generated for you from the first message and are editable inline.

## Topics, forking and branching

A conversation is at its best as **one conversation, one topic** — the shared context is what makes
later replies smart. If you drift onto something unrelated, the agent notices: it flags the reply as
a new topic and offers a **one-click fork** that starts a fresh conversation pre-filled with your
message. Take the fork to keep each thread's context clean.

That fork starts EMPTY. When you instead want to try a second approach **from where you already
are** — same history, same setup, without risking the thread you have — use **⑂ branch** in the
header.

### Branching

A branch forks the conversation at a turn you choose into a new one that inherits:

- the **config** — models, permissions, capabilities, rules, connections, folder access, budgets
  and deliberation;
- the **history** up to that turn, as a COPY — plus the `state/` and `attachments/` files that
  history refers to. It resumes as an ordinary continued conversation from turn one.

The original is **untouched**: a branch reads the same past but cannot rewrite it, so two lines of
work can run side by side and neither can damage the other. The header of each shows the family —
where a branch came from and at which turn, and which branches came off this conversation.

Branch a **finished** reply, not one in flight: the fork point has to be a settled turn.

### Handing a branch back

Branches are deliberately **not merged**. Two divergent histories cannot be interleaved into one
coherent conversation — the result would be a record of a conversation that never happened. Instead
a branch **hands its result back**: press **↩ hand back** (it appears only on a branch), write the
summary the parent should read, and the parent receives it as a message with the branch's
`artifacts/` copied into its own `artifacts/from-branch-<slug>/`. It reads exactly like a finished
background task reporting in, because it is the same thing: a child run returning a result.

The hand-back does not wake the parent — its next reply picks the message up. The branch keeps its
own conversation and can hand back again later; a second hand-back replaces the same artefact
folder. Nothing you did not put in `artifacts/` crosses over, and the parent's transcript is never
rewritten.

## Working on a project

Point a conversation at a **project directory** and it can read and edit real files there. Its
**git-checkpoint** practice makes commits *in that external repo* — a checkpoint before risky edits,
one after coherent work, named in the reply — so your project keeps clean undo points even though the
conversation's own directory is unversioned. (Give it the directory as the project dir at creation,
or set it later under ⚙.)

## Playbooks

A good conversation can become a reusable capability. **Save as playbook** distills the session into
a generalized brief in the library; **Update playbook** folds a session's improvements back into the
playbook it started from; and the playbook picker seeds a new conversation from one. See the
**Playbooks** guide for the full loop.

## Deleting

A conversation is deliberately **unversioned** — deleting it is permanent (there's a confirm step).
If any of the work matters, make sure it landed as an artifact, or as a commit in a project
directory, before you delete.

A nightly state mirror (`deploy/backup.sh`) does **not** change that, and should not be mistaken
for an archive. It is a *converging* mirror: a deleted conversation survives in it only until the
next run, which propagates the deletion. That leaves a recovery window of at most a day — real,
but not something to rely on. Restoring from it means copying the directory back out of the
mirror before that run.

## See also

- **Playbooks** — turn a conversation into a reusable one-shot brief, and reuse it.
- **Traits & permissions** — how a session's conduct and capability are set.
- **Getting started** — routines, the scheduled counterpart, and the pieces both share.
