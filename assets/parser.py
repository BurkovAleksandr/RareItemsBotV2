from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from assets.currency_rates import Currency
from assets.proxy import ProxyManager
from assets.session import AsyncSteamSession, SteamSession
from assets.utils import construct_inspect_link, secundomer

logger = logging.getLogger(__name__)


class ListingInfoNotFound(ValueError):
    pass


MARKET_SEARCH_ACTION_TYPE = os.getenv("MARKET_SEARCH_ACTION_TYPE", "4OPT6VBA:Search")

EXTERIOR_FILTERS = {
    "Factory New": "WearCategory0",
    "Minimal Wear": "WearCategory1",
    "Field-Tested": "WearCategory2",
    "Well-Worn": "WearCategory3",
    "Battle-Scarred": "WearCategory4",
}


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


def market_name_from_listing_url(listing_url: str) -> str:
    path_parts = [part for part in urlparse(listing_url).path.split("/") if part]
    if len(path_parts) < 4:
        return ""
    return unquote(path_parts[-1])


def is_market_group_id(item_name: str) -> bool:
    return len(item_name) > 1 and item_name.startswith("G") and item_name[1:].isalnum()


def requested_market_name_from_listing_url(listing_url: str) -> str:
    market_name = market_name_from_listing_url(listing_url)
    return "" if is_market_group_id(market_name) else market_name


def parse_market_listing_url(listing_url: str) -> tuple[int, str]:
    path_parts = [part for part in urlparse(listing_url).path.split("/") if part]
    if len(path_parts) < 4 or path_parts[-4:-2] != ["market", "listings"]:
        raise ValueError(f"Unexpected Steam market listing URL: {listing_url}")
    return int(path_parts[-2]), unquote(path_parts[-1])


def build_market_filters_from_name(market_name: str) -> dict[str, list[str]]:
    filters: dict[str, list[str]] = {}
    if not market_name or is_market_group_id(market_name):
        return filters

    if market_name.startswith("StatTrak"):
        filters["Quality"] = ["strange"]
    elif market_name.startswith("Souvenir "):
        filters["Quality"] = ["tournament"]
    elif any(f"({exterior})" in market_name for exterior in EXTERIOR_FILTERS):
        filters["Quality"] = ["normal"]

    for exterior, filter_value in EXTERIOR_FILTERS.items():
        if f"({exterior})" in market_name:
            filters["Exterior"] = [filter_value]
            break
    return filters


def build_market_search_body(
    listing_url: str,
    requested_market_name: str = "",
    start: int = 0,
) -> list[dict[str, Any]]:
    app_id, item_name = parse_market_listing_url(listing_url)
    return [
        {
            "appid": app_id,
            "strItemName": item_name,
            "filters": build_market_filters_from_name(requested_market_name),
            "accessoryFilters": {},
            "propertyFilters": {},
            "start": start,
        }
    ]


def build_market_search_url(listing_url: str) -> str:
    parsed = urlparse(listing_url)
    return urlunparse(parsed._replace(query="", fragment=""))


def merge_render_assets(payload: dict[str, Any]) -> dict[str, Any]:
    listing_info = payload.get("listinginfo") or {}
    assets = payload.get("assets") or {}
    return merge_listing_assets(listing_info, assets)


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


def normalize_market_search_payload(
    payload: dict[str, Any], requested_market_name: str = ""
) -> dict[str, Any]:
    listing_info: dict[str, Any] = {}
    for listing in payload.get("listings") or []:
        listing_id = str(listing.get("listingid") or "")
        if not listing_id:
            continue

        description = listing.get("description") or {}
        market_hash_name = description.get("market_hash_name") or description.get(
            "market_name"
        )
        if requested_market_name and market_hash_name != requested_market_name:
            continue

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


def validate_render_payload(payload: dict[str, Any], render_url: str) -> None:
    if payload.get("success") in (False, 0):
        message = payload.get("message") or payload.get("error") or "unknown error"
        raise RuntimeError(f"Steam market render failed for {render_url}: {message}")
    if not isinstance(payload.get("listinginfo"), dict):
        raise RuntimeError(
            f"Steam market render returned no listinginfo for {render_url}"
        )


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

    async def get_json_from_market_render(self, url: str) -> dict[str, Any]:
        render_url = build_market_render_url(url)
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            proxy = (
                self.proxy_manager.get_random_proxy() if self.proxy_manager else None
            )
            try:
                async with self.steam_session.get_async_session(
                    render_url
                ) as local_session:
                    response = await local_session.get(
                        render_url,
                        proxy=proxy,
                        ssl=False,
                        timeout=self.request_timeout,
                        headers={"Referer": url},
                    )
                    text = await response.text(encoding="utf-8", errors="replace")
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
                        raise RuntimeError(
                            f"Steam market render returned HTTP {response.status}"
                        )
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
        raise RuntimeError(
            f"Could not fetch Steam market render after {self.max_retries} attempts"
        )

    async def get_json_from_market_search(
        self,
        url: str,
        requested_market_name: str = "",
        start: int = 0,
    ) -> dict[str, Any]:
        search_url = build_market_search_url(url)
        body = build_market_search_body(
            search_url,
            requested_market_name=requested_market_name,
            start=start,
        )
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            proxy = (
                self.proxy_manager.get_random_proxy() if self.proxy_manager else None
            )
            try:
                async with self.steam_session.get_async_session(
                    search_url
                ) as local_session:
                    response = await local_session.post(
                        search_url,
                        json=body,
                        proxy=proxy,
                        ssl=False,
                        timeout=self.request_timeout,
                        headers={
                            "Accept": "*/*",
                            "Content-Type": "application/json; charset=utf-8",
                            "Origin": "https://steamcommunity.com",
                            "Referer": search_url,
                            "x-valve-action-type": MARKET_SEARCH_ACTION_TYPE,
                            "x-valve-request-type": "routeAction",
                        },
                    )
                    logger.debug(
                        "Steam market search POST to %s with body %s returned HTTP %s on attempt %s/%s",
                        search_url,
                        body,
                        response.status,
                        attempt,
                        self.max_retries,
                    )
                    text = await response.text(encoding="utf-8", errors="replace")
                    if response.status == 200:
                        try:
                            payload = json.loads(text)
                        except json.JSONDecodeError as exc:
                            raise RuntimeError(
                                f"Steam market search returned non-JSON response: {text[:200]!r}"
                            ) from exc
                        if not isinstance(payload.get("listings"), list):
                            raise RuntimeError(
                                f"Steam market search returned no listings for {search_url}"
                            )
                        payload.pop("facets", None)
                        return payload

                    logger.warning(
                        "Steam market search returned HTTP %s for %s on attempt %s/%s",
                        response.status,
                        search_url,
                        attempt,
                        self.max_retries,
                    )
                    if response.status not in self.retry_statuses:
                        raise RuntimeError(
                            f"Steam market search returned HTTP {response.status}"
                        )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Steam market search request failed for %s on attempt %s/%s: %s",
                    search_url,
                    attempt,
                    self.max_retries,
                    exc,
                )

            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_base_delay * attempt)

        if last_error:
            raise last_error
        raise RuntimeError(
            f"Could not fetch Steam market search after {self.max_retries} attempts"
        )

    async def _get_listing_info_from_market_search(
        self, url: str, requested_market_name: str
    ) -> dict[str, Any]:
        search_payload = await self.get_json_from_market_search(
            url,
            requested_market_name=requested_market_name,
        )
        return normalize_market_search_payload(
            search_payload,
            requested_market_name=requested_market_name,
        )

    async def _get_listing_info_from_market_legacy(self, url: str) -> dict[str, Any]:
        raw_data, final_url = await self._fetch_raw_data_from_market(url)
        try:
            listing_info = self.extract_json_from_raw_data(raw_data)
            return merge_listing_assets(listing_info, _extract_assets_info(raw_data))
        except ListingInfoNotFound:
            logger.warning(
                "Steam market page did not contain g_rgListingInfo for %s; falling back via %s (%s)",
                url,
                final_url,
                summarize_market_page(raw_data),
            )
            requested_market_name = requested_market_name_from_listing_url(url)
            try:
                search_payload = await self.get_json_from_market_search(
                    final_url,
                    requested_market_name=requested_market_name,
                )
                listing_info = normalize_market_search_payload(
                    search_payload,
                    requested_market_name=requested_market_name,
                )
                if listing_info or not requested_market_name:
                    return listing_info
                logger.warning(
                    "Steam market search returned no listings matching %s via %s",
                    requested_market_name,
                    final_url,
                )
            except Exception as exc:
                logger.warning(
                    "Steam market search fallback failed for %s via %s: %s",
                    url,
                    final_url,
                    exc,
                )

            render_payload = await self.get_json_from_market_render(final_url)
            return merge_render_assets(render_payload)

    async def get_listing_info_from_market(self, url: str) -> dict[str, Any]:
        requested_market_name = requested_market_name_from_listing_url(url)
        try:
            listing_info = await self._get_listing_info_from_market_search(
                url,
                requested_market_name=requested_market_name,
            )
            if listing_info or not requested_market_name:
                return listing_info
            logger.warning(
                "Steam market search returned no listings matching %s via %s",
                requested_market_name,
                url,
            )
        except Exception as exc:
            logger.warning(
                "Direct Steam market search failed for %s: %s; falling back to legacy URL resolution",
                url,
                exc,
            )

        return await self._get_listing_info_from_market_legacy(url)

    def extract_json_from_raw_data(self, raw_data: str) -> dict[str, Any]:
        return _extract_listing_info(raw_data)

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
