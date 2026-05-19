"""Sticker and charm price lookup."""

from __future__ import annotations

import logging
import sqlite3
import threading
from typing import Protocol

import requests

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
                    price REAL
                )
                """
            )
            self.db.commit()

    def update_price(self, sticker_name: str, price: float) -> None:
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO StickerPrices (name, price) VALUES (?, ?)",
                (sticker_name, price),
            )
            self.db.commit()

    def get_price_by_name(self, item_name: str) -> float:
        with self.lock:
            price = self.db.execute(
                "SELECT price FROM StickerPrices WHERE name LIKE ?",
                (f"%{item_name}",),
            ).fetchone()
            return float(price[0]) if price else 0


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
        **legacy_kwargs,
    ):
        self.repository = db_repository or legacy_kwargs.get("db_repostiotory")
        if self.repository is None:
            raise ValueError("db_repository is required")
        self.proxy_manager = proxy_manager
        self.request_timeout = request_timeout

    def get_price_by_name(self, item_name: str) -> float:
        return self.repository.get_price_by_name(item_name)

    def get_all_prices(self) -> list[dict]:
        url = (
            "https://www.csbackpack.net/api/items?"
            "page=1&max=300000&price_real_min=0&price_real_max=100000&item_group=sticker"
        )
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        }
        proxy_str = self.proxy_manager.get_random_proxy() if self.proxy_manager else None
        proxies = {"http": proxy_str, "https": proxy_str} if proxy_str else None

        response = requests.get(url, headers=headers, proxies=proxies, timeout=self.request_timeout)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise ValueError("Unexpected csbackpack response format")
        return data

    def update_all_prices(self, currency: Currency) -> int:
        updated_count = 0
        for sticker in self.get_all_prices():
            sticker_name = sticker.get("markethashname")
            if not sticker_name:
                continue

            sold30d = sticker.get("sold30d", 0)
            if not sold30d or sold30d <= 10:
                continue

            sticker_price = sticker.get("pricelatest") or sticker.get("priceavg7d")
            if sticker_price is None:
                continue

            converted_price = currency.change_currency(sticker_price, 1001)
            self.repository.update_price(sticker_name, round(converted_price, 2))
            updated_count += 1

        logger.info("Updated %s sticker prices", updated_count)
        return updated_count
