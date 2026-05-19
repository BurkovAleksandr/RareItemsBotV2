import pytest

from assets.steam_market_client import (
    SteamMarketClient,
    build_buy_listing_data,
    extract_confirmation_id,
    is_buy_success,
)
from steampy.models import Currency, GameOptions


pytestmark = pytest.mark.mock


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = []

    def post(self, url, data=None, headers=None):
        self.posts.append({"url": url, "data": dict(data or {}), "headers": dict(headers or {})})
        return FakeResponse(self.responses.pop(0))


class FakeSteamClient:
    def __init__(self, responses):
        self._session = FakeSession(responses)
        self.steam_guard = {"steamid": "76561198187797831"}

    def _get_session_id(self):
        return "session"


class ConfirmingMarketClient(SteamMarketClient):
    def __init__(self, steam_client):
        super().__init__(steam_client)
        self.confirmed_ids = []

    def confirm_mobile_confirmation(self, confirmation_id, timeout_seconds=60, interval_seconds=2):
        self.confirmed_ids.append(confirmation_id)
        return {"success": True}


def test_build_buy_listing_data_matches_browser_form():
    data = build_buy_listing_data(
        session_id="session",
        currency=Currency.RUB,
        subtotal=73,
        fee=146,
        total=219,
        confirmation_id="123",
    )

    assert data == {
        "sessionid": "session",
        "currency": str(Currency.RUB.value),
        "subtotal": "73",
        "fee": "146",
        "total": "219",
        "quantity": "1",
        "billing_state": "",
        "save_my_address": "0",
        "tradefee_tax": "0",
        "confirmation": "123",
    }


def test_extract_confirmation_id_from_market_response():
    payload = {"need_confirmation": True, "confirmation": {"confirmation_id": "15228525094114004428"}}

    assert extract_confirmation_id(payload) == "15228525094114004428"


def test_buy_listing_returns_success_without_confirmation():
    steam_client = FakeSteamClient([{"wallet_info": {"success": 1, "wallet_balance": "4731"}}])
    market_client = SteamMarketClient(steam_client)

    result = market_client.buy_listing(
        market_name="SCAR-20 | Short Ochre (Field-Tested)",
        listing_id="492725060002281617",
        total=219,
        fee=146,
        game=GameOptions.CS,
        currency=Currency.RUB,
    )

    assert result.success is True
    assert str(result.wallet_balance) == "47.31"
    assert steam_client._session.posts[0]["data"]["confirmation"] == "0"


def test_buy_listing_completes_after_mobile_confirmation():
    confirmation_id = "15228525094114004428"
    steam_client = FakeSteamClient(
        [
            {"need_confirmation": True, "confirmation": {"confirmation_id": confirmation_id}, "success": 22},
            {"wallet_info": {"success": 1, "wallet_balance": "4731"}},
        ]
    )
    market_client = ConfirmingMarketClient(steam_client)

    result = market_client.buy_listing(
        market_name="SCAR-20 | Short Ochre (Field-Tested)",
        listing_id="492725060002281617",
        total=219,
        fee=146,
        game=GameOptions.CS,
        currency=Currency.RUB,
    )

    assert result.success is True
    assert is_buy_success(result.completion_response)
    assert market_client.confirmed_ids == [confirmation_id]
    assert steam_client._session.posts[0]["data"]["confirmation"] == "0"
    assert steam_client._session.posts[1]["data"]["confirmation"] == confirmation_id
