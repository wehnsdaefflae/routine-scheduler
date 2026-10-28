---
effect:
  with: publishes its own hand-written page or app on the Steward host, its links proven to open
  without: has no page of its own — its runs' summaries say what it did and its asks carry what it needs from you
  when: the routine's work IS a page or app of its own that you or others use, never a status page about its runs
tags: [steward, web, publishing, app]
requires: {}
expects:
  fs-read: ["~/.local/share/routine-scheduler-libraries/web/steward"]
---
# permission: steward-publishing — publish this routine's own page or app on the Steward host

The hub is retired: this covers only a page that IS the routine's own work (an app, a
hand-written page), never a status page about its runs.

How the host works — keys, endpoints, credentials, the publish sequence — is
`web/steward/CONTRACT.md` in the library, read through the kit root this permission expects
(`~/.local/share/routine-scheduler-libraries/web/steward/CONTRACT.md`). Read the section
you need when you publish; never copy it into memory.
A publish is finished only when three proofs pass in this run:
1. every stored copy the page renders from, read back through the API, matches what you built;
2. your page and one data file, fetched with no credential, are refused;
3. every document link the page shows, fetched with your credential, returns the file itself.
Repair what is yours and prove again; report the rest. Your summary links the page and says what
the last check returned.
