"""Utility functions for the Mafia bot.

Contains the broadcast pattern (try/except + delay) and other helpers.
"""

import asyncio
import logging
import random
import string
from typing import Optional

from aiogram import Bot
from aiogram.types import Message

logger = logging.getLogger(__name__)

# Delay between sends to avoid Telegram rate limits (~30 msg/sec)
BROADCAST_DELAY = 0.05


def generate_room_code(length: int = 6) -> str:
    """Generate a random room code: 6 uppercase letters + digits."""
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=length))


def normalize_room_code(code: str) -> str:
    """Normalize a room code: strip whitespace and uppercase."""
    return code.strip().upper()


def get_display_name(username: Optional[str], user_id: int) -> str:
    """Get display name for a user.

    If username is available, return @username.
    Otherwise, return User{last 4 digits of user_id}.
    """
    if username and username != "None":
        if username.startswith("@"):
            return username
        return f"@{username}"
    return f"User{str(user_id)[-4:]}"


async def broadcast_message(
    bot: Bot,
    chat_ids: list[int],
    text: str,
    **kwargs,
) -> list[Optional[Message]]:
    """Send a text message to multiple chats with error handling.

    Wraps each send in try/except; logs errors, continues with others.
    Adds a small delay between sends to avoid rate limits.
    """
    results: list[Optional[Message]] = []
    for chat_id in chat_ids:
        try:
            msg = await bot.send_message(chat_id=chat_id, text=text, **kwargs)
            results.append(msg)
        except Exception as e:
            logger.warning(
                "Failed to send message to %s: %s", chat_id, e
            )
            results.append(None)
        if len(chat_ids) > 1:
            await asyncio.sleep(BROADCAST_DELAY)
    return results


async def broadcast_voice(
    bot: Bot,
    chat_ids: list[int],
    file_id: str,
    caption: Optional[str] = None,
    **kwargs,
) -> list[Optional[Message]]:
    """Send a voice message to multiple chats."""
    results: list[Optional[Message]] = []
    for chat_id in chat_ids:
        try:
            msg = await bot.send_voice(
                chat_id=chat_id, voice=file_id, caption=caption, **kwargs
            )
            results.append(msg)
        except Exception as e:
            logger.warning(
                "Failed to send voice to %s: %s", chat_id, e
            )
            results.append(None)
        if len(chat_ids) > 1:
            await asyncio.sleep(BROADCAST_DELAY)
    return results


async def broadcast_video_note(
    bot: Bot,
    chat_ids: list[int],
    file_id: str,
    **kwargs,
) -> list[Optional[Message]]:
    """Send a video note to multiple chats."""
    results: list[Optional[Message]] = []
    for chat_id in chat_ids:
        try:
            msg = await bot.send_video_note(
                chat_id=chat_id, video_note=file_id, **kwargs
            )
            results.append(msg)
        except Exception as e:
            logger.warning(
                "Failed to send video_note to %s: %s", chat_id, e
            )
            results.append(None)
        if len(chat_ids) > 1:
            await asyncio.sleep(BROADCAST_DELAY)
    return results


async def broadcast_media(
    bot: Bot,
    chat_ids: list[int],
    file_id: str,
    media_type: str,
    caption: Optional[str] = None,
    **kwargs,
) -> list[Optional[Message]]:
    """Send voice or video_note to multiple chats based on media_type.

    Preserves the original type (voice -> send_voice, video_note -> send_video_note).
    """
    if media_type == "voice":
        return await broadcast_voice(bot, chat_ids, file_id, caption=caption, **kwargs)
    elif media_type == "video_note":
        return await broadcast_video_note(bot, chat_ids, file_id, **kwargs)
    else:
        logger.error("Unknown media type: %s", media_type)
        return []
