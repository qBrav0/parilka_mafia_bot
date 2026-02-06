"""Timer service for managing game phase timers.

Uses asyncio background tasks to update timer messages via edit_message.
Updates every 15 seconds to stay within Telegram rate limits.
"""

import asyncio
import logging
from typing import Callable, Coroutine, Optional

from aiogram import Bot

from messages import TIMER_TEXT

logger = logging.getLogger(__name__)

# Timer update interval (seconds) — 15 sec is safe for rate limits
TIMER_UPDATE_INTERVAL = 15


class TimerService:
    """Manages asyncio background tasks for countdown timers."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._tasks: dict[str, asyncio.Task] = {}

    def _task_key(self, room_id: str, suffix: str = "") -> str:
        return f"{room_id}:{suffix}" if suffix else room_id

    async def start_timer(
        self,
        room_id: str,
        chat_id: int,
        total_seconds: int,
        on_timeout: Optional[Callable[[], Coroutine]] = None,
        timer_suffix: str = "default",
    ) -> int:
        """Start a countdown timer that updates a message.

        Args:
            room_id: Room identifier
            chat_id: Chat to send timer message to
            total_seconds: Total countdown time
            on_timeout: Async callback when timer expires
            timer_suffix: Suffix for task key (to allow multiple timers)

        Returns:
            message_id of the timer message
        """
        # Cancel any existing timer for this key
        task_key = self._task_key(room_id, timer_suffix)
        await self.cancel_timer(room_id, timer_suffix)

        # Send initial timer message
        msg = await self.bot.send_message(
            chat_id=chat_id,
            text=TIMER_TEXT.format(seconds=total_seconds),
        )

        # Start background task
        task = asyncio.create_task(
            self._countdown(
                task_key=task_key,
                chat_id=chat_id,
                message_id=msg.message_id,
                total_seconds=total_seconds,
                on_timeout=on_timeout,
            )
        )
        self._tasks[task_key] = task
        return msg.message_id

    async def start_broadcast_timer(
        self,
        room_id: str,
        chat_ids: list[int],
        total_seconds: int,
        on_timeout: Optional[Callable[[], Coroutine]] = None,
        timer_suffix: str = "default",
    ) -> dict[int, int]:
        """Start countdown timers for multiple chats.

        Returns dict of chat_id -> message_id.
        """
        task_key = self._task_key(room_id, timer_suffix)
        await self.cancel_timer(room_id, timer_suffix)

        # Send initial timer messages to all
        msg_map: dict[int, int] = {}
        for cid in chat_ids:
            try:
                msg = await self.bot.send_message(
                    chat_id=cid,
                    text=TIMER_TEXT.format(seconds=total_seconds),
                )
                msg_map[cid] = msg.message_id
            except Exception as e:
                logger.warning("Failed to send timer to %s: %s", cid, e)

        # Start background task that updates all messages
        task = asyncio.create_task(
            self._broadcast_countdown(
                task_key=task_key,
                msg_map=msg_map,
                total_seconds=total_seconds,
                on_timeout=on_timeout,
            )
        )
        self._tasks[task_key] = task
        return msg_map

    async def cancel_timer(
        self, room_id: str, timer_suffix: str = "default"
    ) -> None:
        """Cancel a running timer."""
        task_key = self._task_key(room_id, timer_suffix)
        task = self._tasks.pop(task_key, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def cancel_all_room_timers(self, room_id: str) -> None:
        """Cancel all timers for a room."""
        keys_to_cancel = [k for k in self._tasks if k.startswith(room_id)]
        for key in keys_to_cancel:
            task = self._tasks.pop(key, None)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _countdown(
        self,
        task_key: str,
        chat_id: int,
        message_id: int,
        total_seconds: int,
        on_timeout: Optional[Callable[[], Coroutine]] = None,
    ) -> None:
        """Background task: update a single timer message periodically."""
        remaining = total_seconds
        try:
            while remaining > 0:
                wait = min(TIMER_UPDATE_INTERVAL, remaining)
                await asyncio.sleep(wait)
                remaining -= wait

                if remaining > 0:
                    try:
                        await self.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=TIMER_TEXT.format(seconds=remaining),
                        )
                    except Exception as e:
                        logger.debug("Timer edit failed: %s", e)

            # Timer expired
            try:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="⏱️ Час вийшов!",
                )
            except Exception:
                pass

            if on_timeout:
                await on_timeout()

        except asyncio.CancelledError:
            # Timer was cancelled (action taken early)
            try:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="⏱️ ✅",
                )
            except Exception:
                pass
        finally:
            self._tasks.pop(task_key, None)

    async def _broadcast_countdown(
        self,
        task_key: str,
        msg_map: dict[int, int],
        total_seconds: int,
        on_timeout: Optional[Callable[[], Coroutine]] = None,
    ) -> None:
        """Background task: update multiple timer messages periodically."""
        remaining = total_seconds
        try:
            while remaining > 0:
                wait = min(TIMER_UPDATE_INTERVAL, remaining)
                await asyncio.sleep(wait)
                remaining -= wait

                if remaining > 0:
                    for cid, mid in msg_map.items():
                        try:
                            await self.bot.edit_message_text(
                                chat_id=cid,
                                message_id=mid,
                                text=TIMER_TEXT.format(seconds=remaining),
                            )
                        except Exception:
                            pass

            # Timer expired
            for cid, mid in msg_map.items():
                try:
                    await self.bot.edit_message_text(
                        chat_id=cid,
                        message_id=mid,
                        text="⏱️ Час вийшов!",
                    )
                except Exception:
                    pass

            if on_timeout:
                await on_timeout()

        except asyncio.CancelledError:
            for cid, mid in msg_map.items():
                try:
                    await self.bot.edit_message_text(
                        chat_id=cid,
                        message_id=mid,
                        text="⏱️ ✅",
                    )
                except Exception:
                    pass
        finally:
            self._tasks.pop(task_key, None)
