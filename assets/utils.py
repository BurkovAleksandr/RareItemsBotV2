from __future__ import annotations

import json
import logging
import time

from assets.item import ItemData


logger = logging.getLogger(__name__)


def read_json_from_file(file_path) -> dict:
    """Read a JSON file and return it as a dictionary."""
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"File contains invalid JSON: {file_path}") from exc


def secundomer(func):
    async def wrapper(*args, **kwargs):
        started_at = time.time()
        data = await func(*args, **kwargs)
        logger.debug("%s finished in %.2fs", func.__name__, time.time() - started_at)
        return data

    return wrapper


def construct_inspect_link(item_data: dict, listing_id: str) -> str:
    """Build an inspect link from Steam listing data."""
    asset = item_data.get("asset") or {}
    raw_inspect_link = (asset.get("market_actions") or [{}])[0].get("link")
    asset_id = asset.get("id")
    if not raw_inspect_link or not asset_id:
        return ""
    return raw_inspect_link.replace("%listingid%", listing_id).replace("%assetid%", asset_id)


def create_message(item: ItemData) -> str:
    lines = [
        f"{item.item_name}",
        f"Listing: {item.listing_id}",
        f"Market: https://steamcommunity.com/market/listings/730/{item.item_name.replace(' ', '%20')}",
        f"Steam price: {item.item_price} RUB",
        f"Sticker total: {item.stickers_price} RUB",
    ]

    if item.stickers:
        lines.append("Stickers:")
        for sticker in item.stickers:
            lines.append(f"  - {sticker.get('name')} | price: {sticker.get('price')} RUB")

    if item.charm:
        lines.append(f"Charm: {item.charm.get('name')} | price: {item.charm_price} RUB")

    return "\n".join(lines)
