"""Room management service.

Handles room creation, joining, leaving, and player management.
"""

import logging
import re
from typing import Optional

from storage.redis_storage import RedisStorage
from utils import generate_room_code, get_display_name

logger = logging.getLogger(__name__)

# Room code format: 6 uppercase letters + digits
CODE_PATTERN = re.compile(r"^[A-Z0-9]{6}$")


class RoomManager:
    """Manages room lifecycle and player operations."""

    def __init__(self, storage: RedisStorage) -> None:
        self.storage = storage

    async def create_room(
        self,
        creator_id: int,
        chat_id: int,
        username: str,
        max_players: int = 10,
    ) -> str:
        """Create a new room and add the creator as the first player.

        Returns the room code.
        """
        # Generate unique room code
        for _ in range(10):
            room_id = generate_room_code()
            if not await self.storage.room_exists(room_id):
                break
        else:
            raise RuntimeError("Could not generate unique room code")

        await self.storage.create_room(
            room_id=room_id,
            creator_id=creator_id,
            max_players=max_players,
        )

        # Add creator as first player
        await self.storage.add_player(
            room_id=room_id,
            user_id=creator_id,
            chat_id=chat_id,
            username=username,
        )

        logger.info(
            "Room %s created by user %s (max_players=%d)",
            room_id,
            creator_id,
            max_players,
        )
        return room_id

    async def join_room(
        self,
        room_id: str,
        user_id: int,
        chat_id: int,
        username: str,
    ) -> tuple[bool, str]:
        """Try to join a room.

        Returns (success, error_message).
        """
        room = await self.storage.get_room(room_id)
        if not room:
            return False, "not_found"

        if room.get("status") != "waiting":
            return False, "game_started"

        max_players = int(room.get("max_players", 10))
        current_count = await self.storage.get_room_player_count(room_id)
        if current_count >= max_players:
            return False, "full"

        # Check if already in room
        player_ids = await self.storage.get_room_player_ids(room_id)
        if str(user_id) in player_ids:
            return False, "already_in_room"

        await self.storage.add_player(
            room_id=room_id,
            user_id=user_id,
            chat_id=chat_id,
            username=username,
        )

        logger.info("User %s joined room %s", user_id, room_id)
        return True, ""

    async def leave_room(
        self,
        user_id: int,
        room_id: str,
    ) -> tuple[bool, bool]:
        """Leave a room.

        Returns (success, was_host).
        """
        room = await self.storage.get_room(room_id)
        if not room:
            return False, False

        is_host = str(room.get("creator_id")) == str(user_id)

        if room.get("status") != "waiting":
            return False, is_host

        await self.storage.remove_player(room_id, user_id)

        if is_host:
            # Host left: close the room
            await self.close_room(room_id)
            logger.info("Host %s left room %s - room closed", user_id, room_id)
        else:
            await self.storage.refresh_room_ttl(room_id)
            logger.info("User %s left room %s", user_id, room_id)

        return True, is_host

    async def close_room(self, room_id: str) -> list[str]:
        """Close a room and return all player IDs for notification.

        Returns list of player user_ids (excluding host).
        """
        player_ids = await self.storage.get_room_player_ids(room_id)
        room = await self.storage.get_room(room_id)
        creator_id = room.get("creator_id", "") if room else ""

        # Clear user-room mappings for all players
        for uid in player_ids:
            await self.storage.clear_user_room(int(uid))

        # Delete the room
        await self.storage.delete_room(room_id)

        # Return non-host player IDs for notification
        return [uid for uid in player_ids if uid != creator_id]

    async def get_room_info(self, room_id: str) -> Optional[dict]:
        """Get full room information including players."""
        room = await self.storage.get_room(room_id)
        if not room:
            return None

        players = await self.storage.get_all_players_data(room_id)
        player_count = len(players)

        return {
            **room,
            "players": players,
            "player_count": player_count,
        }

    async def get_player_chat_ids(self, room_id: str) -> list[int]:
        """Get chat_ids of all players in a room."""
        players = await self.storage.get_all_players_data(room_id)
        return [int(p["chat_id"]) for p in players if p.get("chat_id")]

    async def get_other_player_chat_ids(
        self, room_id: str, exclude_user_id: int
    ) -> list[int]:
        """Get chat_ids of all players except one."""
        players = await self.storage.get_all_players_data(room_id)
        return [
            int(p["chat_id"])
            for p in players
            if p.get("chat_id") and int(p["user_id"]) != exclude_user_id
        ]

    async def check_min_players(self, room_id: str) -> bool:
        """Check if minimum player count is reached."""
        room = await self.storage.get_room(room_id)
        if not room:
            return False
        min_players = int(room.get("min_players", 6))
        count = await self.storage.get_room_player_count(room_id)
        return count >= min_players

    def validate_room_code(self, code: str) -> bool:
        """Validate a room code format: 6 chars, A-Z and 0-9."""
        return bool(CODE_PATTERN.match(code))

    def format_player_list(
        self, players: list[dict], creator_id: str = ""
    ) -> str:
        """Format a player list for display."""
        lines = []
        for p in players:
            uid = p.get("user_id", "")
            username = get_display_name(
                p.get("username"), int(uid) if uid else 0
            )
            suffix = " (Створювач)" if str(uid) == str(creator_id) else ""
            lines.append(f"• {username}{suffix}")
        return "\n".join(lines)
