import pytest

from api_server import ApiState, build_dashboard, parse_config_update
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
