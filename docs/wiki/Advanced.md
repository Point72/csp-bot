## Command Scheduling

More information coming soon!

## Command Deferral

More information coming soon!

## Running long-running commands in the background

More information coming soon!

## Sending messages over REST

csp-bot runs on csp-gateway, which serves its channels over a REST API. With
`allow_send_messages` turned on, `messages_out` becomes a send channel, so a job
can post a message and have the bot deliver it:

```yaml
# @package _global_
modules:
  bot:
    config:
      allow_send_messages: true
```

```bash
curl -X POST http://localhost:8000/api/v1/send/messages_out \
  -H 'Content-Type: application/json' \
  -H "token: $CSP_BOT_API_KEY" \
  -d '[{"content": "<messageML>deploy finished</messageML>",
        "backend": "symphony",
        "channel": {"id": "..."}}]'
```

The `backend` field names where the message goes; `metadata["backend"]` does the
same and takes precedence, since that is what inbound tagging writes. A message
naming no backend reaches none of them.

Content is whatever the destination expects, so build it with chatom and render
it for the backend rather than writing markup by hand:

```python
from chatom import Format, MessageBuilder

body = MessageBuilder().heading("Deploy finished", level=3).build()
content = body.render(Format.SYMPHONY_MESSAGEML)
```

This is off by default. Turning it on makes the bot relay anything that reaches
its API, so configure authentication at the same time — `MountAPIKeyMiddleware`
rejects unauthenticated requests, but only once it has a key:

```yaml
modules:
  mount_api_key_middleware:
    api_key: ${oc.env:CSP_BOT_API_KEY}
```
