"""Game management service.

Handles role distribution, game phases, night resolution, voting,
and win condition checks.
"""

import logging
import random
from collections import Counter
from typing import Optional

from storage.redis_storage import RedisStorage
from utils import get_display_name

logger = logging.getLogger(__name__)

# Role constants
ROLE_CIVILIAN = "мирний_житель"
ROLE_MAFIA = "мафія"
ROLE_DON = "дон_мафії"
ROLE_SHERIFF = "комісар"
ROLE_DOCTOR = "доктор"

MAFIA_ROLES = {ROLE_MAFIA, ROLE_DON}


class GameManager:
    """Manages game logic, role distribution, and phase transitions."""

    def __init__(self, storage: RedisStorage) -> None:
        self.storage = storage

    # ──────────────────────────────────────────────
    # Role distribution
    # ──────────────────────────────────────────────

    def calculate_roles(
        self,
        player_count: int,
        custom_mafia_count: int | None = None,
        custom_has_doctor: bool | None = None,
    ) -> dict[str, int]:
        """Calculate role distribution based on player count.

        Returns dict: role -> count.
        Distribution rules (from section 3.2.2):
        - 6-7: 2 mafia (1 Don + 1), doctor, rest civilians
        - 8: 2 mafia (1 Don + 1), sheriff, rest civilians
        - 9: 3 mafia (1 Don + 2), sheriff, doctor, rest civilians
        - 10: 3 mafia (1 Don + 2), sheriff, rest civilians
        - 10+: custom or 3 mafia, sheriff (+ optional doctor)
        """
        if player_count <= 7:
            return {
                ROLE_DON: 1,
                ROLE_MAFIA: 1,
                ROLE_DOCTOR: 1,
                ROLE_CIVILIAN: player_count - 3,
            }
        elif player_count == 8:
            return {
                ROLE_DON: 1,
                ROLE_MAFIA: 1,
                ROLE_SHERIFF: 1,
                ROLE_CIVILIAN: player_count - 3,
            }
        elif player_count == 9:
            return {
                ROLE_DON: 1,
                ROLE_MAFIA: 2,
                ROLE_SHERIFF: 1,
                ROLE_DOCTOR: 1,
                ROLE_CIVILIAN: player_count - 5,
            }
        elif player_count == 10:
            return {
                ROLE_DON: 1,
                ROLE_MAFIA: 2,
                ROLE_SHERIFF: 1,
                ROLE_CIVILIAN: player_count - 4,
            }
        else:
            # >10 players: custom settings
            mafia_count = custom_mafia_count or 3
            has_doctor = custom_has_doctor if custom_has_doctor is not None else False

            # Validate: mafia_count < players / 2
            if mafia_count >= player_count // 2:
                mafia_count = player_count // 2 - 1

            roles = {
                ROLE_DON: 1,
                ROLE_MAFIA: mafia_count - 1,
                ROLE_SHERIFF: 1,
            }
            assigned = mafia_count + 1  # Don + mafia + sheriff
            if has_doctor:
                roles[ROLE_DOCTOR] = 1
                assigned += 1
            roles[ROLE_CIVILIAN] = player_count - assigned
            return roles

    async def distribute_roles(
        self,
        room_id: str,
        custom_mafia_count: int | None = None,
        custom_has_doctor: bool | None = None,
    ) -> dict[int, str]:
        """Distribute roles to all players in a room.

        Returns mapping of user_id -> role.
        Also assigns random slots (1-N) for speaking order.
        """
        players = await self.storage.get_all_players_data(room_id)
        player_count = len(players)

        role_counts = self.calculate_roles(
            player_count, custom_mafia_count, custom_has_doctor
        )

        # Build role list
        role_list = []
        for role, count in role_counts.items():
            role_list.extend([role] * count)

        # Shuffle and assign
        random.shuffle(role_list)

        # Shuffle players for slot assignment
        player_ids = [int(p["user_id"]) for p in players]
        random.shuffle(player_ids)

        assignments = {}
        for i, uid in enumerate(player_ids):
            role = role_list[i]
            slot = i + 1
            await self.storage.update_player(
                room_id, uid, role=role, slot=str(slot)
            )
            assignments[uid] = role

        logger.info(
            "Roles distributed in room %s: %s",
            room_id,
            {uid: role for uid, role in assignments.items()},
        )
        return assignments

    async def get_mafia_allies_text(
        self, room_id: str, user_id: int
    ) -> str:
        """Get formatted text of mafia allies for a specific player."""
        mafia_players = []
        don_players = await self.storage.get_players_by_role(room_id, ROLE_DON)
        regular_mafia = await self.storage.get_players_by_role(room_id, ROLE_MAFIA)
        mafia_players = don_players + regular_mafia

        allies = []
        for p in mafia_players:
            uid = int(p["user_id"])
            if uid != user_id:
                name = get_display_name(p.get("username"), uid)
                allies.append(name)

        return ", ".join(allies) if allies else "немає"

    # ──────────────────────────────────────────────
    # Phase transitions
    # ──────────────────────────────────────────────

    async def start_game(
        self,
        room_id: str,
        custom_mafia_count: int | None = None,
        custom_has_doctor: bool | None = None,
    ) -> dict[int, str]:
        """Start the game: distribute roles, set initial state.

        Returns role assignments.
        """
        assignments = await self.distribute_roles(
            room_id, custom_mafia_count, custom_has_doctor
        )

        # Check if Don exists to decide initial phase
        has_don = ROLE_DON in assignments.values()
        initial_phase = "zero_night" if has_don else "night"

        await self.storage.update_room(
            room_id,
            status=initial_phase,
            day_number="1",
        )

        alive_count = len(assignments)
        await self.storage.set_game_state(
            room_id,
            phase=initial_phase,
            day_number="1",
            alive_count=str(alive_count),
            dead_count="0",
            last_night_result="",
            last_night_target="",
        )

        logger.info("Game started in room %s, phase: %s", room_id, initial_phase)
        return assignments

    async def transition_to_night(self, room_id: str) -> None:
        """Transition to night phase."""
        room = await self.storage.get_room(room_id)
        day_number = int(room.get("day_number", "1"))

        await self.storage.update_room(room_id, status="night")
        await self.storage.update_game_state(room_id, phase="night")
        await self.storage.refresh_room_ttl(room_id)

    async def transition_to_day(self, room_id: str) -> None:
        """Transition to day speaking phase."""
        room = await self.storage.get_room(room_id)
        day_number = int(room.get("day_number", "1"))

        await self.storage.update_room(
            room_id, status="day_speaking", current_speaker_index="0"
        )
        await self.storage.update_game_state(room_id, phase="day_speaking")
        await self.storage.refresh_room_ttl(room_id)

    async def transition_to_defense(self, room_id: str) -> None:
        """Transition to defense phase."""
        await self.storage.update_room(room_id, status="defense_phase")
        await self.storage.update_game_state(room_id, phase="defense_phase")

    async def transition_to_voting(self, room_id: str) -> None:
        """Transition to voting phase."""
        await self.storage.update_room(room_id, status="day_voting")
        await self.storage.update_game_state(room_id, phase="day_voting")

    async def transition_to_last_word(self, room_id: str) -> None:
        """Transition to last word phase."""
        await self.storage.update_room(room_id, status="last_word")
        await self.storage.update_game_state(room_id, phase="last_word")

    async def transition_to_revote(self, room_id: str) -> None:
        """Transition to revote phase."""
        await self.storage.update_room(room_id, status="revote")
        await self.storage.update_game_state(room_id, phase="revote")

    async def transition_to_finished(self, room_id: str) -> None:
        """Mark game as finished."""
        await self.storage.update_room(room_id, status="finished")
        await self.storage.update_game_state(room_id, phase="finished")

    # ──────────────────────────────────────────────
    # Night resolution
    # ──────────────────────────────────────────────

    async def resolve_night(
        self, room_id: str, night_num: int
    ) -> dict:
        """Resolve all night actions and return results.

        Returns:
            {
                "killed": user_id or None,
                "saved": bool,
                "mafia_miss": bool,
                "sheriff_target": user_id or None,
                "sheriff_result": "mafia" | "civilian" | None,
            }
        """
        # Get mafia choices
        mafia_choices = await self.storage.get_mafia_choices(room_id, night_num)
        alive_mafia = await self.storage.get_alive_players_by_role(room_id, ROLE_DON)
        alive_mafia += await self.storage.get_alive_players_by_role(room_id, ROLE_MAFIA)

        # Check if all alive mafia chose the same target
        mafia_target = None
        mafia_miss = True
        if mafia_choices:
            targets = list(mafia_choices.values())
            living_mafia_ids = {str(p["user_id"]) for p in alive_mafia}
            # Only count choices from alive mafia
            relevant_choices = {
                k: v for k, v in mafia_choices.items() if k in living_mafia_ids
            }

            if len(relevant_choices) == len(alive_mafia) and len(alive_mafia) > 0:
                target_values = list(relevant_choices.values())
                if len(set(target_values)) == 1:
                    mafia_target = int(target_values[0])
                    mafia_miss = False
        else:
            # No choices at all - miss
            mafia_miss = True

        # Get doctor save
        doctor_save = await self.storage.get_doctor_save(room_id, night_num)
        doctor_save_id = int(doctor_save) if doctor_save else None

        # Check if target was saved
        saved = False
        killed = None
        if mafia_target and not mafia_miss:
            if doctor_save_id == mafia_target:
                saved = True
            else:
                killed = mafia_target
                # Mark player as dead
                await self.storage.update_player(
                    room_id, killed, is_alive="false"
                )
                # Update game state counts
                game_state = await self.storage.get_game_state(room_id)
                if game_state:
                    alive = int(game_state.get("alive_count", "0")) - 1
                    dead = int(game_state.get("dead_count", "0")) + 1
                    await self.storage.update_game_state(
                        room_id,
                        alive_count=str(alive),
                        dead_count=str(dead),
                        last_night_result="killed",
                        last_night_target=str(killed),
                    )

        if saved or mafia_miss:
            await self.storage.update_game_state(
                room_id,
                last_night_result="saved" if saved else "miss",
                last_night_target=str(mafia_target) if mafia_target else "",
            )

        # Sheriff check result
        sheriff_check = await self.storage.get_sheriff_check(room_id, night_num)
        sheriff_target = int(sheriff_check) if sheriff_check else None
        sheriff_result = None
        if sheriff_target:
            target_player = await self.storage.get_player(room_id, sheriff_target)
            if target_player:
                role = target_player.get("role", "")
                if role in MAFIA_ROLES:
                    sheriff_result = "mafia"
                else:
                    sheriff_result = "civilian"

        await self.storage.refresh_room_ttl(room_id)

        return {
            "killed": killed,
            "saved": saved,
            "mafia_miss": mafia_miss,
            "sheriff_target": sheriff_target,
            "sheriff_result": sheriff_result,
        }

    # ──────────────────────────────────────────────
    # Voting
    # ──────────────────────────────────────────────

    async def resolve_votes(
        self, room_id: str, day_num: int
    ) -> dict:
        """Resolve voting results.

        Returns:
            {
                "vote_counts": {user_id: count},
                "eliminated": user_id or None,
                "tie": bool,
                "tied_players": [user_id, ...],
                "all_votes": {voter_id: voted_for_id},
            }
        """
        votes = await self.storage.get_votes(room_id, day_num)
        auto_votes = await self.storage.get_auto_votes(room_id, day_num)

        # Merge all votes
        all_votes = {**votes, **auto_votes}

        # Count votes
        vote_counts: Counter = Counter()
        for voted_for in all_votes.values():
            vote_counts[voted_for] += 1

        if not vote_counts:
            return {
                "vote_counts": {},
                "eliminated": None,
                "tie": False,
                "tied_players": [],
                "all_votes": all_votes,
            }

        # Find max votes
        max_votes = max(vote_counts.values())
        top_players = [
            uid for uid, count in vote_counts.items() if count == max_votes
        ]

        if len(top_players) == 1:
            eliminated = int(top_players[0])
            await self.storage.update_player(
                room_id, eliminated, is_alive="false"
            )
            # Update counts
            game_state = await self.storage.get_game_state(room_id)
            if game_state:
                alive = int(game_state.get("alive_count", "0")) - 1
                dead = int(game_state.get("dead_count", "0")) + 1
                await self.storage.update_game_state(
                    room_id, alive_count=str(alive), dead_count=str(dead)
                )
            return {
                "vote_counts": dict(vote_counts),
                "eliminated": eliminated,
                "tie": False,
                "tied_players": [],
                "all_votes": all_votes,
            }
        else:
            return {
                "vote_counts": dict(vote_counts),
                "eliminated": None,
                "tie": True,
                "tied_players": [int(uid) for uid in top_players],
                "all_votes": all_votes,
            }

    # ──────────────────────────────────────────────
    # Win conditions
    # ──────────────────────────────────────────────

    async def check_win_condition(
        self, room_id: str
    ) -> Optional[str]:
        """Check if the game is over.

        Returns:
            "mafia" if mafia wins,
            "civilians" if civilians win,
            None if game continues.
        """
        alive_players = await self.storage.get_alive_players(room_id)

        mafia_count = 0
        civilian_count = 0

        for p in alive_players:
            role = p.get("role", "")
            if role in MAFIA_ROLES:
                mafia_count += 1
            else:
                civilian_count += 1

        # Mafia wins when mafia >= civilians
        if mafia_count >= civilian_count and mafia_count > 0:
            return "mafia"

        # Civilians win when all mafia are dead
        if mafia_count == 0:
            return "civilians"

        return None

    async def get_mafia_list_text(self, room_id: str) -> str:
        """Get formatted list of all mafia players (for game over announcement)."""
        don_players = await self.storage.get_players_by_role(room_id, ROLE_DON)
        mafia_players = await self.storage.get_players_by_role(room_id, ROLE_MAFIA)
        all_mafia = don_players + mafia_players

        lines = []
        for p in all_mafia:
            uid = int(p["user_id"])
            name = get_display_name(p.get("username"), uid)
            lines.append(f"• {name}")
        return "\n".join(lines)

    # ──────────────────────────────────────────────
    # Speaking order helpers
    # ──────────────────────────────────────────────

    async def get_speaking_order(self, room_id: str) -> list[dict]:
        """Get alive players sorted by slot for speaking order."""
        alive = await self.storage.get_alive_players(room_id)
        return sorted(alive, key=lambda p: int(p.get("slot", "0")))

    async def get_current_speaker(self, room_id: str) -> Optional[dict]:
        """Get the current speaker based on speaker index."""
        room = await self.storage.get_room(room_id)
        if not room:
            return None

        index = int(room.get("current_speaker_index", "0"))
        order = await self.get_speaking_order(room_id)

        if index < len(order):
            return order[index]
        return None

    async def advance_speaker(self, room_id: str) -> Optional[dict]:
        """Move to the next speaker. Returns the next speaker or None if done."""
        room = await self.storage.get_room(room_id)
        if not room:
            return None

        index = int(room.get("current_speaker_index", "0"))
        order = await self.get_speaking_order(room_id)

        next_index = index + 1
        await self.storage.update_room(
            room_id, current_speaker_index=str(next_index)
        )

        if next_index < len(order):
            return order[next_index]
        return None

    # ──────────────────────────────────────────────
    # Shout (out-of-turn) handling
    # ──────────────────────────────────────────────

    async def process_shout(
        self, room_id: str, user_id: int
    ) -> tuple[int, bool]:
        """Process a shout from a player.

        Returns (new_penalty_count, killed).
        """
        player = await self.storage.get_player(room_id, user_id)
        if not player:
            return 0, False

        penalties = int(player.get("shout_penalties", "0")) + 1
        killed = penalties >= 3

        await self.storage.update_player(
            room_id, user_id, shout_penalties=str(penalties)
        )

        if killed:
            await self.storage.update_player(
                room_id, user_id, is_alive="false"
            )
            game_state = await self.storage.get_game_state(room_id)
            if game_state:
                alive = int(game_state.get("alive_count", "0")) - 1
                dead = int(game_state.get("dead_count", "0")) + 1
                await self.storage.update_game_state(
                    room_id, alive_count=str(alive), dead_count=str(dead)
                )

        return penalties, killed

    # ──────────────────────────────────────────────
    # Night phase helpers
    # ──────────────────────────────────────────────

    async def has_role_in_game(self, room_id: str, role: str) -> bool:
        """Check if a role exists in the game (regardless of alive/dead)."""
        players = await self.storage.get_players_by_role(room_id, role)
        return len(players) > 0

    async def has_alive_role(self, room_id: str, role: str) -> bool:
        """Check if an alive player with the role exists."""
        players = await self.storage.get_alive_players_by_role(room_id, role)
        return len(players) > 0

    async def increment_day(self, room_id: str) -> int:
        """Increment day number and return new value."""
        room = await self.storage.get_room(room_id)
        day = int(room.get("day_number", "1")) + 1
        await self.storage.update_room(room_id, day_number=str(day))
        await self.storage.update_game_state(room_id, day_number=str(day))
        return day
