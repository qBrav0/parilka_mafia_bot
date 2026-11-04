"""Handler for /start command and main menu buttons.

Handles:
- /start command (with context-aware behaviour)
- Rules and Help buttons
- Room status and game status buttons
"""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import messages as msg
from keyboards import MAIN_MENU_KB

logger = logging.getLogger(__name__)
router = Router(name="start")


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, **kwargs) -> None:
    """Handle /start command.

    - During FSM input (code/number): reset FSM, show main menu
    - During active game: show 'you are in game' message
    - Otherwise: show welcome + main menu
    """
    storage = kwargs.get("game_storage")
    user_id = message.from_user.id

    # Check if user is in a room
    current_room = None
    if storage:
        current_room = await storage.get_user_room(user_id)

    if current_room:
        room = await storage.get_room(current_room)
        if room and room.get("status") != "waiting":
            # In active game
            await message.answer(msg.IN_GAME_START)
            return

    # Reset any FSM state (covers code input, custom value input)
    await state.clear()

    await message.answer(msg.WELCOME, reply_markup=MAIN_MENU_KB)


@router.message(Command("rules"))
@router.message(F.text == "📜 Правила")
async def cmd_rules(message: Message, **kwargs) -> None:
    """Show game rules."""
    await message.answer(msg.RULES)


@router.message(Command("help"))
@router.message(F.text == "❓ Допомога")
async def cmd_help(message: Message, **kwargs) -> None:
    """Show help information."""
    await message.answer(msg.HELP)


@router.message(F.text == "📊 Статус кімнати")
async def cmd_room_status(message: Message, **kwargs) -> None:
    """Show room status (waiting phase)."""
    storage = kwargs.get("game_storage")
    user_id = message.from_user.id

    room_id = await storage.get_user_room(user_id)
    if not room_id:
        await message.answer(msg.NOT_IN_ROOM)
        return

    room = await storage.get_room(room_id)
    if not room:
        await message.answer(msg.NOT_IN_ROOM)
        return

    players = await storage.get_all_players_data(room_id)
    player_count = len(players)
    max_players = room.get("max_players", "10")
    creator_id = room.get("creator_id", "")

    from services.room_manager import RoomManager

    rm = RoomManager(storage)
    player_list = rm.format_player_list(players, creator_id)

    await message.answer(
        msg.ROOM_STATUS.format(
            room_id=room_id,
            player_count=player_count,
            max_players=max_players,
            player_list=player_list,
        )
    )


@router.message(F.text == "📊 Статус гри")
async def cmd_game_status(message: Message, **kwargs) -> None:
    """Show game status (during active game)."""
    storage = kwargs.get("game_storage")
    user_id = message.from_user.id

    room_id = await storage.get_user_room(user_id)
    if not room_id:
        await message.answer(msg.NOT_IN_ROOM)
        return

    game_state = await storage.get_game_state(room_id)
    if not game_state:
        await message.answer(msg.NOT_IN_ROOM)
        return

    phase = game_state.get("phase", "")
    phase_names = {
        "zero_night": "Нульова ніч",
        "night": "Ніч",
        "day_speaking": "День — виступи",
        "defense_phase": "Захист виставлених",
        "day_voting": "Голосування",
        "last_word": "Останнє слово",
        "revote": "Переголосування",
        "finished": "Гру завершено",
    }
    phase_emojis = {
        "zero_night": "🌙",
        "night": "🌙",
        "day_speaking": "☀️",
        "defense_phase": "🛡️",
        "day_voting": "🗳️",
        "last_word": "📢",
        "revote": "🔄",
        "finished": "🏁",
    }
    status_texts = {
        "zero_night": "Очікування дій спеціальних ролей...",
        "night": "Очікування дій спеціальних ролей...",
        "day_speaking": "Тривають виступи гравців",
        "defense_phase": "Виставлені захищаються",
        "day_voting": "Голосування триватиме...",
        "last_word": "Очікування останнього слова",
        "revote": "Переголосування",
        "finished": "Гра завершена",
    }

    await message.answer(
        msg.GAME_STATUS.format(
            phase_emoji=phase_emojis.get(phase, "🎭"),
            phase=phase_names.get(phase, phase),
            alive_count=game_state.get("alive_count", "0"),
            dead_count=game_state.get("dead_count", "0"),
            status_text=status_texts.get(phase, ""),
        )
    )
