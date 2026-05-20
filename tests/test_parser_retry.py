import asyncio
import json

import pytest

from assets.parser import (
    AsyncParser,
    build_market_filters_from_name,
    build_market_render_url,
    build_market_search_body,
    merge_render_assets,
    normalize_market_search_payload,
    requested_market_name_from_listing_url,
)


pytestmark = pytest.mark.mock


class FakeResponse:
    def __init__(self, status: int, text: str, url: str | None = None):
        self.status = status
        self._text = text
        self.url = url

    async def text(self):
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
        self.owner.calls += 1
        self.owner.requests.append({"method": "POST", "args": args, "kwargs": kwargs})
        return self.owner.responses.pop(0)


class FakeSteamSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.requests = []

    def get_async_session(self, url=None):
        return FakeClientSession(self)


def test_async_parser_retries_rate_limited_market_request():
    session = FakeSteamSession(
        [
            FakeResponse(429, "rate limited"),
            FakeResponse(200, "ok"),
        ]
    )
    parser = AsyncParser(session, request_timeout=1, max_retries=2, retry_base_delay=0)

    text = asyncio.run(parser.get_raw_data_from_market("https://steamcommunity.com/market/listings/730/Test"))

    assert text == "ok"
    assert session.calls == 2


def test_build_market_render_url():
    render_url = build_market_render_url(
        "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Slate%20%28Field-Tested%29"
    )

    assert render_url.startswith(
        "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Slate%20%28Field-Tested%29/render?"
    )
    assert "start=0" in render_url
    assert "count=10" in render_url
    assert "currency=5" in render_url


def test_build_market_search_body_uses_exact_item_filters():
    body = build_market_search_body(
        "https://steamcommunity.com/market/listings/730/G1807208B083004",
        requested_market_name="AK-47 | Slate (Field-Tested)",
    )

    assert body == [
        {
            "appid": 730,
            "strItemName": "G1807208B083004",
            "filters": {"Quality": ["normal"], "Exterior": ["WearCategory2"]},
            "accessoryFilters": {},
            "propertyFilters": {},
            "start": 0,
        }
    ]


def test_build_market_filters_from_name_handles_stattrak_exterior():
    assert build_market_filters_from_name("StatTrak™ AK-47 | Slate (Well-Worn)") == {
        "Quality": ["strange"],
        "Exterior": ["WearCategory3"],
    }


def test_requested_market_name_ignores_new_market_group_ids():
    assert (
        requested_market_name_from_listing_url(
            "https://steamcommunity.com/market/listings/730/G1807208B083004"
        )
        == ""
    )


def test_async_parser_falls_back_to_render_endpoint_when_listing_info_missing():
    payload = {
        "success": True,
        "listinginfo": {
            "123": {
                "converted_price": 100,
                "converted_fee": 15,
                "asset": {"appid": 730, "contextid": "2", "id": "asset-1"},
            }
        },
        "assets": {
            "730": {
                "2": {
                    "asset-1": {
                        "market_actions": [
                            {
                                "link": "steam://inspect/%listingid%/%assetid%",
                                "name": "Inspect in Game...",
                            }
                        ]
                    }
                }
            }
        },
    }
    session = FakeSteamSession(
        [
            FakeResponse(200, "<html><title>Steam Community</title></html>"),
            FakeResponse(500, "new market search unavailable"),
            FakeResponse(200, json.dumps(payload)),
        ]
    )
    parser = AsyncParser(session, request_timeout=1, max_retries=1, retry_base_delay=0)

    listing_info = asyncio.run(
        parser.get_listing_info_from_market(
            "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Slate%20%28Field-Tested%29"
        )
    )
    items = parser.extract_item_data(listing_info)

    assert session.calls == 3
    assert session.requests[1]["method"] == "POST"
    assert session.requests[2]["method"] == "GET"
    assert "/render?" in session.requests[2]["args"][0]
    assert items == [
        {
            "listing_id": "123",
            "inspect_link": "steam://inspect/123/asset-1",
            "price": 115,
            "price_no_fee": 100,
            "fee": 15,
            "asset_id": "asset-1",
            "appid": 730,
            "contextid": "2",
            "market_name": "",
            "float_value": None,
            "pattern_template": None,
            "item_certificate": None,
            "stickers": [],
            "charm": {},
        }
    ]


def test_async_parser_uses_new_market_search_before_render_fallback():
    payload = {
        "more": True,
        "start": 0,
        "total_count": 1,
        "listings": [
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
        ],
    }
    session = FakeSteamSession(
        [
            FakeResponse(
                200,
                "<html><title>AK-47 | Slate</title></html>",
                url="https://steamcommunity.com/market/listings/730/G1807208B083004",
            ),
            FakeResponse(200, json.dumps(payload)),
        ]
    )
    parser = AsyncParser(session, request_timeout=1, max_retries=1, retry_base_delay=0)

    listing_info = asyncio.run(
        parser.get_listing_info_from_market(
            "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Slate%20%28Field-Tested%29"
        )
    )
    items = parser.extract_item_data(listing_info)

    assert session.requests[1]["method"] == "POST"
    assert session.requests[1]["args"][0] == "https://steamcommunity.com/market/listings/730/G1807208B083004"
    assert session.requests[1]["kwargs"]["json"][0]["filters"] == {
        "Quality": ["normal"],
        "Exterior": ["WearCategory2"],
    }
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


def test_normalize_market_search_payload_filters_mixed_bucket_listings():
    payload = {
        "listings": [
            {
                "listingid": "1",
                "unPrice": 100,
                "unFee": 15,
                "eCurrency": 5,
                "description": {"market_hash_name": "AK-47 | Slate (Battle-Scarred)"},
                "asset": {"id": "asset-1"},
            },
            {
                "listingid": "2",
                "unPrice": 200,
                "unFee": 30,
                "eCurrency": 5,
                "description": {"market_hash_name": "AK-47 | Slate (Field-Tested)"},
                "asset": {"id": "asset-2"},
            },
        ]
    }

    listing_info = normalize_market_search_payload(
        payload,
        requested_market_name="AK-47 | Slate (Field-Tested)",
    )

    assert list(listing_info) == ["2"]
    assert listing_info["2"]["converted_price"] == 200
    assert listing_info["2"]["converted_fee"] == 30


def test_async_parser_uses_final_redirect_url_for_render_fallback():
    payload = {
        "success": True,
        "listinginfo": {
            "123": {
                "converted_price": 100,
                "converted_fee": 15,
                "asset": {"appid": 730, "contextid": "2", "id": "asset-1"},
            }
        },
        "assets": {},
    }
    original_url = (
        "https://steamcommunity.com/market/listings/730/"
        "AK-47%20%7C%20Slate%20%28Field-Tested%29"
    )
    final_url = "https://steamcommunity.com/market/listings/730/G1807208B083004"
    session = FakeSteamSession(
        [
            FakeResponse(
                200,
                "<html><title>AK-47 | Slate - Steam Community Market</title></html>",
                url=final_url,
            ),
            FakeResponse(500, "new market search unavailable"),
            FakeResponse(200, json.dumps(payload)),
        ]
    )
    parser = AsyncParser(session, request_timeout=1, max_retries=1, retry_base_delay=0)

    listing_info = asyncio.run(parser.get_listing_info_from_market(original_url))

    assert listing_info == payload["listinginfo"]
    assert session.requests[1]["method"] == "POST"
    assert session.requests[1]["args"][0] == final_url
    assert session.requests[2]["args"][0].startswith(f"{final_url}/render?")
    assert session.requests[2]["kwargs"]["headers"] == {"Referer": final_url}


def test_merge_render_assets_keeps_listing_without_asset_description():
    payload = {"listinginfo": {"123": {"asset": {"appid": 730, "contextid": "2", "id": "missing"}}}}

    assert merge_render_assets(payload) == payload["listinginfo"]


def test_async_parser_extracts_market_page_asset_metadata():
    assets = {
        "730": {
            "2": {
                "asset-1": {
                    "appid": 730,
                    "contextid": "2",
                    "id": "asset-1",
                    "market_hash_name": "AK-47 | Slate (Field-Tested)",
                    "market_actions": [
                        {"link": "steam://inspect/%listingid%/%assetid%", "name": "Inspect in Game..."}
                    ],
                    "asset_properties": [
                        {"propertyid": 1, "int_value": "570"},
                        {"propertyid": 2, "float_value": "0.359291106462478638"},
                        {"propertyid": 6, "string_value": "certificate"},
                    ],
                    "descriptions": [
                        {
                            "type": "html",
                            "name": "sticker_info",
                            "value": (
                                '<div><img src="sticker.png" '
                                'title="Sticker: Bad News Eagles (Glitter) | Paris 2023"></div>'
                            ),
                        },
                        {
                            "type": "html",
                            "name": "keychain_info",
                            "value": '<div><img src="charm.png" title="Charm: Biomech"></div>',
                        },
                    ],
                }
            }
        }
    }
    listing_info = {
        "listing-1": {
            "converted_price": 100,
            "converted_fee": 15,
            "asset": {"appid": 730, "contextid": "2", "id": "asset-1"},
        }
    }
    html = (
        '<script type="text/javascript">'
        f"var g_rgAssets = {json.dumps(assets)};\n"
        f"var g_rgListingInfo = {json.dumps(listing_info)};\n"
        "</script>"
    )
    session = FakeSteamSession([FakeResponse(200, html)])
    parser = AsyncParser(session, request_timeout=1, max_retries=1, retry_base_delay=0)

    parsed_listing_info = asyncio.run(
        parser.get_listing_info_from_market(
            "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Slate%20%28Field-Tested%29"
        )
    )
    items = parser.extract_item_data(parsed_listing_info)

    assert items == [
        {
            "listing_id": "listing-1",
            "inspect_link": "steam://inspect/listing-1/asset-1",
            "price": 115,
            "price_no_fee": 100,
            "fee": 15,
            "asset_id": "asset-1",
            "appid": 730,
            "contextid": "2",
            "market_name": "AK-47 | Slate (Field-Tested)",
            "float_value": 0.35929110646247864,
            "pattern_template": 570,
            "item_certificate": "certificate",
            "stickers": [
                {
                    "name": "Bad News Eagles (Glitter) | Paris 2023",
                    "icon_url": "sticker.png",
                }
            ],
            "charm": {"name": "Biomech", "icon_url": "charm.png"},
        }
    ]
