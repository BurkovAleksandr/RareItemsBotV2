import pytest

from assets.prices import (
    ItemPriceFetcher,
    PriceEntry,
    PricesRepository,
    SkinPockPriceProvider,
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


def test_skinpock_provider_parses_list_payload():
    payload = [
        {
            "markethashname": "Sticker | Liquid Fire",
            "pricelatest": "1.25",
            "sold30d": 12,
        },
        {
            "markethashname": "Sticker | Low Volume",
            "pricelatest": 2.0,
            "sold30d": 2,
        },
    ]

    entries, total_pages, has_more = SkinPockPriceProvider(max_pages=1).parse_payload(
        payload
    )

    assert entries == [PriceEntry("Sticker | Liquid Fire", 1.25, "skinpock", 12)]
    assert total_pages is None
    assert has_more is None


def test_skinpock_provider_parses_dict_payload_with_pagination():
    payload = {
        "data": [
            {
                "market_hash_name": "Sticker | Flexible Schema",
                "price_real": "$3.50",
                "volume": "25",
            }
        ],
        "totalPages": 4,
        "hasMore": True,
    }

    entries, total_pages, has_more = SkinPockPriceProvider(max_pages=1).parse_payload(
        payload
    )

    assert entries == [PriceEntry("Sticker | Flexible Schema", 3.5, "skinpock", 25)]
    assert total_pages == 4
    assert has_more is True


class FakeSkinPockResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSkinPockSession:
    def __init__(self):
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        page = kwargs["params"]["page"]
        payloads = {
            1: {
                "data": [
                    {
                        "market_hash_name": "Sticker | Page One",
                        "price_real": 1.0,
                        "volume": 15,
                    }
                ],
                "totalPages": 2,
            },
            2: {
                "data": [
                    {
                        "market_hash_name": "Sticker | Page Two",
                        "price_real": 2.0,
                        "volume": 20,
                    }
                ],
                "totalPages": 2,
            },
        }
        return FakeSkinPockResponse(payloads[page])


def test_skinpock_provider_streams_pages():
    session = FakeSkinPockSession()
    provider = SkinPockPriceProvider(session=session, page_size=1, max_pages=5)

    pages = list(provider.iter_price_pages())

    assert pages == [
        [PriceEntry("Sticker | Page One", 1.0, "skinpock", 15)],
        [PriceEntry("Sticker | Page Two", 2.0, "skinpock", 20)],
    ]
    assert [request[1]["params"]["page"] for request in session.requests] == [1, 2]


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


class FreshAndNewProvider:
    name = "fresh-new"

    def fetch_prices(self):
        return [
            PriceEntry("Sticker | Fresh", 2.0, self.name),
            PriceEntry("Sticker | New", 3.0, self.name),
        ]


def test_item_price_fetcher_skips_recent_prices(tmp_path):
    repository = PricesRepository(str(tmp_path / "prices.db"))
    repository.update_price("Sticker | Fresh", 10)
    fetcher = ItemPriceFetcher(
        db_repository=repository,
        providers=[FreshAndNewProvider()],
        recent_price_max_age_hours=24,
    )

    updated = fetcher.update_all_prices(FakeCurrency())

    assert updated == 1
    assert repository.get_price_by_name("Sticker | Fresh") == 10
    assert repository.get_price_by_name("Sticker | New") == 300


class StreamingProvider:
    name = "streaming"

    def __init__(self, repository):
        self.repository = repository

    def iter_price_pages(self):
        yield [PriceEntry("Sticker | First Page", 1.0, self.name)]
        assert self.repository.get_price_by_name("Sticker | First Page") == 100
        yield [PriceEntry("Sticker | Second Page", 2.0, self.name)]


def test_item_price_fetcher_updates_database_after_each_page(tmp_path):
    repository = PricesRepository(str(tmp_path / "prices.db"))
    fetcher = ItemPriceFetcher(
        db_repository=repository,
        providers=[StreamingProvider(repository)],
        recent_price_max_age_hours=24,
    )

    updated = fetcher.update_all_prices(FakeCurrency())

    assert updated == 2
    assert repository.get_price_by_name("Sticker | Second Page") == 200
