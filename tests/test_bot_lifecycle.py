import asyncio

import pytest

from assets.bot import AsyncSteamBot
from assets.config import Config


pytestmark = pytest.mark.mock


class FakeSession:
    def is_alive(self):
        return True


class FakeItems:
    def __init__(self):
        self.purchase_attempts = []

    def get_track_items(self):
        return []

    def check(self, listing_id):
        return False

    def add_to_checked(self, listing_id):
        pass

    def add_to_bought_items(self, *args, **kwargs):
        self.purchase_attempts.append((args, kwargs))


class FakeParser:
    pass


class FakeInfoFetcher:
    pass


class FakePriceFetcher:
    def get_price_by_name(self, item_name):
        return 20


class FakeBuyModule:
    pass


class FailingBuyModule:
    def buy_item(self, *args):
        raise RuntimeError("You cannot buy this item. Somebody already bought it.")


class ExpiredSession:
    def __init__(self):
        self.active = False
        self.login_calls = 0
        self.saved_to = None

    def is_alive(self):
        return self.active

    def login(self):
        self.login_calls += 1
        self.active = True

    def save_client(self, accounts_dir):
        self.saved_to = accounts_dir


def test_bot_stop_exits_empty_items_sleep_promptly():
    async def run_bot():
        bot = AsyncSteamBot(
            FakeSession(),
            FakeParser(),
            FakeInfoFetcher(),
            FakePriceFetcher(),
            Config(0, 0, 0, False, 0),
            FakeBuyModule(),
            FakeItems(),
        )
        task = asyncio.create_task(bot.start())
        await asyncio.sleep(0.05)

        assert bot.is_running

        bot.stop()
        await asyncio.wait_for(task, timeout=1)

        assert not bot.is_running

    asyncio.run(run_bot())


def test_bot_relogs_in_inactive_parser_session():
    async def run_bot():
        session = ExpiredSession()
        bot = AsyncSteamBot(
            session,
            FakeParser(),
            FakeInfoFetcher(),
            FakePriceFetcher(),
            Config(0, 0, 0, False, 0),
            FakeBuyModule(),
            FakeItems(),
            accounts_dir="./accounts-test/",
        )
        task = asyncio.create_task(bot.start())
        await asyncio.sleep(0.05)

        bot.stop()
        await asyncio.wait_for(task, timeout=1)

        assert session.login_calls == 1
        assert session.saved_to == "./accounts-test/"
        assert not bot.is_running

    asyncio.run(run_bot())


def test_bot_records_failed_purchase_attempt():
    async def run_bot():
        items = FakeItems()
        bot = AsyncSteamBot(
            FakeSession(),
            FakeParser(),
            FakeInfoFetcher(),
            FakePriceFetcher(),
            Config(0, 0, 0.1, True, 0),
            FailingBuyModule(),
            items,
        )

        await bot.process_items(
            "AK-47 | Redline",
            [
                {
                    "listing_id": "listing-1",
                    "price": 1000,
                    "fee": 100,
                    "inspect_link": "steam://inspect/listing-1",
                    "stickers": [{"name": "Sticker | Crown"}],
                }
            ],
        )

        assert len(items.purchase_attempts) == 1
        args, kwargs = items.purchase_attempts[0]
        assert args[:4] == ("AK-47 | Redline", "listing-1", 10.0, 20)
        assert kwargs["success"] is False
        assert "Somebody already bought it" in kwargs["error"]

    asyncio.run(run_bot())
