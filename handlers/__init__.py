"""Handlers module for Telegram bot commands and callbacks."""

from aiogram import Router

from .callbacks import router as callbacks_router
from .game import router as game_router
from .rooms import router as rooms_router
from .start import router as start_router


def get_all_routers() -> list[Router]:
    """Return all handler routers in registration order."""
    return [
        start_router,
        rooms_router,
        callbacks_router,
        game_router,
    ]
