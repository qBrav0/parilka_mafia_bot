"""Statistics service for recording game history and player stats in PostgreSQL.

All methods are fire-and-forget: errors are logged but never propagate
to the game flow. If DATABASE_URL is not set, the service is not created.
"""

import logging
import time
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)

# Role name -> player_stats column mapping
_ROLE_COL = {
    "мирний_житель": "civilian",
    "мафія": "mafia",
    "дон_мафії": "don",
    "комісар": "sheriff",
    "доктор": "doctor",
}

MAFIA_ROLES = {"мафія", "дон_мафії"}

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS games (
    id               SERIAL PRIMARY KEY,
    room_id          VARCHAR(6) NOT NULL,
    started_at       TIMESTAMP NOT NULL DEFAULT NOW(),
    finished_at      TIMESTAMP DEFAULT NOW(),
    duration_seconds INTEGER,
    player_count     INTEGER NOT NULL,
    mafia_count      INTEGER NOT NULL,
    winner           VARCHAR(20) NOT NULL,
    day_count        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS game_players (
    id              SERIAL PRIMARY KEY,
    game_id         INTEGER REFERENCES games(id) ON DELETE CASCADE,
    user_id         BIGINT NOT NULL,
    username        VARCHAR(100),
    role            VARCHAR(20) NOT NULL,
    is_alive        BOOLEAN NOT NULL,
    death_cause     VARCHAR(20),
    death_day       INTEGER,
    shout_penalties INTEGER NOT NULL DEFAULT 0,
    UNIQUE(game_id, user_id)
);

CREATE TABLE IF NOT EXISTS player_stats (
    user_id              BIGINT PRIMARY KEY,
    username             VARCHAR(100),
    games_played         INTEGER NOT NULL DEFAULT 0,
    games_won            INTEGER NOT NULL DEFAULT 0,
    games_as_civilian    INTEGER NOT NULL DEFAULT 0,
    games_as_mafia       INTEGER NOT NULL DEFAULT 0,
    games_as_don         INTEGER NOT NULL DEFAULT 0,
    games_as_sheriff     INTEGER NOT NULL DEFAULT 0,
    games_as_doctor      INTEGER NOT NULL DEFAULT 0,
    wins_as_civilian     INTEGER NOT NULL DEFAULT 0,
    wins_as_mafia        INTEGER NOT NULL DEFAULT 0,
    wins_as_don          INTEGER NOT NULL DEFAULT 0,
    wins_as_sheriff      INTEGER NOT NULL DEFAULT 0,
    wins_as_doctor       INTEGER NOT NULL DEFAULT 0,
    times_survived       INTEGER NOT NULL DEFAULT 0,
    times_killed_night   INTEGER NOT NULL DEFAULT 0,
    times_voted_out      INTEGER NOT NULL DEFAULT 0,
    times_shout_killed   INTEGER NOT NULL DEFAULT 0,
    total_shout_penalties INTEGER NOT NULL DEFAULT 0,
    sheriff_checks       INTEGER NOT NULL DEFAULT 0,
    sheriff_correct      INTEGER NOT NULL DEFAULT 0,
    doctor_saves         INTEGER NOT NULL DEFAULT 0,
    doctor_successful    INTEGER NOT NULL DEFAULT 0,
    current_streak       INTEGER NOT NULL DEFAULT 0,
    max_streak           INTEGER NOT NULL DEFAULT 0,
    last_played_at       TIMESTAMP
);
"""


class StatsService:
    """Async PostgreSQL client for game statistics."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        """Create connection pool and ensure tables exist."""
        try:
            self._pool = await asyncpg.create_pool(self._database_url, min_size=1, max_size=5)
            async with self._pool.acquire() as conn:
                await conn.execute(_CREATE_TABLES)
            logger.info("StatsService connected to PostgreSQL")
        except Exception as e:
            logger.error("StatsService failed to connect: %s", e)
            self._pool = None

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            logger.info("StatsService connection closed")

    @property
    def available(self) -> bool:
        return self._pool is not None

    # ──────────────────────────────────────────────
    # Record a completed game
    # ──────────────────────────────────────────────

    async def record_game(
        self,
        room_id: str,
        winner: str,
        players: list[dict],
        day_count: int,
        started_at: Optional[float] = None,
    ) -> None:
        """Record a finished game and update every player's aggregate stats.

        Args:
            room_id: 6-char room code
            winner: 'mafia' or 'civilians'
            players: list of player dicts from Redis
                     (must have user_id, username, role, is_alive,
                      shout_penalties, death_cause, death_day)
            day_count: number of days the game lasted
            started_at: Unix timestamp of game start (for duration calc)
        """
        if not self.available:
            return
        try:
            duration = int(time.time() - started_at) if started_at else None
            player_count = len(players)
            mafia_count = sum(1 for p in players if p.get("role") in MAFIA_ROLES)

            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    # Insert game row
                    game_id = await conn.fetchval(
                        """
                        INSERT INTO games
                            (room_id, duration_seconds, player_count,
                             mafia_count, winner, day_count)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        RETURNING id
                        """,
                        room_id,
                        duration,
                        player_count,
                        mafia_count,
                        winner,
                        day_count,
                    )

                    # Insert game_players rows
                    for p in players:
                        uid = int(p["user_id"])
                        alive = p.get("is_alive") == "true"
                        death_cause = p.get("death_cause") or None
                        death_day_str = p.get("death_day")
                        death_day = int(death_day_str) if death_day_str else None
                        penalties = int(p.get("shout_penalties", "0"))

                        await conn.execute(
                            """
                            INSERT INTO game_players
                                (game_id, user_id, username, role,
                                 is_alive, death_cause, death_day,
                                 shout_penalties)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                            """,
                            game_id,
                            uid,
                            p.get("username", ""),
                            p.get("role", ""),
                            alive,
                            death_cause,
                            death_day,
                            penalties,
                        )

                    # Update aggregate player_stats for each player
                    for p in players:
                        await self._update_player_stats(
                            conn, p, winner
                        )

            logger.info(
                "Stats recorded for game %s in room %s (winner=%s)",
                game_id,
                room_id,
                winner,
            )
        except Exception as e:
            logger.error("Failed to record game stats: %s", e)

    async def _update_player_stats(
        self,
        conn: asyncpg.Connection,
        player: dict,
        winner: str,
    ) -> None:
        """Upsert a single player's aggregate stats row."""
        uid = int(player["user_id"])
        username = player.get("username", "")
        role = player.get("role", "")
        alive = player.get("is_alive") == "true"
        death_cause = player.get("death_cause") or ""
        penalties = int(player.get("shout_penalties", "0"))

        role_key = _ROLE_COL.get(role, "civilian")

        # Determine if this player won
        player_is_mafia = role in MAFIA_ROLES
        won = (winner == "mafia" and player_is_mafia) or (
            winner == "civilians" and not player_is_mafia
        )

        await conn.execute(
            """
            INSERT INTO player_stats (user_id, username, games_played, games_won,
                games_as_{role_col}, wins_as_{role_col},
                times_survived, times_killed_night, times_voted_out,
                times_shout_killed, total_shout_penalties,
                current_streak, max_streak, last_played_at)
            VALUES ($1, $2, 1, $3,
                1, $3,
                $4, $5, $6,
                $7, $8,
                $9, $9, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                username = $2,
                games_played = player_stats.games_played + 1,
                games_won = player_stats.games_won + $3,
                games_as_{role_col} = player_stats.games_as_{role_col} + 1,
                wins_as_{role_col} = player_stats.wins_as_{role_col} + $3,
                times_survived = player_stats.times_survived + $4,
                times_killed_night = player_stats.times_killed_night + $5,
                times_voted_out = player_stats.times_voted_out + $6,
                times_shout_killed = player_stats.times_shout_killed + $7,
                total_shout_penalties = player_stats.total_shout_penalties + $8,
                current_streak = CASE WHEN $10 THEN player_stats.current_streak + 1 ELSE 0 END,
                max_streak = GREATEST(player_stats.max_streak,
                    CASE WHEN $10 THEN player_stats.current_streak + 1 ELSE 0 END),
                last_played_at = NOW()
            """.format(role_col=role_key),
            uid,                                         # $1
            username,                                    # $2
            1 if won else 0,                             # $3  games_won / wins_as_X
            1 if alive else 0,                           # $4  times_survived
            1 if death_cause == "night_kill" else 0,     # $5  times_killed_night
            1 if death_cause == "voted_out" else 0,      # $6  times_voted_out
            1 if death_cause == "shout_penalty" else 0,  # $7  times_shout_killed
            penalties,                                   # $8  total_shout_penalties
            1 if won else 0,                             # $9  streak init value
            won,                                         # $10 bool for streak calc
        )

    # ──────────────────────────────────────────────
    # Incremental night-role counters
    # ──────────────────────────────────────────────

    async def record_sheriff_check(
        self, user_id: int, was_correct: bool
    ) -> None:
        """Increment sheriff check counters."""
        if not self.available:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO player_stats (user_id, sheriff_checks, sheriff_correct)
                    VALUES ($1, 1, $2)
                    ON CONFLICT (user_id) DO UPDATE SET
                        sheriff_checks = player_stats.sheriff_checks + 1,
                        sheriff_correct = player_stats.sheriff_correct + $2
                    """,
                    user_id,
                    1 if was_correct else 0,
                )
        except Exception as e:
            logger.error("Failed to record sheriff check: %s", e)

    async def record_doctor_save(
        self, user_id: int, was_successful: bool
    ) -> None:
        """Increment doctor save counters."""
        if not self.available:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO player_stats (user_id, doctor_saves, doctor_successful)
                    VALUES ($1, 1, $2)
                    ON CONFLICT (user_id) DO UPDATE SET
                        doctor_saves = player_stats.doctor_saves + 1,
                        doctor_successful = player_stats.doctor_successful + $2
                    """,
                    user_id,
                    1 if was_successful else 0,
                )
        except Exception as e:
            logger.error("Failed to record doctor save: %s", e)
