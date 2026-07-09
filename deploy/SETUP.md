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

## 3. Connect GitHub  — Settings → GitHub

Your workflow / fragment / util libraries and the source repo live in git. To clone/pull/push them
(especially if they're **private**), click **Connect GitHub** and follow the on-screen device flow:

1. The UI shows a one-time code and a link to `github.com/login/device`.
2. Open it in your browser, paste the code, authorize.
3. The scheduler stores the token via `gh` (persisted in the mounted `~/.config/gh`) and wires `git`.

No container terminal needed. Skip this only if all your repos are public and you never push.

> Advanced: to brand the authorize screen, register your own GitHub OAuth App (device flow enabled)
> and set `github_client_id` in `config.yaml`. Otherwise it uses the GitHub CLI's public app.

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
