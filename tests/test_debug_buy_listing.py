import pytest

from debug_buy_listing import (
    MobileConfirmation,
    ListingCandidate,
    build_buy_listing_data,
    extract_confirmation_id,
    _is_probable_steam_id64,
    is_buy_success,
    is_confirmation_success,
    parse_listing_candidates,
    parse_wallet_info,
    purchase_verification_succeeded,
    select_candidate,
)


pytestmark = pytest.mark.mock


def test_parse_listing_candidates_from_market_html():
    html = """
    <script>
    var g_rgAssets = {"730":{"2":{"asset-1":{"market_hash_name":"AK-47 | Slate (Field-Tested)"}}}};
    var g_rgListingInfo = {"123456":{"listingid":"123456","converted_price":1000,"converted_fee":150,"converted_currencyid":5,"asset":{"appid":730,"contextid":"2","id":"asset-1"}}};
    </script>
    """

    candidates = parse_listing_candidates(html)

    assert len(candidates) == 1
    assert candidates[0].listing_id == "123456"
    assert candidates[0].market_name == "AK-47 | Slate (Field-Tested)"
    assert candidates[0].subtotal == 1000
    assert candidates[0].fee == 150
    assert candidates[0].total == 1150
    assert candidates[0].currency_id == 5


def test_parse_wallet_info_from_market_html():
    html = """
    <script>
    var g_rgWalletInfo = {"wallet_currency":5,"wallet_balance":6353};
    </script>
    """

    wallet_info = parse_wallet_info(html)

    assert wallet_info["wallet_currency"] == 5
    assert wallet_info["wallet_balance"] == 6353


def test_select_candidate_by_listing_id():
    candidates = parse_listing_candidates(
        """
        <script>
        var g_rgListingInfo = {"1":{"converted_price":100,"converted_fee":15},"2":{"converted_price":200,"converted_fee":30}};
        </script>
        """,
        market_name_override="Test Item",
    )

    assert select_candidate(candidates, "2").listing_id == "2"


def test_extract_confirmation_id_from_buy_response():
    payload = {
        "need_confirmation": True,
        "confirmation": {"confirmation_id": "12807629570442145278"},
        "success": 22,
    }

    assert extract_confirmation_id(payload) == "12807629570442145278"


def test_probable_steam_id64_validation():
    assert _is_probable_steam_id64("76561198187797831")
    assert not _is_probable_steam_id64("2275832103")
    assert not _is_probable_steam_id64("")


def test_mobile_confirmation_matches_creator_id_from_buy_response():
    confirmation = MobileConfirmation(
        data_confid="20630129032",
        nonce="123",
        creator_id="2432986906225858653",
        type=12,
        type_name="Purchase",
    )

    assert confirmation.matches("20630129032")
    assert confirmation.matches("2432986906225858653")
    assert not confirmation.matches("999")


def test_buy_and_confirmation_success_helpers():
    assert is_buy_success({"wallet_info": {"success": 1}})
    assert not is_buy_success({"success": 22})
    assert is_confirmation_success({"response": {"success": True}})
    assert not is_confirmation_success({"response": {"success": False}})
    assert not purchase_verification_succeeded({"confirmation_removed": True})
    assert purchase_verification_succeeded({"balance_decreased": True})
    assert purchase_verification_succeeded({"final_check": {"asset_found_in_inventory": True}})
    assert not purchase_verification_succeeded({"final_check": {"confirmation_removed": True}})


def test_build_buy_listing_data_matches_browser_form_fields():
    candidate = ListingCandidate(
        listing_id="492725060002290365",
        market_name="SCAR-20 | Short Ochre (Field-Tested)",
        subtotal=73,
        fee=146,
        total=219,
        currency_id=5,
    )

    data = build_buy_listing_data("session", candidate, wallet_currency=5)

    assert data == {
        "sessionid": "session",
        "currency": "5",
        "subtotal": "73",
        "fee": "146",
        "total": "219",
        "quantity": "1",
        "billing_state": "",
        "save_my_address": "0",
        "tradefee_tax": "0",
        "confirmation": "0",
    }

    confirmed_data = build_buy_listing_data("session", candidate, wallet_currency=5, confirmation_id="123")
    assert confirmed_data["confirmation"] == "123"
