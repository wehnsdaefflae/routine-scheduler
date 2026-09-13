// Option ladders and explanatory copy for the abilities panel.
export const CONFIRM_OPTIONS = [
  ["off", "off — engine rejects write_util"],
  ["always", "on — every create/revise asks you"],
  ["creations", "on — new utils ask; revisions are autonomous"],
  ["never", "on — fully autonomous (selftest-gated)"],
];
export const RULE_CONFIRM_OPTIONS = [
  ["off", "off — engine rejects write_rule"],
  ["always", "on — every rule change asks you"],
  ["creations", "on — new rules ask; revisions are autonomous"],
  ["never", "on — fully autonomous (lint-gated)"],
];
export const RUNS_OPTIONS = [
  ["none", "off — previous runs unreadable"],
  ["last", "the last run only"],
  ["all", "all previous runs"],
];
export const RUNS_RANK = { none: 0, last: 1, all: 2 };
export const WF_OPTIONS = [
  ["catalog", "catalog — pick existing patterns only"],
  ["generate", "generate — also draft a new pattern when none fits"],
];
export const WF_RANK = { catalog: 0, generate: 1 };
// ONE control for the reminder layer, because the two dials behind it are one decision: which
// stores the routine reads, and — only once it reaches the shared one — who approves a write
// there. A `local` routine has nothing to approve: its own store is autonomous by design.
export const REMINDERS_OPTIONS = [
  ["off", "off — nothing is stored, nothing is held"],
  ["local", "its own store — cautions it wrote for itself"],
  ["global:always", "+ the shared store — every shared change asks you"],
  ["global:creations", "+ the shared store — new ones ask; edits are autonomous"],
  ["global:never", "+ the shared store — fully autonomous"],
];
export const REM_RANK = { none: 0, local: 1, global: 2 };

// What a gated capability MEANS, with a concrete example — a bare action kind told the reader
// nothing (F178, user order 2026-07-23). Kept verbatim from the panel this replaces.
export const ACTION_HELP = {
  write_util: "create or revise the shared global utils every routine can call — e.g. write a "
    + "`pdf-stamp` script once and every routine can sign PDFs from then on",
  remove_util: "retire a global util from the shared library (refused while another util still "
    + "calls it) — e.g. delete a scraper after its site shut down",
  revise_util: "change an existing global util in place, without being able to create new ones",
  memory_read: "read the routine's .memory/ notebook — facts earlier runs paid to learn, "
    + "e.g. \"the API rejects requests without a language header\"",
  memory_write: "add or revise .memory/ notes when reality contradicts an assumption",
  detach: "start a long background job that outlives the current reply — e.g. kick off a "
    + "two-hour bulk conversion, keep chatting, and the result is delivered back when it finishes",
  schedule_run: "arm a one-shot future run of this or a sibling routine — e.g. \"re-check the "
    + "parcel status in 3 days\" instead of waiting for the next scheduled fire",
  write_rule: "author or revise a general rule in the shared library — the text every routine "
    + "holding that rule follows from its next run",
  script: "run the routine's own persistent scripts/<name>.py helpers",
  shell: "run one arbitrary command on the host — the escape hatch around the util library, "
    + "e.g. a quick `git log` no util covers. It runs in the same sandbox a util does; "
    + "anything the routine does twice should become a proper util instead",
  write_recipe: "rewrite this routine's OWN instructions — main.md, stages/ and tuning.yaml. "
    + "Its config (routine.yaml) stays sealed either way. Hold it where refining the recipe "
    + "IS the job; an ordinary routine reports a wrong instruction instead of rewording it",
};
export const UTIL_HELP = {
  discord: "the phone channel: blocking questions mirror to Discord and are answerable in one "
    + "reply — e.g. \"apply to this project? approve / decline\" reaches you away from the console",
  remote: "act on a bound remote machine over SSH — e.g. fetch a file from the NAS or restart "
    + "a service on another box",
};
// A reserved util a held doc requires that the library does not have. The mapping says on, so
// the row would otherwise read as satisfied while every call fails — this is the line the
// surface's `install_util` diagnosis lands on, carrying the sentence that closes it.
export const ABSENT_UTIL = "no util by that name is in the library — every call fails. Only a run "
  + "writes a util, so the act here is unticking this ability.";
export const KIND_LABEL = { "secret": "secret", "fs-write": "write root", "fs-read": "read root",
                     "machine": "machine", "connection": "connection", "util": "util",
                     "action": "action", "permission": "conduct" };

