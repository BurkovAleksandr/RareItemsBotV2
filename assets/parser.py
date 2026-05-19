from __future__ import annotations

import json
import logging
from typing import Any

from bs4 import BeautifulSoup

from assets.currency_rates import Currency
from assets.proxy import ProxyManager
from assets.session import AsyncSteamSession, SteamSession
from assets.utils import construct_inspect_link, secundomer


logger = logging.getLogger(__name__)


def _extract_listing_info(raw_data: str) -> dict[str, Any]:
    soup = BeautifulSoup(raw_data, "html.parser")
    scripts = soup.find_all("script", {"type": "text/javascript"})
    for script in reversed(scripts):
        script_text = script.string or script.get_text()
        marker = "var g_rgListingInfo = "
        if marker in script_text:
            listing_info = script_text.split(marker, 1)[1].split(";", 1)[0]
            return json.loads(listing_info)
    raise ValueError("Could not find g_rgListingInfo in Steam market page")


class AsyncParser:
    def __init__(
        self,
        session: AsyncSteamSession,
        proxy_manager: ProxyManager | None = None,
    ):
        self.steam_session = session
        self.proxy_manager = proxy_manager

    @secundomer
    async def get_raw_data_from_market(self, url: str) -> str:
        proxy = self.proxy_manager.get_random_proxy() if self.proxy_manager else None
        async with self.steam_session.get_async_session() as local_session:
            response = await local_session.get(url, proxy=proxy, ssl=False, timeout=10)
            text = await response.text()
            if response.status != 200:
                logger.warning("Steam market page returned HTTP %s for %s", response.status, url)
            return text

    def extract_json_from_raw_data(self, raw_data: str) -> dict[str, Any]:
        return _extract_listing_info(raw_data)

    def calculate_price(self, item_data: dict) -> tuple[int, int, int]:
        price_no_fee = int(item_data.get("converted_price", 0))
        fee = int(item_data.get("converted_fee", 0))
        price = price_no_fee + fee
        return price_no_fee, fee, price

    def extract_item_data(self, items_json: dict) -> list[dict]:
        extracted_items = []
        for listing_id, item_data in items_json.items():
            inspect_link = construct_inspect_link(item_data, listing_id)
            price_no_fee, fee, price = self.calculate_price(item_data)
            extracted_items.append(
                {
                    "listing_id": listing_id,
                    "inspect_link": inspect_link,
                    "price": price,
                    "price_no_fee": price_no_fee,
                    "fee": fee,
                }
            )
        return extracted_items


class Parser:
    def __init__(self, session: SteamSession, currency: Currency):
        self.steam_session = session
        self.currency = currency

    def get_raw_data_from_market(self, url: str) -> str:
        response = self.steam_session.session.get(url)
        if response.status_code != 200:
            raise RuntimeError(f"Steam market page returned HTTP {response.status_code}")
        return response.text

    def extract_json_from_raw_data(self, raw_data: str) -> dict[str, Any]:
        return _extract_listing_info(raw_data)

    def calculate_price(self, item_data: dict) -> float:
        price_no_fee = int(item_data.get("price", 0))
        fee = int(item_data.get("fee", 0))
        currency_id = item_data.get("currencyid")
        if currency_id is None:
            raise ValueError("Missing currency_id in item data")
        price = (price_no_fee + fee) / 100
        return self.currency.change_currency(price, currency_id)

    def extract_item_data(self, items_json: dict) -> list[dict]:
        extracted_items = []
        for listing_id, item_data in items_json.items():
            inspect_link = construct_inspect_link(item_data, listing_id)
            price = self.calculate_price(item_data)
            extracted_items.append(
                {
                    "listing_id": listing_id,
                    "inspect_link": inspect_link,
                    "price": price,
                }
            )
        return extracted_items
