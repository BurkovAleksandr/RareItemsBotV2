from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Protocol

from assets.buy import BuyModule
from assets.config import Config
from assets.database import Items
from assets.inspect import IItemInfoFetcher
from assets.item import ItemData
from assets.parser import AsyncParser
from assets.prices import IItemPriceFetcher
from assets.session import AsyncSteamSession
from assets.utils import create_message


logger = logging.getLogger(__name__)


class ISteamBot(Protocol):
    async def start(self, stop_event: asyncio.Event | None = None) -> None:
        pass

    def stop(self) -> None:
        pass


class AsyncSteamBot:
    def __init__(
        self,
        session: AsyncSteamSession,
        parser: AsyncParser,
        itemInfoFetcher: IItemInfoFetcher,
        itemPriceFetcher: IItemPriceFetcher,
        config: Config,
        buy_module: BuyModule,
        items: Items,
    ):
        self.session = session
        self.parser = parser
        self.itemInfoFetcher = itemInfoFetcher
        self.itemPriceFetcher = itemPriceFetcher
        self.config = config
        self.buy_module = buy_module
        self.items_manager = items
        self._stop_event: asyncio.Event | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()

    def _stop_requested(self) -> bool:
        return bool(self._stop_event and self._stop_event.is_set())

    async def _sleep_or_stop(self, delay: float) -> bool:
        if delay <= 0:
            return self._stop_requested()
        if not self._stop_event:
            await asyncio.sleep(delay)
            return False
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
            return True
        except asyncio.TimeoutError:
            return False

    async def get_items_from_market(self, item_url: str) -> list[dict]:
        raw_data = await self.parser.get_raw_data_from_market(item_url)
        json_data = self.parser.extract_json_from_raw_data(raw_data)
        return self.parser.extract_item_data(json_data)

    async def create_one_task(self, item_name: str, item_url: str, delay: float) -> None:
        if await self._sleep_or_stop(delay):
            return
        try:
            listings = await self.get_items_from_market(item_url)
        except Exception:
            logger.exception("Failed to fetch market listings for %s", item_name)
            return
        if self._stop_requested():
            return
        await self.process_items(item_name, listings)

    async def create_task_queue(self, items: list[dict], batch: int = 2, batch_queue: int = 10) -> list:
        tasks = []
        for cycle in range(batch_queue):
            for index, item in enumerate(items):
                item_name, item_url = next(iter(item.items()))
                delay = (cycle * batch + index % batch) * 0.8
                tasks.append(self.create_one_task(item_name, item_url, delay=delay))
        return tasks

    async def start(self, stop_event: asyncio.Event | None = None) -> None:
        if self._running:
            raise RuntimeError("Bot is already running")

        self._stop_event = stop_event or asyncio.Event()
        self._running = True
        try:
            if not await asyncio.to_thread(self.session.is_alive):
                raise RuntimeError("Steam session is not alive")

            logger.info("Bot started with an active Steam session")
            counter = 0
            completed_requests = 0
            while not self._stop_requested():
                items = await asyncio.to_thread(self.items_manager.get_track_items)
                if not items:
                    logger.warning("No tracked items configured; sleeping before the next check")
                    await self._sleep_or_stop(5)
                    continue

                queue = await self.create_task_queue(items=items, batch=1, batch_queue=20)
                completed_requests += len(queue)

                started_at = time.time()
                await asyncio.gather(*queue, return_exceptions=True)
                logger.info(
                    "Iteration %s finished: %s requests, total requests %s, elapsed %.2fs",
                    counter,
                    len(queue),
                    completed_requests,
                    time.time() - started_at,
                )
                counter += 1
        finally:
            self._running = False
            logger.info("Bot stopped")

    async def process_items(self, item_name: str, items: list[dict]) -> None:
        if not items:
            logger.warning("No listings found for %s", item_name)
            return

        for item in items:
            if self._stop_requested():
                return

            listing_id = item.get("listing_id")
            already_checked = await asyncio.to_thread(self.items_manager.check, listing_id) if listing_id else True
            if not listing_id or already_checked:
                continue

            await asyncio.to_thread(self.items_manager.add_to_checked, listing_id)
            price_cents = item.get("price")
            fee = item.get("fee")
            if not price_cents or fee is None:
                logger.info("Listing %s is sold or missing price data", listing_id)
                continue

            item_price = price_cents / 100
            item_obj = ItemData(
                self.itemInfoFetcher,
                self.itemPriceFetcher,
                item_name,
                listing_id,
                item.get("inspect_link"),
                item_price,
            )
            try:
                await asyncio.to_thread(item_obj.update_item_info)
            except Exception:
                logger.exception("Failed to fetch inspect data for listing %s", listing_id)
                continue

            logger.info(create_message(item_obj))
            if not self.calculate_sticker_profitability(item_obj):
                continue

            if not self.config.autobuy:
                logger.info("Autobuy disabled; profitable listing %s was not bought", listing_id)
                continue

            try:
                purchase_result = await asyncio.to_thread(
                    self.buy_module.buy_item,
                    item_name,
                    listing_id,
                    price_cents,
                    fee,
                )
                await asyncio.to_thread(
                    self.items_manager.add_to_bought_items,
                    item_name,
                    listing_id,
                    item_price,
                    item_obj.stickers_price,
                    datetime.now(),
                )
                logger.info(
                    "Bought %s listing %s for %.2f; wallet balance after buy: %s",
                    item_name,
                    listing_id,
                    item_price,
                    purchase_result.wallet_balance,
                )
            except Exception:
                logger.exception("Failed to buy %s listing %s", item_name, listing_id)

    def calculate_sticker_profitability(self, item: ItemData) -> bool:
        if item.item_price <= 0 or item.stickers_price < self.config.min_stickers_price:
            return False

        if item.strick.strick:
            profit_threshold = {
                3: self.config.strick3,
                4: self.config.strick45,
                5: self.config.strick45,
            }.get(item.strick.strick_count)
            if not profit_threshold:
                return False
            return (
                item.strick.sum_price_strick / item.item_price > profit_threshold
                and item.strick.sum_price_strick > self.config.min_stickers_price
            )

        return item.stickers_price / item.item_price > self.config.nostrick
