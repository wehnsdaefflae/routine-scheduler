"""OAuth "connections" framework.

The one operator connects external service accounts (Notion first) via OAuth in the web UI; the
resulting tokens are stored by the daemon/web process and a routine run reads a short-lived access
token from disk to act on that account's behalf. Consent + refresh live server-side (they can't
happen in a headless, sandboxed run); a run only ever reads.

Layout:
- `providers.py` — the per-provider metadata registry + client-credential resolution.
- `store.py` — the daemon-owned connection store (single writer) + `tokens_for_routine`.
- `web/settings/oauth.py` — the authorize flow + the public `/oauth/callback` route.
- `daemon/oauth_refresh.py` — refreshes expiring access tokens on the scheduler tick.

Connections are a RESOURCE binding (routine.yaml `connections:`), like models/fs_roots — the
binding is the grant; there is no capability layer and no run may configure them.
"""
