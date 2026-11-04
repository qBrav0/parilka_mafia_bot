"""Day voting and win conditions."""

from services.game_manager import ROLE_CIVILIAN, ROLE_DON, ROLE_MAFIA
from tests.conftest import make_room

ROLES = [ROLE_DON, ROLE_MAFIA, ROLE_CIVILIAN, ROLE_CIVILIAN, ROLE_CIVILIAN, ROLE_CIVILIAN]


async def test_majority_vote_eliminates_player(storage, game_manager):
    don, mafia, c1, c2, c3, c4 = await make_room(storage, "V1", ROLES)
    for voter in (c1, c2, c3):
        await storage.set_vote("V1", 1, voter, mafia)
    await storage.set_vote("V1", 1, don, c4)

    result = await game_manager.resolve_votes("V1", 1)

    assert result["eliminated"] == mafia
    assert result["tie"] is False
    assert (await storage.get_player("V1", mafia))["death_cause"] == "voted_out"


async def test_tie_returns_tied_players_without_elimination(storage, game_manager):
    don, mafia, c1, c2, _, _ = await make_room(storage, "V2", ROLES)
    await storage.set_vote("V2", 1, c1, don)
    await storage.set_vote("V2", 1, c2, mafia)

    result = await game_manager.resolve_votes("V2", 1)

    assert result["tie"] is True
    assert result["eliminated"] is None
    assert sorted(result["tied_players"]) == sorted([don, mafia])


async def test_auto_votes_are_counted(storage, game_manager):
    _, mafia, c1, c2, _, _ = await make_room(storage, "V3", ROLES)
    await storage.set_vote("V3", 1, c1, mafia)
    await storage.set_auto_vote("V3", 1, c2, mafia)

    result = await game_manager.resolve_votes("V3", 1)

    assert result["vote_counts"] == {str(mafia): 2}


async def test_no_votes_means_no_elimination(storage, game_manager):
    await make_room(storage, "V4", ROLES)
    result = await game_manager.resolve_votes("V4", 1)
    assert result["eliminated"] is None
    assert result["tie"] is False


async def test_game_continues_while_mafia_is_a_minority(storage, game_manager):
    await make_room(storage, "W1", ROLES)
    assert await game_manager.check_win_condition("W1") is None


async def test_civilians_win_when_all_mafia_are_dead(storage, game_manager):
    don, mafia, *_ = await make_room(storage, "W2", ROLES)
    for uid in (don, mafia):
        await storage.update_player("W2", uid, is_alive="false")
    assert await game_manager.check_win_condition("W2") == "civilians"


async def test_mafia_wins_at_parity(storage, game_manager):
    _, _, c1, c2, _, _ = await make_room(storage, "W3", ROLES)
    for uid in (c1, c2):
        await storage.update_player("W3", uid, is_alive="false")
    assert await game_manager.check_win_condition("W3") == "mafia"


async def test_third_out_of_turn_shout_eliminates_player(storage, game_manager):
    _, _, c1, *_ = await make_room(storage, "S1", ROLES)

    assert await game_manager.process_shout("S1", c1) == (1, False)
    assert await game_manager.process_shout("S1", c1) == (2, False)
    assert await game_manager.process_shout("S1", c1) == (3, True)

    player = await storage.get_player("S1", c1)
    assert player["death_cause"] == "shout_penalty"
