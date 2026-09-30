---
effect:
  with: posts to Discord through the bot — by default to your agent channel, which pushes to your phone
  without: reads the channel wherever the bot secret is granted, but never posts
  when: it has something you want pushed to your phone, or its audience is on a Discord channel
tags: [communication, messaging, outbound]
requires:
  utils: [discord:send]
---
# permission: messaging-discord — post to Discord through the bot

The default channel is the operator's agent channel, shared by every routine: a post there is a
notice to him. It is never a decision surface — a decision goes through `ask_user`; a reply here
answers none.
Send and read with your own `--cursor` (your slug), so the replies you read are the ones
addressed to you.
