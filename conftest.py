import os

import pytest

from assets.steampy_compat import apply_steampy_compat


apply_steampy_compat()


def pytest_configure(config):
    config.addinivalue_line("markers", "mock: tests that do not call Steam")
    config.addinivalue_line("markers", "real: tests that call live Steam services")


def pytest_collection_modifyitems(config, items):
    if os.getenv("RUN_STEAM_REAL_TESTS") == "1":
        return

    skip_real = pytest.mark.skip(
        reason="set RUN_STEAM_REAL_TESTS=1 and Steam env vars to run live Steam tests"
    )
    for item in items:
        if "mock" not in item.keywords:
            item.add_marker(pytest.mark.real)
            item.add_marker(skip_real)
