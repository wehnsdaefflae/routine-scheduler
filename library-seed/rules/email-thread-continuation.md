---
effect:
  with: replies inside the existing email thread
  without: may open a fresh thread for a reply, scattering the conversation
  when: the routine sends email that answers or forwards something
tags: [communication, tool-use, self-management]
---
# rule: email thread continuation — reply inside the thread, never open a new one

When this routine's task includes sending email, an outgoing message that is a RESPONSE to, or a FORWARD of, an existing message must continue that message's thread. Opening a fresh thread for a reply scatters a conversation across disconnected messages, drops the recipient's context, and reads as a machine that never actually read what it was answering.

- **Carry the threading headers.** A reply or forward sets `In-Reply-To` and `References` to the source message's `Message-ID` (accumulate `References` across the chain), and keeps the original subject with the conventional `Re:`/`Fwd:` prefix rather than inventing a new one. The mail-sending capability supports these headers — the run must populate them from the message it is answering, not send a bare new message.

- **Only a genuinely new topic starts a new thread.** Continue the thread whenever the message reacts to prior correspondence; start a fresh thread only for a genuinely unrelated, first-contact subject. When in doubt, continue.

- **Preserve the conversational context.** Quote or summarize the relevant part of what is being answered so the reply stands on its own in the thread, and address what the counterparty actually said. Threading is the mechanism; continuity of meaning is the point.

This applies to every email surface a routine drives — client updates, application correspondence, notifications that follow up on an earlier one: the recipient should see one coherent conversation, not a stream of orphaned messages.
