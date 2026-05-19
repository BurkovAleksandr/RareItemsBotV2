import base64

import pytest
import requests

from assets.steampy_compat import (
    _cookie_value,
    _decode_jwt_payload,
    _int_from_steam_key,
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
