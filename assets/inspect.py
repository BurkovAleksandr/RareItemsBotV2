"""Item inspect data fetchers."""

from __future__ import annotations

import os
from typing import Protocol

import aiohttp
import requests


DEFAULT_INSPECT_SERVER_URL = "http://127.0.0.1:80/"


def _inspect_server_url() -> str:
    return os.getenv("INSPECT_SERVER_URL", DEFAULT_INSPECT_SERVER_URL)


class IItemInfoFetcher(Protocol):
    def get_sticker_and_charm_info(self, inspect_link: str) -> dict:
        pass

    def extract_sticker_info(self, item_info: dict) -> list[dict]:
        pass

    def extract_charm_info(self, item_info: dict) -> dict:
        pass


class MockItemInfoFetcher(IItemInfoFetcher):
    def get_sticker_and_charm_info(self, inspect_link):
        return {
            "stickers": [
                {"name": "Liquid Fire", "wear": 0.5},
                {"name": "Navi", "wear": 0.1},
            ],
            "charm": {"name": "Loh"},
        }

    def extract_sticker_info(self, item_info):
        return item_info["stickers"]

    def extract_charm_info(self, item_info):
        return item_info["charm"]


class ItemInfoFetcher(IItemInfoFetcher):
    def __init__(self, inspect_server_url: str | None = None, timeout: int = 15):
        self.inspect_server_url = inspect_server_url or _inspect_server_url()
        self.timeout = timeout

    def get_sticker_and_charm_info(self, inspect_link: str) -> dict:
        response = requests.get(
            self.inspect_server_url,
            params={"url": inspect_link},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json().get("iteminfo") or {}

    def extract_sticker_info(self, item_info: dict) -> list[dict]:
        return item_info.get("stickers") or []

    def extract_charm_info(self, item_info: dict) -> dict:
        return item_info.get("keychains") or item_info.get("charm") or {}


class AsyncItemInfoFetcher(IItemInfoFetcher):
    def __init__(self, inspect_server_url: str | None = None, timeout: int = 15):
        self.inspect_server_url = inspect_server_url or _inspect_server_url()
        self.timeout = timeout

    async def get_sticker_and_charm_info(self, inspect_link: str) -> dict:
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            response = await session.get(self.inspect_server_url, params={"url": inspect_link})
            response.raise_for_status()
            payload = await response.json()
            return payload.get("iteminfo") or {}

    def extract_sticker_info(self, item_info: dict) -> list[dict]:
        return item_info.get("stickers") or []

    def extract_charm_info(self, item_info: dict) -> dict:
        return item_info.get("keychains") or item_info.get("charm") or {}
