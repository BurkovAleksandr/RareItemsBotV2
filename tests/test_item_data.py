import pytest

from assets.item import ItemData


pytestmark = pytest.mark.mock


class FailingInfoFetcher:
    def get_sticker_and_charm_info(self, inspect_link):
        raise AssertionError("listing metadata should avoid inspect fetch")


class FakePriceFetcher:
    def get_price_by_name(self, item_name):
        prices = {
            "TYLOO | 2020 RMR": 2,
            "Biomech": 10,
        }
        return prices.get(item_name, 0)


def test_item_data_uses_listing_metadata_without_inspect_fetch():
    item = ItemData(
        FailingInfoFetcher(),
        FakePriceFetcher(),
        "AK-47 | Slate (Field-Tested)",
        "listing-1",
        "steam://inspect/listing-1/asset-1",
        50,
        listing_metadata={
            "asset_id": "asset-1",
            "float_value": 0.2604,
            "pattern_template": 918,
            "stickers": [
                {"name": "TYLOO | 2020 RMR"},
                {"name": "TYLOO | 2020 RMR"},
                {"name": "TYLOO | 2020 RMR"},
                {"name": "TYLOO | 2020 RMR"},
            ],
            "charm": {"name": "Biomech"},
        },
    )

    item.update_item_info()

    assert item.asset_id == "asset-1"
    assert item.float_value == 0.2604
    assert item.pattern_template == 918
    assert item.stickers_price == 8
    assert item.charm_price == 10
    assert item.strick.strick is True
    assert item.strick.strick_count == 4
    assert item.strick.sum_price_strick == 8
