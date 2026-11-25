---
slug: chat-history-review
title: Read a long chat history and answer a question about it
when: You want a messenger thread read properly — what was agreed, how a pattern developed, who said what when — rather than scrolling months of it yourself.
tags:
- messaging
- analysis
- history
axis: which THREAD, which messenger and which question vary; what stays fixed is syncing the real archive, bounding the window, quoting with dates, and counting rather than impressing
updated: '2026-11-25'
setup:
  permissions:
  - messaging-whatsapp
  - messaging-signal
  - messaging-telegram
  rules:
  - verify-independently
  - evidence-discipline
  - interface-craft
  connections: {}
  fs_read_roots: []
  fs_write_roots: []
  note: >-
    Each messenger is its OWN reserved permission — grant only the one whose history is being
    read, and drop the rest when creating the conversation: a thread in one messenger needs no
    access to another. These permissions cover READING the archive as well as sending, so a
    review-only conversation has more reach than it needs; where that matters, say in the first
    message that nothing is to be sent. A messenger also depends on a session store the
    principal has already logged in — that is a configured resource, not something a run can
    establish for itself.
---

## Parameters

- `{{thread}}` — whose conversation, in which messenger.
- `{{question}}` — what you want to know from it.
- `{{window}}` — the period that matters. Default: the whole thread, bounded by what the archive
  actually holds.
- `{{output}}` — where the answer lands. Default: a short reply, with anything long written to
  `artifacts/` so it renders in the side panel.

## Instructions

1. **Sync the archive before reading it.** A messenger capability reads a local mirror, and an
   unsynced mirror ends days ago — an analysis of yesterday's thread that silently stops last
   week is worse than no answer. Say how many messages arrived in the sync and what the newest
   timestamp now is.
2. **State the real bounds of what you have:** first and last message, total count, and any gap.
   An archive that begins mid-thread cannot answer "has this always been like this", and saying
   so is part of the answer.
3. **Read the whole `{{window}}`, in order.** For a long thread, work in date blocks and keep a
   running note per block rather than holding it all at once. A sampled read produces confident
   claims about a pattern that the unsampled messages contradict.
4. **Count what is countable.** Messages per person, per month, response delays, how often a
   thing recurs. A number from the archive beats an adjective: "fourteen postponements, eleven of
   them within a day of the meeting" is a finding; "often postponed" is an impression.
5. **Quote with the date and the author** for every load-bearing claim. Where the messages are in
   another language, keep the original and add a translation — a translated quote presented as
   the original is a changed quote.
6. **Separate what was SAID from what you infer.** Intentions, moods and motives are inferences:
   mark them as yours, and say which messages support them. This material is personal, and a
   confident inference about someone's state of mind is the error that costs most here.
7. **Answer `{{question}}` first, in the reply itself,** then put the long form in `{{output}}`.
   Nobody wants a document when they asked a question; and nobody wants a 5,000-message analysis
   pasted into a chat.
8. **Where the thread contains something genuinely urgent** — a deadline, a commitment, an
   unanswered direct question — surface it even if `{{question}}` did not ask.
9. **Send nothing.** Reading a history and replying in it are two different jobs; if a reply is
   wanted, that is a separate instruction and a separate decision.

## Notes

- Group threads have joins and leaves: a participant list from the newest message is not the list
  from a year ago.
- Media, polls and voice notes usually do not appear as text; say what you could not read rather
  than treating an archive of text as the whole conversation.
- A quoted reply, a forward and an original message read identically once flattened — keep the
  distinction where it changes who said a thing.
- Deleted-for-everyone messages leave a hole: note it rather than reading past it.
