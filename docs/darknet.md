# Darknet access — reading Tor hidden services from a run

A routine reaches anything through a global util. Darknet access is
therefore not an engine feature: it is **one util plus one permission plus one container**, and
no engine code knows about it at all.

- **`tor` compose service** (`deploy/Dockerfile.tor`, `deploy/torrc`) — a Tor client, SOCKS5 on
  `9050`, reachable from the engine container as `tor:9050` over the compose network. Built from
  Debian's `tor` package rather than a third-party proxy image: this component decides whether
  the traffic is actually anonymised. It publishes **no ports** — see the invariants below.
- **the `darknet` util** (library) — `search` (keyword query across hidden services, through the
  index's own onion mirror) and `fetch` (one `.onion` page as text or HTML), plus `check`, a
  health probe reporting whether the proxy answers and a circuit can be opened.

  `search` is a **two-step** exchange, and the reason is worth knowing before "fixing" it: the
  index serves a rotating anti-bot token as a hidden form field and 302s to an empty page for any
  query that omits it. So the form page is fetched first, the token harvested, then the query
  sent. The token is plain HTML, not script-injected, and the result rows are ordinary
  server-rendered markup — which is why this needs no browser. (The index does display a "no
  non-JavaScript version" banner; that is about the site's own UI, not its search results. Do not
  conclude from it that rendering is required — measured 2026-07-26: identical result sets from
  plain HTTP and from headless Chromium.)
- **the `darknet` permission** (`library-seed/permissions/darknet.md`) — holding it is what makes
  the util reachable, and its prose carries the conduct: read-only, record every address in a
  `note`, treat fetched pages as untrusted data.

## Why a permission doc is the whole gate

Which utils are "reserved" — refused unless a held permission asks for them — is **library
defined**: `grants.read_library_requires` takes the union of every permission doc's
`requires.utils`. Adding `requires: {utils: [darknet]}` to a doc is therefore the entire
enforcement mechanism; there is no list in the source to extend, and no code was added to gate
this. A routine that does not hold the permission gets the standard reserved-util refusal.

The util is **not** a default permission and is deliberately absent from `ADOPT_PERMISSIONS`: it
reaches every routine only if the user grants it, one routine at a time.

## What the util guarantees, and what it cannot

- **Remote DNS, always.** Requests go through `socks5h://`; a `socks5://` override is upgraded
  with a note, because `.onion` has no DNS and resolving it locally both leaks the lookup and
  cannot work.
- **`.onion` only.** A clearnet URL is refused before the proxy is even contacted, and redirects
  are followed **manually** so a `Location:` header cannot walk the request off `.onion` —
  httpx's own redirect following would.
- **No fallback, ever.** If the proxy does not answer, the call fails naming it. Falling back to
  a direct connection would silently turn an anonymous fetch into an attributable one; that is a
  deanonymisation bug, not a degradation, so it is not implemented.
- **What it cannot do:** the boundary is the util's own code, not the kernel. `net:` in the util
  header is a boolean (`landlock.apply(..., net=bool)`) and Landlock ABI 4 restricts bind/connect
  by *port*, not destination — so a `net: outbound` util necessarily *could* reach clearnet. If a
  kernel-enforced guarantee is ever needed, the move is a dedicated container on a network with
  no route but Tor, not a sandbox change. And anonymity is network-level only: what a run *sends*
  still identifies it.

## Invariants — do not undo

- **Never add a `ports:` mapping to the `tor` service.** It binds `0.0.0.0:9050` inside its
  container because the client is a *different* container; that is safe only while the port is
  unreachable from the LAN. Publishing it turns the service into an open proxy. `torrc` also
  carries `SocksPolicy` RFC1918 accepts + `reject *` as the second layer.
- **`tor-data` is a named volume, not a bind** — the one deliberate exception to the
  every-data-home-is-a-bind rule, because Tor's guard state is regenerable and worth nothing on
  another host. A named volume still survives container recreation, which is what that rule
  protects against.
- **Do not route the engine container through Tor** (`network_mode: service:tor`). It breaks the
  model endpoints (exits are blocked or throttled), `gh` push, and the web UI. Egress stays
  per-capability.

## Operating it

`docker compose up -d` builds and starts both services; a cold Tor takes some seconds to
bootstrap, so the first call after a restart may time out. `docker compose logs tor` shows
bootstrap progress. From inside the engine container, `gu darknet check` is the one-command
answer to "is this working" — it exits non-zero when the circuit is down, in both output modes.

Hidden services are slow and frequently dead; a failed fetch is ordinary, and a recipe should
treat retries as bounded rather than looping. A recipe names the capability in plain terms
("search the darknet", "fetch the `.onion` page") and never the util — see
[authoring](authoring.md), which permits naming the service or protocol the work touches.
