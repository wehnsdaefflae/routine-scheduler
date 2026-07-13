# Instruction

Maintain the workflow library at ~/.local/share/routine-scheduler-libraries/workflows.

Each week, ingest the top-level run transcripts and LEDGERs of all routines under
~/routines (skip dot-directories and yourself), identify flaws and optimization potential
in the workflows they materialized from, apply fixes AND restructures directly
(lint-gated, committed, version-bumped — the user reviews through the Library tab's
edit/delete, not an approval gate), and draft new library workflows when a recurring
instruction shape has no fit. Never edit other routines' directories — findings about a specific routine become
deferred questions naming it.

Start every analysis from ~/routines/.control/workflow-usage.jsonl — one line per finished
run AND per finished sub-workflow ({routine, run_id, workflow, depth, status, turns,
tokens}). It tells you which patterns are actually used (including the per-purpose child
patterns parents spawn, depth > 0), which fail or burn outsized turn/token budgets, and
which are never picked — dead weight to question, or a sign the spawn catalog lacks a
fitting pattern. Weight your attention by that evidence before opening any transcript.
