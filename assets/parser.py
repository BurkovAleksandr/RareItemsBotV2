from __future__ import annotations

import asyncio
import codecs
import json
import logging
import os
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from assets.currency_rates import Currency
from assets.proxy import ProxyManager
from assets.session import AsyncSteamSession, SteamSession
from assets.utils import construct_inspect_link, secundomer

logger = logging.getLogger(__name__)


class ListingInfoNotFound(ValueError):
    pass


def _extract_js_object(raw_data: str, variable_name: str) -> dict[str, Any]:
    soup = BeautifulSoup(raw_data, "html.parser")
    scripts = soup.find_all("script", {"type": "text/javascript"})
    decoder = json.JSONDecoder()
    marker = f"var {variable_name} = "
    for script in reversed(scripts):
        script_text = script.string or script.get_text()
        if marker in script_text:
            json_text = script_text.split(marker, 1)[1].lstrip()
            parsed_object, _ = decoder.raw_decode(json_text)
            return parsed_object
    raise ListingInfoNotFound(f"Could not find {variable_name} in Steam market page")


def _extract_listing_info(raw_data: str) -> dict[str, Any]:
    try:
        return _extract_js_object(raw_data, "g_rgListingInfo")
    except ListingInfoNotFound as exc:
        raise ListingInfoNotFound(
            "Could not find g_rgListingInfo in Steam market page"
        ) from exc


def _extract_assets_info(raw_data: str) -> dict[str, Any]:
    try:
        return _extract_js_object(raw_data, "g_rgAssets")
    except ListingInfoNotFound:
        return {}


def merge_listing_assets(
    listing_info: dict[str, Any], assets: dict[str, Any]
) -> dict[str, Any]:
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


def normalize_new_market_listings(listings: list[dict[str, Any]]) -> dict[str, Any]:
    listing_info: dict[str, Any] = {}
    for listing in listings:
        listing_id = str(listing.get("listingid") or "")
        if not listing_id:
            continue

        description = listing.get("description") or {}
        asset = dict(description)
        asset.update(listing.get("asset") or {})
        asset["market_actions"] = (
            description.get("market_actions")
            or description.get("actions")
            or asset.get("market_actions")
        )

        listing_info[listing_id] = {
            **listing,
            "converted_price": int(listing.get("unPrice") or 0),
            "converted_fee": int(listing.get("unFee") or 0),
            "currencyid": 2000 + int(listing.get("eCurrency") or 0),
            "asset": asset,
        }
    return listing_info


def _decode_ssr_script(script_text: str) -> str:
    decoded = script_text
    for _ in range(2):
        if "\\\"" not in decoded and "\\\\" not in decoded:
            break
        decoded = codecs.decode(decoded, "unicode_escape", errors="replace")
    return decoded


def _extract_json_after_marker(raw_data: str, marker: str) -> Any:
    start = raw_data.find(marker)
    if start == -1:
        raise ListingInfoNotFound(f"Could not find {marker} in Steam market page")
    start += len(marker)
    return json.JSONDecoder().raw_decode(raw_data[start:].lstrip())[0]


def _extract_new_market_listing_info(raw_data: str) -> dict[str, Any]:
    soup = BeautifulSoup(raw_data, "html.parser")
    for script in soup.find_all("script"):
        script_text = script.string or script.get_text() or ""
        if "listingid" not in script_text or "unPrice" not in script_text:
            continue

        decoded_script = _decode_ssr_script(script_text)
        try:
            listings = _extract_json_after_marker(decoded_script, '"listings":')
        except (json.JSONDecodeError, ListingInfoNotFound):
            continue

        if isinstance(listings, list):
            listing_info = normalize_new_market_listings(listings)
            if listing_info:
                return listing_info

    raise ListingInfoNotFound("Could not find new Steam market listings in page HTML")


def _asset_property(
    asset: dict[str, Any], property_id: int, value_key: str = "string_value"
):
    for item_property in asset.get("asset_properties") or []:
        if item_property.get("propertyid") == property_id:
            return item_property.get(value_key)
    return None


def _accessory_property(
    accessory: dict[str, Any], property_id: int, value_key: str = "string_value"
):
    for properties_key in ("parent_relationship_properties", "standalone_properties"):
        for item_property in accessory.get(properties_key) or []:
            if item_property.get("propertyid") == property_id:
                return item_property.get(value_key)
    return None


def extract_float_value(asset: dict[str, Any]) -> float | None:
    value = _asset_property(asset, 2, "float_value")
    return float(value) if value not in (None, "") else None


def extract_pattern_template(asset: dict[str, Any]) -> int | None:
    value = _asset_property(asset, 1, "int_value")
    return int(value) if value not in (None, "") else None


def extract_item_certificate(asset: dict[str, Any]) -> str | None:
    return _asset_property(asset, 6, "string_value")


def _description_html(asset: dict[str, Any], description_name: str) -> str:
    parts = []
    for description in asset.get("descriptions") or []:
        value = description.get("value") or ""
        if description.get("name") == description_name or description_name in value:
            parts.append(value)
    return "\n".join(parts)


def _extract_titled_images(raw_html: str, prefix: str) -> list[dict[str, str]]:
    if not raw_html:
        return []

    soup = BeautifulSoup(raw_html, "html.parser")
    items = []
    for image in soup.find_all("img"):
        title = (image.get("title") or "").strip()
        if not title.startswith(prefix):
            continue
        items.append(
            {
                "name": title.removeprefix(prefix).strip(),
                "icon_url": image.get("src") or "",
            }
        )
    return items


def _extract_accessories(asset: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    accessories = []
    for accessory in asset.get("accessory_properties") or []:
        description = accessory.get("description") or {}
        market_name = (
            description.get("market_hash_name")
            or description.get("market_name")
            or description.get("name")
        )
        item_type = description.get("type") or ""
        if kind not in item_type and not str(market_name or "").startswith(f"{kind} |"):
            continue

        item = {
            "name": market_name or "",
            "icon_url": description.get("icon_url") or "",
            "classid": str(accessory.get("classid") or ""),
        }
        wear = _accessory_property(accessory, 4, "float_value")
        if wear is not None:
            item["wear"] = wear
        charm_pattern = _accessory_property(accessory, 3, "int_value")
        if charm_pattern is not None:
            item["pattern_template"] = charm_pattern
        accessories.append(item)
    return accessories


def extract_stickers(asset: dict[str, Any]) -> list[dict[str, str]]:
    stickers = _extract_titled_images(
        _description_html(asset, "sticker_info"), "Sticker:"
    )
    return stickers or _extract_accessories(asset, "Sticker")


def extract_charm(asset: dict[str, Any]) -> dict[str, str]:
    charms = _extract_titled_images(_description_html(asset, "keychain_info"), "Charm:")
    if charms:
        return charms[0]
    accessories = _extract_accessories(asset, "Charm")
    return accessories[0] if accessories else {}


def extract_asset_metadata(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset_id": str(asset.get("id") or asset.get("assetid") or ""),
        "appid": asset.get("appid"),
        "contextid": str(asset.get("contextid") or ""),
        "market_name": asset.get("market_name")
        or asset.get("market_hash_name")
        or asset.get("name")
        or "",
        "float_value": extract_float_value(asset),
        "pattern_template": extract_pattern_template(asset),
        "item_certificate": extract_item_certificate(asset),
        "stickers": extract_stickers(asset),
        "charm": extract_charm(asset),
    }


def summarize_market_page(raw_data: str) -> str:
    soup = BeautifulSoup(raw_data, "html.parser")
    title = (
        (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    )
    lowered = raw_data.lower()
    hints = []
    userinfo = soup.find(attrs={"data-userinfo": True})
    data_userinfo = userinfo.get("data-userinfo", "") if userinfo else ""
    if '"logged_in":false' in data_userinfo or (
        "global_action_link" in lowered and "sign in" in lowered
    ):
        hints.append("login")
    if "too many requests" in lowered:
        hints.append("rate_limit")
    if "there was an error" in lowered:
        hints.append("steam_error")
    if "captcha" in lowered:
        hints.append("captcha")
    hint_text = f", hints={','.join(hints)}" if hints else ""
    return f"title={title!r}, length={len(raw_data)}{hint_text}"


class AsyncParser:
    def __init__(
        self,
        session: AsyncSteamSession,
        proxy_manager: ProxyManager | None = None,
        request_timeout: int = 10,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        debug_response_path: str | None = None,
    ):
        self.steam_session = session
        self.proxy_manager = proxy_manager
        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.debug_response_path = debug_response_path or os.getenv(
            "MARKET_RESPONSE_DEBUG_PATH"
        )
        self.retry_statuses = {429, 500, 502, 503, 504}

    def _save_debug_response(self, text: str) -> None:
        if not self.debug_response_path:
            return

        debug_path = Path(self.debug_response_path)
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(text, encoding="utf-8")

    @secundomer
    async def get_raw_data_from_market(self, url: str) -> str:
        text, _ = await self._fetch_raw_data_from_market(url)
        return text

    async def _fetch_raw_data_from_market(self, url: str) -> tuple[str, str]:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            proxy = (
                self.proxy_manager.get_random_proxy() if self.proxy_manager else None
            )
            try:
                async with self.steam_session.get_async_session(url) as local_session:
                    response = await local_session.get(
                        url,
                        proxy=proxy,
                        ssl=False,
                        timeout=self.request_timeout,
                    )
                    text = await response.text(encoding="utf-8", errors="replace")
                    self._save_debug_response(text)
                    response_url = getattr(response, "url", None)
                    final_url = str(response_url) if response_url else url
                    logger.info(
                        "Steam market GET completed: status=%s requested=%s final=%s bytes=%s attempt=%s/%s",
                        response.status,
                        url,
                        final_url,
                        len(text),
                        attempt,
                        self.max_retries,
                    )
                    if final_url != url:
                        logger.warning(
                            "Steam market page redirected from %s to %s",
                            url,
                            final_url,
                        )
                    if response.status == 200:
                        return text, final_url

                    logger.warning(
                        "Steam market page returned HTTP %s for %s on attempt %s/%s",
                        response.status,
                        url,
                        attempt,
                        self.max_retries,
                    )
                    if response.status not in self.retry_statuses:
                        return text, final_url
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
        raise RuntimeError(
            f"Could not fetch Steam market page after {self.max_retries} attempts"
        )

    async def get_listing_info_from_market(self, url: str) -> dict[str, Any]:
        logger.info("Fetching Steam market page: %s", url)
        raw_data, final_url = await self._fetch_raw_data_from_market(url)
        logger.info(
            "Parsing Steam market page HTML: requested=%s final=%s %s",
            url,
            final_url,
            summarize_market_page(raw_data),
        )
        try:
            listing_info = self.extract_json_from_raw_data(raw_data)
        except Exception as exc:
            logger.error(
                "Could not parse Steam market page for %s: %s (%s)",
                url,
                exc,
                summarize_market_page(raw_data),
            )
            raise

        logger.info(
            "Parsed %s listings from Steam market page %s",
            len(listing_info),
            final_url,
        )
        return listing_info

    def extract_json_from_raw_data(self, raw_data: str) -> dict[str, Any]:
        return _extract_new_market_listing_info(raw_data)

    def calculate_price(self, item_data: dict) -> tuple[int, int, int]:
        price_no_fee = int(
            item_data.get("converted_price") or item_data.get("price") or 0
        )
        fee = int(item_data.get("converted_fee") or item_data.get("fee") or 0)
        price = price_no_fee + fee
        return price_no_fee, fee, price

    def extract_item_data(self, items_json: dict) -> list[dict]:
        extracted_items = []
        for listing_id, item_data in items_json.items():
            inspect_link = construct_inspect_link(item_data, listing_id)
            price_no_fee, fee, price = self.calculate_price(item_data)
            asset = item_data.get("asset") or {}
            metadata = extract_asset_metadata(asset)
            extracted_items.append(
                {
                    "listing_id": listing_id,
                    "inspect_link": inspect_link,
                    "price": price,
                    "price_no_fee": price_no_fee,
                    "fee": fee,
                    **metadata,
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
            raise RuntimeError(
                f"Steam market page returned HTTP {response.status_code}"
            )
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
