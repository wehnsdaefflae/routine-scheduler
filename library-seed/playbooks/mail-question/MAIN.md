---
slug: mail-question
title: Answer a question about my mail, and draft or send the reply
when: You ask something that lives in a mailbox — what did they say, has it arrived, what do I owe them — and may want a reply drafted or sent.
tags:
- email
- correspondence
- inbox
axis: the MAILBOX, the question and whether a reply is sent or only drafted; what stays fixed is reading the real message before answering, quoting it, and never sending without your say-so
updated: '2026-11-25'
setup:
  permissions:
  - outbound-mail
  rules:
  - correspondence
  - write-as-the-principal
  - verify-independently
  - ask-policy
  connections: {}
  fs_read_roots: []
  fs_write_roots: []
  note: >-
    `outbound-mail` is what the SENDING half needs; a conversation that should only ever draft
    is created WITHOUT it, and then a send is refused by the engine rather than by good
    intentions. Which mailbox is reachable is an account binding, not a permission: a mail
    capability reads the account names it has been configured with, so name the account in
    `{{mailbox}}` and let the catalog resolve it.
---

## Parameters

- `{{mailbox}}` — whose mail, which account. Default: the primary personal account.
- `{{question}}` — what you want to know, or what you want said.
- `{{send}}` — `draft` (leave it in drafts for you to read) or `send` (send it yourself).
  Default: `draft`.
- `{{window}}` — how far back to look. Default: the last 30 days, widened if the thread is older.

## Instructions

1. **Read the actual messages before answering anything.** Your catalog has a mail capability
   that lists, searches and reads a message including its attachments. Never answer a question
   about mail from a subject line or from a search snippet: the body is where the commitment is.
2. **Find the whole thread, not the newest message.** A reply that ignores what was already
   agreed three messages up reads as not having been read. Note who is on it, and whether anyone
   was dropped or added.
3. **Check attachments explicitly.** A message saying "see attached" with nothing attached, and
   a message whose attachment you did not open, lead to opposite answers. If an attachment
   exists, read it — a mail capability that lists attachments is how you know, not the body text.
4. **Answer `{{question}}` with quotes.** Name the sender, the date, and the words they used.
   Where the mail does not settle it, say so plainly rather than inferring an answer the sender
   never gave.
5. **If a reply is wanted, draft it in the principal's own voice** — their language, their
   register, their usual length. Keep the thread's quoting conventions. State what is being
   agreed to or asked for in one unmistakable sentence, and never commit them to a date, a sum
   or a deliverable that was not in `{{question}}`.
6. **For `{{send}}` = `draft`: put it in the drafts folder and stop.** Say where it is and what
   it says. Do not send "to be helpful" — a sent mail cannot be unsent, and the person asked for
   a draft.
7. **For `{{send}}` = `send`: send it, then VERIFY it left.** Read the sent folder back and
   confirm the message is there, to the right recipients. A send that reported success and did
   not arrive is indistinguishable from one you never attempted unless you look.
8. **Say what you did NOT do.** Mail you chose not to answer, a thread that needs the principal
   themselves, a question in the mail that `{{question}}` did not cover.

## Notes

- A draft can leave the drafts folder because someone sent it from a phone. If a draft you wrote
  earlier is gone, check the sent folder before assuming it vanished.
- Addresses that differ by one character are a real category of mistake: read the recipient
  list aloud against the thread before a send.
- An auto-reply, a delivery failure and a real answer all look like "a reply arrived". Read it.
- Where a mail asks for money, credentials or a signature, that is a decision for the principal:
  surface it with the `ask_user` action, with your recommendation, instead of answering it.
