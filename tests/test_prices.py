import pytest
import requests

from assets.prices import (
    ItemPriceFetcher,
    PriceEntry,
    PricesRepository,
    SkinPockPriceProvider,
    SteamMarketSearchPriceProvider,
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


def test_sticker_price_lookup_uses_normalized_name(tmp_path):
    repository = PricesRepository(str(tmp_path / "prices.db"))
    repository.update_price("Sticker | 00 Nation (Gold)Rio 2022", 42)

    assert repository.get_price_by_name("Sticker | 00 Nation (Gold) | Rio 2022") == 42

    repository.update_price("Sticker | 00 Nation (Gold) | Rio 2022", 50)

    assert repository.get_price_by_name("Sticker | 00 Nation (Gold) | Rio 2022") == 50


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


def test_steamanalyst_provider_prefers_image_alt_name():
    html = """
    <a href="/skin/sticker-00-nation-gold-rio-2022">
      <span class="item-card-name-link">00 Nation (Gold)Rio 2022</span>
      <img alt="Sticker | 00 Nation (Gold) | Rio 2022" src="sticker.png">
      <span>$5.00</span>
    </a>
    """

    entries = SteamAnalystPriceProvider(max_pages=1).parse_html(html)

    assert entries == [
        PriceEntry("Sticker | 00 Nation (Gold) | Rio 2022", 5.0, "steamanalyst")
    ]


def test_steamanalyst_provider_skips_sticker_slabs():
    html = """
    <a href="/skin/sticker-slab-test">
      <span>Sticker Slab | Test</span>
      <img alt="Sticker | Test" src="sticker.png">
      <span>$10.00</span>
    </a>
    <a href="/skin/sticker-real-test">
      <span>Sticker | Real Test</span>
      <span>$1.25</span>
    </a>
    """

    entries = SteamAnalystPriceProvider(max_pages=1).parse_html(html)

    assert entries == [PriceEntry("Sticker | Real Test", 1.25, "steamanalyst")]


def test_steam_market_search_provider_parses_current_sell_prices():
    payload = {
        "start": 30,
        "total_count": 8795,
        "results": [
            {
                "strHash": "Sticker | Renegades | Stockholm 2021",
                "cSellOrders": 1389,
                "cBuyOrders": 0,
                "unSteamFee": 71,
                "unPublisherFee": 71,
                "eCurrency": 5,
                "strMinSellSubtotal": "RUB\u00a05.70",
                "asset_description": {
                    "market_hash_name": "Sticker | Renegades | Stockholm 2021"
                },
            },
            {
                "strHash": "Sticker Slab | Renegades | Stockholm 2021",
                "cSellOrders": 10,
                "unSteamFee": 1,
                "unPublisherFee": 1,
                "eCurrency": 5,
                "strMinSellSubtotal": "RUB\u00a01.00",
            },
        ],
    }

    entries, total_count, result_count = SteamMarketSearchPriceProvider().parse_payload(
        payload
    )

    assert total_count == 8795
    assert result_count == 2
    assert entries == [
        PriceEntry(
            "Sticker | Renegades | Stockholm 2021",
            7.12,
            "steam",
            1389,
            2005,
        )
    ]


class FakeSteamSearchResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSteamRateLimitResponse:
    status_code = 429
    headers = {}

    def raise_for_status(self):
        raise requests.HTTPError(response=self)


class FakeSteamSearchSession:
    def __init__(self):
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        start = kwargs["json"][0]["start"]
        payloads = {
            0: {
                "start": 0,
                "total_count": 31,
                "results": [
                    {
                        "strHash": "Sticker | Page One",
                        "cSellOrders": 15,
                        "unSteamFee": 10,
                        "unPublisherFee": 15,
                        "eCurrency": 5,
                        "strMinSellSubtotal": "RUB 1.00",
                    }
                    for _ in range(30)
                ],
            },
            30: {
                "start": 30,
                "total_count": 31,
                "results": [
                    {
                        "strHash": "Sticker | Page Two",
                        "cSellOrders": 20,
                        "unSteamFee": 20,
                        "unPublisherFee": 30,
                        "eCurrency": 5,
                        "strMinSellSubtotal": "RUB 2.00",
                    }
                ],
            },
        }
        return FakeSteamSearchResponse(payloads[start])


def test_steam_market_search_provider_streams_pages_by_start():
    session = FakeSteamSearchSession()
    provider = SteamMarketSearchPriceProvider(session=session)

    pages = list(provider.iter_price_pages())

    assert pages[0][0] == PriceEntry("Sticker | Page One", 1.25, "steam", 15, 2005)
    assert pages[1] == [PriceEntry("Sticker | Page Two", 2.5, "steam", 20, 2005)]
    assert [request[1]["json"][0]["start"] for request in session.requests] == [0, 30]
    headers = session.requests[0][1]["headers"]
    assert headers["x-valve-request-type"] == "routeAction"
    assert headers["x-valve-action-type"] == "ZFJAHYDA:SearchMarketListings"


class RateLimitedThenSuccessSteamSession:
    def __init__(self):
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        if len(self.requests) <= 3:
            return FakeSteamRateLimitResponse()
        return FakeSteamSearchResponse(
            {
                "start": 0,
                "total_count": 1,
                "results": [
                    {
                        "strHash": "Sticker | Retry Success",
                        "cSellOrders": 15,
                        "unSteamFee": 10,
                        "unPublisherFee": 15,
                        "eCurrency": 5,
                        "strMinSellSubtotal": "RUB 1.00",
                    }
                ],
            }
        )


def test_steam_market_search_provider_retries_rate_limits():
    session = RateLimitedThenSuccessSteamSession()
    provider = SteamMarketSearchPriceProvider(
        session=session,
        max_pages=1,
        retry_delay_seconds=0,
    )

    pages = list(provider.iter_price_pages())

    assert len(session.requests) == 4
    assert pages == [[PriceEntry("Sticker | Retry Success", 1.25, "steam", 15, 2005)]]


class RateLimitedSecondPageSteamSession:
    def __init__(self):
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        start = kwargs["json"][0]["start"]
        if start == 0:
            return FakeSteamSearchResponse(
                {
                    "start": 0,
                    "total_count": 60,
                    "results": [
                        {
                            "strHash": "Sticker | Saved Before 429",
                            "cSellOrders": 15,
                            "unSteamFee": 10,
                            "unPublisherFee": 15,
                            "eCurrency": 5,
                            "strMinSellSubtotal": "RUB 1.00",
                        }
                        for _ in range(30)
                    ],
                }
            )
        return FakeSteamRateLimitResponse()


class FallbackAfterRateLimitProvider:
    name = "fallback-after-rate-limit"

    def fetch_prices(self):
        return [PriceEntry("Sticker | Fallback After 429", 2.0, self.name)]


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


def test_skinpock_provider_skips_sticker_slabs():
    payload = [
        {
            "markethashname": "Sticker Slab | Test",
            "pricelatest": "10.00",
            "sold30d": 15,
        },
        {
            "markethashname": "Sticker | Real Test",
            "pricelatest": "1.25",
            "sold30d": 15,
        },
    ]

    entries, total_pages, has_more = SkinPockPriceProvider(max_pages=1).parse_payload(
        payload
    )

    assert entries == [PriceEntry("Sticker | Real Test", 1.25, "skinpock", 15)]
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


class RecordingCurrency:
    def __init__(self):
        self.calls = []

    def change_currency(self, price, start_currency):
        self.calls.append((price, start_currency))
        return price


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


class StickersAndSlabsProvider:
    name = "stickers-slabs"

    def fetch_prices(self):
        return [
            PriceEntry("Sticker | Real Test", 1.0, self.name),
            PriceEntry("Sticker Slab | Test", 2.0, self.name),
            PriceEntry("Sticker | Slab Test", 2.5, self.name),
            PriceEntry("Charm | Test", 3.0, self.name),
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


def test_item_price_fetcher_updates_only_sticker_prices(tmp_path):
    repository = PricesRepository(str(tmp_path / "prices.db"))
    fetcher = ItemPriceFetcher(
        db_repository=repository,
        providers=[StickersAndSlabsProvider()],
        recent_price_max_age_hours=24,
    )

    updated = fetcher.update_all_prices(FakeCurrency())

    rows = repository.db.execute(
        "SELECT name, price FROM StickerPrices ORDER BY name"
    ).fetchall()
    assert updated == 1
    assert rows == [("Sticker | Real Test", 100)]


class SteamCurrencyProvider:
    name = "steam-currency"

    def fetch_prices(self):
        return [PriceEntry("Sticker | Steam Price", 7.12, self.name, 100, 2005)]


def test_item_price_fetcher_uses_entry_currency_id(tmp_path):
    repository = PricesRepository(str(tmp_path / "prices.db"))
    currency = RecordingCurrency()
    fetcher = ItemPriceFetcher(
        db_repository=repository,
        providers=[SteamCurrencyProvider()],
        recent_price_max_age_hours=24,
    )

    updated = fetcher.update_all_prices(currency)

    assert updated == 1
    assert currency.calls == [(7.12, 2005)]
    assert repository.get_price_by_name("Sticker | Steam Price") == 7.12


def test_item_price_fetcher_keeps_saved_pages_when_steam_rate_limit_fails(tmp_path):
    repository = PricesRepository(str(tmp_path / "prices.db"))
    currency = RecordingCurrency()
    steam_provider = SteamMarketSearchPriceProvider(
        session=RateLimitedSecondPageSteamSession(),
        retry_delay_seconds=0,
    )
    fetcher = ItemPriceFetcher(
        db_repository=repository,
        providers=[steam_provider, FallbackAfterRateLimitProvider()],
        recent_price_max_age_hours=24,
    )

    updated = fetcher.update_all_prices(currency)

    assert updated == 1
    assert repository.get_price_by_name("Sticker | Saved Before 429") == 1.25
    assert repository.get_price_by_name("Sticker | Fallback After 429") == 2.0


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
