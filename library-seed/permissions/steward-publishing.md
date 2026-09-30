---
effect:
  with: publishes a page on your Steward hub that you can check between runs, its links proven to open
  without: leaves its state in run summaries you have to go and read
  when: it works on something over weeks and you want one place to look
tags: [steward, web, publishing, reporting]
requires: {}
expects:
  fs-read: ["/home/mark/.local/share/routine-scheduler-libraries/web/steward"]
---
# permission: steward-publishing — publish this routine's page on the Steward hub

How the hub works — keys, endpoints, credentials, the publish sequence — is
`web/steward/CONTRACT.md` in the library, read through the kit root this permission expects
(`/home/mark/.local/share/routine-scheduler-libraries/web/steward/CONTRACT.md`). Read the section
you need when you publish; never copy it into memory.
A publish is finished only when three proofs pass in this run:
1. every stored copy the page renders from, read back through the API, matches what you built;
2. your page and one data file, fetched with no credential, are refused;
3. every document link the page shows, fetched with your credential, returns the file itself.
Repair what is yours and prove again; report the rest. Your summary links the page and says what
the last check returned.
