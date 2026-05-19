from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from assets.config import Config
from assets.logger import setup_logging


logger = setup_logging()


def _config_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", ""}:
            return False
    return bool(value)


@dataclass(frozen=True)
class RuntimeConfig:
    api_key: str
    parser_login: str
    parser_password: str
    parser_mafile: str
    buyer_login: str
    buyer_password: str
    buyer_mafile: str
    strick3: float
    strick45: float
    nostrick: float
    autobuy: bool
    min_stickers_price: float
    accounts_dir: str = "./accounts/"
    db_path: str = "./db.db"
    use_proxies: bool = True
    proxies_path: str = "./proxies.txt"
    refresh_currency_rates: bool = True
    refresh_item_prices: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "RuntimeConfig":
        def value(name: str, default=None):
            env_value = os.getenv(name)
            if env_value not in (None, ""):
                return env_value
            return data.get(name, default)

        required = [
            "API_KEY",
            "PARSER_LOGIN",
            "PARSER_PASSWORD",
            "PARSER_MAFILE",
            "BUYER_LOGIN",
            "BUYER_PASSWORD",
            "BUYER_MAFILE",
        ]
        missing = [name for name in required if not value(name)]
        if missing:
            raise ValueError(f"Missing required config values: {', '.join(missing)}")

        return cls(
            api_key=value("API_KEY"),
            parser_login=value("PARSER_LOGIN"),
            parser_password=value("PARSER_PASSWORD"),
            parser_mafile=value("PARSER_MAFILE"),
            buyer_login=value("BUYER_LOGIN"),
            buyer_password=value("BUYER_PASSWORD"),
            buyer_mafile=value("BUYER_MAFILE"),
            strick3=float(value("STRICK3", 0)),
            strick45=float(value("STRICK45", 0)),
            nostrick=float(value("NOSTRICK", 0)),
            autobuy=_config_bool(value("AUTOBUY", False)),
            min_stickers_price=float(value("MIN_STICKERS_PRICE", 0)),
            accounts_dir=value("ACCOUNTS_DIR", "./accounts/"),
            db_path=value("DB_PATH", "./db.db"),
            use_proxies=_config_bool(value("USE_PROXIES", True), default=True),
            proxies_path=value("PROXIES_PATH", "./proxies.txt"),
            refresh_currency_rates=_config_bool(value("REFRESH_CURRENCY_RATES", True), default=True),
            refresh_item_prices=_config_bool(value("REFRESH_ITEM_PRICES", False)),
        )

    def to_config_dict(self) -> dict[str, Any]:
        mapping = {
            "api_key": "API_KEY",
            "parser_login": "PARSER_LOGIN",
            "parser_password": "PARSER_PASSWORD",
            "parser_mafile": "PARSER_MAFILE",
            "buyer_login": "BUYER_LOGIN",
            "buyer_password": "BUYER_PASSWORD",
            "buyer_mafile": "BUYER_MAFILE",
            "strick3": "STRICK3",
            "strick45": "STRICK45",
            "nostrick": "NOSTRICK",
            "autobuy": "AUTOBUY",
            "min_stickers_price": "MIN_STICKERS_PRICE",
            "accounts_dir": "ACCOUNTS_DIR",
            "db_path": "DB_PATH",
            "use_proxies": "USE_PROXIES",
            "proxies_path": "PROXIES_PATH",
            "refresh_currency_rates": "REFRESH_CURRENCY_RATES",
            "refresh_item_prices": "REFRESH_ITEM_PRICES",
        }
        data = asdict(self)
        return {config_name: data[field_name] for field_name, config_name in mapping.items()}


def load_config_data(path: str | None = None) -> dict:
    config_path = path or os.getenv("BOT_CONFIG_PATH", "./config.json")
    if not Path(config_path).exists():
        return {}
    try:
        return json.loads(Path(config_path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"File contains invalid JSON: {config_path}") from exc


def load_runtime_config(path: str | None = None) -> RuntimeConfig:
    return RuntimeConfig.from_dict(load_config_data(path))


def write_config_data(path: str | None, data: dict) -> None:
    config_path = Path(path or os.getenv("BOT_CONFIG_PATH", "./config.json"))
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


async def get_steam_session(
    login: str,
    password: str,
    mafile: str,
    api_key: str,
    accounts_dir: str,
):
    from assets.session import AsyncSteamSession, SteamPyClient

    steam_session = AsyncSteamSession(
        SteamPyClient(),
        login,
        password,
        mafile,
        api_key=api_key,
    )

    try:
        steam_session.load_client(accounts_dir)
        if steam_session.is_alive():
            logger.info("Loaded active Steam session for %s", login)
            return steam_session
        logger.info("Saved Steam session for %s is stale", login)
    except Exception:
        logger.info("No reusable Steam session for %s; logging in", login)

    steam_session.login()
    steam_session.save_client(accounts_dir)
    logger.info("Logged in and saved Steam session for %s", login)
    return steam_session


def load_proxy_manager(runtime_config: RuntimeConfig):
    if not runtime_config.use_proxies:
        logger.info("Proxy usage disabled by config")
        return None

    from assets.proxy import ProxyManager

    proxy_manager = ProxyManager(enabled=True)
    proxy_manager.load_proxies(runtime_config.proxies_path)
    logger.info("Loaded %s proxies", len(proxy_manager.proxies))
    return proxy_manager


async def create_bot(runtime_config: RuntimeConfig):
    from assets.bot import AsyncSteamBot
    from assets.buy import BuyModule
    from assets.currency_rates import Currency
    from assets.database import Items, SqliteItemsRepository
    from assets.inspect import ItemInfoFetcher
    from assets.parser import AsyncParser
    from assets.prices import ItemPriceFetcher, PricesRepository

    parser_session = await get_steam_session(
        runtime_config.parser_login,
        runtime_config.parser_password,
        runtime_config.parser_mafile,
        runtime_config.api_key,
        runtime_config.accounts_dir,
    )

    buyer_session = await get_steam_session(
        runtime_config.buyer_login,
        runtime_config.buyer_password,
        runtime_config.buyer_mafile,
        runtime_config.api_key,
        runtime_config.accounts_dir,
    )

    currency_rates = Currency(runtime_config.api_key)
    if runtime_config.refresh_currency_rates:
        currency_rates.update_steam_currency_rates()

    proxy_manager = load_proxy_manager(runtime_config)

    price_repository = PricesRepository(runtime_config.db_path)
    item_price_fetcher = ItemPriceFetcher(
        db_repository=price_repository,
        proxy_manager=proxy_manager,
    )
    if runtime_config.refresh_item_prices:
        item_price_fetcher.update_all_prices(currency_rates)

    bot_config = Config(
        runtime_config.strick3,
        runtime_config.strick45,
        runtime_config.nostrick,
        runtime_config.autobuy,
        runtime_config.min_stickers_price,
    )

    return AsyncSteamBot(
        parser_session,
        AsyncParser(parser_session, proxy_manager=proxy_manager),
        ItemInfoFetcher(),
        item_price_fetcher,
        bot_config,
        BuyModule(buyer_session),
        Items(SqliteItemsRepository(runtime_config.db_path)),
    )
