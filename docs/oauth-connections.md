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
  **Consent scopes** are the user's config, not a code default: a `scoped=True` provider (Google,
  Slack) reads its requested scopes from `<PROVIDER>_OAUTH_SCOPES` in the Secrets store
  (`authorize_scopes`, space/newline/comma-separated), and `authorize-start` **errors** if that
  secret is unset — there is no hardcoded fallback, so a connection can never silently consent to a
  narrower set than intended. `scoped=False` (Notion) sends no scope param and needs no secret, as
  its scopes are fixed on the integration. Widen or narrow a Google connection in the **scope
  editor** under its provider in Settings → Connections (*scopes (n / 50)*): the list is shown
  grouped by the API each scope's URL names, a short name (`drive.readonly`) expands to the full
  URL, the count is held against the provider's consent ceiling (Google's consent page hangs past
  50 scopes — observed 2026-11-08, undocumented), each connected account's GRANTED scopes are
  compared with the list as edited, and *save* writes `<PROVIDER>_OAUTH_SCOPES`
  (`PUT /api/settings/oauth/{provider}/scopes`, judged by `oauth/scopes.py`). Saving changes no
  token — *save & reconnect <account>* (or the row's *reconnect*) is what does. A scope is also
  useless until its API is ENABLED in the Google Cloud project (Docs answered `SERVICE_DISABLED`
  with its scope granted, 2026-11-10), so the editor links the project's console — read off the
  client id's numeric prefix, the project number: *enabled APIs*, *consent screen*, and per API
  row a *console* link to the project's API library searched for that API (searched, because a
  scope's URL does not name its service: `documents` is docs.googleapis.com) — never
  *disconnect* then *connect*: reconnect runs the same consent for the same `provider:account`
  and the callback overwrites that key only once the new consent has SUCCEEDED, so the working
  token stays bound to its routines until then (a disconnect first lost google:personal's token
  for nine routines on 2026-10-29, when the redirect was broken and the connect failed). The
  same secret is what any Google util reads.
- **Google Health is its own connection** (`google_health`, 2026-11-09). The Google Health API
  refuses a token carrying ANY scope outside `googlehealth.*` (403 `DISALLOWED_OAUTH_SCOPES`,
  naming every other scope on it), so it can never share the general Google token. It is the
  same Google OAuth app (`creds_from: google` — no second client to register), consented for
  `googlehealth.*` alone (`GOOGLE_HEALTH_OAUTH_SCOPES`; the editor refuses anything else) and
  injected as `GOOGLE_HEALTH_ACCESS_TOKEN`, so a routine binds both. The `google-api` util calls
  `health.googleapis.com` with it and every other host with `GOOGLE_ACCESS_TOKEN`. A failing util
  that serves several connections is routed to request one only when NONE of them is bound
  (`exec_env._unbound_connection_request`): with Google bound, a Drive 403 is not a missing
  Health connection.
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
- **`oauth/redirecturi.py`** — `private_host_caveat(redirect_uri)`: one sentence when the target
  resolves into a range a provider will refuse (RFC1918, loopback, link-local, and the IPv6
  equivalents), "" otherwise. An unresolvable name says nothing — a guess is not a cause.
- **`daemon/oauth_refresh.py`** — `OAuthRefreshManager`, started by each scheduler tick but run
  beside it (`Scheduler._off_tick`: one pass at a time, so a provider that is down and answers in
  its 20 s timeout never holds a cron fire). It refreshes
  any *expiring*-provider connection within ~5 min of expiry, persists a rotated `refresh_token`, and
  on a provider REFUSAL (HTTP 400/401/403, RFC 6749 §5.2 — a 5xx, 429 or timeout is retried
  next tick like a network error) flags the connection `needs_reauth`, which badges it in Settings →
  Connections ("needs re-auth — reconnect", the row's *reconnect* promoted to its primary
  control; a successful re-consent writes a fresh record, flag cleared) — the console record IS the notification, and no implicit outbound send exists. A
  no-op for non-expiring providers (Notion), so a Notion-only instance never touches it.
- **Engine injection** — `executor.do_util` resolves the routine's bound connections to
  `{<PROVIDER>_ACCESS_TOKEN: token}` and passes them to `utils_run.run_util` as `extra_secrets`.
  `_child_env` injects each **only if the util declares the var** in its `secrets:` line — the same
  declared-only rule store secrets obey, extended to these engine-provided tokens. So the token
  reaches a util iff (routine binds the connection) AND (the util declares the var). A run may
  also request a binding it lacks (`ask_user` with `request: "connection:<provider>"`,
  docs/rules-permissions.md): allow-forever binds the provider's single connected account in
  routine.yaml; allow-now injects that account's token for the asking run only. With zero or
  several connected accounts the request is corrected in-cycle — the account choice stays yours.

## Setting it up (Notion)

1. **Create a Notion integration** (a *public* OAuth integration) at notion.so/my-integrations;
   note its client id + secret and its redirect URI requirement.
2. **Settings → Secrets**: set `NOTION_OAUTH_CLIENT_ID` and `NOTION_OAUTH_CLIENT_SECRET`.
3. **Settings → Connections → Public URL**: set the instance's external https BASE url — the
   base, not a path. The redirect is browser-side, so the base only has to be reachable from
   the BROWSER that consents; it does have to be https, because most providers refuse any other
   redirect URI — and it must NOT resolve to a private address. Google refuses an authorization
   request whose `redirect_uri` is a LAN, loopback or link-local address *before* it shows any
   consent screen ("Access blocked: Authorization error — device_id and device_name are required
   for private IP"), which is why a `192.168.x.x` base makes every re-consent impossible with
   nothing in the console to explain it (R3061). `authorize-start` now returns a `caveat` naming
   the host and the cause when it sees one, and the card states it and asks before opening the
   provider's tab — a warning, not a block: the URL still comes back, since whether a target is
   acceptable is the provider's call and a tunnel may resolve differently than this box does.
   See deploy/DOCKER.md,
   "HTTPS for the console". The card then shows the exact callback (`<that>/oauth/callback`) to register as the
   integration's redirect URI. This base is persisted as `public_url` in config.yaml.
4. **Connect**: enter an account label (e.g. `personal`), click *connect*, consent in the new tab.
   The connection appears under "Connected accounts". To re-consent later (changed scopes, a
   `needs_reauth` flag) click that row's *reconnect*: the old token stays until the new consent
   lands.
5. **Bind it**: on a routine's page, *Limits & reach* → *more* → *Connections* → pick the account
   for the provider → *accept changes*. The routine's utils that declare `NOTION_ACCESS_TOKEN` (the
   `notion` util does) now receive the token.

## Security

- `/oauth/callback` is intentionally unauthenticated (a redirect carries no bearer); a random
  (256-bit), TTL'd, single-use `state` is the CSRF guard — the first callback presenting it
  consumes it — and PKCE-S256 protects the exchange. Authorization codes and `state` are never
  logged or echoed.
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
