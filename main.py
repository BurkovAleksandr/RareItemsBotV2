import asyncio

from assets.logger import setup_logging
from assets.runtime import RuntimeConfig, create_bot, load_runtime_config


logger = setup_logging()


async def main() -> None:
    runtime_config = load_runtime_config()
    bot = await create_bot(runtime_config)
    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
