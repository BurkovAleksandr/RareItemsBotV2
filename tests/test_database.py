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
    repository.add_to_bought_items(
        "Second item",
        "2",
        11.5,
        21.0,
        "2026-05-20 10:00:00",
        success=False,
        error="Somebody already bought it",
    )

    assert repository.count_bought_items() == 2
    assert repository.get_recent_bought_items(limit=1) == [
        {
            "item_name": "Second item",
            "listing_id": "2",
            "price": "11.5",
            "stickers_price": "21.0",
            "date": "2026-05-20 10:00:00",
            "success": False,
            "status": "failed",
            "error": "Somebody already bought it",
            "market_url": "https://steamcommunity.com/market/listings/730/Second%20item",
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

    item = repository.get_recent_checked_items(limit=1)[0]

    assert item["item_name"] == "AK-47 | Slate"
    assert item["listing_id"] == "listing-1"
    assert item["price"] == 100.0
    assert item["stickers_price"] == 25.5
    assert item["float_value"] == 0.123
    assert item["pattern_template"] == "777"
    assert item["profitable"] is True
    assert item["stickers"][0]["name"] == "Sticker | Test"
    assert item["stickers"][0]["market_url"].endswith("Sticker%20%7C%20Test")
    assert item["streak"]["has_streak"] is False
    assert item["market_url"].endswith("AK-47%20%7C%20Slate")
    assert item["checked_at"] == "2026-05-20 11:00:00"


def test_sticker_market_url_adds_sticker_prefix_once(tmp_path):
    repository = SqliteItemsRepository(str(tmp_path / "items.db"))

    repository.add_checked_item_details(
        item_name="AK-47 | Slate",
        listing_id="listing-1",
        price=100.0,
        stickers_price=25.5,
        stickers=[
            {"name": "Crown", "price": 20},
            {"name": "Sticker | Test", "price": 5.5},
        ],
        checked_at="2026-05-20 11:00:00",
    )

    item = repository.get_recent_checked_items(limit=1)[0]

    assert item["stickers"][0]["market_url"].endswith("Sticker%20%7C%20Crown")
    assert item["stickers"][1]["market_url"].endswith("Sticker%20%7C%20Test")


def test_checked_items_filter_and_streak_metadata(tmp_path):
    repository = SqliteItemsRepository(str(tmp_path / "items.db"))
    stickers = [
        {"name": "Sticker | Crown", "price": 10},
        {"name": "Sticker | Crown", "price": 10},
        {"name": "Sticker | Crown", "price": 10},
        {"name": "Sticker | Other", "price": 5},
    ]
    repository.add_checked_item_details(
        item_name="AK-47 | Redline",
        listing_id="listing-1",
        price=20.0,
        stickers_price=35.0,
        stickers=stickers,
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

    items = repository.get_checked_items(
        min_stickers_price=30,
        has_streak=True,
        limit=10,
    )

    assert [item["listing_id"] for item in items] == ["listing-1"]
    assert items[0]["streak"] == {
        "has_streak": True,
        "name": "Sticker | Crown",
        "count": 3,
        "single_price": 10.0,
        "sum_price": 30.0,
    }
    assert items[0]["stickers_to_price_ratio"] == 1.75
