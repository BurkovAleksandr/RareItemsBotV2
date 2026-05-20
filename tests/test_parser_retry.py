import asyncio
import codecs
import json

import pytest

from assets.parser import AsyncParser, normalize_new_market_listings


pytestmark = pytest.mark.mock


class FakeResponse:
    def __init__(self, status: int, text: str, url: str | None = None):
        self.status = status
        self._text = text
        self.url = url

    async def text(self, *args, **kwargs):
        return self._text


class FakeClientSession:
    def __init__(self, owner):
        self.owner = owner

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, *args, **kwargs):
        self.owner.calls += 1
        self.owner.requests.append({"method": "GET", "args": args, "kwargs": kwargs})
        return self.owner.responses.pop(0)

    async def post(self, *args, **kwargs):
        raise AssertionError("Market parser must not use POST requests")


class FakeSteamSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.requests = []

    def get_async_session(self, url=None):
        return FakeClientSession(self)


def _escaped_market_html(listings: list[dict]) -> str:
    payload = json.dumps({"state": {"data": {"pages": [{"listings": listings}]}}})
    escaped = codecs.encode(payload, "unicode_escape").decode("ascii")
    escaped = codecs.encode(escaped, "unicode_escape").decode("ascii")
    return f"<html><script>{escaped}</script></html>"


def test_async_parser_retries_rate_limited_market_request():
    session = FakeSteamSession(
        [
            FakeResponse(429, "rate limited"),
            FakeResponse(200, "ok"),
        ]
    )
    parser = AsyncParser(session, request_timeout=1, max_retries=2, retry_base_delay=0)

    text = asyncio.run(
        parser.get_raw_data_from_market(
            "https://steamcommunity.com/market/listings/730/Test"
        )
    )

    assert text == "ok"
    assert session.calls == 2
    assert [request["method"] for request in session.requests] == ["GET", "GET"]


def test_normalize_new_market_listings_maps_new_page_shape():
    listing_info = normalize_new_market_listings(
        [
            {
                "listingid": "listing-1",
                "unPrice": 37090,
                "unFee": 5563,
                "eCurrency": 5,
                "description": {
                    "market_hash_name": "AK-47 | Slate (Field-Tested)",
                    "market_actions": [
                        {
                            "link": "steam://inspect/%listingid%/%assetid%",
                            "name": "Inspect in Game...",
                        }
                    ],
                },
                "asset": {
                    "appid": 730,
                    "contextid": "2",
                    "id": "asset-1",
                    "asset_properties": [
                        {"propertyid": 1, "int_value": "680"},
                        {"propertyid": 2, "float_value": 0.6228509545326233},
                    ],
                    "accessory_properties": [
                        {
                            "classid": "7729073545",
                            "parent_relationship_properties": [
                                {"propertyid": 4, "float_value": 0}
                            ],
                            "description": {
                                "type": "High Grade Sticker",
                                "market_hash_name": "Sticker | Test",
                                "icon_url": "sticker.png",
                            },
                        }
                    ],
                },
            }
        ]
    )

    parser = AsyncParser(FakeSteamSession([]), request_timeout=1)
    items = parser.extract_item_data(listing_info)

    assert items == [
        {
            "listing_id": "listing-1",
            "inspect_link": "steam://inspect/listing-1/asset-1",
            "price": 42653,
            "price_no_fee": 37090,
            "fee": 5563,
            "asset_id": "asset-1",
            "appid": 730,
            "contextid": "2",
            "market_name": "AK-47 | Slate (Field-Tested)",
            "float_value": 0.6228509545326233,
            "pattern_template": 680,
            "item_certificate": None,
            "stickers": [
                {
                    "name": "Sticker | Test",
                    "icon_url": "sticker.png",
                    "classid": "7729073545",
                    "wear": 0,
                }
            ],
            "charm": {},
        }
    ]


def test_async_parser_gets_market_page_and_parses_embedded_listings():
    listings = [
        {
            "listingid": "listing-1",
            "unPrice": 100,
            "unFee": 15,
            "eCurrency": 5,
            "description": {
                "market_hash_name": "AK-47 | Slate (Field-Tested)",
                "market_actions": [
                    {
                        "link": "steam://inspect/%listingid%/%assetid%",
                        "name": "Inspect in Game...",
                    }
                ],
            },
            "asset": {
                "appid": 730,
                "contextid": "2",
                "id": "asset-1",
                "asset_properties": [
                    {"propertyid": 1, "int_value": "570"},
                    {"propertyid": 2, "float_value": "0.359291106462478638"},
                    {"propertyid": 6, "string_value": "certificate"},
                ],
            },
        }
    ]
    session = FakeSteamSession(
        [
            FakeResponse(
                200,
                _escaped_market_html(listings),
                url="https://steamcommunity.com/market/listings/730/G1807208B083004",
            )
        ]
    )
    parser = AsyncParser(session, request_timeout=1, max_retries=1, retry_base_delay=0)

    listing_info = asyncio.run(
        parser.get_listing_info_from_market(
            "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Slate%20%28Field-Tested%29"
        )
    )
    items = parser.extract_item_data(listing_info)

    assert [request["method"] for request in session.requests] == ["GET"]
    assert items[0]["listing_id"] == "listing-1"
    assert items[0]["inspect_link"] == "steam://inspect/listing-1/asset-1"
    assert items[0]["price"] == 115
    assert items[0]["float_value"] == 0.35929110646247864
    assert items[0]["pattern_template"] == 570
    assert items[0]["item_certificate"] == "certificate"
