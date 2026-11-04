"""Audio handler service.

Validates and broadcasts voice/video_note messages.
"""

import logging
from typing import Optional

from aiogram import Bot
from aiogram.types import Message

from utils import broadcast_message

logger = logging.getLogger(__name__)


def get_media_info(message: Message) -> Optional[dict]:
    """Extract media info from a message.

    Returns dict with file_id, type, and duration, or None if not voice/video_note.
    """
    if message.voice:
        return {
            "file_id": message.voice.file_id,
            "type": "voice",
            "duration": message.voice.duration,
        }
    elif message.video_note:
        return {
            "file_id": message.video_note.file_id,
            "type": "video_note",
            "duration": message.video_note.duration,
        }
    return None


def validate_audio_duration(
    duration: int, max_duration: int = 60
) -> bool:
    """Check if audio duration is within limits."""
    return duration <= max_duration


async def broadcast_speech(
    bot: Bot,
    chat_ids: list[int],
    file_id: str,
    media_type: str,
    caption_text: str,
) -> None:
    """Broadcast a speech (voice/video_note) to all chat_ids.

    Sends the media first, then the caption as a separate message
    (since video_note doesn't support captions).
    """
    if media_type == "video_note":
        # video_note doesn't support caption, send separately
        from utils import broadcast_video_note

        await broadcast_video_note(bot, chat_ids, file_id)
        if caption_text:
            await broadcast_message(bot, chat_ids, caption_text)
    else:
        # voice supports caption
        from utils import broadcast_voice

        await broadcast_voice(bot, chat_ids, file_id, caption=caption_text)
