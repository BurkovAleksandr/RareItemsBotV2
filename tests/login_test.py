import os

import pytest

from assets.steampy_compat import apply_steampy_compat

apply_steampy_compat()

from steampy.client import SteamClient


@pytest.mark.real
def test_steam_login_smoke():
    api_key = os.getenv("STEAM_API_KEY")
    username = os.getenv("STEAM_USERNAME")
    password = os.getenv("STEAM_PASSWORD")
    mafile_path = os.getenv("STEAM_MAFILE_PATH")

    if not all((api_key, username, password, mafile_path)):
        pytest.skip("Steam credentials are required for live login smoke test")

    client = SteamClient(
        api_key=api_key,
        username=username,
        password=password,
        steam_guard=mafile_path,
    )
    client.login()

    assert client.is_session_alive()
