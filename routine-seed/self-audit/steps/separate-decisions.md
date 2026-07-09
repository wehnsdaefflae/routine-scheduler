# Step: separate-decisions

Decide what you may settle yourself vs what needs the user.

## Do
1. For each finding, apply the **autonomy gate**:
   - **Act (safe fix)** — reversible code/tests/config edits, added logging/telemetry, docs,
     small refactors that keep contracts intact. Stays a finding you will apply next step.
   - **Decision (surface, don't apply)** — anything that changes behaviour/priorities, is
     irreversible or outward-facing, OR touches a **contract**: the action schema, transcript
     `EVENT_TYPES`, or the ownership rules in the repo's CLAUDE.md. Never apply these as a
     "self-evident fix".
2. Frame each decision with 2–4 options and **always include "leave as-is"**. Give context and a
   recommendation, not an exhaustive survey. Assign/reuse a stable id (D1, D2…).
3. Fold in reviewer **decision** feedback: `[AUDIT decision · D1] selected: <option>` is now a
   **settled work order** — move it into the act list (treat as a safe, authorised change; still
   test-gated). A reviewer **note** is guidance to weigh across items.
4. Produce two explicit lists to carry forward: **APPLY** (safe fixes + settled decisions +
   instrumentation) and **SURFACE** (open decisions).

## Next
Write `state/phase.json` = `{"state": "act-apply-fixes"}` and read `steps/act-apply-fixes.md`.
