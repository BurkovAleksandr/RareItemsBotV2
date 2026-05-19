from __future__ import annotations

import random
from pathlib import Path
from typing import Protocol


class IProxyManager(Protocol):
    def load_proxies(self, path: str | Path) -> None:
        pass

    def get_random_proxy(self) -> str | None:
        pass


class ProxyManager(IProxyManager):
    def __init__(self) -> None:
        self.proxies: list[str] = []

    def load_proxies(self, path: str | Path) -> None:
        proxy_path = Path(path)
        if not proxy_path.exists():
            self.proxies = []
            return

        self.proxies = [
            line.strip()
            for line in proxy_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    def get_random_proxy(self) -> str | None:
        if not self.proxies:
            return None

        proxy = random.choice(self.proxies).strip()
        if proxy.startswith(("http://", "https://", "socks5://")):
            return proxy

        parts = proxy.split(":")
        if len(parts) == 4:
            ip, port, user, password = parts
            return f"http://{user}:{password}@{ip}:{port}"

        if len(parts) == 2:
            ip, port = parts
            return f"http://{ip}:{port}"

        raise ValueError(f"Unsupported proxy format: {proxy}")
