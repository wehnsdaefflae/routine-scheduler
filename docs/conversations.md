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
| Budget | Per **reply** (≈10 turns), fresh each message | Per **run** |
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
- **Model** — start on a specific catalog model (picked by name), or the system default
  (switchable any time later).
- **Shell** — off by default (the agent works through selftested utils); flip it on for the rare
  session that needs the escape hatch.
- **Attachments** — drop in files (or paste a screenshot straight into the box).

## The reply cycle

A conversation is **one continuous run**, and every reply is a self-contained leg of it:

- You send a message → the agent works (each step visible in the transcript) → it **finishes with a
  reply**. That reply *is* the finish summary of this leg.
- You send another message → the same run **resumes in place** with a fresh budget window. Nothing
  is lost between messages; the files, the LEDGER, and everything the agent observed carry over.
- If you message while the agent is still working, it's delivered as an injection and **picked up at
  the next turn** rather than starting a new leg.

Each reply gets roughly **10 turns** of budget. When the agent nears that ceiling it gets an 85%
warning and wraps up: it records where it is and replies with honest progress, ending with an offer
to continue. Say **continue** and it picks up right where it left off, in the same conversation, with
a fresh window. Tokens are unlimited by default — the turn cap is what bounds a reply, so long jobs
proceed a chunk at a time with you in the loop.

Because chat replies draw from a **reserved interactive pool**, a busy schedule never makes you wait
in line behind cron runs, and vice versa.

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

## Artifacts — deliverables in the side panel

When the agent produces something that's more than a chat answer — a report, a generated page, a
chart, a data file — it writes it into the conversation's **artifacts** folder, and the right-hand
panel lists and renders it: HTML (sandboxed), Markdown, images, PDF, CSV, and JSON all display
inline. Re-writing the same filename updates the artifact in place. The agent names each artifact it
produced in its reply, so you always know what to look at.

## Capabilities, budgets, and model

Open **⚙ capabilities & budgets** at the top of a conversation to tune it — changes apply from the
next reply:

- **Budgets** are **per reply**: turns, minutes, and tokens for each message, not the whole session.
- **Permissions** work exactly as they do for routines (see the *Traits & permissions* guide). A
  conversation starts with the default set; **shell** is a one-click grant. Previous-run depth is
  greyed out — a conversation is one continuous run, so it doesn't apply.
- **Model** switches from the line at the top. Change it any time; if a reply is in flight, it
  switches at its next turn boundary too.
- **Traits** (its practice files — how it asks you, uses utils, researches, keeps a LEDGER, and makes
  git checkpoints) are shown read-only.

Title and tags are generated for you from the first message and are editable inline.

## Topics and forking

A conversation is at its best as **one conversation, one topic** — the shared context is what makes
later replies smart. If you drift onto something unrelated, the agent notices: it flags the reply as
a new topic and offers a **one-click fork** that starts a fresh conversation pre-filled with your
message. Take the fork to keep each thread's context clean.

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

## See also

- **Playbooks** — turn a conversation into a reusable one-shot brief, and reuse it.
- **Traits & permissions** — how a session's conduct and capability are set.
- **Getting started** — routines, the scheduled counterpart, and the pieces both share.
