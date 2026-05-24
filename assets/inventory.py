from __future__ import annotations

import re
from collections import defaultdict, deque
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from http import HTTPStatus
from typing import Any, Callable
from urllib.parse import quote

from steampy.exceptions import ApiException, TooManyRequests
from steampy.models import Currency, GameOptions
from steampy.utils import merge_items_with_descriptions_from_inventory


STEAM_CDN_URL = "https://community.cloudflare.steamstatic.com/economy/image/"
DEFAULT_MARKET_FEE_RATE = Decimal("0.13")
CS_INVENTORY_GAME = GameOptions("730", "16")
DEFAULT_INVENTORY_COUNT = 75


def parse_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?[0-9][0-9\s.,]*", str(value))
    if not match:
        return default
    number = match.group(0).replace(" ", "")
    if "," in number and "." not in number:
        number = number.replace(",", ".")
    else:
        number = number.replace(",", "")
    try:
        return float(number)
    except ValueError:
        return default


def market_url(item_name: str) -> str:
    return f"https://steamcommunity.com/market/listings/730/{quote(str(item_name or ''))}"


def icon_url(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    return f"{STEAM_CDN_URL}{value}"


def steam_id_from_client(client: Any) -> str:
    steam_guard = getattr(client, "steam_guard", None) or {}
    try:
        steam_id = str(client.get_steam_id() or "").strip()
        if steam_id:
            return steam_id
    except Exception:
        pass
    steam_id = str(steam_guard.get("steamid") or "").strip()
    if steam_id:
        return steam_id
    raise ValueError("Could not resolve Steam ID for inventory request")


def steam_inventory_error(
    response: Any,
    payload: Any,
    *,
    steam_id: str = "",
    url: str = "",
    params: dict[str, Any] | None = None,
) -> str:
    context = (
        f"status={getattr(response, 'status_code', '-')}, "
        f"steam_id={steam_id or '-'}, "
        f"url={url or getattr(response, 'url', '-')}, "
        f"params={params or {}}, "
    )
    if isinstance(payload, dict):
        message = (
            payload.get("Error")
            or payload.get("error")
            or payload.get("message")
            or payload.get("err_msg")
            or ""
        )
        keys = ", ".join(sorted(str(key) for key in payload.keys()))
        preview = str(payload)[:500]
        return (
            "Steam inventory request failed: "
            f"{context}"
            f"success={payload.get('success')!r}, "
            f"error={message or '-'}, keys=[{keys}], preview={preview}"
        )
    return (
        "Steam inventory request failed: "
        f"{context}payload={str(payload)[:500]}"
    )


def _request_inventory_payload(
    client: Any,
    url: str,
    *,
    steam_id: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    response = client._session.get(
        url,
        params=params,
        headers={
            "Accept": "application/json,text/plain,*/*",
            "Referer": f"https://steamcommunity.com/profiles/{steam_id}/inventory",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0",
        },
    )
    if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
        raise TooManyRequests("Too many requests, try again later.")

    try:
        payload = response.json()
    except ValueError as exc:
        raise ApiException(
            f"Steam inventory returned non-JSON response: status={response.status_code}, "
            f"steam_id={steam_id}, url={url}, params={params}, body={response.text[:500]}"
        ) from exc

    if not isinstance(payload, dict) or payload.get("success") not in (1, True):
        raise ApiException(
            steam_inventory_error(response, payload, steam_id=steam_id, url=url, params=params)
        )
    return payload


def _fetch_inventory_v2(
    client: Any,
    steam_id: str,
    game: GameOptions,
    count: int,
    merge: bool,
) -> dict:
    url = f"https://steamcommunity.com/inventory/{steam_id}/{game.app_id}/{game.context_id}"
    assets: list[dict] = []
    descriptions: list[dict] = []
    start_assetid = None

    while True:
        params: dict[str, Any] = {
            "l": "english",
            "count": count,
            "preserve_bbcode": 1,
            "raw_asset_properties": 1,
        }
        if start_assetid:
            params["start_assetid"] = start_assetid
        payload = _request_inventory_payload(client, url, steam_id=steam_id, params=params)

        assets.extend(payload.get("assets") or [])
        descriptions.extend(payload.get("descriptions") or [])

        if not payload.get("more_items") or not payload.get("last_assetid"):
            break
        start_assetid = str(payload["last_assetid"])

    payload = {"success": 1, "assets": assets, "descriptions": descriptions}
    return merge_items_with_descriptions_from_inventory(payload, game) if merge else payload


def _legacy_inventory_to_v2_payload(payload: dict[str, Any]) -> dict[str, Any]:
    inventory = payload.get("rgInventory") or {}
    descriptions = payload.get("rgDescriptions") or {}

    assets = []
    for asset_id, item in inventory.items():
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row.setdefault("assetid", str(row.get("id") or asset_id))
        row.setdefault("amount", row.get("amount") or "1")
        assets.append(row)

    description_rows = []
    for key, description in descriptions.items():
        if not isinstance(description, dict):
            continue
        row = dict(description)
        if "_" in str(key):
            classid, instanceid = str(key).split("_", 1)
            row.setdefault("classid", classid)
            row.setdefault("instanceid", instanceid)
        description_rows.append(row)

    return {"success": 1, "assets": assets, "descriptions": description_rows}


def _fetch_inventory_legacy(
    client: Any,
    steam_id: str,
    game: GameOptions,
    merge: bool,
) -> dict:
    url = f"https://steamcommunity.com/profiles/{steam_id}/inventory/json/{game.app_id}/{game.context_id}"
    payload = _request_inventory_payload(
        client,
        url,
        steam_id=steam_id,
        params={"l": "english"},
    )
    normalized = _legacy_inventory_to_v2_payload(payload)
    return merge_items_with_descriptions_from_inventory(normalized, game) if merge else normalized


def fetch_steam_inventory(
    client: Any,
    game: GameOptions = CS_INVENTORY_GAME,
    *,
    count: int = DEFAULT_INVENTORY_COUNT,
    merge: bool = True,
) -> dict:
    steam_id = steam_id_from_client(client)
    attempts = []
    counts = []
    for value in (count, 75, 200, 500):
        value = max(1, min(500, int(value)))
        if value not in counts:
            counts.append(value)

    for page_size in counts:
        try:
            return _fetch_inventory_v2(client, steam_id, game, page_size, merge)
        except ApiException as exc:
            attempts.append(f"v2 count={page_size}: {exc}")

    try:
        return _fetch_inventory_legacy(client, steam_id, game, merge)
    except ApiException as exc:
        attempts.append(f"legacy: {exc}")

    raise ApiException("Could not load Steam inventory. Attempts: " + " | ".join(attempts))


def clean_html_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def extract_trade_lock(item: dict[str, Any]) -> dict[str, Any]:
    values = []
    for key in ("owner_descriptions", "descriptions"):
        for entry in item.get(key) or []:
            if isinstance(entry, dict):
                values.append(clean_html_text(str(entry.get("value") or "")))

    text = " ".join(value for value in values if value)
    match = re.search(
        r"((?:Tradable|Marketable) After\s+([A-Za-z]{3}\s+\d{1,2},\s+\d{4}\s+\(\d{1,2}:\d{2}:\d{2}\)\s+GMT))",
        text,
        flags=re.I,
    )
    if not match:
        locked = not bool(item.get("tradable")) or not bool(item.get("marketable"))
        return {"locked": locked, "text": "", "available_at": ""}

    available_at = ""
    try:
        parsed = datetime.strptime(match.group(2), "%b %d, %Y (%H:%M:%S) GMT")
        available_at = parsed.isoformat(sep=" ")
    except ValueError:
        available_at = match.group(2)

    return {"locked": True, "text": match.group(1), "available_at": available_at}


def normalize_inventory_item(item: dict[str, Any]) -> dict[str, Any]:
    asset_id = str(item.get("assetid") or item.get("asset_id") or item.get("id") or "")
    item_name = str(
        item.get("market_hash_name")
        or item.get("market_name")
        or item.get("name")
        or ""
    ).strip()
    action_link = ""
    for action in item.get("actions") or item.get("market_actions") or []:
        if isinstance(action, dict) and action.get("link"):
            action_link = str(action["link"]).replace("%assetid%", asset_id)
            break

    trade_lock = extract_trade_lock(item)
    owner_text = " ".join(
        clean_html_text(str(entry.get("value") or ""))
        for entry in item.get("owner_descriptions") or []
        if isinstance(entry, dict)
    ).casefold()
    listed_on_market = (
        "listed on the steam community market" in owner_text
        or "view my listing" in owner_text
    )
    return {
        "asset_id": asset_id,
        "classid": str(item.get("classid") or ""),
        "instanceid": str(item.get("instanceid") or ""),
        "item_name": item_name,
        "name": str(item.get("name") or item_name),
        "icon_url": icon_url(str(item.get("icon_url_large") or item.get("icon_url") or "")),
        "tradable": bool(item.get("tradable")),
        "marketable": bool(item.get("marketable")),
        "trade_lock": trade_lock,
        "inspect_link": action_link,
        "market_url": market_url(item_name) if item_name else "",
        "listed_on_market": listed_on_market,
        "raw": item,
    }


def normalize_active_listings(listings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    active: dict[str, dict[str, Any]] = {}
    for listing_id, listing in (listings.get("sell_listings") or {}).items():
        description = listing.get("description") or {}
        asset_id = str(
            description.get("assetid")
            or description.get("asset_id")
            or description.get("id")
            or ""
        )
        if not asset_id:
            continue
        item_name = str(
            description.get("market_hash_name")
            or description.get("market_name")
            or description.get("name")
            or ""
        )
        active[asset_id] = {
            "listing_id": str(listing.get("listing_id") or listing_id),
            "buyer_pay": str(listing.get("buyer_pay") or ""),
            "you_receive": str(listing.get("you_receive") or ""),
            "created_on": str(listing.get("created_on") or ""),
            "need_confirmation": bool(listing.get("need_confirmation")),
            "item_name": item_name,
        }
    return active


def target_buyer_price_to_receive(
    target_price: float | Decimal,
    fee_rate: Decimal = DEFAULT_MARKET_FEE_RATE,
) -> float:
    target = Decimal(str(target_price or 0))
    if target <= 0:
        return 0.0
    receive = target / (Decimal("1") + fee_rate)
    return float(receive.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def calculate_sale_price(
    *,
    base_price: float,
    first_sticker_price: float,
    fee_rate: Decimal = DEFAULT_MARKET_FEE_RATE,
) -> dict[str, Any]:
    base = Decimal(str(base_price or 0))
    sticker = Decimal(str(first_sticker_price or 0))
    target = (base * (Decimal("1") + fee_rate)) + sticker
    target = target.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    receive = Decimal(str(target_buyer_price_to_receive(target, fee_rate)))
    return {
        "base_price": float(base),
        "fee_rate": float(fee_rate),
        "first_sticker_price": float(sticker),
        "suggested_price": float(target),
        "price_to_receive": float(receive),
    }


def build_inventory_cards(
    *,
    purchases: list[dict[str, Any]],
    inventory: dict[str, dict[str, Any]],
    active_listings: dict[str, dict[str, Any]] | None = None,
    market_price_lookup: Callable[[str], float | None] | None = None,
) -> list[dict[str, Any]]:
    active_listings = active_listings or {}
    normalized_inventory = [
        normalize_inventory_item(item)
        for item in inventory.values()
        if isinstance(item, dict)
    ]

    exact_purchases: dict[str, dict[str, Any]] = {}
    name_purchases: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for purchase in purchases:
        if not purchase.get("success"):
            continue
        asset_id = str(purchase.get("asset_id") or "")
        if asset_id:
            exact_purchases[asset_id] = purchase
        else:
            name_purchases[str(purchase.get("item_name") or "")].append(purchase)

    cards = []
    for item in normalized_inventory:
        asset_id = item["asset_id"]
        purchase = exact_purchases.get(asset_id)
        if purchase is None:
            candidates = name_purchases.get(item["item_name"])
            if not candidates:
                continue
            purchase = candidates.popleft()

        stickers = purchase.get("stickers") or []
        first_sticker_price = 0.0
        for sticker in stickers:
            if isinstance(sticker, dict):
                first_sticker_price = parse_float(sticker.get("price"))
                if first_sticker_price > 0:
                    break

        live_market_price = None
        if market_price_lookup:
            live_market_price = market_price_lookup(item["item_name"])
        base_price = live_market_price or parse_float(purchase.get("price"))
        suggestion = calculate_sale_price(
            base_price=base_price,
            first_sticker_price=first_sticker_price,
        )
        suggestion["base_source"] = "steam" if live_market_price else "purchase"

        active_listing = active_listings.get(asset_id)
        db_listing_id = str(purchase.get("sell_listing_id") or "")
        listed = bool(active_listing or db_listing_id or item.get("listed_on_market"))
        cards.append(
            {
                **item,
                "purchase": purchase,
                "stickers": stickers,
                "stickers_price": purchase.get("stickers_price"),
                "suggestion": suggestion,
                "listed": listed,
                "listing": active_listing
                or {
                    "listing_id": db_listing_id or ("inventory" if item.get("listed_on_market") else ""),
                    "buyer_pay": purchase.get("sell_price") or "",
                    "you_receive": purchase.get("sell_price_to_receive") or "",
                    "created_on": purchase.get("listed_at") or "",
                    "need_confirmation": purchase.get("sell_status") == "pending_confirmation",
                },
                "sell_status": purchase.get("sell_status") or ("listed" if listed else ""),
                "sell_error": purchase.get("sell_error") or "",
            }
        )

    cards.sort(key=lambda item: str((item.get("purchase") or {}).get("date") or ""), reverse=True)
    return cards


def parse_market_price(payload: dict[str, Any]) -> float | None:
    for key in ("lowest_price", "median_price"):
        value = payload.get(key)
        if value:
            parsed = parse_float(value)
            if parsed > 0:
                return parsed
    return None


def fetch_market_price(client: Any, item_name: str) -> float | None:
    payload = client.market.fetch_price(
        item_name,
        GameOptions.CS,
        currency=Currency.RUB,
        country="RU",
    )
    return parse_market_price(payload)
