# Instance-wide search

The search box in the app header answers "which run mentioned X?" and "what did I decide
about Y last month?" across the WHOLE instance — every routine and every conversation —
without opening runs one by one. Press `/` (or `Ctrl-K`) anywhere in the console, type,
and hits drop down grouped by routine/conversation; each one deep-links into the run,
conversation, decisions, or routine view it came from.

## What is searchable

Model-authored and user-authored **prose**, never raw tool output:

- **Run transcripts** (both homes, gzipped archives and subrun trees included): the
  model's `say` narration and `note` lines, finish summaries, questions and answers,
  and your injected messages.
- **result.md** finish reports and the navigable `history/` archives compaction writes.
- **LEDGER.md** per routine, and **`.memory/`** notes.
- **Durable decision records** (`questions/pending/` — asks and util approvals).
- **Recipe files**: `main.md`, `stages/`, `traits/`, and `instruction.md`.

Deliberately NOT indexed: `routine.yaml` / `tuning.yaml` / server config, `state/`,
`inbox/`, artifacts and attachments, and anything under `background_home` (transient by
design — a detached task's results land in its owner conversation's transcript). Tool
observations are excluded on purpose: they carry file contents wholesale — bulky,
derivative, and where a leaked secret would live.

## Query syntax

Plain words are ANDed, and English inflections fold together (porter stemming:
`playbook` finds `playbooks`; words it doesn't recognize match their exact form). FTS5
syntax passes through when it parses — `"exact phrase"`, `telem*` prefix matching,
`zebra OR okapi`, `NEAR(a b)`. Anything the FTS5 parser rejects (hyphens, stray quotes)
is retried with every term escaped as a plain phrase, so everyday queries never see a
syntax error.

## How the index works

An SQLite **FTS5** database (stdlib `sqlite3`) at
`<routines_home>/.control/search.sqlite3` — a **pure cache**: delete the file and it
rebuilds from disk; the flat files stay the only source of truth (the no-database
philosophy of `registry.py`). The daemon/web process is its ONLY writer — engine
subprocesses never touch it.

Freshness is per-file stat fingerprints (inode + mtime + size, the registry's model):
each refresh re-stats the sources, reindexes what changed (newest runs first), and
prunes rows for files gone from disk — run retention and routine deletion clean the
index automatically. Two triggers keep it warm:

- a **maintainer task** in the daemon runs a bounded refresh pass in a worker thread
  (back-to-back while a backlog remains, then once a minute), and
- every **query** tops up freshness with a ~2s budget first, so results never show a
  deleted run and always include the latest finished one.

`GET /api/search?q=…&limit=…` (bearer-authed like every route) returns ranked hits
with snippets and enough metadata (home, slug, run ts, subrun path, event kind, turn,
phase) to group and deep-link. Malformed queries come back as a 400, never a 500.
