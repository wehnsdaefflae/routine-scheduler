---
effect:
  with: acts inside the instance's shared browser, in the web sessions a person signed into once
  without: sees the web only as an anonymous visitor, so anything behind a login stays out of reach
  when: a source it needs is readable only while signed in — a Slack workspace, a job board's inbox, an account page
tags: [tool-use, browser, sessions, cdp]
requires:
  utils: [browser-session]
---
# permission: browser-sessions — the shared browser that is already signed in

The shared browser is at **`http://172.30.7.10:9222`**: one long-lived headful Chrome that the
operator signs into, once, on its own screen (noVNC), and whose sign-ins persist across restarts.
Reach it ONLY through `browser-session attach --cdp http://172.30.7.10:9222 --name <job>`, which
opens a tab of your own there; `do --name <job>` acts in that tab and screenshots it; `stop
--name <job>` closes that tab and nothing else. Never launch a browser of your own for anything
that needs a login: a fresh profile is signed out, and headless is a bot signal. Never touch a tab
you did not open — other routines and the operator hold theirs. Never type credentials or login
codes, and never ask for them: a sign-in, or a session that lapsed, is the operator's to do on the
browser's screen — file ONE deferred question naming the site, carry on with what needs no login,
and check again next run. Detect a lapse by reading the page (a login wall where content was) and
say so once. Close your tab when you are done: memory is this browser's real limit. `start`, a
throwaway headless browser of your own, is for pages that need no session at all.
