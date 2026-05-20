"""Sticker and charm price lookup."""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import requests
from bs4 import BeautifulSoup

from assets.currency_rates import Currency
from assets.proxy import ProxyManager


logger = logging.getLogger(__name__)


class IPricesRepository(Protocol):
    def get_price_by_name(self, item_name: str) -> float:
        pass

    def update_price(self, item_name: str, price: float) -> None:
        pass


class PricesRepository(IPricesRepository):
    def __init__(self, db_path: str):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        with self.lock:
            self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS StickerPrices (
                    name TEXT PRIMARY KEY,
                    price REAL,
                    updated_at TEXT
                )
                """
            )
            self._ensure_column("updated_at", "TEXT")
            self.db.commit()

    def _ensure_column(self, column: str, definition: str) -> None:
        columns = [row[1] for row in self.db.execute("PRAGMA table_info(StickerPrices)").fetchall()]
        if column not in columns:
            self.db.execute(f"ALTER TABLE StickerPrices ADD COLUMN {column} {definition}")

    def update_price(self, sticker_name: str, price: float) -> None:
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO StickerPrices (name, price, updated_at) VALUES (?, ?, ?)",
                (sticker_name, price, datetime.now().isoformat(sep=" ", timespec="seconds")),
            )
            self.db.commit()

    def get_price_by_name(self, item_name: str) -> float:
        with self.lock:
            price = self.db.execute(
                "SELECT price FROM StickerPrices WHERE name LIKE ?",
                (f"%{item_name}",),
            ).fetchone()
            return float(price[0]) if price else 0


@dataclass(frozen=True)
class PriceEntry:
    name: str
    price_usd: float
    source: str
    volume: int | None = None


class IPriceProvider(Protocol):
    name: str

    def fetch_prices(self) -> list[PriceEntry]:
        pass


class BaseHttpPriceProvider:
    name = "base"

    def __init__(
        self,
        proxy_manager: ProxyManager | None = None,
        request_timeout: int = 20,
        session: requests.Session | None = None,
    ):
        self.proxy_manager = proxy_manager
        self.request_timeout = request_timeout
        self.session = session or requests.Session()

    def _request_get(self, url: str, **kwargs) -> requests.Response:
        proxy_str = self.proxy_manager.get_random_proxy() if self.proxy_manager else None
        proxies = {"http": proxy_str, "https": proxy_str} if proxy_str else None
        response = self.session.get(url, proxies=proxies, timeout=self.request_timeout, **kwargs)
        response.raise_for_status()
        return response


class SteamAnalystPriceProvider(BaseHttpPriceProvider):
    name = "steamanalyst"

    def __init__(
        self,
        base_url: str = "https://www.steamanalyst.com/type/sticker",
        max_pages: int | None = 1000,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.base_url = base_url
        self.max_pages = max_pages

    def fetch_prices(self) -> list[PriceEntry]:
        entries: list[PriceEntry] = []
        page = 1
        total_pages = self.max_pages
        while total_pages is None or page <= total_pages:
            url = self._page_url(page)
            response = self._request_get(
                url,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                    ),
                },
            )
            page_entries = self.parse_html(response.text)
            if not page_entries:
                break
            entries.extend(page_entries)

            if total_pages is None:
                total_pages = self.extract_total_pages(response.text) or page
            page += 1
        return entries

    def _page_url(self, page: int) -> str:
        return self.base_url if page <= 1 else f"{self.base_url}?page={page}"

    def parse_html(self, raw_html: str) -> list[PriceEntry]:
        soup = BeautifulSoup(raw_html, "html.parser")
        entries = self._parse_cards(soup)
        if entries:
            return entries
        return self._parse_text(soup.get_text("\n"))

    def _parse_cards(self, soup: BeautifulSoup) -> list[PriceEntry]:
        entries: list[PriceEntry] = []
        seen: set[str] = set()
        for link in soup.find_all("a", href=True):
            href = str(link.get("href") or "")
            if "sticker" not in href.lower():
                continue

            parts = [part.strip() for part in link.get_text("\n").splitlines() if part.strip()]
            price_index = next((index for index, part in enumerate(parts) if _is_usd_price_label(part)), None)
            if price_index is None:
                continue

            name = normalize_steamanalyst_name(parts[:price_index])
            price = _parse_usd_price(parts[price_index])
            if not name or price is None or name in seen:
                continue
            entries.append(PriceEntry(name=name, price_usd=price, source=self.name))
            seen.add(name)
        return entries

    def _parse_text(self, text: str) -> list[PriceEntry]:
        entries: list[PriceEntry] = []
        seen: set[str] = set()
        pattern = re.compile(
            r"(?P<name>(?:Sticker(?: Slab)? \| )?[A-Za-z0-9][A-Za-z0-9 .,'’()|\-:]+?)\s+\$(?P<price>[0-9][0-9,]*(?:\.[0-9]+)?)"
        )
        for match in pattern.finditer(text):
            name = normalize_steamanalyst_name([match.group("name")])
            price = _parse_usd_price(match.group("price"))
            if not name or price is None or name in seen:
                continue
            entries.append(PriceEntry(name=name, price_usd=price, source=self.name))
            seen.add(name)
        return entries

    def extract_total_pages(self, raw_html: str) -> int | None:
        numbers = [int(value) for value in re.findall(r"(?:page=|Page\s+)(\d{1,5})", raw_html, flags=re.I)]
        return max(numbers) if numbers else None


class CsBackpackPriceProvider(BaseHttpPriceProvider):
    name = "csbackpack"

    def fetch_prices(self) -> list[PriceEntry]:
        url = (
            "https://www.csbackpack.net/api/items?"
            "page=1&max=300000&price_real_min=0&price_real_max=100000&item_group=sticker"
        )
        response = self._request_get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                )
            },
        )
        data = response.json()
        if not isinstance(data, list):
            raise ValueError("Unexpected csbackpack response format")

        entries: list[PriceEntry] = []
        for sticker in data:
            sticker_name = sticker.get("markethashname")
            if not sticker_name:
                continue

            sold30d = sticker.get("sold30d", 0)
            if not sold30d or sold30d <= 10:
                continue

            sticker_price = sticker.get("pricelatest") or sticker.get("priceavg7d")
            if sticker_price is None:
                continue
            entries.append(
                PriceEntry(
                    name=str(sticker_name),
                    price_usd=float(sticker_price),
                    source=self.name,
                    volume=int(sold30d),
                )
            )
        return entries


def normalize_steamanalyst_name(parts: list[str]) -> str:
    clean_parts = [re.sub(r"\s+", " ", part).strip(" -") for part in parts if part and part.strip()]
    clean_parts = [part for part in clean_parts if part and not part.startswith("$")]
    if not clean_parts:
        return ""

    if any(part.startswith(("Sticker |", "Sticker Slab |")) for part in clean_parts):
        return next(part for part in clean_parts if part.startswith(("Sticker |", "Sticker Slab |")))

    if clean_parts[0].startswith("Sticker "):
        return clean_parts[0]

    if len(clean_parts) >= 2:
        return f"Sticker | {clean_parts[0]} | {clean_parts[1]}"
    return clean_parts[0] if clean_parts[0].startswith("Sticker") else f"Sticker | {clean_parts[0]}"


def _parse_usd_price(value: str) -> float | None:
    match = re.search(r"\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)", str(value))
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _is_usd_price_label(value: str) -> bool:
    return bool(re.match(r"^\s*\$\s*[0-9][0-9,]*(?:\.[0-9]+)?\s*$", str(value)))


class IItemPriceFetcher(Protocol):
    def get_price_by_name(self, item_name: str) -> float:
        pass


class MockItemPriceFetcher(IItemPriceFetcher):
    def get_price_by_name(self, item_name: str) -> float:
        return 500


class ItemPriceFetcher(IItemPriceFetcher):
    def __init__(
        self,
        db_repository: PricesRepository | None = None,
        proxy_manager: ProxyManager | None = None,
        request_timeout: int = 20,
        providers: list[IPriceProvider] | None = None,
        **legacy_kwargs,
    ):
        self.repository = db_repository or legacy_kwargs.get("db_repostiotory")
        if self.repository is None:
            raise ValueError("db_repository is required")
        self.proxy_manager = proxy_manager
        self.request_timeout = request_timeout
        self.providers = providers or [
            SteamAnalystPriceProvider(proxy_manager=proxy_manager, request_timeout=request_timeout),
            CsBackpackPriceProvider(proxy_manager=proxy_manager, request_timeout=request_timeout),
        ]

    @property
    def provider_names(self) -> list[str]:
        return [provider.name for provider in self.providers]

    def get_price_by_name(self, item_name: str) -> float:
        return self.repository.get_price_by_name(item_name)

    def get_all_prices(self) -> list[dict]:
        for provider in self.providers:
            try:
                return [
                    {
                        "markethashname": entry.name,
                        "pricelatest": entry.price_usd,
                        "source": entry.source,
                        "sold30d": entry.volume,
                    }
                    for entry in provider.fetch_prices()
                ]
            except Exception:
                logger.exception("Sticker price provider %s failed", provider.name)
        raise RuntimeError("All sticker price providers failed")

    def update_all_prices(self, currency: Currency) -> int:
        last_error: Exception | None = None
        for provider in self.providers:
            try:
                entries = provider.fetch_prices()
            except Exception as exc:
                last_error = exc
                logger.exception("Sticker price provider %s failed", provider.name)
                continue

            updated_count = 0
            for entry in entries:
                converted_price = currency.change_currency(entry.price_usd, 1001)
                self.repository.update_price(entry.name, round(converted_price, 2))
                updated_count += 1

            logger.info("Updated %s sticker prices from %s", updated_count, provider.name)
            return updated_count

        raise RuntimeError("All sticker price providers failed") from last_error
