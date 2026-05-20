import pytest

from assets.prices import PricesRepository


pytestmark = pytest.mark.mock


def test_sticker_price_update_sets_timestamp(tmp_path):
    repository = PricesRepository(str(tmp_path / "prices.db"))

    repository.update_price("Sticker | Test", 12.34)

    row = repository.db.execute(
        "SELECT price, updated_at FROM StickerPrices WHERE name = ?",
        ("Sticker | Test",),
    ).fetchone()
    assert row[0] == 12.34
    assert row[1]
