"""Night phase resolution: mafia consensus, doctor save, sheriff check."""

from services.game_manager import (
    ROLE_CIVILIAN,
    ROLE_DOCTOR,
    ROLE_DON,
    ROLE_MAFIA,
    ROLE_SHERIFF,
)
from tests.conftest import make_room

ROLES = [ROLE_DON, ROLE_MAFIA, ROLE_SHERIFF, ROLE_DOCTOR, ROLE_CIVILIAN, ROLE_CIVILIAN]


async def test_unanimous_mafia_choice_kills_target(storage, game_manager):
    don, mafia, _, _, civ, _ = await make_room(storage, "R1", ROLES)
    await storage.set_mafia_choice("R1", 1, don, civ)
    await storage.set_mafia_choice("R1", 1, mafia, civ)

    result = await game_manager.resolve_night("R1", 1)

    assert result["killed"] == civ
    assert result["mafia_miss"] is False
    player = await storage.get_player("R1", civ)
    assert player["is_alive"] == "false"
    assert player["death_cause"] == "night_kill"
    state = await storage.get_game_state("R1")
    assert (state["alive_count"], state["dead_count"]) == ("5", "1")


async def test_split_mafia_vote_is_a_miss(storage, game_manager):
    don, mafia, _, _, civ1, civ2 = await make_room(storage, "R2", ROLES)
    await storage.set_mafia_choice("R2", 1, don, civ1)
    await storage.set_mafia_choice("R2", 1, mafia, civ2)

    result = await game_manager.resolve_night("R2", 1)

    assert result["killed"] is None
    assert result["mafia_miss"] is True
    assert len(await storage.get_alive_players("R2")) == 6


async def test_missing_mafia_choice_is_a_miss(storage, game_manager):
    don, _, _, _, civ, _ = await make_room(storage, "R3", ROLES)
    await storage.set_mafia_choice("R3", 1, don, civ)  # the second mafioso did not choose

    result = await game_manager.resolve_night("R3", 1)

    assert result["mafia_miss"] is True
    assert result["killed"] is None


async def test_doctor_saves_the_target(storage, game_manager):
    don, mafia, _, _, civ, _ = await make_room(storage, "R4", ROLES)
    await storage.set_mafia_choice("R4", 1, don, civ)
    await storage.set_mafia_choice("R4", 1, mafia, civ)
    await storage.set_doctor_save("R4", 1, civ)

    result = await game_manager.resolve_night("R4", 1)

    assert result["saved"] is True
    assert result["killed"] is None
    assert (await storage.get_player("R4", civ))["is_alive"] == "true"


async def test_sheriff_check_reveals_mafia_and_civilians(storage, game_manager):
    don, _, _, doctor, _, _ = await make_room(storage, "R5", ROLES)

    await storage.set_sheriff_check("R5", 1, don)
    assert (await game_manager.resolve_night("R5", 1))["sheriff_result"] == "mafia"

    await storage.set_sheriff_check("R5", 2, doctor)
    assert (await game_manager.resolve_night("R5", 2))["sheriff_result"] == "civilian"
