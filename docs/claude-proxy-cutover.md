# Subscription proxy setup

The scheduler sends completions through its existing Anthropic Messages adapter to
CLIProxyAPI. The scheduler owns the agent loop, actions, history and compaction;
the proxy owns subscription login, token refresh and account routing. The custom
Claude CLI endpoint was removed in 0.315.0.

## Deployment

On the scheduler Docker host, from the source checkout:

```bash
bash deploy/cliproxy-init.sh
docker compose --profile claude-proxy up -d --no-deps cliproxy
```

The image is pinned by version and digest in Compose. State lives in
`~/.config/routine-scheduler/cliproxy/`, included in the existing backup inventory.
Keep config.yaml, client.env and auth/ private and back them up together. The config
initializer refuses to overwrite an existing configuration. The proxy has prompt
cloaking, extra retries and automatic model substitutions disabled.

Host ports are loopback only: 8317 for the API, 54545 for Claude OAuth and 1455 for
Codex OAuth. Use the proxy login flow, not a Claude setup-token.

## Signing in, and signing back in

A session dies two ways, and the endpoint card in Settings → Endpoints shows which: the
proxy's account rows carry its own status words — `token expired` when a refresh token was
rejected (`invalid_grant`), a usage-limit message with a `retry after` time when the
subscription window is spent. The first needs a fresh sign-in; the second only waits. On
2026-09-14 both hit at once (Codex in weekly cooldown, the Claude refresh token invalid) and
every run whose fallback chain ran through the proxy died at turn 0.

**From the console** (the normal way): each proxy endpoint's card lists the proxy's
accounts for ITS models — the Claude endpoint's card the Claude account, the Codex
endpoint's the Codex one, decided by the family of the model ids bound to that endpoint —
and offers that provider's sign-in; one endpoint per proxy carrying *Proxy management:
CLIProxyAPI* (the management key) serves every sibling on the same origin. On the card
press *re-authenticate Claude* (or, on the Codex card, *re-authenticate Codex*). The card
shows a link — open it and finish consent.
The consent page then sends the browser to `http://localhost:54545/callback?code=…&state=…`
(`localhost:1455` for Codex). That address is the PROXY'S callback and exists only on the
server, so on your own device the page fails to load — its address bar still carries the
code. Copy the whole address, paste it into the card's box and press *finish sign-in*; if
the consent page shows a code instead of redirecting, paste that. The console hands the
code to the proxy's management callback and polls until the token exchange settles, then
the account row and the quota line reload. The console never sees a token: the three
routes (`GET …/proxy-accounts`, `POST …/proxy-login`, `POST …/proxy-login/complete`) carry
the consent link, the state and the proxy's status words, and the two POSTs are refused for
the read-only routine token like every config-mutating route.

**From a terminal** (the fallback when the console is what is broken): forward the callback
port over SSH so the redirect lands on the server, then run the proxy's own login:

```bash
ssh -N -L 54545:127.0.0.1:54545 mark@192.168.0.128
```

On the server:

```bash
docker compose --profile claude-proxy exec cliproxy /CLIProxyAPI/CLIProxyAPI --claude-login --no-browser
```

Open the printed URL and finish consent; with the tunnel up the redirect completes by
itself. Codex has the same two routes (`--codex-login`, or the card) with port 1455.
Management requests require the separate management key. Never expose that key to routines.

## Scheduler configuration

Copy the client and management keys from private client.env into Settings → Secrets
as CLIPROXY_API_KEY and CLIPROXY_MANAGEMENT_KEY. Configure:

```yaml
endpoints:
  claude-proxy:
    kind: anthropic
    base_url: http://cliproxy:8317
    key_var: CLIPROXY_API_KEY
    quota_source: cliproxy
    quota_key_var: CLIPROXY_MANAGEMENT_KEY
```

Use the proxy root URL, without /v1. For a host installation use
http://127.0.0.1:8317. A blank quota_auth_index automatically selects the single
enabled Claude account; select an explicit index when there is more than one.
Bind catalog models to exact IDs from /v1/models, then verify inference. Set each
model's context allowance, output limit, effort and vision support deliberately.
Existing routine references use catalog names and need no changes.

Codex models also use the `anthropic` kind: the forced-action tool route passed
our live action schema. The OpenAI strict-schema route rejects optional nested
fields and falls back to unconstrained JSON, so it is not the validated route.

## Verification and operations

Use the Settings endpoint test, then the opt-in synthetic transport suite with
CLIPROXY_API_KEY exported securely:

```bash
RSCHED_LIVE_TESTS=1 RSCHED_PROXY_URL=http://127.0.0.1:8317 \
  RSCHED_PROXY_MODEL=claude-sonnet-4-6 \
  .venv/bin/python -m pytest -n0 -q tests/test_proxy_live.py::test_proxy_scheduler_contract
```

For Codex use RSCHED_CODEX_MODEL=gpt-5.6-luna and select `-k "codex and anthropic"`.
These are real subscription calls. They check actions, prompt preservation, history,
images and usage; the Claude case checks actual cache reads. PDF capability and
large or concurrent workflows require their own validation.

The proxy refreshes OAuth credentials. Its supported management refresh operation
is POST /v0/management/auth-files/refresh with the selected auth file name; keep
its response private because it can contain authentication data. Verify inference
and quota after refresh and after restarting the proxy while the scheduler is idle.

Independent live utilities (claude, claude-login, claude-usage and frame-fill) still
use the CLI. Its installation, dedicated state mount and utility credentials are
retained for those consumers; scheduler model calls no longer use them. Do not delete
a human login or utility credential as part of endpoint cleanup.

## Migration evidence, 2026-09-10

Production bindings were switched on 2026-09-10 after all six model/effort probes
passed the action schema without degraded retries. Each binding also passed the
deployed scheduler's endpoint test after saving. Scheduling was paused while idle
and resumed afterward; no catalog entry references `claude-cli` now. Original
bindings are saved privately in `cliproxy/production-cutover.json` as historical rollback evidence (rollback also requires the previous code).
The inherited 2,000,000-character context setting was made explicit on migrated
entries, preserving the prior configuration rather than inheriting the proxy's
smaller default. Other attributes and fallback chains were retained.

| Catalog name | Explicit proxy model | Effort |
| --- | --- | --- |
| Haiku | claude-haiku-4-5-20251001 | unchanged default |
| Sonnet | claude-sonnet-5 | unchanged default |
| Opus | claude-opus-4-8 | unchanged default |
| Opus Low | claude-opus-4-8 | low |
| Fable | claude-opus-4-8 | high |
| Opus 5 max | claude-opus-4-8 | max |

Minimal legacy calls confirmed the actual Sonnet/Opus/Fable alias resolutions;
Fable resolved to Opus 4.8 in that check, and the entry named Opus 5 max used the
same `opus` alias. These existing naming mismatches were retained rather than
silently upgrading models. The Haiku legacy probe failed without model usage;
its built-in alias target was independently verified through the proxy. Explicit
proxy IDs stop future CLI alias changes from changing these bindings automatically.
Verification on 2026-09-10: the dedicated trial was temporarily scheduled through
cron with the production `Sonnet` binding. Run `claude-proxy-trial:20260910-090603`
finished `ok` in 33 seconds/six turns, with all model requests using
`claude-proxy/claude-sonnet-5` and no errors. The original disabled trial configuration
and recipe were restored. This exercises the real scheduler, not a full overnight
production workload.

The pinned proxy's supported `POST /v0/management/auth-files/refresh` endpoint
successfully rotated the Claude access token and advanced its expiry. Subsequent
inference and quota reads passed, including after a controlled proxy restart with
no active runs. Scheduling was restored afterward. Only timestamps and boolean verification results
were recorded; no tokens were printed. Detailed local evidence is in
`cliproxy/scheduled-verification.json` and `cliproxy/refresh-verification.json`.

Claude routine trial on 2026-09-10: `claude-proxy-trial:20260910-085336`
finished with `ok` in 53 seconds and six turns, without intervention or errors.
The actions were read input, two LLM calls, write report, read report, and finish.
All 11 model requests (including scheduler classification) used
`claude-proxy/claude-sonnet-4-6`; cache reads and writes were recorded. The dedicated
routine remains disabled with no schedule; production routine settings were unchanged.
The report is `claude-proxy-trial/artifacts/result.md` under the routines home.
This verifies the bounded scheduler loop, not every production model or complex workflow.

Codex trial on 2026-09-10: `token-lab:20260910-073700` finished in 366 seconds
at turn 35. All 44 recorded model requests (including classification calls) used
`codex-proxy/gpt-5.6-luna`. Both arithmetic experiment calls returned 45; the
scheduler recorded input/output usage of 324/93 and 317/20 tokens, plus cache
hits during the agent loop. File actions and finish handling worked. Original
routine model assignments and budgets were restored and verified.

This was a transport pass, not a clean unattended workflow pass: the routine
initially followed its normal research backlog and needed a scope reminder. It
finished with `partial` status because its full-run deliverables were not met.
The trial's inherited 100,000-character context setting with a 16,384-token output
reserve also caused repeated clamping. Tune that allocation and use a dedicated
bounded trial workflow before broader Codex rollout. Usage was present in the task
log even though the model-facing subcall observation did not expose it.


The legacy endpoint, adapter/wire modules, credential field, quota reader and UI
were removed after these checks. Full production overnight workflows were not
claimed as tested by the bounded scheduled trial.

Upstream: [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI).
