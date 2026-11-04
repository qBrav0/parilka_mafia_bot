"""Role distribution rules."""

import pytest

from services.game_manager import (
    MAFIA_ROLES,
    ROLE_CIVILIAN,
    ROLE_DOCTOR,
    ROLE_DON,
    ROLE_SHERIFF,
)
from tests.conftest import make_room


@pytest.mark.parametrize("players", range(6, 16))
def test_role_counts_add_up_to_player_count(game_manager, players):
    roles = game_manager.calculate_roles(players)
    assert sum(roles.values()) == players
    assert roles[ROLE_DON] == 1
    assert roles[ROLE_CIVILIAN] > 0


@pytest.mark.parametrize("players", range(6, 16))
def test_mafia_is_always_a_minority(game_manager, players):
    roles = game_manager.calculate_roles(players)
    mafia = sum(count for role, count in roles.items() if role in MAFIA_ROLES)
    assert mafia < players - mafia


def test_small_game_has_doctor_but_no_sheriff(game_manager):
    roles = game_manager.calculate_roles(6)
    assert roles.get(ROLE_DOCTOR) == 1
    assert ROLE_SHERIFF not in roles


def test_custom_mafia_count_is_capped_for_large_rooms(game_manager):
    roles = game_manager.calculate_roles(12, custom_mafia_count=8)
    mafia = sum(count for role, count in roles.items() if role in MAFIA_ROLES)
    assert mafia == 12 // 2 - 1


def test_custom_doctor_flag_for_large_rooms(game_manager):
    assert ROLE_DOCTOR in game_manager.calculate_roles(12, custom_has_doctor=True)
    assert ROLE_DOCTOR not in game_manager.calculate_roles(12, custom_has_doctor=False)


async def test_start_game_assigns_every_player_a_role_and_unique_slot(storage, game_manager):
    await make_room(storage, "ROOM01", [""] * 8)

    assignments = await game_manager.start_game("ROOM01")

    players = await storage.get_all_players_data("ROOM01")
    assert len(assignments) == 8
    assert all(p["role"] for p in players)
    assert sorted(int(p["slot"]) for p in players) == list(range(1, 9))
    room = await storage.get_room("ROOM01")
    assert room["status"] == "zero_night"  # the Don records a strategy first
