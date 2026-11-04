"""Shared fixtures: an in-memory Redis (fakeredis) wired into RedisStorage."""

import fakeredis
import pytest
import pytest_asyncio

from services.game_manager import GameManager
from services.room_manager import RoomManager
from storage.redis_storage import RedisStorage


@pytest_asyncio.fixture
async def storage():
    store = RedisStorage("redis://unused")
    store._redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield store
    await store._redis.flushall()
    await store._redis.aclose()


@pytest.fixture
def game_manager(storage):
    return GameManager(storage)


@pytest.fixture
def room_manager(storage):
    return RoomManager(storage)


async def make_room(storage, room_id: str, roles: list[str]) -> list[int]:
    """Create a room with one player per role; return the players' user_ids."""
    user_ids = [1000 + i for i in range(len(roles))]
    await storage.create_room(room_id=room_id, creator_id=user_ids[0])
    for uid, role in zip(user_ids, roles):
        await storage.add_player(room_id, uid, chat_id=uid, username=f"p{uid}")
        await storage.update_player(room_id, uid, role=role)
    await storage.set_game_state(room_id, alive_count=str(len(roles)), dead_count="0")
    return user_ids
