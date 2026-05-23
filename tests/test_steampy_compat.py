import base64

import pytest
import requests

from assets.steampy_compat import (
    MOBILE_APP_CODE_TYPE,
    _cookie_value,
    _decode_jwt_payload,
    _int_from_steam_key,
    _patched_update_steam_guard,
    apply_steampy_compat,
)

apply_steampy_compat()

from steampy.models import Asset, GameOptions


pytestmark = pytest.mark.mock


def test_asset_from_dict_compatibility():
    asset = Asset.from_dict(
        {
            "app_id": 730,
            "context_id": "2",
            "assetid": "11111111",
            "amount": "1",
        }
    )

    assert asset.to_dict() == {
        "appid": 730,
        "contextid": "2",
        "amount": 1,
        "assetid": "11111111",
    }


def test_steam_key_decoder_accepts_hex_and_base64():
    assert _int_from_steam_key("010001") == 65537
    assert _int_from_steam_key(base64.b64encode((65537).to_bytes(3, "big")).decode()) == 65537


def test_cookie_lookup_handles_duplicate_names_across_domains():
    session = requests.Session()
    session.cookies.set("sessionid", "community", domain="steamcommunity.com", path="/")
    session.cookies.set("sessionid", "store", domain="store.steampowered.com", path="/")

    assert _cookie_value(session, "sessionid", ("steamcommunity.com",)) == "community"


def test_jwt_payload_decoder_reads_expiration_claims():
    payload = base64.urlsafe_b64encode(b'{"sub":"76561198187797831","exp":4102444800}').decode().rstrip("=")

    assert _decode_jwt_payload(f"header.{payload}.signature") == {
        "sub": "76561198187797831",
        "exp": 4102444800,
    }


class FakeAuthResponse:
    status_code = 200
    text = "{}"

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeLoginExecutor:
    def __init__(self):
        self.shared_secret = "shared-secret"
        self.session = requests.Session()
        self.session._steam_auth_debug = {}
        self.api_calls = []
        self.poll_calls = []

    def _api_call(self, method, service, endpoint, version="v1", params=None):
        self.api_calls.append(
            {
                "method": method,
                "service": service,
                "endpoint": endpoint,
                "version": version,
                "params": dict(params or {}),
            }
        )
        return FakeAuthResponse({"response": {}})

    def _pool_sessions_steam(self, client_id, request_id):
        self.poll_calls.append((client_id, request_id))


def test_steam_guard_update_submits_numeric_mobile_code_type(monkeypatch):
    executor = FakeLoginExecutor()
    monkeypatch.setattr(
        "assets.steampy_compat.guard.generate_one_time_code",
        lambda shared_secret: "ABCDE",
    )

    _patched_update_steam_guard(
        executor,
        {
            "client_id": "123",
            "request_id": "request",
            "steamid": "76561198187797831",
        },
    )

    params = executor.api_calls[0]["params"]
    assert params["code"] == "ABCDE"
    assert params["code_type"] == MOBILE_APP_CODE_TYPE
    assert params["code_type"] == 3
    assert executor.poll_calls == [("123", "request")]
    assert executor.session._steam_auth_debug["steam_guard_code"]["submitted"] is True
