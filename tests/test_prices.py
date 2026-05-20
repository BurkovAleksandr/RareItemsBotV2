import pytest

from assets.prices import (
    ItemPriceFetcher,
    PriceEntry,
    PricesRepository,
    SteamAnalystPriceProvider,
)


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


def test_steamanalyst_provider_parses_card_html():
    html = """
    <a href="/skin/sticker-00-nation-glitter-rio-2022">
      <span>00 Nation (Glitter)</span>
      <span>Rio 2022</span>
      <span>$1.03</span>
    </a>
    <a href="/skin/sticker-apex-cluj-napoca-2015">
      <span>Sticker | apEX | Cluj-Napoca 2015</span>
      <span>$3.45</span>
    </a>
    """

    entries = SteamAnalystPriceProvider(max_pages=1).parse_html(html)

    assert entries == [
        PriceEntry("Sticker | 00 Nation (Glitter) | Rio 2022", 1.03, "steamanalyst"),
        PriceEntry("Sticker | apEX | Cluj-Napoca 2015", 3.45, "steamanalyst"),
    ]


class FailingProvider:
    name = "failing"

    def fetch_prices(self):
        raise RuntimeError("boom")


class WorkingProvider:
    name = "working"

    def fetch_prices(self):
        return [PriceEntry("Sticker | Test", 1.5, self.name)]


class FakeCurrency:
    def change_currency(self, price, start_currency):
        assert start_currency == 1001
        return price * 100


def test_item_price_fetcher_falls_back_between_providers(tmp_path):
    repository = PricesRepository(str(tmp_path / "prices.db"))
    fetcher = ItemPriceFetcher(
        db_repository=repository,
        providers=[FailingProvider(), WorkingProvider()],
    )

    updated = fetcher.update_all_prices(FakeCurrency())

    assert updated == 1
    assert repository.get_price_by_name("Sticker | Test") == 150
