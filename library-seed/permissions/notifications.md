---
effect:
  with: pushes a short notice to your own phone or desktop through ntfy
  without: reaches you only through the console and the browser push that carries its decisions
  when: you want telling about something without going to look
tags: [notifications, attention, outbound]
requires:
  utils: [ntfy]
---
# permission: notifications — push a notice to the operator's own devices

A push interrupts him. Send at most one per run. It stands alone (what happened, what you need)
and carries only what he would want to be interrupted for: a result he is waiting on, a failure
he must know about now.
A decision goes through `ask_user`, which already reaches his devices by browser push; never push
it a second time.
Success he did not ask to hear about stays in your summary.
