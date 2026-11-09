"""Tests for reading the Snugban receiver's settings from config and the environment."""

from __future__ import annotations

import pytest

from nanobot.cli.commands import _webhook_address
from nanobot.config.schema import Config, SnugbanConfig
from nanobot.snugban.settings import snugban_settings

URL = "http://snugban-host.tailnet.ts.net:7420"
KEY = "sb_agent_settings_key"
SECRET = "whsec_settings_secret"


def test_config_accepts_the_snugban_block_in_camel_case():
    config = Config.model_validate({
        "gateway": {"snugban": {"url": URL, "agentKey": KEY, "webhookSecret": SECRET}}
    })
    assert config.gateway.snugban == SnugbanConfig(url=URL, agent_key=KEY, webhook_secret=SECRET)


def test_nothing_configured_means_no_receiver():
    assert snugban_settings(SnugbanConfig(), environ={}) is None


def test_settings_come_from_config():
    settings = snugban_settings(
        SnugbanConfig(url=URL, agent_key=KEY, webhook_secret=SECRET), environ={}
    )
    assert (settings.url, settings.agent_key, settings.webhook_secret) == (URL, KEY, SECRET)


def test_settings_fall_back_to_the_environment():
    settings = snugban_settings(SnugbanConfig(), environ={
        "SNUGBAN_URL": URL, "SNUGBAN_AGENT_KEY": KEY, "SNUGBAN_WEBHOOK_SECRET": SECRET,
    })
    assert (settings.url, settings.agent_key, settings.webhook_secret) == (URL, KEY, SECRET)


def test_config_wins_over_the_environment():
    settings = snugban_settings(
        SnugbanConfig(url=URL, agent_key=KEY, webhook_secret=SECRET),
        environ={"SNUGBAN_WEBHOOK_SECRET": "other"},
    )
    assert settings.webhook_secret == SECRET


@pytest.mark.parametrize("missing", ["url", "agent_key", "webhook_secret"])
def test_a_partial_configuration_is_refused_naming_what_is_missing(missing):
    values = {"url": URL, "agent_key": KEY, "webhook_secret": SECRET}
    values[missing] = "  "
    with pytest.raises(ValueError) as error:
        snugban_settings(SnugbanConfig(**values), environ={})
    message = str(error.value)
    assert missing.replace("_", "") in message.replace("_", "").lower()
    assert KEY not in message and SECRET not in message


def test_a_url_that_is_not_http_is_refused():
    with pytest.raises(ValueError):
        snugban_settings(
            SnugbanConfig(url="snugban-host:7420", agent_key=KEY, webhook_secret=SECRET),
            environ={},
        )


def test_the_key_and_secret_never_appear_in_the_settings_repr():
    settings = snugban_settings(
        SnugbanConfig(url=URL, agent_key=KEY, webhook_secret=SECRET), environ={}
    )
    assert KEY not in repr(settings) and SECRET not in repr(settings)


def test_the_webhook_server_binds_the_configured_host_and_port():
    config = Config.model_validate({"gateway": {"host": "100.64.0.5", "port": 18800}})
    assert _webhook_address(config, None) == ("100.64.0.5", 18800)


def test_the_port_option_overrides_the_configured_port():
    config = Config.model_validate({"gateway": {"host": "127.0.0.1", "port": 18800}})
    assert _webhook_address(config, 18999) == ("127.0.0.1", 18999)


def test_the_default_address_is_unchanged():
    assert _webhook_address(Config(), None) == ("0.0.0.0", 18790)
