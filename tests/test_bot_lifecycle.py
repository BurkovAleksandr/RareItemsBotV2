import asyncio

import pytest

from assets.bot import AsyncSteamBot
from assets.config import Config


pytestmark = pytest.mark.mock


class FakeSession:
    def is_alive(self):
        return True


class FakeItems:
    def get_track_items(self):
        return []

    def check(self, listing_id):
        return False

    def add_to_checked(self, listing_id):
        pass

    def add_to_bought_items(self, *args):
        pass


class FakeParser:
    pass


class FakeInfoFetcher:
    pass


class FakePriceFetcher:
    pass


class FakeBuyModule:
    pass


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
