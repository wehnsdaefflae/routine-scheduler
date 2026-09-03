"""The per-kind PROMPT surface: which action kinds a run may actually use, and the
projection of ACTION_SCHEMA onto them.

`actions.py` stays the single source of truth for what a turn may do — this module only
NARROWS what the model is shown to what the engine would accept anyway. A run whose
workflow `tools:` allowlist and capabilities permit 8 of the 27 kinds was previously sent
all 27 in the schema (8k chars, ~36% of the fixed prompt) plus a prose bullet each: the
model read, every turn, the full description of channels the validator would reject. The
projection is derived from `actions.KIND_FIELDS` — the same map `validate_action` builds
its allowed-field set from — so the shown schema and the enforced contract cannot drift.

Two consumers, one derivation: the composed prompt (composer.build_system_prompt) and the
endpoint's constrained-decoding schema (completion.next_action). Sending the narrowed
schema to the transport also makes a disallowed kind UNGENERATABLE rather than generated
and then rejected — a saved turn, not just saved tokens. Validation keeps the FULL schema
(control.run_user_command, the retry cycle): rejecting a well-formed action the run wasn't
allowed to take must stay a precise, teaching denial, not a schema parse error.
"""

from __future__ import annotations

import copy

from .actions import ALWAYS_KINDS, KIND_FIELDS
from .actionschema import ACTION_SCHEMA, KINDS

# Fields every kind carries (actions.validate_action allows `note` on any kind, like `say`).
_UNIVERSAL_FIELDS = ("say", "note", "kind")

_CLAUSE_SEP = " · "


def effective_kinds(allowed_kinds: set[str] | None = None, grants=None) -> list[str]:
    """The kinds this run may actually emit: workflow `tools:` ∩ (base ∪ capabilities),
    plus ALWAYS_KINDS. In KINDS order, so every surface lists them the same way.

    The one owner of this computation — capabilities.py, the composer's prose bullets and
    the schema projection all read it here, so the CAPABILITIES section can never advertise
    a kind the schema omits (or vice versa).
    """
    return [k for k in KINDS
            if (allowed_kinds is None or k in allowed_kinds or k in ALWAYS_KINDS)
            and (grants is None or grants.allows_kind(k))]


def _clause_kinds(clause: str) -> set[str]:
    """The action kinds a description clause is about, from its `kind1/kind2: …` lead.

    Tolerant by design: a lead may carry extra words ("write_util edit mode: …"), so each
    slash-separated part contributes its FIRST token. An unrecognized lead yields the empty
    set, which `_project_description` treats as "keep" — trimming never drops prose it did
    not positively understand.
    """
    head, sep, _ = clause.partition(": ")
    if not sep:
        return set()
    return {tok for part in head.split("/")
            if (tok := part.strip().split(" ")[0]) in KINDS}


def _project_description(description: str, kinds: set[str]) -> str:
    """Drop the ` · `-separated clauses that speak only to kinds this run cannot use."""
    clauses = description.split(_CLAUSE_SEP)
    kept = [c for c in clauses if not (found := _clause_kinds(c)) or (found & kinds)]
    # All clauses were kind-specific and none matched: the property is being kept for a
    # reason the lead didn't express — keep the text rather than ship an empty description.
    return _CLAUSE_SEP.join(kept) if kept else description


def schema_for_kinds(kinds: list[str] | set[str] | None) -> dict:
    """ACTION_SCHEMA narrowed to `kinds`: the `kind` enum, the properties those kinds use
    (per `KIND_FIELDS`), and each surviving description trimmed to its relevant clauses.

    `None` (or the full set) returns the schema unchanged — a run with everything enabled
    pays nothing for the machinery, and the prompt-caching contract sees the same bytes.
    """
    if kinds is None:
        return ACTION_SCHEMA
    keep = {k for k in kinds if k in KIND_FIELDS} | set(ALWAYS_KINDS)
    if keep >= set(KINDS):
        return ACTION_SCHEMA
    fields = set(_UNIVERSAL_FIELDS)
    for kind in keep:
        required, optional = KIND_FIELDS[kind]
        fields.update(required)
        fields.update(optional)
    out = copy.deepcopy(ACTION_SCHEMA)
    # filter the DEEPCOPY's specs, never the original's: taking them from ACTION_SCHEMA
    # here handed the description-trimming loop below references into the shared global,
    # so every projection permanently trimmed the full schema for the whole process
    # (cross-run contamination in the daemon; surfaced by test_projection_is_materially_
    # smaller going order-dependent).
    out["properties"] = {
        name: spec for name, spec in out["properties"].items() if name in fields
    }
    for name, spec in out["properties"].items():
        if name not in _UNIVERSAL_FIELDS and isinstance(spec.get("description"), str):
            spec["description"] = _project_description(spec["description"], keep)
    out["properties"]["kind"] = {**ACTION_SCHEMA["properties"]["kind"],
                                 "enum": [k for k in KINDS if k in keep]}
    return out


# The harness contract's per-kind bullets, keyed by the kinds each one covers (a few
# bullets describe a family — read_file/write_file/edit_file share one). Same filter as the
# schema: a run is told how to use the channels it HAS, and nothing about the ones it
# doesn't. The memory bullet was already grant-conditional in the composer; making every
# bullet conditional generalizes that precedent instead of adding a second mechanism.
#
# Two placeholders are substituted (not f-string interpolated — the prose contains braces
# in code fragments): {util_confirm} and {ask_timeout_min}.
KIND_PROSE: tuple[tuple[tuple[str, ...], str], ...] = (
    (("util",), """- util: run a global util — name + optional args (append "--json" for \
structured output).
Utils are your primary tools — the CAPABILITIES section below lists what exists (name + \
summary); for ONE util's exact usage run `util name=list args=["<util-name>"]` before relying \
on it (bare name=list re-dumps the whole catalog you already have). Observation = exit code + \
captured output."""),
    (("shell",), """- shell: run ONE ad-hoc command on the host — `command` (a single \
string, handed to \
`bash -c`, so pipes and `&&` work), optional `timeout_s` (default 120) and `path` (a working \
directory; default yours). It runs NON-INTERACTIVELY inside your sandbox — the same filesystem \
jail your utils get, and no secret from the store reaches it — so anything that waits for a \
keystroke never returns, and a credential-needing command belongs in a util instead. \
Observation = exit code + captured output (capped; the overflow is saved to a file the \
observation names). This is the ESCAPE HATCH, not a tool: use it for the one-off look no util \
covers, and the moment you run something a SECOND time, write the util (or a scripts/ helper) \
that does it properly."""),
    (("write_util",), """- write_util: create or revise a global util — name (kebab-case) \
+ content (a complete
PEP 723 script: `# /// script` deps block, a module docstring whose first line is
`<name> — <one-line summary>` then a `usage:` line, a `--json` flag, a `--selftest` that runs
built-in checks, data on stdout / diagnostics on stderr / exit 0 on success; on invalid or
missing arguments it MUST print its own usage line to stderr and exit 2 — an error that
doesn't teach the correct call wastes every future caller's turn). The engine runs
`--selftest` and only commits if it passes. To REVISE an existing util surgically, pass
`anchor`/`replacement` INSTEAD of content — a verbatim in-place patch like edit_file, so a \
small fix never re-emits the whole script (read the current source first: `util name=show \
args=["<name>", "--full"]`). A util may call sibling utils via `gu <name>` — \
declare those on a `calls: <name>, …` header line. If it \
needs a secret (token, password, API key), read it env-first — `os.environ["NAME"]` — never \
hardcode or prompt for it, AND declare the names in a header `secrets: NAME1, NAME2` line so the \
UI tells the user what to set (they set it once in the Secrets store; the engine injects it — \
ONLY declared secrets reach the util). Declare network use with a `net: outbound` (or \
`net: none`) header line: utils run in a filesystem/network sandbox and an undeclared \
network need fails. Declare filesystem use the same way, on an `fs:` header line — \
`fs: roots` when the util opens paths its CALLER passes it (the common case), `fs: none` when \
it touches no file outside its own temp space, or `fs: rw <path>` / `fs: ro <path>` for a \
private store the util reaches on its own (a session directory, a state file). A declared \
path is mounted only when the routine was already granted it, so declaring one asks for \
nothing — it narrows what this util sees, and keeps a store like that out of every OTHER \
util's jail.{util_confirm}"""),
    (("remove_util",), """- remove_util: delete a global util the library no longer needs — \
name (kebab-case). The \
curation counterpart to write_util, gated by the same util-authoring capability (and, unless \
that capability is fully autonomous, the same approval step). The engine REFUSES if any other \
util still declares it on a `calls:` line — remove or update those callers first; the deletion \
is committed to the library and stays recoverable from git history. Check the catalog before \
removing something another routine relies on."""),
    (("read_file", "write_file", "edit_file"), """- read_file / write_file / edit_file: read \
or write a file (within the working dir or an \
allowed root). read_file takes `path` or `paths` (several files in ONE action — batch related \
reads instead of spending a turn per file). edit_file replaces an exact `anchor` string with \
`replacement` IN PLACE — for touching a few lines of a large file, use it instead of \
re-emitting the whole document through write_file. write_file REPLACES wholesale: overwriting \
an existing file outside your working dir is rejected until this run has read it."""),
    (("view_image",), """- view_image: SEE an image or PDF (png/jpeg/webp/gif/pdf) at `path` \
(or `paths`) — for \
attachments and files a util produced. When this run's model is multimodal the file is shown \
to you DIRECTLY on the next turn; otherwise the `vision` util describes it and you get text \
back. Set `prompt` (what to look for) so that fallback is useful."""),
    (("memory_read", "memory_write"), """- memory_read / memory_write: your persistent topic \
notes under .memory/ — for what was \
EXPENSIVE to find out (environment quirks, working solutions, constraints nobody wrote \
down), not what the instruction or a plain look at the data would tell anyone. \
memory_write(name, content, about) writes ONE kebab-named note of at most 100 lines and \
the engine maintains .memory/INDEX.md from `about`; delete: true removes a note. \
memory_read(name) returns one. The state digest shows the INDEX at run start — consult it \
before re-discovering anything; revise notes that turned out wrong instead of appending \
contradictions. read_file / write_file are rejected on .memory/ paths."""),
    (("read_rule",), """- read_rule: read a GENERAL RULE from the shared library — \
`name: "list"` for the catalog, `name: "<slug>"` for one rule's prose. The rules that bind you are \
named in Standing practices; read one before the situation it governs and apply it to the case in \
front of you. Reading one you do NOT hold applies it for the rest of this run only; which rules \
bind you is the user's call, so if one keeps proving necessary, name it in your finish \
summary."""),
    (("write_rule",), """- write_rule: author a NEW general rule, or revise an existing one, in \
the shared library — `content` (the complete rule markdown) to create, `anchor` + `replacement` \
to revise in place. What you write binds EVERY routine holding that rule from its next run, so \
say what evidence made the current wording wrong. A rule states a principle and names no tool, \
no routine and no file — mechanism belongs in a recipe or a conduct doc. The library linter \
gates the write and your approval level decides whether the user confirms it. There is no \
delete: a rule that should go is a report or a deferred ask_user naming it."""),
    (("llm",), """- llm: one scoped, stateless LLM subcall (default: this routine's tool-call \
model; `model` overrides per call — a role or a catalog model name, `list_models` shows them). \
It sees ONLY \
your prompt/system — include everything it needs; set response_schema for structured replies."""),
    (("spawn",), """- spawn: start a CHILD RUN scheduled in PARALLEL with you. Every child \
run — however \
scheduled — works the same way: its OWN directory (runs/<ts>/sub/<n>/, NOT your working tree — \
R405/R406), its OWN budget sliced from your remainder, its own fresh context and pattern, and it \
sees NOTHING of your conversation beyond the "prompt" you give it. So relative paths resolve \
THERE: name absolute paths (within the allowed roots) for anything it must read. It hands back \
its finish summary always, and hands back FILES by writing them into its own artifacts/ — the \
engine copies those into YOUR artifacts/from-sub-<n>/ when it exits and names the landed paths, \
so you never go looking in its dir. Parallel is the mode where you KEEP WORKING while it runs \
and are notified when it exits. Pick its "workflow" for the child's PURPOSE from the patterns \
listed under CAPABILITIES (default general-task) and give it a fully self-contained "prompt". A \
child runs on your MAIN model unless `model` picks a role or a catalog model for it."""),
    (("subtask",), """- subtask: start a CHILD RUN scheduled SEQUENTIALLY — the same child run \
`spawn` starts \
(own directory, own budget, own fresh context and pattern, hands files back through its own \
artifacts/ into your artifacts/from-sub-<n>/), differing ONLY in how you schedule it. Use it to \
decompose a large task into ordered steps. It does NOT block you: to keep the order, `wait` for \
it (n=N) before starting the next one and fold its result into that brief — the wait YIELDS if \
the user writes (so the conversation stays live) and you are notified when it finishes; or do \
other work meanwhile. Pick its "workflow" for that step's purpose (or omit for the default, or \
"generate" to DRAFT one when none fits — only if that capability is enabled); give a \
self-contained "prompt"; "turns" bounds it (default: half your remaining). It runs on the \
routine's MAIN model unless `model` picks a role or a catalog model for it."""),
    (("detach",), """- detach: start a LONG background task that OUTLIVES this reply — for a \
big, self-contained job (a \
large scrape, a bulk conversion, a slow build) you want to kick off and keep chatting around. \
Unlike spawn/subtask (children that die when this reply's process ends), a detached task runs as \
its OWN process; when it finishes the engine delivers its result back into this conversation and \
you relay it to the user. Give a complete self-contained "prompt" (it CANNOT ask you blocking \
questions) and pick its "workflow"; then `finish` the reply ("started it — I'll report back") and \
do NOT wait. Its status is in state/background.json. Only from a conversation, only for jobs too \
long to finish in this reply — otherwise do the work directly or use subtask."""),
    (("schedule_run",), """- schedule_run: arm a ONE-SHOT future run of a routine — `target` \
(the routine slug, \
self-target always allowed), `fire_at` (an absolute ISO-8601 UTC instant or a relative offset \
like "+3d" / "+2h" / "+30m"), `reason` (a provenance line injected into the target's inbox just \
before it fires). The daemon fires the one-shot ONCE at fire_at, then CONSUMES it (it never \
repeats — no cron to clean up). Cancel with `cancel: true` (+ `id` for one, or without to clear \
all armed on the target). For a run to schedule its own follow-up ("re-check in 3 days") or arm \
a milestone run on a sibling routine — gated by the scheduling permission."""),
    (("create_routine",), """- create_routine: turn what you and the user have worked out in \
THIS conversation into a real \
scheduled routine — `target` (its new kebab-case slug), `name` (its display name), `prompt` (the \
clarified task, decomposed into the routine's stages — say WHAT it does, not when), and optional \
`workflow` (the library pattern to build from, default general-task). PRECONDITION — settle the \
clarification WITH the user BEFORE the first call: what the routine PRODUCES each run (the \
artefact, named, and where it lands), what "done" looks like for ONE run, and which pattern it \
is built on. ASK THESE AS DECISIONS, NOT AS PROSE: one `ask_user` per open point, each carrying \
`options`, which the console renders as numbered picks — an option-less question is a blank box \
that makes the user compose an answer you already knew how to offer. The test is whether you \
could QUOTE the user's own answer for each; if you would be inferring one, it is still open, so \
ask it. A draft that guesses presents your assumption as their decision, and the routine then \
runs for months on it. The WORKFLOW question is always asked this way, and its options are the \
catalog the draft observation carries — which always ends in `generate`, "draft a NEW pattern \
fitted to this task", because no catalog covers every task and "none of these fit" must always \
be one of the choices; picking it makes the confirming call draft that pattern and build on it. \
It is a TWO-STEP flow: the first call stores a DRAFT and returns a preview — put its open \
points to the user as those decisions and finish your reply; after the user answers, call it \
again with the SAME fields to materialize (its own dir, its held rules, git repo). A changed \
field updates the draft instead. \
The daemon picks the new routine up on its next registry rescan; tell the user it exists and \
what to set next (its schedule). This is the ONLY way a routine is created. WITHOUT a user in \
the loop (a scheduled run) the same call QUEUES a proposal on the Decisions page instead of \
creating anything — one call, then carry on with the work that does not depend on the routine \
existing; your next run learns from your inbox whether the user approved it."""),
    (("manage_group",), """- manage_group: manage routine GROUPS (ordered collections that fire \
back-to-back) from THIS conversation via a `verb`: list (the whole store), create (`name` + \
optional `members` + `on_failure` + `cron`), update (`target` = the group id, plus any \
of name/members/on_failure/cron/paused), delete (`target`), set-default (`on_failure` = \
stop|continue, the instance-wide mid-chain-failure default), run (`target` — arm a sequential \
fire the daemon runs on its next tick). `members` is the ORDERED routine slugs and each must \
name a real routine; the chain fires ONCE, every member in order. A flow with an inbound and an \
outbound end BRACKETS the group: a dedicated inbound-router member placed first and a dedicated \
outbound-sender member placed last — two single-purpose members, never one member run twice. \
The routines page manages the same store — this is it, reachable from chat. WITHOUT a user in \
the loop (a scheduled run) `list` still answers directly, but every CHANGING verb queues a \
proposal on the Decisions page instead of applying — one call, and your next run learns from \
your inbox whether the user approved it."""),
    (("list_models",), """- list_models: the model catalog + this run's resolved role \
bindings (main / tool_call / uncensored), read-only — consult it BEFORE setting a `model` \
override on llm/spawn/subtask so the name you pass is one the catalog actually carries."""),
    (("subruns",), """- subruns: a status table of your child runs, whatever mode each was \
started in (state, turns, elapsed)."""),
    (("kill", "wait"), """- kill: terminate child run "n". wait: block until child run \
"n" / "all": true / any \
unreported exit (timeout_s, default 600) — it returns AT ONCE when a finished child hasn't \
been reported to you yet, or when nothing is running. Children never outlive you — your \
finish kills them."""),
    (("ask_user",), """- ask_user: mode "deferred" (default) files the question and CONTINUES \
— plan around the missing \
answer. Mode "blocking" pauses the run until answered; after {ask_timeout_min} minutes without \
an answer the run CONTINUES on your stated `default` (set it on every blocking ask) and the \
question stays open for a future run. Ask sparingly; batch what can wait until run end."""),
    (("report",), """- report: raise something that needs doing and is NOT your task — a \
defect, friction, a
missing or broken tool, a recipe or config that is wrong. `title` (one line) + `detail` (the \
artefact, what is wrong, the evidence — a run id, a `path:line`, an error — and what "done" \
looks like; whoever picks it up has none of your context). Available to EVERY routine, always.
Set `target` to the routine that OWNS the problem and the report is delivered into its inbox, \
which it reads on its NEXT SCHEDULED RUN — nothing is started and nobody is interrupted. Leave \
`target` out when you cannot name the owner: the report goes to triage and is routed for you. \
Omitting it is always allowed; guessing wrong sends the work to someone who will bounce it.
A report addressed to YOU arrives in its own prompt section — act on it, or close it by \
reporting back to the sender with `answers` set to its id. A reply that completes the \
exchange sets `closes: true` so the thread ends settled — without it your answer is itself a \
new open report waiting for one more reply; a message marked "no reply needed" gets none. Use \
this for problems you notice in passing, not for your own task's outcome (that belongs in \
your finish summary)."""),
    (("finish",), """- finish: end the run with status ok|partial|failed and a DETAILED 8-20 \
line summary: concrete \
outcomes (numbers, names, links), decisions taken and why, what changed on disk, open ends and \
what the next run should pick up. That summary is what the user and the next run see — it is \
the ONLY part of this conversation that survives, so err on the side of detail. It renders as \
Markdown in the UI, including GitHub-style pipe tables and > blockquotes — give tabular results \
(shortlists, comparisons, digests) a real pipe table instead of ASCII art."""),
)


def kind_bullets(kinds: list[str] | set[str], *, util_confirm: str = "",
                 ask_timeout_min: object = "") -> str:
    """The `Action kinds:` block, carrying only the bullets for `kinds`."""
    allowed = set(kinds)
    out = [prose for covers, prose in KIND_PROSE if allowed & set(covers)]
    return ("\n".join(out).replace("{util_confirm}", util_confirm)
            .replace("{ask_timeout_min}", str(ask_timeout_min)))
