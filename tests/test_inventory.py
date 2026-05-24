import pytest

from assets.inventory import (
    build_inventory_cards,
    calculate_sale_price,
    fetch_steam_inventory,
    normalize_active_listings,
    normalize_inventory_item,
    target_buyer_price_to_receive,
)


pytestmark = pytest.mark.mock


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self.payload = payload
        self.text = str(payload)

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.requests = []

    def get(self, url, params=None, headers=None):
        self.requests.append({"url": url, "params": params, "headers": headers})
        if not self.payloads:
            raise AssertionError("No fake Steam response left")
        return FakeResponse(self.payloads.pop(0))


class FakeClient:
    steam_guard = {"steamid": "76561198187797831"}

    def __init__(self, payloads, steam_id="76561198187797831"):
        self._session = FakeSession(payloads)
        self.steam_id = steam_id

    def get_steam_id(self):
        return self.steam_id


def test_normalize_inventory_item_extracts_trade_lock():
    item = normalize_inventory_item(
        {
            "id": "asset-1",
            "market_hash_name": "AK-47 | Redline (Field-Tested)",
            "icon_url": "icon/path",
            "tradable": 0,
            "marketable": 0,
            "owner_descriptions": [
                {"value": "Tradable After May 31, 2026 (7:00:00) GMT"},
                {"value": "This item is listed on the Steam Community Market."},
            ],
        }
    )

    assert item["asset_id"] == "asset-1"
    assert item["trade_lock"]["locked"] is True
    assert item["trade_lock"]["available_at"] == "2026-05-31 07:00:00"
    assert item["listed_on_market"] is True
    assert item["icon_url"].endswith("icon/path")


def test_build_inventory_cards_keeps_only_bought_items_and_listing_state():
    purchases = [
        {
            "purchase_id": 1,
            "item_name": "AK-47 | Redline (Field-Tested)",
            "price": "100",
            "stickers_price": "25",
            "success": True,
            "stickers": [{"name": "Sticker | Crown", "price": 25}],
        }
    ]
    inventory = {
        "asset-1": {
            "id": "asset-1",
            "market_hash_name": "AK-47 | Redline (Field-Tested)",
            "tradable": 1,
            "marketable": 1,
        },
        "asset-2": {
            "id": "asset-2",
            "market_hash_name": "Nova | Predator (Field-Tested)",
            "tradable": 1,
            "marketable": 1,
        },
    }
    active_listings = normalize_active_listings(
        {
            "sell_listings": {
                "sell-1": {
                    "buyer_pay": "150 RUB",
                    "you_receive": "132 RUB",
                    "description": {"id": "asset-1", "market_hash_name": "AK-47 | Redline (Field-Tested)"},
                }
            }
        }
    )

    cards = build_inventory_cards(
        purchases=purchases,
        inventory=inventory,
        active_listings=active_listings,
        market_price_lookup=lambda _: 120,
    )

    assert len(cards) == 1
    assert cards[0]["asset_id"] == "asset-1"
    assert cards[0]["listed"] is True
    assert cards[0]["listing"]["listing_id"] == "sell-1"
    assert cards[0]["suggestion"]["base_source"] == "steam"
    assert cards[0]["suggestion"]["suggested_price"] == 160.6


def test_sale_price_formula_uses_fee_and_first_sticker():
    suggestion = calculate_sale_price(base_price=100, first_sticker_price=25)

    assert suggestion["suggested_price"] == 138.0
    assert target_buyer_price_to_receive(138.0) == 122.12


def test_fetch_steam_inventory_error_contains_steam_payload():
    client = FakeClient(
        [
            {"success": 8, "Error": "Inventory is not available"},
            {"success": 8, "Error": "Inventory is not available"},
            {"success": 8, "Error": "Inventory is not available"},
            {"success": 8, "Error": "Inventory is not available"},
        ]
    )

    with pytest.raises(Exception) as exc_info:
        fetch_steam_inventory(client)

    message = str(exc_info.value)
    assert "success=8" in message
    assert "Inventory is not available" in message
    assert "count=75" in message


def test_fetch_steam_inventory_falls_back_to_legacy_endpoint():
    client = FakeClient(
        [
            None,
            None,
            None,
            {
                "success": True,
                "rgInventory": {
                    "asset-1": {
                        "id": "asset-1",
                        "classid": "class-1",
                        "instanceid": "instance-1",
                        "amount": "1",
                    }
                },
                "rgDescriptions": {
                    "class-1_instance-1": {
                        "name": "AK-47 | Redline",
                        "market_hash_name": "AK-47 | Redline",
                        "tradable": 1,
                        "marketable": 1,
                    }
                },
            },
        ]
    )

    inventory = fetch_steam_inventory(client)

    assert "asset-1" in inventory
    assert inventory["asset-1"]["market_hash_name"] == "AK-47 | Redline"
    assert "/inventory/json/730/2" in client._session.requests[-1]["url"]
