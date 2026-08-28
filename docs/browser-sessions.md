# Browser sessions — the logged-in Chrome behind every `--cdp` util

Some sources are only readable while signed in. A freelance board's message inbox, an
application form, an account page: there is no API and no token, the session IS a cookie jar
inside a browser. The `chrome` compose service is that browser — one long-lived headful Chrome
holding those sessions, so a person signs in **once** and every later run inherits it.

It is a sibling of the `tor` service, and for the same reason: the engine image stays
engine-only and the daemon supervises no second process.

## What talks to it

Any util that takes `--cdp URL` — today `job-scrape` (freelance-de, freelancermap, gulp),
`job-inbox` (all five boards), `job-apply` (the send path) and `browser-session`. Their
default is `http://127.0.0.1:9222`, and that default is correct here with no configuration:
see the network note below.

This is a different mechanism from `page-fetch` / `captcha-fetch`, which drive a **throwaway**
Playwright browser per call. Those need no session and no sidecar. Reach for the sidecar only
when being signed in is the point.

## Three design choices, each of which has a failure mode behind it

**Headful under Xvfb, never `--headless=new`.** freelancermap's invisible reCAPTCHA scores
headless Chrome badly enough to refuse the application form. Under Xvfb the browser genuinely
is not headless — it renders into a framebuffer nobody looks at — so the score stays clean.
A "simplification" to a headless flag silently breaks sending, and breaks it in the one place
where the failure looks like a site problem.

**`network_mode: "service:rsched"`.** Chrome shares the engine container's network stack, which
puts CDP on the engine's own loopback. Going over the compose network instead breaks twice, and
both breakages are obscure: DevTools rejects any `Host` header that is not an IP literal or
`localhost` (so `http://chrome:9222` is refused), and the `webSocketDebuggerUrl` it returns
names the address Chrome bound rather than the one the client dialled. A shared namespace has
neither problem and needs no wiring. The cost is that the service can publish no ports of its
own, so noVNC's `6080` is declared on `rsched`.

**`--password-store=basic`.** There is no keyring in a container. Left to its own
desktop-environment heuristics Chrome can wrap the cookie key in a backend that is not present,
and every start then reads as "logged out". `basic` is deterministic: the key derives from a
fixed constant, so the profile stays readable across container recreation and across hosts.

## The profile is the whole point

`${RSCHED_HOME}/chrome-profile` on the host is bind-mounted in. Every login lands there. Without
the bind it lives in the container layer and dies on the next `docker compose up` recreate,
logging every site out — the same rule that governs `~/routines` and the messenger session
stores. Back it up like a credential, because that is what it is.

### A profile from another machine will NOT carry its logins

Verified 2026-08-28 against the laptop profile this service replaced. Desktop Chrome on a Linux
box with a keyring encrypts cookie values under `v11` — the key is held by gnome-keyring /
libsecret and is **not in the profile directory at all** (`Local State` carries no
`encrypted_key`). Copy such a profile anywhere else and Chrome finds 71 cookies it cannot
decrypt, discards them, and presents a signed-out browser. Only a `v10` profile (written where
no keyring existed, i.e. one this service wrote itself) is portable.

So a migration off a desktop machine is not a file copy. It is: bring the service up, open
noVNC, sign in to each site once.

## Signing in

The browser has a real screen; noVNC is how a person reaches it.

Compose publishes it on the **host's loopback only** (`127.0.0.1:6080`). This is a keyboard and
a mouse attached to a browser holding live sessions, so it is deliberately not a LAN port. Two
ways in:

```bash
ssh -N -L 6080:127.0.0.1:6080 mark@192.168.0.128
```

then open `http://127.0.0.1:6080/vnc.html` and click Connect. Or, to reach it from a phone the
way the console is already reached, publish it on the tailnet:

```bash
docker exec tailscale tailscale serve --bg --https=8443 http://127.0.0.1:6080
```

which serves it at `https://ubuntuserver.taild5768c.ts.net:8443/vnc.html`, tailnet-only. That
is persistent configuration on the tailnet — set it deliberately, not as a side effect.

Sign in to each site normally. Choose "stay signed in" where offered. Nothing else is needed:
the profile is written as you go.

## Operating it

- **Memory is the real constraint.** Chrome is the largest single consumer on this host. Keep
  tabs closed after a login; the utils open and close their own.
- **A killed container leaves `Singleton*` locks** in the profile. Chrome then refuses the
  directory and quietly opens a throwaway one instead — which presents as "all my logins are
  gone". The entrypoint clears them on every start, so a restart is the fix.
- **Chrome runs in the foreground.** If it dies the container exits and `restart:
  unless-stopped` brings it back with the profile intact.
- **`docker compose up -d` does not restart a running service** whose config has not changed
  (see the note in `CLAUDE.md`). To pick up an image rebuild:
  `docker compose up -d --force-recreate chrome`.
- **Chrome's own renderer sandbox is kept**, which is why the service relaxes seccomp: this is
  the one process here that renders untrusted pages, and `--no-sandbox` would trade a
  container-level restriction for no browser-level one at all.

## Checking it is alive

From the engine container, the browser answers on the loopback it shares:

```bash
docker exec -u 1000:1000 rsched curl -s http://127.0.0.1:9222/json/version
```

A JSON body naming the Chrome build means CDP is up. Whether a given site is *signed in* is a
separate question, and the honest test is the util itself — every `job-inbox` source reports a
`logged_out` flag rather than returning an empty list.
