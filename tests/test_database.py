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
