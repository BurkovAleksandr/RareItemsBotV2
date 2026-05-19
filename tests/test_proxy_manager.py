import pytest

from assets.proxy import ProxyManager


pytestmark = pytest.mark.mock


def test_disabled_proxy_manager_returns_none(tmp_path):
    proxy_file = tmp_path / "proxies.txt"
    proxy_file.write_text("127.0.0.1:8080\n", encoding="utf-8")

    manager = ProxyManager(enabled=False)
    manager.load_proxies(proxy_file)

    assert manager.proxies == []
    assert manager.get_random_proxy() is None


def test_proxy_manager_accepts_host_port_format(tmp_path):
    proxy_file = tmp_path / "proxies.txt"
    proxy_file.write_text("127.0.0.1:8080\n", encoding="utf-8")

    manager = ProxyManager()
    manager.load_proxies(proxy_file)

    assert manager.get_random_proxy() == "http://127.0.0.1:8080"
