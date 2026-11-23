Configuration for `csp-bot` is driven by [`ccflow`](https://github.com/Point72/ccflow).
`ccflow` leverages [Pydantic](https://docs.pydantic.dev/latest/) for type validation, and combines it with [Hydra](https://hydra.cc/) / [OmegaConf](https://omegaconf.readthedocs.io/en/2.3_branch/) for config-driven initialization.
The [`ccflow` examples](https://github.com/Point72/ccflow/wiki/First-Steps) provide a nice overview of its functionality.

In `csp-bot`, this means commands, backends, and their settings are all controlled from a small set of composable yaml files.

## Two config groups

`csp-bot` exposes two Hydra config groups:

- `gateway` — assembles the bot module and the `csp-gateway` server around it.
- `backend` — one fragment per chat platform (`slack`, `discord`, `symphony`, `telegram`, `zulip`, `irc`, `matrix`), each contributing its configuration to the bot.

The `backend` group is what makes running against any mix of platforms easy.
Because each fragment writes to a different field of the bot config, you can select **any combination** of them at once.

## Selecting backends

The bare `bot` gateway has no backends of its own.
Add backends by listing them from the `backend` group:

```bash
# One backend
csp-bot-start +gateway=bot +backend='[slack]'

# Any combination — no dedicated config required
csp-bot-start +gateway=bot +backend='[slack,telegram]'
csp-bot-start +gateway=bot +backend='[slack,discord,symphony,telegram]'
```

Each backend reads its credentials from environment variables, so nothing else is required on the command line.
See [Backends](Backends) for the full list of variables.

## Pre-canned gateways

For the common cases, ready-made `gateway` configs select the backends for you:

| Gateway             | Backends                              |
| :------------------ | :------------------------------------ |
| `+gateway=slack`    | Slack                                 |
| `+gateway=discord`  | Discord                               |
| `+gateway=symphony` | Symphony                              |
| `+gateway=telegram` | Telegram                              |
| `+gateway=mixed`    | Slack + Discord                       |
| `+gateway=all`      | Slack + Discord + Symphony + Telegram |
| `+gateway=bot`      | none — add your own with `+backend`   |

```bash
csp-bot-start +gateway=all
```

## Using a local config file

Selecting backends from the command line is convenient, but a small yaml file is easier to version and share.
Hydra unions configs, so a local file only needs to declare which gateway it builds on:

**example/bot/slack.yaml**

```yaml
# @package _global_
defaults:
  - /gateway: slack
  - _self_

bot_name: CSP Bot

# Tokens are read from the environment: SLACK_BOT_TOKEN, SLACK_APP_TOKEN
# csp-bot-start --config-dir=example +bot=slack
```

To pick your own mix of backends in a file, build on the bare `bot` gateway and list them:

**example/bot/custom.yaml**

```yaml
# @package _global_
defaults:
  - /gateway: bot
  - /backend:
      - slack
      - telegram
  - _self_

bot_name: CSP Bot
```

Run either with:

```bash
csp-bot-start --config-dir=example +bot=slack
csp-bot-start --config-dir=example +bot=custom
```

## What a gateway config expands to

A `gateway` config is built from the bare `bot` skeleton plus the selected `backend` fragments.
The `bot` gateway defines the bot module and an empty `BotConfig`:

```yaml
# @package _global_
defaults:
  - /modules
  - _self_

bot_name: CSP Bot

modules:
  bot:
    _target_: csp_bot.Bot
    config:
      _target_: csp_bot.BotConfig
```

Each `backend` fragment fills in one field of that `BotConfig`.
For example, the `slack` fragment:

```yaml
# @package modules.bot.config
slack:
  _target_: csp_bot.SlackConfig
  bot_name: ${bot_name}
  config:
    _target_: chatom.slack.SlackConfig
    bot_token: ${oc.env:SLACK_BOT_TOKEN}
    app_token: ${oc.env:SLACK_APP_TOKEN}
```

Selecting `+backend='[slack,telegram]'` merges the `slack` and `telegram` fragments into the same `BotConfig`, leaving the unused backends unset.
The per-platform `config` block is the corresponding [`chatom`](https://github.com/Point72/chatom) backend config, so any field that backend supports can be set here.

All of these configs live in-source under [csp_bot/config](https://github.com/Point72/csp-bot/tree/main/csp_bot/config); copy or extend them as the basis for your own.

## Configure an agent model

Install the agent dependencies:

```bash
pip install "csp-bot[agent]"
```

Define an agent command and its Hydra model:

```python
from typing import Type

from pydantic_ai import Agent

from csp_bot.commands import AgentCommand, AgentCommandModel


class AskCommand(AgentCommand):
  def command(self) -> str:
    return "ask"

  def name(self) -> str:
    return "Ask"

  def help(self) -> str:
    return "Ask the configured model a question"

  def build_agent(self, command):
    toolset = self.build_toolset(command)
    return Agent(
      self.get_model(),
      toolsets=[toolset] if toolset else [],
      instructions="Answer questions using the available chat tools.",
    )

  def build_prompt(self, command) -> str:
    return " ".join(command.args)


class AskCommandModel(AgentCommandModel):
  command: Type[AgentCommand] = AskCommand
```

Set `model_name` when registering the command:

```yaml
gateway:
  commands:
  - _target_: my_bot.AskCommandModel
    model_name: "github-copilot:<model>"
```

`model_name` accepts PydanticAI's fully qualified model strings:

| Credential source                   | Model string                    |
| ----------------------------------- | ------------------------------- |
| Anthropic API or configured gateway | `anthropic:<model>`             |
| Codex login                         | `openai-codex:<model>`          |
| GitHub Copilot token                | `github-copilot:<model>`        |
| OpenRouter API key                  | `openrouter:<provider>/<model>` |

Use `codex login` before selecting `openai-codex:`. For the other providers,
set the environment variables documented by PydanticAI for that provider.
Provider credentials are resolved by PydanticAI and are not stored by
`csp-bot`.

### Use the Claude Agent SDK runtime

Install the separate Claude Agent SDK extra:

```bash
pip install "csp-bot[claude-agent]"
```

Inherit from `ClaudeAgentCommand` when the command should run through the
Claude Agent SDK rather than a PydanticAI model provider:

```python
from typing import Type

from csp_bot.commands import AgentCommand, AgentCommandModel, ClaudeAgentCommand


class ClaudeAskCommand(ClaudeAgentCommand):
  def command(self) -> str:
    return "claude"

  def name(self) -> str:
    return "Claude"

  def help(self) -> str:
    return "Ask Claude a question"

  def build_prompt(self, command) -> str:
    return " ".join(command.args)


class ClaudeAskCommandModel(AgentCommandModel):
  command: Type[AgentCommand] = ClaudeAskCommand
```

Register it with a Claude model name:

```yaml
gateway:
  commands:
  - _target_: my_bot.ClaudeAskCommandModel
    model_name: "claude-sonnet-4-6"
```

The Claude runtime exposes only the invocation backend's Chatom MCP tools. It
does not enable Claude Code's file, shell, or ambient project tools. Claude
Agent SDK authentication is resolved by the SDK in the bot process environment.
