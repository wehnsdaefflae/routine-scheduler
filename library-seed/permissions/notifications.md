---
effect:
  with: push a short notice to your own phone or desktop
  without: cannot reach you anywhere but the console
  when: you want telling about something without going and looking
tags: [notifications, attention, conduct]
requires:
  utils: [ntfy]
---
# permission: notifications — push a notice to the user's own devices via ntfy

Unlocks the `ntfy` util: publish a short message or notification to the user's own
ntfy topic — their phone or desktop. This is a one-way push to the user's OWN devices:
a much smaller intrusion than personal messaging (no third party, no conversation),
but it still spends the user's attention, so it is an attention claim, not a free
log line.

Push only what the user would want to be interrupted for: a result they are waiting
on, a decision that blocks progress, a failure they need to know about now. Batch —
one notice per run carrying everything that matters, never a stream of progress pings.
Anything that can wait for the run summary waits there. Write the notice to stand
alone (what happened, what you need) and never push routine success the user did not
ask to be told about. Silence is the default; a notification is a deliberate choice
you could defend.
