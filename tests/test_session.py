import pytest
import requests

from assets.session import AsyncSteamSession


pytestmark = pytest.mark.mock


class FakeSteamPyClient:
    def __init__(self, session):
        self._session = session

    def get_session(self):
        return self._session


def test_async_session_builds_cookie_header_for_target_domain():
    sync_session = requests.Session()
    sync_session.cookies.set(
        "steamLoginSecure",
        "community-secure",
        domain="steamcommunity.com",
        path="/",
    )
    sync_session.cookies.set(
        "steamLoginSecure",
        "store-secure",
        domain="store.steampowered.com",
        path="/",
    )
    sync_session.cookies.set(
        "sessionid",
        "community-session",
        domain="steamcommunity.com",
        path="/",
    )

    steam_session = AsyncSteamSession(
        FakeSteamPyClient(sync_session),
        username="user",
        password="password",
        path_to_mafile="guard.maFile",
        api_key="",
    )

    cookie_header = steam_session._cookie_header_for_url(
        "https://steamcommunity.com/market/listings/730/Test"
    )

    assert "steamLoginSecure=community-secure" in cookie_header
    assert "sessionid=community-session" in cookie_header
    assert "store-secure" not in cookie_header
