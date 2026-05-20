import pytest

from web_app import BotWebHandler, parse_track_items


pytestmark = pytest.mark.mock


def test_parse_track_items_from_names_and_urls():
    items = parse_track_items(
        "\n".join(
            [
                "AK-47 | Slate (Field-Tested)",
                "https://steamcommunity.com/market/listings/730/Nova%20%7C%20Predator%20%28Well-Worn%29",
            ]
        )
    )

    assert items == [
        (
            "AK-47 | Slate (Field-Tested)",
            "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Slate%20%28Field-Tested%29",
        ),
        (
            "Nova | Predator (Well-Worn)",
            "https://steamcommunity.com/market/listings/730/Nova%20%7C%20Predator%20%28Well-Worn%29",
        ),
    ]


def test_parse_track_items_expands_exteriors():
    items = parse_track_items("AK-47 | Slate", expand_exteriors=True)

    assert len(items) == 5
    assert items[0][0] == "AK-47 | Slate (Factory New)"


def test_secret_config_field_does_not_render_value():
    rendered = BotWebHandler.render_config_field(
        object(),
        {"API_KEY": "super-secret"},
        ("API_KEY", "Steam API key", "password", ""),
    )

    assert "super-secret" not in rendered
    assert 'placeholder="unchanged"' in rendered


def test_dashboard_widgets_render_core_values():
    dashboard = {
        "bot_state": "RUNNING",
        "bot_state_class": "ok",
        "buyer_session": {"login": "buyer", "active": True, "wallet_balance": "47.31", "source": "saved"},
        "parser_session": {"login": "parser", "active": False, "error": "stale", "source": "saved"},
        "tracked_count": 2,
        "purchase_count": 1,
        "recent_purchase_count": 1,
        "recent_checked_count": 1,
        "sticker_price_count": 5925,
        "recent_sticker_price_count": 8,
        "proxy_count": 0,
        "proxies_enabled": False,
        "checked_at": "2026-05-20 12:00:00",
    }

    handler = object.__new__(BotWebHandler)
    rendered = handler.render_metrics(dashboard)

    assert "RUNNING" in rendered
    assert "47.31 RUB" in rendered
    assert "parser via saved; stale" in rendered
    assert "Sticker prices" in rendered
