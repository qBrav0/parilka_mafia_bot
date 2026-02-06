"""Keyboards (Reply and Inline) for the Mafia bot.

Uses ReplyKeyboardMarkup for navigation and InlineKeyboardMarkup for actions.
callback_data format: short prefix + user_id (max 64 bytes).
Prefixes: m: (mafia), s: (sheriff), d: (doctor), n: (nominate), v: (vote),
          mp: (max_players), sg: (start game), mc: (mafia count), hd: (has doctor)
"""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

# ──────────────────────────────────────────────
# Reply keyboards (persistent bottom buttons)
# ──────────────────────────────────────────────

MAIN_MENU_KB = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="➕ Створити кімнату"),
            KeyboardButton(text="🔗 Приєднатися"),
        ],
        [
            KeyboardButton(text="📜 Правила"),
            KeyboardButton(text="❓ Допомога"),
        ],
    ],
    resize_keyboard=True,
)

WAITING_ROOM_KB = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="📊 Статус кімнати"),
            KeyboardButton(text="🚪 Вийти з кімнати"),
        ],
    ],
    resize_keyboard=True,
)

IN_GAME_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Статус гри")],
    ],
    resize_keyboard=True,
)


# ──────────────────────────────────────────────
# Inline keyboards (action buttons)
# ──────────────────────────────────────────────

def max_players_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for selecting max players when creating a room."""
    buttons = []
    row = []
    for count in [6, 7, 8, 9, 10, 12, 15, 20, 25, 30]:
        row.append(
            InlineKeyboardButton(
                text=str(count), callback_data=f"mp:{count}"
            )
        )
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append(
        [
            InlineKeyboardButton(
                text="Власне значення...", callback_data="mp:custom"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def start_game_keyboard() -> InlineKeyboardMarkup:
    """Keyboard with 'Start game' button (for host)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▶️ Почати гру", callback_data="sg:start"
                )
            ]
        ]
    )


def role_settings_keyboard(
    player_count: int,
) -> InlineKeyboardMarkup:
    """Keyboard for role settings (when > 10 players).

    Allows selecting mafia count and doctor presence.
    """
    buttons = []

    # Mafia count options (2 to min(5, players//2 - 1))
    max_mafia = min(5, player_count // 2 - 1)
    mafia_row = []
    for count in range(2, max_mafia + 1):
        mafia_row.append(
            InlineKeyboardButton(
                text=f"{count} мафії", callback_data=f"mc:{count}"
            )
        )
    buttons.append(mafia_row)

    # Doctor toggle
    buttons.append(
        [
            InlineKeyboardButton(
                text="Лікар: ✅ Так", callback_data="hd:yes"
            ),
            InlineKeyboardButton(
                text="Лікар: ❌ Ні", callback_data="hd:no"
            ),
        ]
    )

    # Start game button
    buttons.append(
        [
            InlineKeyboardButton(
                text="▶️ Почати гру", callback_data="sg:start"
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def player_select_keyboard(
    players: list[dict],
    prefix: str,
    exclude_ids: list[int] | None = None,
) -> InlineKeyboardMarkup:
    """Build inline keyboard with player buttons.

    Args:
        players: list of player dicts (must have 'user_id' and 'username')
        prefix: callback_data prefix (m:, s:, d:, n:, v:)
        exclude_ids: optional list of user_ids to exclude from the list
    """
    exclude = set(exclude_ids or [])
    buttons = []
    row = []
    for p in players:
        uid = int(p["user_id"])
        if uid in exclude:
            continue
        username = p.get("username", f"User{str(uid)[-4:]}")
        display = username if username.startswith("@") else f"@{username}"
        row.append(
            InlineKeyboardButton(
                text=display, callback_data=f"{prefix}{uid}"
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def nominate_keyboard(
    alive_players: list[dict],
    already_nominated: list[str],
    nominator_id: int,
) -> InlineKeyboardMarkup:
    """Build nomination keyboard (exclude dead and already nominated players)."""
    exclude = [int(nid) for nid in already_nominated]
    return player_select_keyboard(alive_players, "n:", exclude_ids=exclude)


def voting_keyboard(
    nominated_players: list[dict],
) -> InlineKeyboardMarkup:
    """Build voting keyboard (only nominated players)."""
    buttons = []
    row = []
    for p in nominated_players:
        uid = int(p["user_id"])
        username = p.get("username", f"User{str(uid)[-4:]}")
        display = username if username.startswith("@") else f"@{username}"
        row.append(
            InlineKeyboardButton(
                text=display, callback_data=f"v:{uid}"
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def nominate_button_keyboard() -> InlineKeyboardMarkup:
    """Button to open nomination selection."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎯 Виставити гравця на голосування",
                    callback_data="n:open",
                )
            ]
        ]
    )
