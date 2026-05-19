from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from assets.bot import AsyncSteamBot
from assets.buy import BuyModule
from assets.config import Config
from assets.currency_rates import Currency
from assets.database import Items, SqliteItemsRepository
from assets.inspect import ItemInfoFetcher
from assets.logger import setup_logging
from assets.parser import AsyncParser
from assets.prices import ItemPriceFetcher, PricesRepository
from assets.proxy import ProxyManager
from assets.session import AsyncSteamSession, SteamPyClient
from assets.utils import read_json_from_file


logger = setup_logging()


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
            autobuy=bool(int(value("AUTOBUY", 0))),
            min_stickers_price=float(value("MIN_STICKERS_PRICE", 0)),
            accounts_dir=value("ACCOUNTS_DIR", "./accounts/"),
            db_path=value("DB_PATH", "./db.db"),
            proxies_path=value("PROXIES_PATH", "./proxies.txt"),
            refresh_currency_rates=bool(int(value("REFRESH_CURRENCY_RATES", 1))),
            refresh_item_prices=bool(int(value("REFRESH_ITEM_PRICES", 0))),
        )


def load_runtime_config(path: str | None = None) -> RuntimeConfig:
    config_path = path or os.getenv("BOT_CONFIG_PATH", "./config.json")
    return RuntimeConfig.from_dict(read_json_from_file(config_path))


async def get_steam_session(
    login: str,
    password: str,
    mafile: str,
    api_key: str,
    accounts_dir: str,
) -> AsyncSteamSession:
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


async def create_bot(runtime_config: RuntimeConfig) -> AsyncSteamBot:
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

    proxy_manager = ProxyManager()
    proxy_manager.load_proxies(runtime_config.proxies_path)
    logger.info("Loaded %s proxies", len(proxy_manager.proxies))

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


async def main() -> None:
    runtime_config = load_runtime_config()
    bot = await create_bot(runtime_config)
    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
