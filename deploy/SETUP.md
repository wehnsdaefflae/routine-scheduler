# First-run setup

routine-scheduler ships **no secrets and no repo URLs** — you point it at your own model providers
and your own git repos on first launch. This is the setup checklist after `docker compose up -d`
(see [DOCKER.md](DOCKER.md) for the container/compose details).

The image already contains everything the setup needs: `git`, the **GitHub CLI (`gh`)**, Node + the
`claude` CLI, and `uv`.

---

## 1. Open the UI

```bash
docker compose up -d
# the access token is generated into config.yaml on first boot:
docker exec rsched sh -c "grep '^token:' ~/.config/routine-scheduler/config.yaml"
```
Browse to `http://<host>:8321` and paste the token when prompted.

## 2. Add your model providers  — Settings → LLM endpoints

Endpoints are **model transports only**. Add the ones you use (OpenAI-compatible / Anthropic /
`claude-cli`) and put the API keys in `~/.credentials/*.env` on the host (they're bind-mounted, never
baked into the image). Set the default roles (orchestrator / subcall / cheap).

## 3. Authenticate GitHub  — one-time, for private repos + pushing

Your workflow / fragment / util libraries and the source repo live in git. To clone/pull/push them
(especially if they're **private**), authenticate `gh` inside the container — it persists in the
mounted `~/.config/gh`:

```bash
docker exec -it rsched gh auth login        # choose GitHub.com → HTTPS → device flow, paste the code
docker exec    rsched gh auth setup-git      # makes git use gh's credentials for clone/pull/push
```

> Skip this only if all your repos are public and you never push.

## 4. Point at your repos  — Settings → Library repositories + Source repository

For each library (workflows / fragments / utils) and the source repo, paste your git URL and click
**Test**. The button runs `git ls-remote` and tells you immediately:

- **✓ reachable — N branch(es)** → good, click *save*.
- **✗ Authentication failed / repository not found** → the repo is private and step 3 isn't done (or
  the URL is wrong). The exact git error is shown.
- **✗ timed out** → the host is unreachable from the container.

Fix any ✗ before saving, so the daemon never silently fails to sync later.

---

## Notes

- **LAN only.** The container binds `0.0.0.0`; the access token is the only auth. Keep it on a trusted
  network (or front it with a reverse proxy + TLS).
- **`claude-cli` token** (only if you use that transport) can't be refreshed headless — run
  `gh`-style device auth on a machine with a browser and drop the token into
  `~/.credentials/claude-code-oauth.env`; the mount picks it up.
- **What's git-backed vs. local:** the three libraries + the source repo have remotes and are meant to
  live on GitHub. Your **routines** (run history, ledgers, state) are **local-only** — back them up by
  copying `~/routines`, not via a remote.
