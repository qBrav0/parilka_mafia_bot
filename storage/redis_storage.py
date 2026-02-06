"""Redis storage for game data.

Key structure follows section 3.3.5 of the specification.
"""

import logging
import time
from typing import Any, Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# TTL for inactive rooms (30 minutes)
ROOM_TTL = 1800


class RedisStorage:
    """Async Redis client for game data storage."""

    def __init__(self, redis_url: str) -> None:
        self._redis: Optional[aioredis.Redis] = None
        self._redis_url = redis_url

    async def connect(self) -> None:
        """Establish connection to Redis."""
        self._redis = aioredis.from_url(
            self._redis_url,
            decode_responses=True,
        )
        await self._redis.ping()
        logger.info("Connected to Redis")

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            logger.info("Redis connection closed")

    @property
    def redis(self) -> aioredis.Redis:
        if self._redis is None:
            raise RuntimeError("Redis not connected. Call connect() first.")
        return self._redis

    # ──────────────────────────────────────────────
    # Room operations
    # ──────────────────────────────────────────────

    async def create_room(
        self,
        room_id: str,
        creator_id: int,
        max_players: int = 10,
        min_players: int = 6,
    ) -> None:
        """Create a new room with default settings."""
        key = f"room:{room_id}"
        room_data = {
            "room_id": room_id,
            "creator_id": str(creator_id),
            "status": "waiting",
            "day_number": "0",
            "current_speaker_index": "0",
            "min_players": str(min_players),
            "max_players": str(max_players),
            "speech_time": "90",
            "max_audio_duration": "60",
            "voting_time": "30",
            "defense_prep_time": "30",
            "last_word_max_audio": "45",
            "last_word_time": "60",
            "night_action_time": "15",
            "mafia_count": "",
            "has_doctor": "",
        }
        pipe = self.redis.pipeline()
        pipe.hset(key, mapping=room_data)
        pipe.expire(key, ROOM_TTL)
        pipe.sadd("rooms:active", room_id)
        await pipe.execute()

    async def get_room(self, room_id: str) -> Optional[dict]:
        """Get room data."""
        data = await self.redis.hgetall(f"room:{room_id}")
        return data if data else None

    async def update_room(self, room_id: str, **fields: Any) -> None:
        """Update room fields."""
        if fields:
            str_fields = {k: str(v) for k, v in fields.items()}
            await self.redis.hset(f"room:{room_id}", mapping=str_fields)

    async def delete_room(self, room_id: str) -> None:
        """Delete a room and all related keys."""
        # Get all players first
        player_ids = await self.get_room_player_ids(room_id)

        pipe = self.redis.pipeline()
        pipe.delete(f"room:{room_id}")
        pipe.delete(f"room:{room_id}:players")
        pipe.srem("rooms:active", room_id)

        # Delete player data
        for uid in player_ids:
            pipe.delete(f"room:{room_id}:player:{uid}")
            pipe.delete(f"user:{uid}:room")

        # Delete game-related keys (best effort)
        pipe.delete(f"room:{room_id}:game_state")
        pipe.delete(f"room:{room_id}:don_strategy:file_id")
        pipe.delete(f"room:{room_id}:don_strategy:type")
        pipe.delete(f"room:{room_id}:don_strategy:duration")
        pipe.delete(f"room:{room_id}:don_strategy:timestamp")
        pipe.delete(f"room:{room_id}:timer:speech")
        pipe.delete(f"room:{room_id}:timer:voting")
        pipe.delete(f"room:{room_id}:timer:defense")
        pipe.delete(f"room:{room_id}:timer:message_id")

        await pipe.execute()

    async def room_exists(self, room_id: str) -> bool:
        """Check if a room exists."""
        return await self.redis.exists(f"room:{room_id}") > 0

    async def refresh_room_ttl(self, room_id: str) -> None:
        """Refresh TTL on room and related keys."""
        pipe = self.redis.pipeline()
        pipe.expire(f"room:{room_id}", ROOM_TTL)
        pipe.expire(f"room:{room_id}:players", ROOM_TTL)
        await pipe.execute()

    # ──────────────────────────────────────────────
    # Player operations
    # ──────────────────────────────────────────────

    async def add_player(
        self,
        room_id: str,
        user_id: int,
        chat_id: int,
        username: str,
    ) -> None:
        """Add a player to a room."""
        player_key = f"room:{room_id}:player:{user_id}"
        player_data = {
            "user_id": str(user_id),
            "chat_id": str(chat_id),
            "username": username,
            "role": "",
            "slot": "0",
            "is_alive": "true",
            "shout_penalties": "0",
            "joined_at": str(int(time.time())),
        }
        pipe = self.redis.pipeline()
        pipe.hset(player_key, mapping=player_data)
        pipe.sadd(f"room:{room_id}:players", str(user_id))
        pipe.set(f"user:{user_id}:room", room_id)
        pipe.expire(player_key, ROOM_TTL)
        await pipe.execute()
        await self.refresh_room_ttl(room_id)

    async def get_player(self, room_id: str, user_id: int) -> Optional[dict]:
        """Get player data."""
        data = await self.redis.hgetall(f"room:{room_id}:player:{user_id}")
        return data if data else None

    async def update_player(self, room_id: str, user_id: int, **fields: Any) -> None:
        """Update player fields."""
        if fields:
            str_fields = {k: str(v) for k, v in fields.items()}
            await self.redis.hset(
                f"room:{room_id}:player:{user_id}", mapping=str_fields
            )

    async def remove_player(self, room_id: str, user_id: int) -> None:
        """Remove a player from a room."""
        pipe = self.redis.pipeline()
        pipe.delete(f"room:{room_id}:player:{user_id}")
        pipe.srem(f"room:{room_id}:players", str(user_id))
        pipe.delete(f"user:{user_id}:room")
        await pipe.execute()

    async def get_room_player_ids(self, room_id: str) -> list[str]:
        """Get all player user_ids in a room."""
        members = await self.redis.smembers(f"room:{room_id}:players")
        return list(members)

    async def get_room_player_count(self, room_id: str) -> int:
        """Get the number of players in a room."""
        return await self.redis.scard(f"room:{room_id}:players")

    async def get_all_players_data(self, room_id: str) -> list[dict]:
        """Get data for all players in a room."""
        player_ids = await self.get_room_player_ids(room_id)
        players = []
        for uid in player_ids:
            data = await self.redis.hgetall(f"room:{room_id}:player:{uid}")
            if data:
                players.append(data)
        return players

    async def get_alive_players(self, room_id: str) -> list[dict]:
        """Get all alive players in a room."""
        all_players = await self.get_all_players_data(room_id)
        return [p for p in all_players if p.get("is_alive") == "true"]

    async def get_dead_players(self, room_id: str) -> list[dict]:
        """Get all dead players in a room."""
        all_players = await self.get_all_players_data(room_id)
        return [p for p in all_players if p.get("is_alive") == "false"]

    async def get_players_by_role(self, room_id: str, role: str) -> list[dict]:
        """Get all players with a specific role."""
        all_players = await self.get_all_players_data(room_id)
        return [p for p in all_players if p.get("role") == role]

    async def get_alive_players_by_role(self, room_id: str, role: str) -> list[dict]:
        """Get alive players with a specific role."""
        all_players = await self.get_all_players_data(room_id)
        return [
            p
            for p in all_players
            if p.get("role") == role and p.get("is_alive") == "true"
        ]

    # ──────────────────────────────────────────────
    # User-room mapping
    # ──────────────────────────────────────────────

    async def get_user_room(self, user_id: int) -> Optional[str]:
        """Get the room a user is currently in."""
        return await self.redis.get(f"user:{user_id}:room")

    async def set_user_room(self, user_id: int, room_id: str) -> None:
        """Set the room a user is in."""
        await self.redis.set(f"user:{user_id}:room", room_id)

    async def clear_user_room(self, user_id: int) -> None:
        """Clear the user's room mapping."""
        await self.redis.delete(f"user:{user_id}:room")

    # ──────────────────────────────────────────────
    # Game state
    # ──────────────────────────────────────────────

    async def set_game_state(self, room_id: str, **fields: Any) -> None:
        """Set game state fields."""
        str_fields = {k: str(v) for k, v in fields.items()}
        await self.redis.hset(f"room:{room_id}:game_state", mapping=str_fields)

    async def get_game_state(self, room_id: str) -> Optional[dict]:
        """Get game state."""
        data = await self.redis.hgetall(f"room:{room_id}:game_state")
        return data if data else None

    async def update_game_state(self, room_id: str, **fields: Any) -> None:
        """Update game state fields."""
        await self.set_game_state(room_id, **fields)

    # ──────────────────────────────────────────────
    # Night actions
    # ──────────────────────────────────────────────

    async def set_mafia_choice(
        self, room_id: str, night_num: int, mafia_id: int, target_id: int
    ) -> None:
        """Record a mafia member's choice."""
        key = f"room:{room_id}:night:{night_num}:mafia_choices"
        await self.redis.hset(key, str(mafia_id), str(target_id))

    async def get_mafia_choices(self, room_id: str, night_num: int) -> dict:
        """Get all mafia choices for a night."""
        key = f"room:{room_id}:night:{night_num}:mafia_choices"
        return await self.redis.hgetall(key)

    async def set_sheriff_check(
        self, room_id: str, night_num: int, target_id: int
    ) -> None:
        """Record the sheriff's check."""
        key = f"room:{room_id}:night:{night_num}:sheriff_check"
        await self.redis.set(key, str(target_id))

    async def get_sheriff_check(
        self, room_id: str, night_num: int
    ) -> Optional[str]:
        """Get the sheriff's check target."""
        key = f"room:{room_id}:night:{night_num}:sheriff_check"
        return await self.redis.get(key)

    async def set_doctor_save(
        self, room_id: str, night_num: int, target_id: int
    ) -> None:
        """Record the doctor's save."""
        key = f"room:{room_id}:night:{night_num}:doctor_save"
        await self.redis.set(key, str(target_id))

    async def get_doctor_save(
        self, room_id: str, night_num: int
    ) -> Optional[str]:
        """Get the doctor's save target."""
        key = f"room:{room_id}:night:{night_num}:doctor_save"
        return await self.redis.get(key)

    # ──────────────────────────────────────────────
    # Day actions
    # ──────────────────────────────────────────────

    async def add_nominated(
        self, room_id: str, day_num: int, player_id: int
    ) -> None:
        """Add a player to the nominated list (ordered by nomination time)."""
        key = f"room:{room_id}:day:{day_num}:nominated"
        await self.redis.rpush(key, str(player_id))

    async def get_nominated(self, room_id: str, day_num: int) -> list[str]:
        """Get the ordered list of nominated players."""
        key = f"room:{room_id}:day:{day_num}:nominated"
        return await self.redis.lrange(key, 0, -1)

    async def set_nominated_by(
        self,
        room_id: str,
        day_num: int,
        nominated_id: int,
        nominator_id: int,
    ) -> None:
        """Record who nominated whom."""
        key = f"room:{room_id}:day:{day_num}:nominated_by"
        await self.redis.hset(key, str(nominated_id), str(nominator_id))

    async def get_nominated_by(self, room_id: str, day_num: int) -> dict:
        """Get the nomination mapping (nominated_id -> nominator_id)."""
        key = f"room:{room_id}:day:{day_num}:nominated_by"
        return await self.redis.hgetall(key)

    async def set_vote(
        self, room_id: str, day_num: int, voter_id: int, voted_for_id: int
    ) -> None:
        """Record a vote."""
        key = f"room:{room_id}:day:{day_num}:votes"
        await self.redis.hset(key, str(voter_id), str(voted_for_id))

    async def get_votes(self, room_id: str, day_num: int) -> dict:
        """Get all votes for a day."""
        key = f"room:{room_id}:day:{day_num}:votes"
        return await self.redis.hgetall(key)

    async def set_auto_vote(
        self, room_id: str, day_num: int, voter_id: int, voted_for_id: int
    ) -> None:
        """Record an automatic vote."""
        key = f"room:{room_id}:day:{day_num}:auto_votes"
        await self.redis.hset(key, str(voter_id), str(voted_for_id))

    async def get_auto_votes(self, room_id: str, day_num: int) -> dict:
        """Get all automatic votes."""
        key = f"room:{room_id}:day:{day_num}:auto_votes"
        return await self.redis.hgetall(key)

    # ──────────────────────────────────────────────
    # Don strategy
    # ──────────────────────────────────────────────

    async def set_don_strategy(
        self,
        room_id: str,
        file_id: str,
        media_type: str,
        duration: int,
    ) -> None:
        """Save the Don's strategy audio info."""
        pipe = self.redis.pipeline()
        pipe.set(f"room:{room_id}:don_strategy:file_id", file_id)
        pipe.set(f"room:{room_id}:don_strategy:type", media_type)
        pipe.set(f"room:{room_id}:don_strategy:duration", str(duration))
        pipe.set(
            f"room:{room_id}:don_strategy:timestamp", str(int(time.time()))
        )
        await pipe.execute()

    async def get_don_strategy(self, room_id: str) -> Optional[dict]:
        """Get the Don's strategy audio info."""
        pipe = self.redis.pipeline()
        pipe.get(f"room:{room_id}:don_strategy:file_id")
        pipe.get(f"room:{room_id}:don_strategy:type")
        pipe.get(f"room:{room_id}:don_strategy:duration")
        pipe.get(f"room:{room_id}:don_strategy:timestamp")
        results = await pipe.execute()
        if results[0] is None:
            return None
        return {
            "file_id": results[0],
            "type": results[1],
            "duration": results[2],
            "timestamp": results[3],
        }

    # ──────────────────────────────────────────────
    # Timer
    # ──────────────────────────────────────────────

    async def set_timer_message(
        self, room_id: str, user_id: int, message_id: int
    ) -> None:
        """Store a timer message_id for a specific user."""
        await self.redis.set(
            f"room:{room_id}:timer:{user_id}:message_id", str(message_id)
        )

    async def get_timer_message(
        self, room_id: str, user_id: int
    ) -> Optional[str]:
        """Get the timer message_id for a user."""
        return await self.redis.get(
            f"room:{room_id}:timer:{user_id}:message_id"
        )

    async def delete_timer_message(self, room_id: str, user_id: int) -> None:
        """Delete the timer message_id for a user."""
        await self.redis.delete(f"room:{room_id}:timer:{user_id}:message_id")

    # ──────────────────────────────────────────────
    # Cleanup helpers
    # ──────────────────────────────────────────────

    async def cleanup_night_keys(self, room_id: str, night_num: int) -> None:
        """Remove night action keys for a specific night."""
        pipe = self.redis.pipeline()
        pipe.delete(f"room:{room_id}:night:{night_num}:mafia_choices")
        pipe.delete(f"room:{room_id}:night:{night_num}:sheriff_check")
        pipe.delete(f"room:{room_id}:night:{night_num}:doctor_save")
        await pipe.execute()

    async def cleanup_day_keys(self, room_id: str, day_num: int) -> None:
        """Remove day action keys for a specific day."""
        pipe = self.redis.pipeline()
        pipe.delete(f"room:{room_id}:day:{day_num}:nominated")
        pipe.delete(f"room:{room_id}:day:{day_num}:nominated_by")
        pipe.delete(f"room:{room_id}:day:{day_num}:votes")
        pipe.delete(f"room:{room_id}:day:{day_num}:auto_votes")
        await pipe.execute()
