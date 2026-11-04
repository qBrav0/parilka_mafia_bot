"""Handlers for room creation, joining, and leaving.

Handles:
- Create room flow (max player selection, custom value)
- Join room flow (code input, validation)
- Leave room
"""

import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import messages as msg
from keyboards import (
    MAIN_MENU_KB,
    WAITING_ROOM_KB,
    max_players_keyboard,
    start_game_keyboard,
)
from services.room_manager import RoomManager
from states import GameStates, MenuStates
from utils import broadcast_message, get_display_name, normalize_room_code

logger = logging.getLogger(__name__)
router = Router(name="rooms")


@router.message(F.text == "➕ Створити кімнату")
async def create_room_start(message: Message, state: FSMContext, **kwargs) -> None:
    """Start room creation flow: show max players selection."""
    storage = kwargs.get("game_storage")
    user_id = message.from_user.id

    # Check if already in a room
    current_room = await storage.get_user_room(user_id)
    if current_room:
        await message.answer(msg.ALREADY_IN_ROOM)
        return

    await message.answer(
        msg.CREATE_ROOM_CHOOSE_MAX,
        reply_markup=max_players_keyboard(),
    )


@router.message(MenuStates.waiting_custom_max_players)
async def create_room_custom_value(
    message: Message, state: FSMContext, **kwargs
) -> None:
    """Handle custom max players input."""
    storage = kwargs.get("game_storage")
    bot = kwargs.get("bot")

    text = message.text.strip() if message.text else ""

    try:
        value = int(text)
    except (ValueError, TypeError):
        await message.answer(msg.CREATE_ROOM_INVALID_NUMBER)
        return

    if value < 6 or value > 100:
        await message.answer(msg.CREATE_ROOM_INVALID_NUMBER)
        return

    await state.clear()
    await _create_room(message, state, storage, bot, max_players=value)


@router.message(F.text == "🔗 Приєднатися")
async def join_room_start(message: Message, state: FSMContext, **kwargs) -> None:
    """Start join room flow: ask for room code."""
    storage = kwargs.get("game_storage")
    user_id = message.from_user.id

    # Check if already in a room
    current_room = await storage.get_user_room(user_id)
    if current_room:
        await message.answer(msg.ALREADY_IN_ROOM)
        return

    await state.set_state(MenuStates.waiting_room_code)
    await message.answer(msg.JOIN_ROOM_PROMPT)


@router.message(MenuStates.waiting_room_code)
async def join_room_code_input(
    message: Message, state: FSMContext, **kwargs
) -> None:
    """Handle room code input for joining."""
    storage = kwargs.get("game_storage")
    bot = kwargs.get("bot")

    text = message.text.strip() if message.text else ""
    code = normalize_room_code(text)

    # Validate format
    if not re.match(r"^[A-Z0-9]{6}$", code):
        await message.answer(msg.JOIN_ROOM_INVALID_CODE)
        return

    rm = RoomManager(storage)

    # Check if room exists
    room = await storage.get_room(code)
    if not room:
        await message.answer(msg.JOIN_ROOM_NOT_FOUND.format(room_id=code))
        return

    # Try to join
    user_id = message.from_user.id
    chat_id = message.chat.id
    username = message.from_user.username or f"User{str(user_id)[-4:]}"

    success, error = await rm.join_room(code, user_id, chat_id, username)

    if not success:
        if error == "full":
            max_p = room.get("max_players", "10")
            await message.answer(
                msg.JOIN_ROOM_FULL.format(room_id=code, max_players=max_p)
            )
        elif error == "game_started":
            await message.answer(msg.JOIN_ROOM_GAME_STARTED)
        elif error == "already_in_room":
            await message.answer(msg.ALREADY_IN_ROOM)
        else:
            await message.answer(msg.JOIN_ROOM_NOT_FOUND.format(room_id=code))
        return

    await state.clear()
    await state.set_state(GameStates.waiting_game_start)

    # Get updated room info
    players = await storage.get_all_players_data(code)
    player_count = len(players)
    max_players = room.get("max_players", "10")
    creator_id = room.get("creator_id", "")

    player_list = rm.format_player_list(players, creator_id)

    # Send success to the joiner
    await message.answer(
        msg.JOIN_ROOM_SUCCESS.format(
            room_id=code,
            player_count=player_count,
            max_players=max_players,
            player_list=player_list,
        ),
        reply_markup=WAITING_ROOM_KB,
    )

    # Notify all other players
    display_name = get_display_name(message.from_user.username, user_id)
    other_chat_ids = await rm.get_other_player_chat_ids(code, user_id)
    await broadcast_message(
        bot,
        other_chat_ids,
        msg.PLAYER_JOINED_NOTIFICATION.format(
            player_count=player_count,
            max_players=max_players,
            username=display_name,
        ),
    )

    # Check if min players reached — notify host
    if await rm.check_min_players(code):
        creator_id_int = int(creator_id)
        creator_player = await storage.get_player(code, creator_id_int)
        if creator_player:
            creator_chat_id = int(creator_player["chat_id"])
            if player_count <= 10:
                await bot.send_message(
                    chat_id=creator_chat_id,
                    text=msg.ENOUGH_PLAYERS,
                    reply_markup=start_game_keyboard(),
                )
            else:
                from keyboards import role_settings_keyboard

                await bot.send_message(
                    chat_id=creator_chat_id,
                    text=msg.ENOUGH_PLAYERS_ROLE_SETTINGS.format(
                        player_count=player_count
                    ),
                    reply_markup=role_settings_keyboard(player_count),
                )


@router.message(F.text == "🚪 Вийти з кімнати")
async def leave_room(message: Message, state: FSMContext, **kwargs) -> None:
    """Handle leaving a room."""
    storage = kwargs.get("game_storage")
    bot = kwargs.get("bot")
    user_id = message.from_user.id

    room_id = await storage.get_user_room(user_id)
    if not room_id:
        await message.answer(msg.NOT_IN_ROOM)
        return

    room = await storage.get_room(room_id)
    if not room:
        await state.clear()
        await message.answer(msg.NOT_IN_ROOM, reply_markup=MAIN_MENU_KB)
        return

    # Cannot leave during active game
    if room.get("status") != "waiting":
        await message.answer(msg.CANNOT_LEAVE_DURING_GAME)
        return

    rm = RoomManager(storage)
    display_name = get_display_name(message.from_user.username, user_id)

    # Get other players before leaving
    other_chat_ids = await rm.get_other_player_chat_ids(room_id, user_id)

    success, was_host = await rm.leave_room(user_id, room_id)

    if not success:
        await message.answer(msg.CANNOT_LEAVE_DURING_GAME)
        return

    await state.clear()

    if was_host:
        # Host left — room is closed
        await message.answer(
            msg.LEAVE_ROOM_SUCCESS.format(room_id=room_id),
            reply_markup=MAIN_MENU_KB,
        )
        # Notify all other players and return them to main menu
        await broadcast_message(
            bot,
            other_chat_ids,
            msg.HOST_LEFT_ROOM_CLOSED.format(room_id=room_id),
        )
        # Send main menu keyboard to everyone
        for cid in other_chat_ids:
            try:
                await bot.send_message(
                    chat_id=cid,
                    text="Оберіть дію:",
                    reply_markup=MAIN_MENU_KB,
                )
            except Exception:
                pass
    else:
        # Regular player left
        await message.answer(
            msg.LEAVE_ROOM_SUCCESS.format(room_id=room_id),
            reply_markup=MAIN_MENU_KB,
        )
        # Notify remaining players
        remaining_count = await storage.get_room_player_count(room_id)
        max_players = room.get("max_players", "10")
        await broadcast_message(
            bot,
            other_chat_ids,
            msg.PLAYER_LEFT_NOTIFICATION.format(
                player_count=remaining_count,
                max_players=max_players,
                username=display_name,
            ),
        )


async def _create_room(
    message: Message,
    state: FSMContext,
    storage,
    bot,
    max_players: int,
) -> None:
    """Internal: create a room and send confirmation."""
    user_id = message.from_user.id
    chat_id = message.chat.id
    username = message.from_user.username or f"User{str(user_id)[-4:]}"

    rm = RoomManager(storage)
    room_id = await rm.create_room(
        creator_id=user_id,
        chat_id=chat_id,
        username=username,
        max_players=max_players,
    )

    await state.set_state(GameStates.waiting_game_start)

    await message.answer(
        msg.ROOM_CREATED.format(
            room_id=room_id,
            max_players=max_players,
        ),
        reply_markup=WAITING_ROOM_KB,
    )
