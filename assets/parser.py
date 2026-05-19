from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from assets.currency_rates import Currency
from assets.proxy import ProxyManager
from assets.session import AsyncSteamSession, SteamSession
from assets.utils import construct_inspect_link, secundomer


logger = logging.getLogger(__name__)


class ListingInfoNotFound(ValueError):
    pass


def _extract_listing_info(raw_data: str) -> dict[str, Any]:
    soup = BeautifulSoup(raw_data, "html.parser")
    scripts = soup.find_all("script", {"type": "text/javascript"})
    for script in reversed(scripts):
        script_text = script.string or script.get_text()
        marker = "var g_rgListingInfo = "
        if marker in script_text:
            listing_info = script_text.split(marker, 1)[1].split(";", 1)[0]
            return json.loads(listing_info)
    raise ListingInfoNotFound("Could not find g_rgListingInfo in Steam market page")


def build_market_render_url(
    listing_url: str,
    start: int = 0,
    count: int = 10,
    country: str = "RU",
    language: str = "english",
    currency: int = 5,
) -> str:
    parsed = urlparse(listing_url)
    path = parsed.path.rstrip("/")
    if not path.endswith("/render"):
        path = f"{path}/render"

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(
        {
            "query": query.get("query", ""),
            "start": str(start),
            "count": str(count),
            "country": country,
            "language": language,
            "currency": str(currency),
        }
    )
    return urlunparse(parsed._replace(path=path, query=urlencode(query)))


def merge_render_assets(payload: dict[str, Any]) -> dict[str, Any]:
    listing_info = payload.get("listinginfo") or {}
    assets = payload.get("assets") or {}
    for item_data in listing_info.values():
        asset = item_data.get("asset") or {}
        app_id = str(asset.get("appid") or "")
        context_id = str(asset.get("contextid") or "")
        asset_id = str(asset.get("id") or asset.get("assetid") or "")
        description = assets.get(app_id, {}).get(context_id, {}).get(asset_id, {})
        if description:
            merged_asset = dict(description)
            merged_asset.update(asset)
            item_data["asset"] = merged_asset
    return listing_info


def summarize_market_page(raw_data: str) -> str:
    soup = BeautifulSoup(raw_data, "html.parser")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    lowered = raw_data.lower()
    hints = []
    if "login" in lowered or "sign in" in lowered:
        hints.append("login")
    if "too many requests" in lowered:
        hints.append("rate_limit")
    if "there was an error" in lowered:
        hints.append("steam_error")
    if "captcha" in lowered:
        hints.append("captcha")
    hint_text = f", hints={','.join(hints)}" if hints else ""
    return f"title={title!r}, length={len(raw_data)}{hint_text}"


def validate_render_payload(payload: dict[str, Any], render_url: str) -> None:
    if payload.get("success") in (False, 0):
        message = payload.get("message") or payload.get("error") or "unknown error"
        raise RuntimeError(f"Steam market render failed for {render_url}: {message}")
    if not isinstance(payload.get("listinginfo"), dict):
        raise RuntimeError(f"Steam market render returned no listinginfo for {render_url}")


class AsyncParser:
    def __init__(
        self,
        session: AsyncSteamSession,
        proxy_manager: ProxyManager | None = None,
        request_timeout: int = 10,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ):
        self.steam_session = session
        self.proxy_manager = proxy_manager
        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.retry_statuses = {429, 500, 502, 503, 504}

    @secundomer
    async def get_raw_data_from_market(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            proxy = self.proxy_manager.get_random_proxy() if self.proxy_manager else None
            try:
                async with self.steam_session.get_async_session() as local_session:
                    response = await local_session.get(
                        url,
                        proxy=proxy,
                        ssl=False,
                        timeout=self.request_timeout,
                    )
                    text = await response.text()
                    if response.status == 200:
                        return text

                    logger.warning(
                        "Steam market page returned HTTP %s for %s on attempt %s/%s",
                        response.status,
                        url,
                        attempt,
                        self.max_retries,
                    )
                    if response.status not in self.retry_statuses:
                        return text
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Steam market request failed for %s on attempt %s/%s: %s",
                    url,
                    attempt,
                    self.max_retries,
                    exc,
                )

            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_base_delay * attempt)

        if last_error:
            raise last_error
        raise RuntimeError(f"Could not fetch Steam market page after {self.max_retries} attempts")

    async def get_json_from_market_render(self, url: str) -> dict[str, Any]:
        render_url = build_market_render_url(url)
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            proxy = self.proxy_manager.get_random_proxy() if self.proxy_manager else None
            try:
                async with self.steam_session.get_async_session() as local_session:
                    response = await local_session.get(
                        render_url,
                        proxy=proxy,
                        ssl=False,
                        timeout=self.request_timeout,
                        headers={"Referer": url},
                    )
                    text = await response.text()
                    if response.status == 200:
                        try:
                            payload = json.loads(text)
                        except json.JSONDecodeError as exc:
                            raise RuntimeError(
                                f"Steam market render returned non-JSON response: {text[:200]!r}"
                            ) from exc
                        validate_render_payload(payload, render_url)
                        return payload

                    logger.warning(
                        "Steam market render returned HTTP %s for %s on attempt %s/%s",
                        response.status,
                        render_url,
                        attempt,
                        self.max_retries,
                    )
                    if response.status not in self.retry_statuses:
                        raise RuntimeError(f"Steam market render returned HTTP {response.status}")
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Steam market render request failed for %s on attempt %s/%s: %s",
                    render_url,
                    attempt,
                    self.max_retries,
                    exc,
                )

            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_base_delay * attempt)

        if last_error:
            raise last_error
        raise RuntimeError(f"Could not fetch Steam market render after {self.max_retries} attempts")

    async def get_listing_info_from_market(self, url: str) -> dict[str, Any]:
        raw_data = await self.get_raw_data_from_market(url)
        try:
            return self.extract_json_from_raw_data(raw_data)
        except ListingInfoNotFound:
            logger.warning(
                "Steam market page did not contain g_rgListingInfo for %s; falling back to render endpoint (%s)",
                url,
                summarize_market_page(raw_data),
            )
            render_payload = await self.get_json_from_market_render(url)
            return merge_render_assets(render_payload)

    def extract_json_from_raw_data(self, raw_data: str) -> dict[str, Any]:
        return _extract_listing_info(raw_data)

    def calculate_price(self, item_data: dict) -> tuple[int, int, int]:
        price_no_fee = int(item_data.get("converted_price") or item_data.get("price") or 0)
        fee = int(item_data.get("converted_fee") or item_data.get("fee") or 0)
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
