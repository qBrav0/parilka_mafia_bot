"""Room lifecycle and room-code helpers."""

from utils import generate_room_code, get_display_name, normalize_room_code


def test_generated_room_codes_are_valid(room_manager):
    for _ in range(50):
        assert room_manager.validate_room_code(generate_room_code())


def test_room_code_normalisation(room_manager):
    assert normalize_room_code("  ab12cd ") == "AB12CD"
    assert not room_manager.validate_room_code("ab12cd")
    assert not room_manager.validate_room_code("ABC12")


def test_display_name_falls_back_to_user_id_suffix():
    assert get_display_name("alice", 1) == "@alice"
    assert get_display_name("@bob", 1) == "@bob"
    assert get_display_name(None, 123456789) == "User6789"


async def test_create_and_join_room(storage, room_manager):
    room_id = await room_manager.create_room(creator_id=1, chat_id=1, username="host")

    ok, err = await room_manager.join_room(room_id, user_id=2, chat_id=2, username="p2")

    assert (ok, err) == (True, "")
    assert await storage.get_room_player_count(room_id) == 2
    assert await storage.get_user_room(2) == room_id


async def test_join_rejects_duplicates_full_rooms_and_unknown_codes(storage, room_manager):
    room_id = await room_manager.create_room(
        creator_id=1, chat_id=1, username="host", max_players=2
    )
    assert await room_manager.join_room(room_id, 1, 1, "host") == (False, "already_in_room")
    assert (await room_manager.join_room(room_id, 2, 2, "p2"))[0] is True
    assert await room_manager.join_room(room_id, 3, 3, "p3") == (False, "full")
    assert await room_manager.join_room("NOPE00", 4, 4, "p4") == (False, "not_found")


async def test_cannot_join_after_game_started(storage, room_manager):
    room_id = await room_manager.create_room(creator_id=1, chat_id=1, username="host")
    await storage.update_room(room_id, status="night")
    assert await room_manager.join_room(room_id, 2, 2, "p2") == (False, "game_started")


async def test_host_leaving_closes_the_room(storage, room_manager):
    room_id = await room_manager.create_room(creator_id=1, chat_id=1, username="host")
    await room_manager.join_room(room_id, 2, 2, "p2")

    ok, was_host = await room_manager.leave_room(user_id=1, room_id=room_id)

    assert (ok, was_host) == (True, True)
    assert not await storage.room_exists(room_id)
    assert await storage.get_user_room(2) is None
