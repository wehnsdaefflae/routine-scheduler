---
slug: website-interaction
title: Do something on a website for me — read it, fill it in, or carry a task through
when: 'A task lives behind a web page: a portal to check, a form to complete, an account to change, a signed-in page whose state you want read back.'
tags:
- web
- browser
- forms
- portal
axis: the SITE and the task vary, and whether it needs a signed-in session; what stays fixed is reading the live page before acting, changing one thing at a time, and reading the result back from the site itself
updated: '2026-11-25'
setup:
  permissions:
  - browser-sessions
  rules:
  - verify-independently
  - ask-policy
  - interface-craft
  connections: {}
  fs_read_roots: []
  fs_write_roots: []
  note: >-
    `browser-sessions` is the reserved permission a signed-in page needs, and it is the whole
    point of this playbook: a public page can be fetched without it, but anything behind a login
    needs the shared browser the principal is already signed into. That browser is a SHARED
    resource — work in your own tab and close it when you are done, or the next task finds the
    page you left behind. A site needing an OAuth account (a calendar, a photo library) is a
    CONNECTION binding chosen at creation, not a permission.
---

## Parameters

- `{{site}}` — the page or service, by URL.
- `{{task}}` — what should be true on that site when this is done.
- `{{signed_in}}` — whether this needs the principal's logged-in session. Default: assume yes if
  the task touches an account, no if it only reads public content.
- `{{evidence}}` — what proof you should bring back: a screenshot, a confirmation number, the
  page text, a downloaded document. Default: whatever the site itself shows as confirmation.

## Instructions

1. **Decide the cheapest route that can actually work.** A public page that needs no session is
   a plain fetch, and your catalog has a capability that retrieves and extracts page text. Reach
   for the shared signed-in browser only when the task needs the principal's identity — it is a
   limited resource, and a fetch is faster and leaves nothing open.
2. **Look at the live page before you act on it.** Sites change: a selector, a button label or a
   flow step from a previous session is a guess until this session confirms it. Where a page is
   visual, look at it as an image rather than reasoning about its markup.
3. **Never guess past a login wall.** If a session is needed and is not signed in, stop and say
   so with what you saw — asking for a sign-in is cheap, and a login attempt against the wrong
   credential can lock an account.
4. **Change one thing at a time, and read it back.** Fill a field, confirm it took the value you
   meant, then move on. Forms silently reformat dates, truncate at a character cap, and clear
   dependent fields when an earlier one changes.
5. **Respect the site's own limits.** Where a field shows a counter or a cap, satisfy the cap as
   the SITE counts it, not as you count it; a rich-text editor and a plain textarea do not agree
   about length.
6. **Stop before the irreversible step and ask.** Submitting an application, cancelling a
   contract, paying, deleting an account: these are the principal's to authorize. Prepare
   everything, then surface the decision with the `ask_user` action, naming exactly what the
   button will do.
7. **Take `{{evidence}}` from the site, not from your own actions.** A confirmation number, the
   state of the page after a reload, the document the site generates. "I clicked submit" is not
   evidence that anything was submitted.
8. **Close what you opened.** Leave the shared browser as you found it: your tab closed, no
   half-filled form, no modal waiting. Say what state the account or page is now in.
9. **Report with the link and the proof,** plus anything on the page the principal should know
   that `{{task}}` did not ask about — a deadline, a changed fee, a pending action.

## Notes

- A page that renders nothing is often JavaScript the fetch did not run: that is the signal to
  move to the real browser rather than to conclude the page is empty.
- Two tabs on the same site share a session: a second task can log the first one out.
- A download lands in a path the browser chose; find the file before reporting it delivered.
- Where a site offers an API or an export, prefer it: a form filled by hand is a form that
  breaks the next time the site is redesigned.
