"""One response header: the console's Content-Security-Policy (D219).

The console sent no CSP at all. This sends the two directives that cost the console nothing it
actually does, and deliberately NOT the ones that would break it. The narrowness IS the design;
`tests/test_security_headers.py` pins the directive set, so widening it is an act with a test to
change rather than a one-line edit.

WHY NOT MORE. A `blob:` document INHERITS the policy of the page that created it, and the console
shows every artifact from a blob (`api.js apiBlobUrl` — an `<iframe src>` cannot carry the bearer
header). So a `script-src` here would stop the inline scripts of every HTML artifact a run writes:
the artifact panel frames those with `sandbox="allow-scripts"` and `components/blobtab.js` opens
them in a new tab the same way, and the SANDBOX is what makes them safe to run — not a policy that
stops them running. `index.html` carries one inline theme script, and `el()` sets `style=` on
elements all over the frontend, so `default-src`/`style-src` could only arrive with that script
hashed and `'unsafe-inline'` respectively. All of that is the LATER increment, conditioned on
artifacts being served from their own route (the `Console: a Content-Security-Policy (S4)` entry in
docs/designs.md): a full policy, the theme script hashed, `style-src 'unsafe-inline'`, and a
`frame-src` naming the browser-view relay.

WHAT THE TWO DIRECTIVES COST, checked before shipping them:

* `object-src 'none'` — nothing in `static/` or `src/` builds an `<embed>` or an `<object>` (the
  only textual hits are `typeof x === "object"` tests). A PDF artifact renders in an `<iframe>`
  (`components/artifacts.js`), and a PDF opened in a new tab is handed over as itself because a
  browser will not render one in a sandboxed frame at all (`components/blobtab.js`). Neither is an
  object/embed context, so neither is governed by this directive.
* `frame-ancestors 'self'` — it refuses the console to any OTHER origin's frame, and every frame
  the console builds is this origin or a blob of it: the Help pages under `/docs`, the artifact
  frames, and the relayed noVNC screens, which `web/screen_proxy.py` exists to serve from this
  origin precisely so they are same-origin. `frame-ancestors` is checked against the EMBEDDED
  response's own header, and a `blob:` document has no response headers at all, so the artifact
  and wrapper frames are outside this directive entirely. Nothing in this repo publishes the
  console for embedding elsewhere.

WHICH RESPONSES CARRY IT: every http response this app produces — the page, the root-scoped
`/sw.js` and `/manifest.webmanifest`, `/static`, `/docs`, `/api/*`, and the 401/403 refusals,
which is the reason for doing it in one pass instead of per route. A WebSocket scope never reaches
an http middleware (the same reason the timing middleware in `app.py` cannot count a live stream),
so the live streams pass through untouched.

Pure ASGI rather than `@app.middleware("http")`: setting one header needs no `Request` object and
no per-request task, and two lines is all `app.py` had left under the house size bar.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: The policy, as sent. Read the module docstring before adding a directive: the first two
#: candidates (`script-src`, `style-src`) break the artifact viewer and the frontend's inline
#: styles, and the increment that makes them possible has not happened yet.
POLICY = "object-src 'none'; frame-ancestors 'self'"


class SecurityHeaders:
    """Set `Content-Security-Policy` on every http response; pass every other scope through."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def with_policy(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["content-security-policy"] = POLICY
            await send(message)

        await self.app(scope, receive, with_policy)
