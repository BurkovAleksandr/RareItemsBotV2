import pytest

from web_app import parse_track_items


pytestmark = pytest.mark.mock


def test_parse_track_items_from_names_and_urls():
    items = parse_track_items(
        "\n".join(
            [
                "AK-47 | Slate (Field-Tested)",
                "https://steamcommunity.com/market/listings/730/Nova%20%7C%20Predator%20%28Well-Worn%29",
            ]
        )
    )

    assert items == [
        (
            "AK-47 | Slate (Field-Tested)",
            "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Slate%20%28Field-Tested%29",
        ),
        (
            "Nova | Predator (Well-Worn)",
            "https://steamcommunity.com/market/listings/730/Nova%20%7C%20Predator%20%28Well-Worn%29",
        ),
    ]


def test_parse_track_items_expands_exteriors():
    items = parse_track_items("AK-47 | Slate", expand_exteriors=True)

    assert len(items) == 5
    assert items[0][0] == "AK-47 | Slate (Factory New)"
