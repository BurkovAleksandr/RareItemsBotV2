import asyncio

import pytest

from assets.parser import AsyncParser


pytestmark = pytest.mark.mock


class FakeResponse:
    def __init__(self, status: int, text: str):
        self.status = status
        self._text = text

    async def text(self):
        return self._text


class FakeClientSession:
    def __init__(self, owner):
        self.owner = owner

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, *args, **kwargs):
        self.owner.calls += 1
        return self.owner.responses.pop(0)


class FakeSteamSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get_async_session(self):
        return FakeClientSession(self)


def test_async_parser_retries_rate_limited_market_request():
    session = FakeSteamSession(
        [
            FakeResponse(429, "rate limited"),
            FakeResponse(200, "ok"),
        ]
    )
    parser = AsyncParser(session, request_timeout=1, max_retries=2, retry_base_delay=0)

    text = asyncio.run(parser.get_raw_data_from_market("https://steamcommunity.com/market/listings/730/Test"))

    assert text == "ok"
    assert session.calls == 2
