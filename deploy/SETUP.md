# First-run setup

routine-scheduler ships **no secrets and no repo URLs** — you provision everything from the web UI
on first launch. After `docker compose up -d` (see [DOCKER.md](DOCKER.md) for the container details,
including the browser token the compose file needs in `.env` first), the app **redirects you to
Settings** and shows a setup banner until you're done.

The image already contains everything setup needs: `git`, the **GitHub CLI (`gh`)**, Node + the
**`claude` CLI**, and `uv`. Nothing of the maintainer's is baked in.

---

## 1. Open the UI

```bash
docker exec rsched sh -c "grep '^token:' ~/.config/routine-scheduler/config.yaml"
```
Browse to `http://<host>:8321` and paste the token.

## 2. Secrets — the one place for every credential  (Settings → Secrets)

A single `KEY → VALUE` store, **injected into utils and LLM endpoints
at run time**. Values are **write-only** — the UI lists key names, never the values. Example rows:

| KEY | value |
|---|---|
| `OPENROUTER_KEY` | `sk-or-v1-…` |
| `ANTHROPIC_KEY` | `sk-ant-…` |
| `CLIPROXY_API_KEY` | *(proxy client key — see §3)* |
| `BROWSER_CDP_TOKEN` | *(the same value as in `.env` — the signed-in browser's door)* |
| `DISCORD_BOT_TOKEN` | *(for the `discord` util)* |

**"Needed by installed utils"** — this section lists exactly which env vars your utils declare they
need and flags the **unset** ones. So when a routine generates a new util, its required vars show up
here automatically (unset) — click **set** and fill them in. You never have to read a util's source
to discover what to add. (Under the hood each util declares a `secrets: NAME1, NAME2` header line;
the engine surfaces it. This is also required of every `write_util`-generated util.)

## 3. Model providers  (Settings → LLM endpoints)

Add an `openai` or `anthropic` endpoint. Each OpenAI/Anthropic endpoint
reads its key from **Secrets** via its `key_var` (e.g. `openrouter` → `OPENROUTER_KEY`) — so just set
that key in §2 and the endpoint works. (You can also paste a per-endpoint inline key if you prefer.)
Then add the models you use to the **model catalog** on the same page — each a name over an
endpoint and a model id — and pick the **system model**: the one fallback for the scheduler's own
helper calls (the new-routine clarify flow, workflow generation) and for any role a routine leaves
unset. Each routine picks its own models from the catalog (main and tool-call, plus an optional
uncensored one) on its page.

### Using your Claude subscription

Run the pinned CLIProxyAPI sidecar and sign in through its OAuth flow. Configure an
Anthropic-compatible endpoint with the proxy client key. See the
[subscription proxy guide](../docs/claude-proxy-cutover.md).

## 4. Connect GitHub  (Settings → GitHub)

To clone/pull/push your (private) library + source repos, click **Connect GitHub**:

1. The UI shows a one-time code and a link to `github.com/login/device`.
2. Open it in your browser, paste the code, authorize.
3. The token is stored via `gh` (persisted in the mounted `~/.config/gh`) and wired into `git`.

No container terminal, no PAT to mint. Skip only if all your repos are public and you never push.

## 5. The library and the source repository

Workflows, rules, permissions, settings patterns, playbooks and utils live together in ONE git
repo (`~/.local/share/routine-scheduler-libraries`: `workflows/`, `rules/`, `permissions/`,
`patterns/`, `reminders/`, `playbooks/`, `utils/`). It has **no settings surface**: the daemon
creates it — cloned from `libraries_remote` in `config.yaml` when that names a repo of yours,
otherwise empty — and its boot-time sync adds the built-in defaults the library lacks, never
overwriting an edit; the **library-sync** routine keeps it in step with its remote from then on.

**Settings → Source** is the scheduler's own repository, where self-audit commits and pushes:
set the remote to your fork, and **Test** it (`git ls-remote` → **✓ reachable** /
**✗ authentication required** / **✗ not found** — do §4 first for a private repo).

## 6. Finish

Click **finish setup** in the banner (stops the first-launch redirect).

---

## Notes

- **LAN only.** The container binds `0.0.0.0`; the access token is the only auth. Keep it on a
  trusted network (or front it with a reverse proxy + TLS).
- **Secrets are plaintext on disk** (in the config dir, `0600`), like most self-hosted `.env` setups —
  fine on a trusted host; use disk encryption if you need at-rest protection.
- **What's git-backed vs. local:** the library repo + the source repo have remotes (GitHub);
  your **routines** (run history, ledgers), conversations, background tasks, config, secrets and
  linked sessions are local-only — `deploy/backup.sh` keeps nightly dated snapshots of every one
  of those homes (see [DOCKER.md](DOCKER.md) § Backups).
