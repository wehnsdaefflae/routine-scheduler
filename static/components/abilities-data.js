// Option ladders and explanatory copy for the abilities panel.
//
// Every ladder is the SERVER's vocabulary for its key — grants.CONFIRM_LEVELS, RUN_HISTORY_LEVELS,
// reminders.LEVELS, configdial.LEVELS — value for value and in its order, each with its one line
// of help. An option
// the server refuses is a save that 422s; a held value with no option is a dial that cannot show
// what the routine holds (tests/ui/test_ability_settings.py holds both ends). The approval
// ladders once led with an `off` the server has never accepted: a write_util that is off is a
// missing ACTION, not an approval level.
//
// A doc's card carries the approval dial its gated action rides on. The other five are
// SETTINGS no doc switches on (a doc requires actions and utils alone), so they share a card of
// their own: how far back a run reads, the reminder layer, who approves a shared reminder, who
// approves the routine's own CONFIG changes, and the job layer. `config_confirm` is on that
// card rather than a doc's because no permission switches it on: every routine may propose a
// config change on an `ask_user`, so the dial governs something for all of them.
//
// An option marked `heldOnly` is never OFFERED, only shown while it is what the mapping holds:
// `runs: none` is a child run's scope, and a routine's own run reads its last run regardless
// (D96), so choosing it would change nothing.
export const CONFIRM_OPTIONS = [
  ["always", "on — every create/revise asks you"],
  ["creations", "on — new utils ask; revisions are autonomous"],
  ["never", "on — fully autonomous (selftest-gated)"],
];
export const RULE_CONFIRM_OPTIONS = [
  ["always", "on — every rule change asks you"],
  ["creations", "on — new rules ask; revisions are autonomous"],
  ["never", "on — fully autonomous (lint-gated)"],
];
export const RUNS_OPTIONS = [
  ["none", "none — the same as last: its previous run", { heldOnly: true }],
  ["last", "last — its previous run only"],
  ["all", "all — every run it keeps (longitudinal work)"],
];
export const REMINDER_OPTIONS = [
  ["none", "none — off: no reminder holds an action"],
  ["local", "local — leaves its own; heeds the shared ones"],
  ["global", "global — also writes the shared ones, as curator"],
];
// The JOB layer (rsched/jobs.py, docs/jobs.md): on, the routine keeps its standing work as
// jobs — the `job` action exists for it, an open job's workspace is the run's working
// directory, and a run may not finish while a job due in it has no checkpoint.
export const JOB_OPTIONS = [
  ["off", "off — one recipe, no job list"],
  ["on", "on — keeps jobs; every due job is processed before a run ends"],
];
// Who approves a write to the library's SHARED reminders — revealed only at `global`, the one
// level that can make one.
export const REMIND_CONFIRM_OPTIONS = [
  ["always", "on — every shared reminder it writes asks you"],
  ["creations", "on — new ones ask; revising or deleting doesn't"],
  ["never", "on — fully autonomous (regex-checked)"],
];
// Who approves a config change the routine proposes for ITSELF (rsched/configdial.py, D191).
// A run can never write routine.yaml: it files an `ask_user` carrying a `config_patch` and the
// Decisions page applies it with a click. `own_fields` is an allow-list of knobs whose blast
// radius is the routine's own run (budgets, efforts, deliberation, run retention, the ladder's
// two numbers); `never` widens that to every patch field that is not SEALED — so it also
// reaches the schedule, the run gate, the rules and the outcome contract, which is why it is a
// deliberate choice rather than the convenient one. The SEALED set (permissions, capabilities,
// the fs roots, the models, the trial, the grants, the connections, the machines) is absolute
// at every level: the dial can never hand a routine its own authority.
export const CONFIG_CONFIRM_OPTIONS = [
  ["always", "on — every config change it proposes is your decision"],
  ["own_fields", "on — it applies its OWN budgets, efforts, effort level, retention & the "
    + "ladder's two turn numbers; the rest asks"],
  ["never", "on — it applies any unsealed field of its own config (schedule and outcome included)"],
];
// Who may revise THIS routine's recipe (rsched/revisedial.py, D198) — the dial of the routine
// whose instructions would change, never the one proposing the change. A proposer needs
// recipe-authoring to propose at all; this says whether his click stands between the proposal
// and the recipe. Either way the EDIT is made by this routine's own next run, under the one-leg
// unlock, and it may refuse a line that is wrong about what its runs do. routine.yaml stays
// sealed at every level.
export const REVISE_CONFIRM_OPTIONS = [
  ["always", "on — a revision another routine proposes for this recipe is your decision"],
  ["invited", "on — a recipe-authoring routine's revision is handed to its next run without "
    + "asking you"],
];
// What a mapping that leaves a setting out holds once saved: the server fills a missing key
// with its all-off value (grants.SETTING_DEFAULTS, through capabilities_for), so a dial
// resting on it shows what the save would write. Every read the panel is given fills all eight.
export const SETTING_DEFAULTS = {
  confirm: "always", rule_confirm: "always", remind_confirm: "always",
  config_confirm: "always", revise_confirm: "always",
  runs: "none", reminders: "none", jobs: "off",
};

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

