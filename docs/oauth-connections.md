# OAuth connections

A **connection** lets a routine act against a third-party service (Notion first) on behalf of an
external account, without hand-managing tokens. The operator connects an account once in the web UI
(OAuth consent in a browser); the instance stores the resulting token; a routine reads a short-lived
access token from disk at run time.

Connections are a **resource binding**, like `models:` and `fs_roots` — not a capability. The
routine.yaml `connections:` map *is* the grant; no run may create or change one, and there is no
conduct-doc/capability layer to toggle. A util still has to opt in (below), and the token only ever
reaches a util the routine explicitly binds.

## The split that makes it work

OAuth has two halves that live in different places, because a routine run is headless and sandboxed:

- **Consent + refresh — the daemon/web process.** The interactive "Allow" step needs a browser, and
  refreshing a rotating token needs to *write* the token store — neither can happen inside a
  sandboxed run. Both live server-side.
- **Use — the run.** A run only ever *reads* a current access token from the on-disk store (the
  engine↔daemon boundary is filesystem-only; a run never calls the daemon).

## Pieces

- **`oauth/providers.py`** — the provider registry (`PROVIDERS`): non-secret endpoints + flags per
  provider. Notion is implemented (auth-code + PKCE, long-lived token, no device flow); Google and
  Slack are scaffold entries. OAuth *app* credentials live in the central Secrets store as
  `<PROVIDER>_OAUTH_CLIENT_ID` / `<PROVIDER>_OAUTH_CLIENT_SECRET`.
- **`oauth/store.py`** — the connection store: one `connections.json` next to `config.yaml`, keyed
  `"<provider>:<account>"`, written atomically at mode 0600 (modeled on the Secrets store). Values
  are write-only over the API (`list_connections` returns metadata only, never tokens). The
  daemon/web process is the single writer (a lock serializes the connect callback vs. the refresh
  worker). `tokens_for_routine` turns a routine's bindings into the env vars its utils receive.
- **`web/settings/oauth.py`** — the flow. `authorize-start` (authed) mints a `state` + PKCE verifier
  and returns the provider authorize URL; the user consents in a new tab; the provider redirects to
  the **public `GET /oauth/callback`** (mounted without the bearer dependency, like the webhook
  route — the unguessable per-flow `state` is the CSRF guard), which exchanges the code and writes
  the connection. The Settings tab polls the flow until the callback reports `connected`.
- **`daemon/oauth_refresh.py`** — `OAuthRefreshManager`, ticked from the scheduler loop. It refreshes
  any *expiring*-provider connection within ~5 min of expiry, persists a rotated `refresh_token`, and
  on a provider rejection flags the connection `needs_reauth` and notifies through `notify.py`. A
  no-op for non-expiring providers (Notion), so a Notion-only instance never touches it.
- **Engine injection** — `executor.do_util` resolves the routine's bound connections to
  `{<PROVIDER>_ACCESS_TOKEN: token}` and passes them to `utils_lib.run_util` as `extra_secrets`.
  `_child_env` injects each **only if the util declares the var** in its `secrets:` line — the same
  declared-only rule store secrets obey, extended to these engine-provided tokens. So the token
  reaches a util iff (routine binds the connection) AND (the util declares the var).

## Setting it up (Notion)

1. **Create a Notion integration** (a *public* OAuth integration) at notion.so/my-integrations;
   note its client id + secret and its redirect URI requirement.
2. **Settings → Secrets**: set `NOTION_OAUTH_CLIENT_ID` and `NOTION_OAUTH_CLIENT_SECRET`.
3. **Settings → Connections → Redirect URL**: set the instance's external https URL (e.g. a
   Tailscale Serve URL — the redirect is browser-side, so tailnet-reachable is enough). Register
   `<that>/oauth/callback` as the integration's redirect URI. This is persisted as `public_url` in
   config.yaml.
4. **Connect**: enter an account label (e.g. `personal`), click *connect*, consent in the new tab.
   The connection appears under "Connected accounts".
5. **Bind it**: on a routine's page, *Connections* → pick the account for the provider → save. The
   routine's utils that declare `NOTION_ACCESS_TOKEN` (the `notion` util does) now receive the token.

## Security

- `/oauth/callback` is intentionally unauthenticated (a redirect carries no bearer); a random,
  TTL'd, constant-time-compared `state` is the CSRF guard and PKCE-S256 protects the exchange.
  Authorization codes and `state` are never logged or echoed.
- `connections.json` is mode 0600; no API returns token values; tokens never enter the prompt,
  transcripts, or the search index (the config dir is excluded). Refresh tokens stay in the
  daemon/web process — only a short-lived access token crosses into a util, and only under the
  declared-var + bound-connection gate above.

## Limits (today)

- **One account per provider per run** — the injected env var is `<PROVIDER>_ACCESS_TOKEN`. For
  multi-account fan-out within a single run, a util's own `--account` selection over a static token
  map is the pattern, not a connection.
- Notion's *hosted* MCP endpoint (`mcp.notion.com`) is OAuth-only and separate; this framework is
  the direct-API path. See the `notion` util.
- No rsched multi-user login — every connection is owned by the single instance operator.
