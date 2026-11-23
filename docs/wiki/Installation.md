## Pre-requisites

You need Python >=3.11 on your machine to install `csp-bot`.

## Install with `pip`

```bash
pip install csp-bot
```

## Install with `conda`

```bash
conda install csp-bot --channel conda-forge
```

## Backend adapters

`csp-bot` connects to each chat platform through a separate adapter package.
Install the ones you need:

```bash
pip install csp-adapter-slack       # Slack
pip install csp-adapter-discord     # Discord
pip install csp-adapter-symphony    # Symphony
pip install csp-adapter-telegram    # Telegram
pip install csp-adapter-zulip       # Zulip
pip install csp-adapter-irc         # IRC
pip install csp-adapter-matrix      # Matrix
```

See [Backends](Backends) for credentials and per-platform setup.

## Source installation

For other platforms and for development installations, [build `csp-bot` from source](Build-from-Source).
