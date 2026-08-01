# Admin conversations

An **admin conversation** is an ordinary conversation resumed with the full toolset: it runs
with capability gating lifted, so it can reach every gated action kind, every reserved util,
and the deepest previous-run read depth — regardless of what the conversation's own
capabilities are set to. It exists for the moments you are driving a conversation by hand and
want the complete set of tools without editing the conversation's grants first.

Admin lifts **capability** gating only. It does **not** suspend the structural safety gates —
those hold for an admin conversation exactly as they do for any run (see
[what admin does not lift](#what-admin-does-not-and-cannot-lift)).

## Enabling admin for the instance

Admin is **off** for the whole instance until you set the admin token:

1. Set the secret `RSCHED_ADMIN_TOKEN` in the Secrets store to a long random value
   (treat it like the API token — anyone who has it can drive any conversation with the full
   toolset).
2. Restart the daemon so the new secret reaches the web layer.

With no `RSCHED_ADMIN_TOKEN` set (or an empty one), admin is **disabled and fail-closed**: no
request can ever obtain it, and the toggle below has no effect.

## Using it — the Admin toggle

Every conversation composer has an **Admin** button beside `send`.

1. Click **admin**. Paste the admin token into the prompt. The token is stored **for this
   browser session only** — never written to the server, never persisted to the conversation.
2. The button now reads **admin: on** and shows in red — the one unmistakable signal that this
   conversation reaches the full toolset.
3. Every message you send while it is on carries the token; the server re-checks it on each
   request and, on a match, unlocks capability gating for **that one resumed leg only**.
4. Click **admin: on** again to turn it off. The stored token is forgotten and messages send
   normally.

The unlock is **one-shot and per-leg**: it covers exactly the leg you authenticated. A later
message sent without the toggle is an ordinary conversation again, and a sub-workflow the leg
spawns never inherits admin (a child builds its own policy with capabilities off).

Scripted / API use: send the header `x-admin-token: <token>` alongside the normal
`Authorization: Bearer <api-token>` on `POST /api/conversations/{slug}/message` (or
`POST /api/runs/{run_id}/converse`). The admin header is an **additional** factor — it never
replaces the bearer token, and both are checked.

## What admin does NOT (and cannot) lift

These structural / ownership gates stay in force under admin, by design:

- `runs/` stays engine-owned and read-only.
- `routine.yaml` (a routine's or conversation's config) stays the user's — **no** run writes it,
  admin included.
- A routine's own recipe stays sealed (unless a user `fs_write_root` already covers its dir).
- The root-conversation-only gate on `create_routine`, `manage_group` and `detach` still holds.
- The workflow's `tools:` allowlist still applies — a kind must still be surfaced to the run.

Admin means "the operator is driving this conversation by hand and wants the full toolset", not
"structural invariants are suspended".

## The audit trail

Every action a conversation takes under admin appends one line to
`<routines_home>/.control/admin-audit.jsonl` (timestamp, run id, action kind, a short brief), so
the capability bypass is never silent. The token itself is never logged and never reaches the
engine — the web layer is the only place it is compared.

## Rotating the admin token

`RSCHED_ADMIN_TOKEN` is a static instance secret. Rotate it manually whenever it may have been
exposed, or on whatever cadence your security posture calls for:

1. Generate a new long random value (e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`).
2. Update the `RSCHED_ADMIN_TOKEN` secret in the Secrets store with the new value.
3. Restart the daemon so the web layer picks up the new secret.
4. In any browser that had admin on, click **admin: on → off** and re-arm it with the new token
   (the old session-stored token no longer validates — messages sent with it simply resume
   without admin, fail-closed).

To **revoke admin entirely**, clear `RSCHED_ADMIN_TOKEN` (set it empty or remove it) and restart:
admin returns to disabled/fail-closed for the whole instance.
