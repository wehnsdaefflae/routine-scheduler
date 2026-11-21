"""The harness contract's per-kind PROSE — what each action kind is FOR, and the conduct it
carries. NOT what its fields mean: that is the schema's, and the schema travels with every
request.

`engine/kindsurface.py` decides WHICH kinds a run sees; this file is WHAT the run is told about
each. A bullet's wording is prompt surface, pinned byte-for-byte by docs/prompt-anatomy.md §5.1
(tests/test_prompt_anatomy_example.py).

## Why these bullets carry no field mechanics

Field descriptions already reach the model on every turn through the schema (enforced on the
wire, or dumped by `schemaindex.full` / `schemaindex.send_the_contract`), and
`schemaindex.index` names each kind's fields from `actions.KIND_FIELDS`. A field recital here
would be a third, unenforced copy — so the words the model reads about a FIELD are exactly the
schema's.

What a bullet owes: the kind's IDENTITY (`ACTION_SCHEMA["kind"]` is a bare enum) and CONDUCT,
which no schema can hold — when to reach for the kind, when not to, what it costs, what the
engine does around it. A field a run gets wrong is taught when it is wrong, by
`schema_guard.retry_message` with `schemamemo.field_help`.
"""

from __future__ import annotations

from .actions import ASK_OPTIONS_MAX

# The per-kind bullets, keyed by the kinds each covers (a family may share one). Same filter as
# the schema: a run is told only about the channels it HAS. Every kind has a bullet but
# `script`, glossed in CAPABILITIES beside the routine's own scripts.
#
# Three placeholders are substituted (not f-string interpolated — the prose contains braces
# in code fragments): {util_confirm}, {ask_timeout_min} and {ask_options_max}.
KIND_PROSE: tuple[tuple[tuple[str, ...], str], ...] = (
    (("util",), """- util: run a global util.
Utils are your primary tools — the CAPABILITIES section below lists what exists (name + \
summary); for ONE util's exact usage run `util name=list args=["<util-name>"]` before relying \
on it (bare name=list re-dumps the whole catalog you already have). Observation = exit code + \
captured output."""),
    (("websearch", "code_search"), """- websearch: search the web — your 3 most \
distinctive terms in `args`. code_search: regex search across a code tree — the pattern \
first, then the paths. Both SPELL a util call, so the flags and the usage are that util's: \
ask it for them when a call of yours is refused."""),
    (("shell",), """- shell: run ONE ad-hoc command on the host. It runs NON-INTERACTIVELY \
inside your sandbox — the same filesystem jail your utils get, and no secret from the store \
reaches it — so anything that waits for a keystroke never returns, and a credential-needing \
command belongs in a util instead. Observation = exit code + captured output (capped; the \
overflow is saved to a file the observation names). This is the ESCAPE HATCH, not a tool: use \
it for the one-off look no util covers, and the moment you run something a SECOND time, write \
the util (or a scripts/ helper) that does it properly."""),
    (("write_util",), """- write_util: create or revise a global util — a complete
PEP 723 script: `# /// script` deps block, a module docstring whose first line is
`<name> — <one-line summary>` then a `usage:` BLOCK — the call form, and for a
verb-dispatched util one line per verb directly under it (every non-blank line up to the
next header key is part of it, and that whole block is what a caller is shown before it
calls you) — a `--json` flag, a `--selftest` that runs
built-in checks, data on stdout / diagnostics on stderr / exit 0 on success; on invalid or
missing arguments it MUST print its own usage line to stderr and exit 2 — an error that
doesn't teach the correct call wastes every future caller's turn. The engine runs
`--selftest` and only commits if it passes. A util may call sibling utils via `gu <name>` — \
declare those on a `calls: <name>, …` header line. If it needs a secret (token, password, API \
key), read it env-first — `os.environ["NAME"]` — never hardcode or prompt for it, AND declare \
the names in a header `secrets: NAME1, NAME2` line so the UI tells the user what to set (they \
set it once in the Secrets store; the engine injects it — ONLY declared secrets reach the \
util). A credential ONE VERB needs binds to it — `secrets: DEEPL_API@credentials`, a verb your \
`usage:` block lists — and reaches that verb's calls alone. Declare network use with a `net: \
outbound` (or `net: none`) header line: utils run in a filesystem/network sandbox and an \
undeclared network need fails. Declare filesystem use the same way, on an `fs:` header line — \
`fs: roots` when the util opens paths its CALLER passes it (the common case), `fs: none` when \
it touches no file outside its own temp space, or `fs: rw <path>` / `fs: ro <path>` for a \
private store the util reaches on its own (a session directory, a state file). A declared path \
is mounted only when the routine was already granted it, so declaring one asks for nothing — \
it narrows what this util sees, and keeps a store like that out of every OTHER util's \
jail.{util_confirm}"""),
    (("remove_util",), """- remove_util: delete a global util the library no longer needs. The \
curation counterpart to write_util, gated by the util-removal permission (and, unless \
util approval is set to never, the same approval step). The engine REFUSES if any other \
util still declares it on a `calls:` line — remove or update those callers first; the deletion \
is committed to the library and stays recoverable from git history. Check the catalog before \
removing something another routine relies on."""),
    (("read_file", "write_file", "delete", "move", "mkdir", "edit_file"), """- read_file / \
write_file / delete / move / mkdir / edit_file: read or write a file (within the working dir or \
an allowed root). Several files in ONE action — batch related reads instead of spending a turn \
per file; a directory path returns its listing (one entry per line, paged like a file); a PDF \
or an Office document (docx/pptx/xlsx) returns its text \
CONVERTED to Markdown, paged like a file — the observation names the pages and any page with \
no text layer (a scan, not transcribed); any other binary or oversized file only its size — \
page those with shell or a util. For touching a few lines of a large file, use edit_file \
instead of re-emitting the whole \
document through write_file. write_file REPLACES wholesale: overwriting an existing file \
outside your working dir is rejected until this run has read it, and deleting a path outside it \
that this run has not read is rejected the same way (a listing or a size read counts)."""),
    (("view_image",), """- view_image: SEE an image, PDF, audio or video file — for attachments \
and files a util produced. When this run's model carries that \
modality the file is shown to you DIRECTLY on the next turn; an image otherwise goes to the \
`vision` util, which describes it (set `prompt` so that fallback is useful) — audio/video have \
no fallback and are refused, naming the 7 MiB raw cap (base64 inflates ~33%)."""),
    (("memory_read", "memory_write"), """- memory_read / memory_write: your persistent topic \
notes under .memory/ — for what was \
EXPENSIVE to find out (environment quirks, working solutions, constraints nobody wrote \
down), not what the instruction or a plain look at the data would tell anyone. \
The state digest \
shows .memory/INDEX.md at run start, clipped — `memory_read name=index` returns it whole; \
consult it — or search your past runs (`args`) — before re-discovering anything; revise notes \
that \
turned out wrong instead of appending contradictions. read_file / write_file are rejected on \
.memory/ paths."""),
    (("read_rule",), """- read_rule: read a GENERAL RULE from the shared library. \
The rules that bind you are \
named in your digest beside when each applies; read one when its moment comes and apply it to \
the case in front of you. Reading one you do NOT hold applies it for the rest of this run \
only; which rules bind you is the user's call, so if one keeps proving necessary, name it in \
your finish summary."""),
    (("write_rule",), """- write_rule: author a NEW general rule, or revise an existing one, in \
the shared library. What you write binds EVERY routine holding that rule from its next run, so \
say what evidence made the current wording wrong. A rule states a principle and names no tool, \
no routine and no file — mechanism belongs in a recipe or a conduct doc. The library linter \
gates the write and your approval level decides whether the user confirms it. There is no \
delete: a rule that should go is a report or a deferred ask_user naming it."""),
    (("llm",), """- llm: one scoped, stateless subcall. It sees ONLY \
your prompt/system — include everything it needs."""),
    (("decide",), """- decide: put a typed question to a DECISION model and get probabilities \
back, never prose. Use it where a \
step is a classification — route, gate, screen, rate — and act on the probability with a \
threshold you can name; it gives no reasons, so ask `llm` when you need one."""),
    (("child",), """- child: start a CHILD RUN — its own run, with its own fresh context, \
working for you. It sees NOTHING of your conversation beyond the "prompt" you give it, and \
relative paths resolve in ITS directory, so name absolute paths for anything it must read. \
Brief it as you would a competent stranger: everything it needs, nothing it must infer."""),
    (("detach",), """- detach: start a LONG background task that OUTLIVES this reply — for a \
big, self-contained job (a \
large scrape, a bulk conversion, a slow build) you want to kick off and keep chatting around. \
Unlike a `child` (which dies when this reply's process ends), a detached task runs as \
its OWN process; when it finishes the engine delivers its result back into this conversation and \
you relay it to the user. It CANNOT ask you blocking questions; \
then `finish` the reply ("started it — I'll report back") and \
do NOT wait. Its status is in state/background.json. Only from a conversation, only for jobs too \
long to finish in this reply — otherwise do the work directly or use a sequential child."""),
    (("schedule_run",), """- schedule_run: arm a ONE-SHOT future run of a routine. \
The daemon fires the one-shot ONCE at fire_at, then \
CONSUMES it (it never \
repeats — no cron to clean up). For a run to schedule its own follow-up ("re-check in 3 days") \
or arm a milestone run on a sibling routine — gated by the scheduling permission."""),
    (("create_routine",), """- create_routine: turn what you and the user have worked out in \
THIS conversation into a real scheduled routine. PRECONDITION — settle the clarification WITH \
the user BEFORE the \
first call: what the routine PRODUCES each run (the artefact by name and where it lands), what \
one finished run leaves behind, whether it ever finishes for good, anything a run must never do, \
which workflow it is built on, and every question its settings pattern asks that the task does \
not already answer. ASK THESE AS DECISIONS, NOT AS PROSE: one `ask_user` per open point, each \
carrying `options`, which the console renders as numbered picks — an option-less question is a \
blank box that makes the user compose an answer you already knew how to offer. The test is \
whether you could QUOTE the user's own answer for each; if you would be inferring one, it is \
still open, so ask it — a guessed draft runs for months on your assumption. The WORKFLOW \
question is always asked this way, and its options are the catalog the draft observation carries \
— which always ends in `generate`, "draft a NEW pattern fitted to this task", because no catalog \
covers every task and "none of these fit" must always be one of the choices; picking it makes \
the confirming call draft that pattern and build on it. It is a TWO-STEP flow: the first call \
stores a DRAFT and returns a preview — put its open points to the user as those decisions and \
finish your reply, or put the go-ahead as a BLOCKING ask_user; after the user answers, either \
way, call it again with the SAME fields to materialize (its own dir, held rules, git repo); a \
changed field updates the draft instead. Tell the user it exists and that its page shows what is \
specific to it as changes awaiting their accept. This is the ONLY way a routine is created. \
WITHOUT a user in the loop (a scheduled run) the same call QUEUES a proposal on the Decisions \
page instead — one call, then carry on; your next run learns from your inbox whether the user \
approved it."""),
    (("manage_lane",), """- manage_lane: manage routine LANES (ordered sets that fire \
back-to-back on one clock) from THIS conversation. The chain fires ONCE, every member in \
order; a routine belongs to at most one lane. A flow with an inbound and an outbound end \
BRACKETS the lane: a dedicated \
inbound-router member placed first and a dedicated outbound-sender member placed last — two \
single-purpose members, never one member run twice. A lane decides only WHEN routines run: what \
a routine may reach (its settings, its roots, the stores it shares) is its own config — propose \
such a change with ask_user + `config_patch`, never here. The routines page manages the same \
store — this is it, reachable from chat. WITHOUT a \
user in the loop (a scheduled run) `list` still answers directly, but every CHANGING verb \
queues a proposal on the Decisions page instead of applying — one call, then carry on with the \
work that does not depend on it; your next run learns from your inbox whether the user \
approved it."""),
    (("job",), """- job: this routine keeps JOBS — standing units of its work, each with its \
own workspace and, often, its own recipe — and this kind is how you manage them. \
GATED PROCESSING: every job your digest lists as DUE this run must be opened and checkpointed \
before the engine lets you finish — one at a time: opening a job while another is open is \
refused, so checkpoint first (outcome `deferred`, saying why, when you set one aside). While a \
job is OPEN your working directory IS its workspace: relative paths, scripts, shell and util \
calls and memory_read / memory_write resolve there, and its recipe (main.md + stages/ in the \
workspace) is authoritative for its work. \
Create a job when standing work comes up that is not one yet; \
never let one job's work hide inside another's checkpoint. (These were `task` before 0.416.0.)"""),
    (("goal",), """- goal: the GOALS of this run — what a PERSON asked of it, each ONE checkable \
end state (b1, b2, …). You are their scribe, never their author: the engine refuses words it \
cannot find in what they wrote, and a goal is dropped on their later words that withdraw it — \
never because it got hard. \
A goal is an outcome, not a step: the steps toward it belong in your plan. An APPROVAL is a \
goal — "yes, do it", "you fix all of that" answering work you proposed: set it quoting that \
answer. Every open goal is answered at your finish; one a routine run leaves open is CARRIED to \
its next run until a run meets it."""),
    (("list_models",), """- list_models: the model catalog + this run's resolved role \
bindings (main / tool_call / uncensored / supervisor), read-only — consult it BEFORE a `model` \
override on llm/child so the name you pass is one the catalog actually carries."""),
    (("children",), """- children: a status table of your child runs, whatever mode each was \
started in (state, turns, elapsed)."""),
    (("kill", "wait"), """- kill: terminate child run "n". wait: block until a child exits or a \
backgrounded call lands — \
AT ONCE when one is unreported, or nothing is running. Children never outlive you — your \
finish kills them."""),
    (("ask_user",), """- ask_user: mode "deferred" (default) files the question and CONTINUES \
— plan around the missing \
answer. Mode "blocking" pauses the run until answered; after {ask_timeout_min} minutes without \
an answer the run CONTINUES on your stated `default` (set it on every blocking ask) and the \
question stays open for a future run. Ask sparingly; batch what can wait until run end — and \
batch it IN ONE ACTION (five asks as five actions cost \
you five turns and him five cards). Mark one `blocks_work: true` when you cannot do the work \
you were fired to do without its answer; that records its class and suspends nothing."""),
    (("report",), """- report: raise something that needs doing and is NOT your task — a \
defect, friction, a
missing or broken tool, a recipe or config that is wrong. Available to EVERY routine, always.
A report with an owner is delivered into that routine's inbox, \
which it reads on its NEXT SCHEDULED RUN — nothing is started and nobody is interrupted. \
Naming no owner is always allowed; guessing wrong sends the work to someone who will bounce it.
A report addressed to YOU arrives in its own prompt section — act on it, or close it by \
reporting back to the sender, IN THIS RUN: once you finish, \
nothing is left that can close it but a person reading the ledger. A reply that completes the \
exchange sets `closes: true` so the thread ends settled — without it your answer is itself a \
new open report waiting for one more reply; a message marked "no reply needed" gets none.
FOLD a row rather than merely naming it: naming leaves it exactly where it was, to be routed \
again next pass, and a fold is also how you add to a thread you already have open instead of \
opening a second one. \
Make a report a DECISION FOR THE USER (at most {ask_options_max} choices) \
the moment you are blocked on a call only he \
can make: a question parked in a plain report reaches no surface of his, so a run that writes \
"waiting on the operator" without options is waiting on nobody. Do not also file an ask_user \
about it.
Past a few open threads to one owner a new report is REFUSED and the open ids are named — fold \
them. Use this for problems you notice in passing, not for your own task's outcome (that \
belongs in your finish summary)."""),
    (("do_nothing",), """- do_nothing: state that NOTHING WAS DUE this run, and why — the \
reasoning goes in `say` (there are no other fields), and it must not be empty: what you \
checked, and what it showed. Use it when a scheduled fire finds its sources unchanged and you \
want that finding on the record as a turn, rather than inventing an action to have done \
something. It establishes nothing, so it does NOT let a finish(ok) stand on its own — the \
honest ending for an idle run is still `finish`, with the same reasoning in the summary."""),
    (("finish",), """- finish: end the run with status ok (the ordinary ending, including a \
run with a shortfall of its own: do what you can, then name what remains), partial (ONLY \
when something you cannot unblock yourself stopped work you could otherwise have done) or \
failed (the job could not be done) — the only part of this \
conversation that survives, \
so the facts have to be in it; a long artefact is a file it names. Decisions are asks, \
never summary prose: one you cannot work without when you meet it (`blocks_work`), the rest in \
one batch before the finish."""),
)


def kind_bullets(kinds: list[str] | set[str], *, util_confirm: str = "",
                 ask_timeout_min: object = "") -> str:
    """The `Action kinds:` block, carrying only the bullets for `kinds`."""
    allowed = set(kinds)
    out = [prose for covers, prose in KIND_PROSE if allowed & set(covers)]
    return ("\n".join(out).replace("{util_confirm}", util_confirm)
            .replace("{ask_timeout_min}", str(ask_timeout_min))
            # the validator's own constant, so the prose and the refusal cannot drift apart
            .replace("{ask_options_max}", str(ASK_OPTIONS_MAX)))
