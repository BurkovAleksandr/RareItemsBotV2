import pytest

from assets.runtime import RuntimeConfig


pytestmark = pytest.mark.mock


def test_runtime_config_can_disable_proxies():
    config = RuntimeConfig.from_dict(
        {
            "API_KEY": "key",
            "PARSER_LOGIN": "parser",
            "PARSER_PASSWORD": "parser-pass",
            "PARSER_MAFILE": "parser.maFile",
            "BUYER_LOGIN": "buyer",
            "BUYER_PASSWORD": "buyer-pass",
            "BUYER_MAFILE": "buyer.maFile",
            "USE_PROXIES": False,
        }
    )

    assert config.use_proxies is False


def test_runtime_config_accepts_string_booleans():
    config = RuntimeConfig.from_dict(
        {
            "API_KEY": "key",
            "PARSER_LOGIN": "parser",
            "PARSER_PASSWORD": "parser-pass",
            "PARSER_MAFILE": "parser.maFile",
            "BUYER_LOGIN": "buyer",
            "BUYER_PASSWORD": "buyer-pass",
            "BUYER_MAFILE": "buyer.maFile",
            "USE_PROXIES": "0",
            "AUTOBUY": "1",
        }
    )

    assert config.use_proxies is False
    assert config.autobuy is True
    assert config.sticker_price_ttl_hours == 24


def test_runtime_config_accepts_sticker_price_ttl():
    config = RuntimeConfig.from_dict(
        {
            "API_KEY": "key",
            "PARSER_LOGIN": "parser",
            "PARSER_PASSWORD": "parser-pass",
            "PARSER_MAFILE": "parser.maFile",
            "BUYER_LOGIN": "buyer",
            "BUYER_PASSWORD": "buyer-pass",
            "BUYER_MAFILE": "buyer.maFile",
            "STICKER_PRICE_TTL_HOURS": "6",
        }
    )

    assert config.sticker_price_ttl_hours == 6


def test_runtime_config_resolves_env_reference(monkeypatch):
    monkeypatch.setenv("TEST_STEAM_API_KEY", "key-from-env")
    config = RuntimeConfig.from_dict(
        {
            "API_KEY": "env:TEST_STEAM_API_KEY",
            "PARSER_LOGIN": "parser",
            "PARSER_PASSWORD": "parser-pass",
            "PARSER_MAFILE": "parser.maFile",
            "BUYER_LOGIN": "buyer",
            "BUYER_PASSWORD": "buyer-pass",
            "BUYER_MAFILE": "buyer.maFile",
        }
    )

    assert config.api_key == "key-from-env"
