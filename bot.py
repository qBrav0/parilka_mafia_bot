"""Mafia Bot entry point.

Initializes the bot, dispatcher, Redis storage (for FSM and game data),
registers all routers, and starts polling.
"""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.redis import RedisStorage as AiogramRedisStorage

from config import BOT_TOKEN, DATABASE_URL, REDIS_URL
from handlers import get_all_routers
from services.stats_service import StatsService
from services.timer_service import TimerService
from storage.redis_storage import RedisStorage

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Start the bot."""
    # Initialize bot
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=None),
    )

    # FSM storage in Redis (aiogram's built-in)
    fsm_storage = AiogramRedisStorage.from_url(REDIS_URL)

    # Game data storage (our custom Redis client)
    game_storage = RedisStorage(REDIS_URL)
    await game_storage.connect()

    # Timer service
    timer_service = TimerService(bot)

    # Statistics service (optional — only if DATABASE_URL is set)
    stats_service = None
    if DATABASE_URL:
        stats_service = StatsService(DATABASE_URL)
        await stats_service.connect()
        if stats_service.available:
            logger.info("Stats service enabled (PostgreSQL)")
        else:
            logger.warning("Stats service failed to connect — running without stats")
            stats_service = None
    else:
        logger.info("DATABASE_URL not set — running without stats")

    # Attach stats_service to bot so handlers can access it via getattr(bot, ...)
    bot.stats_service = stats_service  # type: ignore[attr-defined]

    # Create dispatcher
    dp = Dispatcher(storage=fsm_storage)

    # Pass dependencies via workflow_data so handlers receive them via **kwargs
    dp.workflow_data.update(
        {
            "bot": bot,
            "game_storage": game_storage,
            "timer_service": timer_service,
        }
    )

    # Register all routers
    for router in get_all_routers():
        dp.include_router(router)

    logger.info("Bot starting...")

    try:
        # Delete pending updates and start polling
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        if stats_service:
            await stats_service.close()
        await game_storage.close()
        await bot.session.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())
