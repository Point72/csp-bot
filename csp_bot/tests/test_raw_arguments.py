from unittest.mock import patch

import pytest
from chatom import Channel, Message, User

from csp_bot import BaseCommandModel, Bot, BotCommand, BotConfig, ReplyCommand


class RawTextCommand(ReplyCommand):
    raw_args = True

    def command(self):
        return "raw"

    def name(self):
        return "Raw"

    def help(self):
        return "Return the unparsed argument text."

    def execute(self, command):
        return Message(content=command.args[0], channel=command.channel)


@pytest.mark.parametrize("backend", ["slack", "symphony"])
@pytest.mark.parametrize("payload", ['{"key": "value",\n  "nested": [1, 2]}', "hello  world\nnext\tline", "/room other @U2", 'a \\"quoted" value'])
def test_raw_arguments_preserve_quotes_whitespace_and_directives(backend, payload):
    bot = Bot(config=BotConfig())
    bot.load_commands([BaseCommandModel(command=RawTextCommand)])
    text = f"/raw {payload}"
    message = Message(content=text, channel=Channel(id="room"), author=User(id="user"))
    with (
        patch.object(Bot, "_get_bot_id", return_value="bot"),
        patch.object(Bot, "_get_bot_name", return_value="helper"),
        patch.object(Bot, "_resolve_channel") as resolve,
    ):
        parsed = bot._extract_commands(message, backend, "room", text, [User(id="U2")])
    assert isinstance(parsed, BotCommand)
    assert parsed.args == (payload,)
    assert parsed.targets == () and parsed.channel_id == "room"
    assert bot._execute_command(parsed)[0].content == payload
    resolve.assert_not_called()
