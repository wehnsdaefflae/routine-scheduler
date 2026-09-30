---
effect:
  with: works inside the shared browser, in the web accounts you signed into once
  without: sees the web only as an anonymous visitor, so anything behind a login stays out of reach
  when: a source it needs is readable only while signed in — a Slack workspace, a job board's inbox, an account page
tags: [tool-use, browser, sessions, cdp]
requires:
  utils: [browser-session]
---
# permission: browser-sessions — work in the shared signed-in browser

Every session there is one of the operator's own accounts. Reading is yours; a click that posts,
sends, applies, buys or changes a setting speaks as him and needs his approval of that act.
Never type a password or a login code; never ask for one either. A lapsed sign-in (a login wall
where content was) is his to renew on the browser's screen: ask once in a deferred question
naming the site, then carry on with what needs no login.
In the shared browser, work in a tab of your own (`attach`) and `stop` it before you finish:
memory is that browser's limit.
