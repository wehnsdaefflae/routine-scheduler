"""The per-kind PROMPT surface: which action kinds a run may actually use, and the
projection of ACTION_SCHEMA onto them.

`actions.py` stays the single source of truth for what a turn may do — this module only
NARROWS what the model is shown to what the engine would accept anyway. A run whose
workflow `tools:` allowlist and capabilities permit 8 of the 21 kinds was previously sent
all 21 in the schema (8k chars, ~36% of the fixed prompt) plus a prose bullet each: the
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

from .actions import ACTION_SCHEMA, ALWAYS_KINDS, KIND_FIELDS, KINDS

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
    out["properties"] = {
        name: spec for name, spec in ACTION_SCHEMA["properties"].items() if name in fields
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
    (("write_util",), """- write_util: create or revise a global util — name (kebab-case) \
+ content (a complete
PEP 723 script: `# /// script` deps block, a module docstring whose first line is
`<name> — <one-line summary>` then a `usage:` line, a `--json` flag, a `--selftest` that runs
built-in checks, data on stdout / diagnostics on stderr / exit 0 on success; on invalid or
missing arguments it MUST print its own usage line to stderr and exit 2 — an error that
doesn't teach the correct call wastes every future caller's turn). The engine runs
`--selftest` and only commits if it passes; a util may call sibling utils via `gu <name>` — \
declare those on a `calls: <name>, …` header line. If it \
needs a secret (token, password, API key), read it env-first — `os.environ["NAME"]` — never \
hardcode or prompt for it, AND declare the names in a header `secrets: NAME1, NAME2` line so the \
UI tells the user what to set (they set it once in the Secrets store; the engine injects it — \
ONLY declared secrets reach the util). Declare network use with a `net: outbound` (or \
`net: none`) header line: utils run in a filesystem/network sandbox and an undeclared \
network need fails.{util_confirm}"""),
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
    (("read_trait",), """- read_trait: CONSULT a practice module from the shared library that \
you do not already hold — \
`name: "list"` for the catalog, `name: "<slug>"` for one module's prose. It applies for the rest \
of THIS run only and is never added to your recipe (your traits/ set is the user's to change); if \
one keeps proving necessary, name it in your finish summary."""),
    (("llm",), """- llm: one scoped, stateless LLM subcall (runs on this routine's tool-call \
model). It sees ONLY \
your prompt/system — include everything it needs; set response_schema for structured replies."""),
    (("spawn",), """- spawn: start a SUB-WORKFLOW that runs IN PARALLEL with you — pick its \
"workflow" for the \
child's PURPOSE from the patterns listed under CAPABILITIES (default general-task) and give it \
a fully self-contained "prompt" as its instruction; it sees nothing else and returns only its \
finish summary. You keep working while it runs; you are notified automatically when it exits. \
Give parallel children disjoint outputs (they share your working directory); they must not \
write LEDGER.md or state/phase.json."""),
    (("subtask",), """- subtask: start a child sub-workflow that runs SEQUENTIALLY in the \
background — decompose a large \
task into ordered steps, each a fresh-context child run with its OWN budget and pattern. It does \
NOT block you: to keep sequential order, `wait` for it (n=N) before starting the next subtask and \
fold its result into that brief — the wait YIELDS if the user writes (so the conversation stays \
live) and you are notified when it finishes; or do other work meanwhile. Pick its "workflow" for \
that step's purpose (or omit for the default, or "generate" to DRAFT one when none fits — only if \
that capability is enabled); give a self-contained "prompt"; "turns" bounds it (default: half your \
remaining). Unlike a plain workflow step it runs on its own context window + pattern."""),
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
    (("subruns",), """- subruns: a status table of your sub-workflows (state, turns, \
elapsed)."""),
    (("kill", "wait"), """- kill: terminate sub-workflow "n". wait: block until sub-workflow \
"n" / "all": true / any \
unreported exit (timeout_s, default 600) — it returns AT ONCE when a finished child hasn't \
been reported to you yet, or when nothing is running. Children never outlive you — your \
finish kills them."""),
    (("ask_user",), """- ask_user: mode "deferred" (default) files the question and CONTINUES \
— plan around the missing \
answer. Mode "blocking" pauses the run until answered; after {ask_timeout_min} minutes without \
an answer the run CONTINUES on your stated `default` (set it on every blocking ask) and the \
question stays open for a future run. Ask sparingly; batch what can wait until run end."""),
    (("report_bug",), """- report_bug: file a bug report about the SCHEDULER itself (the \
engine, a util's CLI, the web \
UI, a workflow) — a defect or friction you hit while running. `title` (one line) + optional \
`detail` (what you did, what happened, what you expected). It appends to a shared bug stream the \
self-audit routine reads every run and turns into findings; it does NOT interrupt anyone or reach \
the user. Available to EVERY routine by default (no capability needed). Use it for scheduler \
defects you notice in passing — not for your own task's problems (those go in your finish \
summary)."""),
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
