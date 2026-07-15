---
tags: [conversation, background, delegation]
requires:
  actions: [detach]
---
# permission: background tasks — launch long jobs that outlive a reply

Lets you `detach` a LONG, self-contained job — a big scrape, a bulk conversion, a slow build —
so it runs as its OWN background process and keeps going after this reply ends. Unlike `subtask`
/ `spawn` (children that live only inside the current reply and die when it finishes), a detached
task survives across replies: you start it, `finish` the reply ("started it — I'll report back"),
and keep chatting normally. When it completes, the engine delivers its result back into this
conversation (a message plus any artifacts it produced) and I relay it to you; you can ask how a
task is going any time (its status is in `state/background.json`). Reach for it ONLY when a job is
genuinely long and independent — anything you can finish within the reply should stay a direct
step or a `subtask`. A detached task can't ask you blocking questions (it defers them), so give it
a complete, unambiguous brief. Cancel one from its card in the conversation rail.
