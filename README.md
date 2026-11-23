<a href="https://github.com/point72/csp-bot">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://github.com/point72/csp-bot/raw/main/docs/img/logo-name-dark.png?raw=true">
    <img alt="csp-bot logo, overlapping blue speech bubbles" src="https://github.com/point72/csp-bot/raw/main/docs/img/logo-name.png?raw=true" width="400">
  </picture>
</a>

<br/>

[![Build Status](https://github.com/point72/csp-bot/actions/workflows/build.yaml/badge.svg?branch=main&event=push)](https://github.com/point72/csp-bot/actions/workflows/build.yaml)
[![codecov](https://codecov.io/gh/Point72/csp-bot/branch/main/graph/badge.svg)](https://codecov.io/gh/Point72/csp-bot)
[![GitHub issues](https://img.shields.io/github/issues/point72/csp-bot.svg)](https://github.com/point72/csp-bot/issues)
[![License](https://img.shields.io/github/license/Point72/csp-bot)](https://github.com/Point72/csp-bot)
[![PyPI](https://img.shields.io/pypi/v/csp-bot.svg)](https://pypi.python.org/pypi/csp-bot)

## Features

`csp-bot` is a framework for building chat bots.
It is built on [csp](https://github.com/point72/csp), [csp-gateway](https://github.com/point72/csp-gateway), [ccflow](https://github.com/point72/ccflow), and [chatom](https://github.com/1kbgz/chatom).

`csp-bot` makes it easy to build extensible command-driven bots, and has some key features:

- connect to multiple backend chat platforms from the same instance
- supports Slack, Symphony, Discord, Telegram, Zulip, IRC, and Matrix
- register custom commands across backends
- create scheduled commands
- create asynchronous commands
- tag users
- redirect commands across rooms/channels
- and more!

For a detailed overview and examples, see our [Documentation](https://github.com/Point72/csp-bot/wiki/Overview).

Community-supported commands can be found in the [csp-bot-commands](https://github.com/csp-community/csp-bot-commands) project.

Legacy command classes can set `raw_args = True` to receive the argument text as
one token, preserving quotes, internal spaces, and line breaks. Mentions and
`/room` directives within that text are treated as data. Outer whitespace is
trimmed. Commands using the default argument parser retain their existing
behavior. This supports JSON and text-processing commands without reparsing
their payloads as bot arguments.

## Installation

Install with `pip`:

```bash
pip install csp csp-bot
```

Install with `conda`

```bash
conda install csp csp-bot -c conda-forge
```

## Management commands

The default configuration includes `/whoami`, `/logs`, and `/messages`.

| Command                      | Access                                                                 | Behavior                                                                                                                           |
| ---------------------------- | ---------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `/whoami`                    | Any authorized bot user                                                | Show backend, user ID, channel ID, and management role.                                                                            |
| `/logs [lines]`              | Admin, in a DM with the bot or a configured log channel                | Show the last 50 lines by default; accept 1-200 lines, reading at most 64 KiB and displaying at most 3,000 characters of log text. |
| `/messages list [before-id]` | Admin, in a DM with the bot or a configured message management channel | Browse recent bot sends across rooms on the invoking backend, five per page.                                                       |
| `/messages edit <id> <text>` | Admin, in a DM with the bot or a configured message management channel | Replace the text of a recorded message.                                                                                            |
| `/messages delete <id>`      | Admin, in a DM with the bot or a configured message management channel | Delete one recorded message.                                                                                                       |

Management administrators are configured by backend and user ID. Display names
and membership in a log channel do not grant admin access. Empty admin lists
deny management access. `/whoami` supplies the IDs needed for configuration.

Any command can be made admin-only by setting `admin: true` on its command
model. Admin-only commands follow the same rules as `/logs` and `/messages`:
only admins can run them, they reject reply-destination redirects, and `/help`
lists them only for admins. `admin: false` lifts the restriction from `/logs` or
`/messages`, though they still require a configured channel.

```yaml
gateway:
  commands:
    - /commands/help
    - _target_: csp_bot.commands.ScheduleCommandModel
      admin: true
```

With a packaged gateway configuration, management settings belong under
`modules.bot.config`:

```yaml
modules:
  bot:
    config:
      management:
        admin_users:
          slack: ["U01234567"]
          discord: ["123456789012345678"]
        log_channels:
          slack: ["C01234567"]
        message_channels:
          slack: ["C01234567"]
        log_file: ${hydra.runtime.output_dir}/csp_bot.log
```

`log_file` is an operator-configured path; `/logs` cannot select another file.
Log retrieval is disabled until a file is configured. An admin can retrieve logs
in a one-to-one DM with the bot or in a channel listed in `log_channels`. Group
DMs do not count. Logs can include chat content and operational details, so
configured log channels should be private. A permitted channel controls the
destination; each caller must still be an admin.

`/logs` and `/messages` reject reply-destination redirects. Admin access is checked before
command preprocessing and again at execution. Attempts are recorded in the bot
log. The existing `user_access_channels` gate still applies to bot interactions.

`/messages` replaces the standalone `/delete` command. Each entry shows a stable
catalog ID, native message ID, room name/ID, recorded UTC send time, status, and
text preview. Edit/delete accepts the catalog ID shown after `ID:`, not a native
ID or a position within a page. The stored backend, room, and native ID identify
the target; identical native IDs in different rooms remain distinct.

```text
/messages list
/messages list <before-id-from-Next>
/messages edit <catalog-id> Replacement text
/messages delete <catalog-id>
```

Pagination uses the last entry's stable ID as a cursor. New sends and edits do
not shift the next page. Deleted entries retain their status and cursor until
evicted. An expired cursor returns an error with instructions to restart listing.

The catalog records successful adapter sends and separate attachment uploads,
plus local agent sends and uploads, including generated files, images, and GIFs.
It retains the latest 1,000 entries across backends. It starts empty and does not
backfill platform history. It is in memory by default; `Bot.set_state_store()`
also configures catalog persistence through the existing state-store interface.
Observing sends does not depend on a platform message-history API.

Message management requires a configured admin, in a one-to-one DM with the bot
or a permitted destination in `message_channels`. Listings can include previews from private rooms and DMs,
so permitted destinations should be private. An admin on one backend does not
gain access to another backend's records. These are bot-wide administrators
within that backend, rather than room-only moderators.

Actions target the recorded room, while their responses stay in the management
channel. Capability checks and native API permissions still apply. Text editing
does not replace attachments; unsupported edits, expired platform permissions,
and externally removed messages can fail. Successful management edits update
the preview and native ID if changed; successful deletes mark the entry deleted.
Changes made through other clients or tools are not automatically reconciled.
Only the selected message is affected; associated replies and recurring schedules
remain separate.

| Backend  | Native edit/delete routing                                                                         |
| -------- | -------------------------------------------------------------------------------------------------- |
| Slack    | Channel ID and message timestamp through `chat.update` / `chat.delete`.                            |
| Discord  | Channel ID and message ID through message edit/delete.                                             |
| Telegram | Chat ID and message ID through text edit/delete; catalog management requires no history retrieval. |
| Symphony | Stream/message IDs for updates and message ID for suppression.                                     |
| Zulip    | Message ID through message update/delete.                                                          |
| Matrix   | Room ID and event ID; an edit sends an `m.replace` event and a delete redacts the event.           |
| IRC      | Not supported, because IRC messages cannot be edited or deleted.                                   |

Local agent commands, including `/ask`, also expose `delete_message` only to
configured management admins. Existing channel and authorship checks still
apply. External MCP services enforce their own authorization.

Custom command lists can register these models from `csp_bot.commands`:
`WhoamiCommandModel`, `LogsCommandModel`, and `MessagesCommandModel`.

## License

This software is licensed under the Apache 2.0 license. See the [LICENSE](https://github.com/Point72/csp-bot/blob/main/LICENSE) file for details.
