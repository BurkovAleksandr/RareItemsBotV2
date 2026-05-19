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
