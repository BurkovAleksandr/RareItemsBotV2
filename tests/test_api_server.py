import pytest
from fastapi.testclient import TestClient

from api_server import ApiState, build_dashboard, create_app, parse_config_update
from assets.database import SqliteItemsRepository
from assets.runtime import write_config_data


pytestmark = pytest.mark.mock


def test_parse_config_update_preserves_masked_secrets():
    current = {"API_KEY": "real-key", "AUTOBUY": False, "STRICK3": 1}

    updated = parse_config_update(
        current,
        {"API_KEY": "********", "AUTOBUY": "on", "STRICK3": "2.5"},
    )

    assert updated["API_KEY"] == "real-key"
    assert updated["AUTOBUY"] is True
    assert updated["STRICK3"] == 2.5


def test_build_dashboard_returns_core_payload(tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "items.db"
    proxies_path = tmp_path / "proxies.txt"
    write_config_data(
        str(config_path),
        {
            "DB_PATH": str(db_path),
            "PROXIES_PATH": str(proxies_path),
            "USE_PROXIES": False,
        },
    )

    state = ApiState(str(config_path))
    try:
        payload = build_dashboard(state)
    finally:
        state.shutdown()

    assert payload["dashboard"]["bot_state"] == "STOPPED"
    assert payload["dashboard"]["tracked_count"] == 0
    assert payload["dashboard"]["sticker_price_count"] == 0
    assert payload["items_text"] == ""


def test_checked_items_endpoint_filters_streaks(tmp_path):
    config_path = tmp_path / "config.json"
    db_path = tmp_path / "items.db"
    write_config_data(str(config_path), {"DB_PATH": str(db_path), "USE_PROXIES": False})

    repository = SqliteItemsRepository(str(db_path))
    repository.add_checked_item_details(
        item_name="AK-47 | Redline",
        listing_id="listing-1",
        price=20.0,
        stickers_price=35.0,
        stickers=[
            {"name": "Sticker | Crown", "price": 10},
            {"name": "Sticker | Crown", "price": 10},
            {"name": "Sticker | Crown", "price": 10},
            {"name": "Sticker | Other", "price": 5},
        ],
        checked_at="2026-05-20 11:00:00",
    )
    repository.add_checked_item_details(
        item_name="Nova | Predator",
        listing_id="listing-2",
        price=15.0,
        stickers_price=5.0,
        stickers=[{"name": "Sticker | Solo", "price": 5}],
        checked_at="2026-05-21 11:00:00",
    )

    app = create_app(str(config_path))
    with TestClient(app) as client:
        response = client.get(
            "/api/checked-items",
            params={"has_streak": "true", "min_stickers_price": 30},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["items"][0]["listing_id"] == "listing-1"
    assert payload["items"][0]["streak"]["count"] == 3
