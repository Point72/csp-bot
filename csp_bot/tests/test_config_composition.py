"""Tests that the packaged gateway/backend config groups compose correctly."""

import os

import pytest
from hydra import compose, initialize_config_dir
from hydra.utils import instantiate
from omegaconf import OmegaConf

import csp_bot.config

CONFIG_DIR = os.path.dirname(csp_bot.config.__file__)

GATEWAY_BACKENDS = {
    "slack": {"slack"},
    "discord": {"discord"},
    "symphony": {"symphony"},
    "telegram": {"telegram"},
    "mixed": {"slack", "discord"},
    "all": {"slack", "discord", "symphony", "telegram"},
}


@pytest.fixture
def backend_env(monkeypatch):
    """Provide dummy values for every backend's credential variables."""
    for name in (
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
        "DISCORD_TOKEN",
        "SYMPHONY_HOST",
        "SYMPHONY_BOT_USERNAME",
        "SYMPHONY_CERT_PATH",
        "TELEGRAM_BOT_TOKEN",
    ):
        monkeypatch.setenv(name, "dummy")


def _compose(overrides):
    with initialize_config_dir(config_dir=CONFIG_DIR, version_base=None):
        cfg = compose(config_name="conf", overrides=overrides)
        return OmegaConf.to_container(cfg.modules.bot.config, resolve=True)


@pytest.mark.parametrize("gateway,expected", GATEWAY_BACKENDS.items())
def test_precanned_gateway_composes(gateway, expected, backend_env):
    """Each pre-canned gateway resolves to its expected backend set."""
    config = _compose([f"+gateway={gateway}"])
    backends = {k for k in config if k != "_target_"}
    assert backends == expected


def test_bare_bot_has_no_backends(backend_env):
    """The bare `bot` gateway selects no backends on its own."""
    config = _compose(["+gateway=bot"])
    assert {k for k in config if k != "_target_"} == set()


def test_ad_hoc_backend_selection(backend_env):
    """Any combination can be assembled from the bare gateway."""
    config = _compose(["+gateway=bot", "+backend=[slack,telegram]"])
    assert {k for k in config if k != "_target_"} == {"slack", "telegram"}


def test_management_configuration_composes_and_instantiates():
    config = instantiate(
        _compose(
            [
                "+gateway=bot",
                "+modules.bot.config.management.admin_users.slack=[UADMIN]",
                "+modules.bot.config.management.log_channels.slack=[CCONTROL]",
                "+modules.bot.config.management.message_channels.slack=[CCONTROL]",
                "+modules.bot.config.management.log_file=/tmp/csp_bot.log",
            ]
        )
    )
    assert config.management.is_admin("slack", "UADMIN")
    assert not config.management.is_admin("discord", "UADMIN")
    assert config.management.log_channels == {"slack": {"CCONTROL"}}
    assert config.management.message_channels == {"slack": {"CCONTROL"}}
    assert config.management.log_file == "/tmp/csp_bot.log"
