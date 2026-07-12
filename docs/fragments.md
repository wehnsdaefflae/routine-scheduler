# Fragments: standards & grants

A **fragment** is a reusable standard a routine toggles on: a short markdown document whose
prose is inlined into every run's system prompt ("STANDARD PRACTICES"). Since the 2026-07
revision a fragment is also the **only way a routine gains a permission**: its frontmatter
may carry a `grants:` key that the engine enforces. One switch — the routine's
`fragments:` list — controls both the norm and the capability, so "what may this routine
do" is answered by one glance at its Standards panel.

## The authority model

Three parties, three responsibilities:

| where | role |
|---|---|
| the **library** copy (`<libraries_home>/fragments/<slug>.md`) | the ONLY source of `grants:` — machine-read at run start |
| the routine's `routine.yaml` `fragments:` list | activation: which fragments (prose + grants) apply |
| the routine-local copy (`<routine>/fragments/<slug>.md`) | freely editable prose, inlined into the prompt — **never** consulted for grants |

A routine can rewrite its local fragment prose however it likes (and its runs can too — the
engine commits the working dir); none of that changes what it is allowed to do. Activating a
fragment is a `routine.yaml` edit, which the web UI only permits while no run is active.
Enforcement is therefore provably independent of anything the routine itself can write.

## The grants schema

```yaml
---
tags: [tool-use, utils, authoring]
grants:
  actions: [util, write_util]   # action kinds this fragment unlocks
  utils: [discord]              # utils reserved for routines carrying this grant
  confirm: true                 # write_util approval: true | false | revisions-only
---
# fragment: <slug> — <summary>
...prose...
```

- **`actions`** — action kinds. Only *gated* kinds are enforced (today: `write_util`);
  listing a base kind like `util` is declarative, for the capability panel.
- **`utils`** — naming a util in ANY library fragment's `utils:` list *reserves* it: from
  then on only routines with such a fragment active may run it. Utils named nowhere stay
  open to every routine.
- **`confirm`** — the `write_util` approval policy. `true`: the user approves every
  create/revise (a blocking question is filed automatically). `revisions-only`: revising an
  existing util is auto-approved once its selftest passes; creating a NEW util still asks.
  `false`: fully autonomous (still selftest-gated and committed). When several active
  fragments grant `write_util`, the most permissive confirm wins; a granting fragment that
  sets none means `true`.

`workflows/lint.py` validates the schema on every library edit (the web editor rejects a
malformed `grants:` block), and `rsched lint` covers the whole library.

## What is gated — and what is not

A run's allowed action kinds are **workflow `tools:` ∩ (base ∪ union of active grants)**.
Base is everything except the gated kinds, so existing routines keep working: `read_file`,
`write_file`, `llm`, `spawn`, and plain `util` calls are never gated by fragments. Gated
are exactly the capabilities that were already gated or reach outside the system:

| capability | granted by | notes |
|---|---|---|
| `write_util` (create/revise global utils) | `util-authoring` (confirm: true), `util-authoring-autonomous` (confirm: revisions-only), `util-authoring-full-auto` (confirm: false) | in `DEFAULT_FRAGMENTS` via `util-authoring`, so new routines behave as before |
| `discord` util | `communication` | the pilot for util-level grants |

A rejected gated call never becomes a turn: `validate_action` refuses it inside the
schema-retry cycle with a precise error naming the fragment that would grant it and routing
the model to a deferred `ask_user` (only you can activate fragments). Budgets, `fs_read_roots`
/ `fs_write_roots` and schedules are resources, not permissions — they stay `routine.yaml`
config.

Sub-workflows (`spawn`) run with fragments off, so they hold **no grants**: they cannot
write utils (unchanged) and cannot touch reserved utils like `discord`.

## Retired: `confirm_util_changes`

The old server/routine config key is gone. Whether util changes need approval now rides the
util-authoring grant itself (`confirm:` above), so curated-vs-autonomous authoring is a
per-routine fragment choice, visible in the same panel as every other capability. Old
config keys are ignored by the lenient loader; remove them.

## Working with grants

- **See** a routine's capabilities: its Standards panel (routine page) or the wizard's
  fragment picker — grant-carrying fragments show a `▸ grants …` line.
- **Give** a routine a capability: activate the granting fragment (Standards panel → save).
  Deactivating revokes it at the next run.
- **Create** a new granted capability: add a `grants:` block to a library fragment (Library
  tab → fragment editor). To reserve a util for a subset of routines, name it in a
  fragment's `utils:` list and activate that fragment where it's needed — every other
  routine loses access at its next run.
- Any future permission-ish lever should become a fragment grant, not a new yaml key.
