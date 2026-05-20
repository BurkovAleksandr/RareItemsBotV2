import pytest

from assets.database import SqliteItemsRepository


pytestmark = pytest.mark.mock


def test_track_items_are_unique_by_url(tmp_path):
    repository = SqliteItemsRepository(str(tmp_path / "items.db"))

    saved = repository.replace_track_items(
        [
            ("AK-47 | Slate", "https://steamcommunity.com/market/listings/730/AK"),
            ("AK-47 | Slate copy", "https://steamcommunity.com/market/listings/730/AK"),
        ]
    )
    added = repository.add_track_items(
        [
            ("AK-47 | Slate duplicate", "https://steamcommunity.com/market/listings/730/AK"),
            ("Nova | Predator", "https://steamcommunity.com/market/listings/730/Nova"),
        ]
    )

    assert saved == 1
    assert added == 1
    assert repository.get_track_items() == [
        {"AK-47 | Slate": "https://steamcommunity.com/market/listings/730/AK"},
        {"Nova | Predator": "https://steamcommunity.com/market/listings/730/Nova"},
    ]


def test_recent_bought_items_are_ordered_by_date(tmp_path):
    repository = SqliteItemsRepository(str(tmp_path / "items.db"))

    repository.add_to_bought_items("First item", "1", 10.5, 20.0, "2026-05-19 10:00:00")
    repository.add_to_bought_items("Second item", "2", 11.5, 21.0, "2026-05-20 10:00:00")

    assert repository.count_bought_items() == 2
    assert repository.get_recent_bought_items(limit=1) == [
        {
            "item_name": "Second item",
            "listing_id": "2",
            "price": "11.5",
            "stickers_price": "21.0",
            "date": "2026-05-20 10:00:00",
        }
    ]


def test_recent_checked_items_store_debug_details(tmp_path):
    repository = SqliteItemsRepository(str(tmp_path / "items.db"))

    repository.add_checked_item_details(
        item_name="AK-47 | Slate",
        listing_id="listing-1",
        price=100.0,
        stickers_price=25.5,
        float_value=0.123,
        pattern_template=777,
        stickers=[{"name": "Sticker | Test", "price": 25.5}],
        profitable=True,
        checked_at="2026-05-20 11:00:00",
    )

    assert repository.get_recent_checked_items(limit=1) == [
        {
            "item_name": "AK-47 | Slate",
            "listing_id": "listing-1",
            "price": 100.0,
            "stickers_price": 25.5,
            "float_value": 0.123,
            "pattern_template": "777",
            "profitable": True,
            "stickers": [{"name": "Sticker | Test", "price": 25.5}],
            "checked_at": "2026-05-20 11:00:00",
        }
    ]
