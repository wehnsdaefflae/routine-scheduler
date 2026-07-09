# First-run setup

routine-scheduler ships **no secrets and no repo URLs** — you provision everything from the web UI
on first launch. After `docker compose up -d` (see [DOCKER.md](DOCKER.md) for the container details),
the app **redirects you to Settings** and shows a setup banner until you're done.

The image already contains everything setup needs: `git`, the **GitHub CLI (`gh`)**, Node + the
**`claude` CLI**, and `uv`. Nothing of the maintainer's is baked in.

---

## 1. Open the UI

```bash
docker exec rsched sh -c "grep '^token:' ~/.config/routine-scheduler/config.yaml"
```
Browse to `http://<host>:8321` and paste the token.

## 2. Secrets — the one place for every credential  (Settings → Secrets)

A single `KEY → VALUE` store, **injected into every util, LLM endpoint, and the Claude subscription
at run time**. Values are **write-only** — the UI lists key names, never the values. Example rows:

| KEY | value |
|---|---|
| `OPENROUTER_KEY` | `sk-or-v1-…` |
| `ANTHROPIC_KEY` | `sk-ant-…` |
| `CLAUDE_CODE_OAUTH_TOKEN` | *(subscription — see §3)* |
| `DISCORD_BOT_TOKEN` | *(for the `discord` util)* |

**"Needed by installed utils"** — this section lists exactly which env vars your utils declare they
need and flags the **unset** ones. So when a routine generates a new util, its required vars show up
here automatically (unset) — click **set** and fill them in. You never have to read a util's source
to discover what to add. (Under the hood each util declares a `secrets: NAME1, NAME2` header line;
the engine surfaces it. This is also required of every `write_util`-generated util.)

## 3. Model providers  (Settings → LLM endpoints)

The fresh config lists `openrouter`, `anthropic`, and `claude-cli`. Each OpenAI/Anthropic endpoint
reads its key from **Secrets** via its `key_var` (e.g. `openrouter` → `OPENROUTER_KEY`) — so just set
that key in §2 and the endpoint works. (You can also paste a per-endpoint inline key if you prefer.)
Then set the default roles (orchestrator / subcall / cheap).

### Using your Claude subscription (`claude-cli`) — where the token comes from

The `claude-cli` transport bills your **Claude subscription**, not an API key. It authenticates with
a long-lived OAuth token (`CLAUDE_CODE_OAUTH_TOKEN`) — **not** by logging Claude Code into your
account inside the container. You mint the token **once, elsewhere**:

1. On any machine with a browser and Claude Code installed (e.g. your laptop):
   ```bash
   claude setup-token
   ```
   Log into your Anthropic account; it prints a long-lived token.
2. Paste that token into **Secrets** as `CLAUDE_CODE_OAUTH_TOKEN`.

Done. The container's `claude` CLI uses the token from the environment — it **never logs in**. (The
CLI *is* installed in the image, but only to run `claude -p` with your token; minting the token is a
one-time browser step on your own machine.) When it eventually expires, re-run `claude setup-token`
and update the Secrets value — no restart.

> Don't want the subscription path? Use an Anthropic **API key** instead: set `ANTHROPIC_KEY` in
> Secrets and use the `anthropic` endpoint (metered billing). Simpler, no `claude setup-token`.

## 4. Connect GitHub  (Settings → GitHub)

To clone/pull/push your (private) library + source repos, click **Connect GitHub**:

1. The UI shows a one-time code and a link to `github.com/login/device`.
2. Open it in your browser, paste the code, authorize.
3. The token is stored via `gh` (persisted in the mounted `~/.config/gh`) and wired into `git`.

No container terminal, no PAT to mint. Skip only if all your repos are public and you never push.

## 5. Set up your libraries  (Settings → Library repositories)

Workflows, fragments, and utils each live in a git repo on your account. On a fresh deploy each row
offers two buttons (do §4 first — both need GitHub):

- **Clone existing** — enter `owner/name` of a repo you already have → its content is pulled in.
- **Create + seed** — enter a new name → a **private** repo is created on your account, seeded with
  the built-in defaults, and pushed. Future generated workflows/fragments/utils auto-sync there.

Once a library has content its row switches to a **remote** field with a **Test** button
(`git ls-remote` → **✓ reachable** / **✗ authentication required** / **✗ not found**). The
**Source repository** (self-audit's push target) works the same way.

## 6. Finish

Click **finish setup** in the banner (stops the first-launch redirect).

---

## Notes

- **LAN only.** The container binds `0.0.0.0`; the access token is the only auth. Keep it on a
  trusted network (or front it with a reverse proxy + TLS).
- **Secrets are plaintext on disk** (in the config dir, `0600`), like most self-hosted `.env` setups —
  fine on a trusted host; use disk encryption if you need at-rest protection.
- **What's git-backed vs. local:** the three libraries + the source repo have remotes (GitHub); your
  **routines** (run history, ledgers) are local-only — back them up by copying `~/routines`.
