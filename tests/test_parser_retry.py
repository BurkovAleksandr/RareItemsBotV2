import asyncio
import json

import pytest

from assets.parser import AsyncParser, build_market_render_url, merge_render_assets


pytestmark = pytest.mark.mock


class FakeResponse:
    def __init__(self, status: int, text: str):
        self.status = status
        self._text = text

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
        self.owner.requests.append({"args": args, "kwargs": kwargs})
        return self.owner.responses.pop(0)


class FakeSteamSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.requests = []

    def get_async_session(self):
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

    assert session.calls == 2
    assert "/render?" in session.requests[1]["args"][0]
    assert items == [
        {
            "listing_id": "123",
            "inspect_link": "steam://inspect/123/asset-1",
            "price": 115,
            "price_no_fee": 100,
            "fee": 15,
        }
    ]


def test_merge_render_assets_keeps_listing_without_asset_description():
    payload = {"listinginfo": {"123": {"asset": {"appid": 730, "contextid": "2", "id": "missing"}}}}

    assert merge_render_assets(payload) == payload["listinginfo"]
