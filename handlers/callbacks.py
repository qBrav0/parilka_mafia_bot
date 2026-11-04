"""Callback query handlers for inline buttons.

Handles:
- mp: max_players selection
- sg: start game
- mc: mafia count selection
- hd: has_doctor toggle
- m: mafia night choice
- s: sheriff night check
- d: doctor night save
- n: nomination
- v: vote
"""

import asyncio
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

import messages as msg
from keyboards import (
    IN_GAME_KB,
    MAIN_MENU_KB,
    WAITING_ROOM_KB,
    nominate_keyboard,
    player_select_keyboard,
    voting_keyboard,
)
from services.game_manager import (
    MAFIA_ROLES,
    ROLE_CIVILIAN,
    ROLE_DOCTOR,
    ROLE_DON,
    ROLE_MAFIA,
    ROLE_SHERIFF,
    GameManager,
)
from states import GameStates, MenuStates
from utils import broadcast_message, get_display_name

logger = logging.getLogger(__name__)
router = Router(name="callbacks")


# ──────────────────────────────────────────────
# Max players selection (mp:)
# ──────────────────────────────────────────────


@router.callback_query(F.data.startswith("mp:"))
async def on_max_players(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Handle max players selection for room creation."""
    storage = kwargs.get("game_storage")
    bot = kwargs.get("bot")
    value = callback.data.split(":")[1]

    if value == "custom":
        await state.set_state(MenuStates.waiting_custom_max_players)
        await callback.message.edit_text(msg.CREATE_ROOM_CUSTOM_PROMPT)
        await callback.answer()
        return

    max_players = int(value)
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    username = callback.from_user.username or f"User{str(user_id)[-4:]}"

    from services.room_manager import RoomManager

    rm = RoomManager(storage)
    room_id = await rm.create_room(
        creator_id=user_id,
        chat_id=chat_id,
        username=username,
        max_players=max_players,
    )

    await state.set_state(GameStates.waiting_game_start)

    await callback.message.edit_text(
        msg.ROOM_CREATED.format(room_id=room_id, max_players=max_players)
    )
    # Send waiting room keyboard
    await bot.send_message(
        chat_id=chat_id,
        text="⏳ Очікування гравців...",
        reply_markup=WAITING_ROOM_KB,
    )
    await callback.answer()


# ──────────────────────────────────────────────
# Role settings (mc:, hd:)
# ──────────────────────────────────────────────


@router.callback_query(F.data.startswith("mc:"))
async def on_mafia_count(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Handle mafia count selection (>10 players)."""
    storage = kwargs.get("game_storage")
    count = int(callback.data.split(":")[1])

    user_id = callback.from_user.id
    room_id = await storage.get_user_room(user_id)
    if not room_id:
        await callback.answer("Кімнату не знайдено")
        return

    await storage.update_room(room_id, mafia_count=str(count))
    await callback.answer(f"Обрано {count} мафії")


@router.callback_query(F.data.startswith("hd:"))
async def on_has_doctor(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Handle doctor toggle (>10 players)."""
    storage = kwargs.get("game_storage")
    value = callback.data.split(":")[1]
    has_doctor = "true" if value == "yes" else "false"

    user_id = callback.from_user.id
    room_id = await storage.get_user_room(user_id)
    if not room_id:
        await callback.answer("Кімнату не знайдено")
        return

    await storage.update_room(room_id, has_doctor=has_doctor)
    label = "Так" if value == "yes" else "Ні"
    await callback.answer(f"Лікар: {label}")


# ──────────────────────────────────────────────
# Start game (sg:)
# ──────────────────────────────────────────────


@router.callback_query(F.data == "sg:start")
async def on_start_game(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Handle game start button."""
    storage = kwargs.get("game_storage")
    bot = kwargs.get("bot")
    timer_service = kwargs.get("timer_service")

    user_id = callback.from_user.id
    room_id = await storage.get_user_room(user_id)
    if not room_id:
        await callback.answer("Кімнату не знайдено")
        return

    room = await storage.get_room(room_id)
    if not room:
        await callback.answer("Кімнату не знайдено")
        return

    # Only host can start
    if str(room.get("creator_id")) != str(user_id):
        await callback.answer("Тільки створювач може почати гру!")
        return

    player_count = await storage.get_room_player_count(room_id)
    if player_count < 6:
        await callback.answer("Потрібно мінімум 6 гравців!")
        return

    await callback.answer("Гра починається!")

    # Get custom role settings if >10 players
    custom_mafia = None
    custom_doctor = None
    if player_count > 10:
        mc = room.get("mafia_count", "")
        if mc:
            custom_mafia = int(mc)
        hd = room.get("has_doctor", "")
        if hd:
            custom_doctor = hd == "true"

    gm = GameManager(storage)
    await gm.start_game(room_id, custom_mafia, custom_doctor)

    # Broadcast "game started" to all
    from services.room_manager import RoomManager

    rm = RoomManager(storage)
    all_chat_ids = await rm.get_player_chat_ids(room_id)

    await broadcast_message(bot, all_chat_ids, msg.GAME_STARTED, reply_markup=IN_GAME_KB)

    # Wait 3 seconds then send private role messages
    await asyncio.sleep(3)

    players = await storage.get_all_players_data(room_id)
    for p in players:
        uid = int(p["user_id"])
        chat_id = int(p["chat_id"])
        role = p.get("role", "")

        try:
            if role == ROLE_CIVILIAN:
                await bot.send_message(chat_id=chat_id, text=msg.ROLE_CIVILIAN)
            elif role == ROLE_DON:
                allies = await gm.get_mafia_allies_text(room_id, uid)
                await bot.send_message(
                    chat_id=chat_id,
                    text=msg.ROLE_DON.format(allies=allies),
                )
            elif role == ROLE_MAFIA:
                allies = await gm.get_mafia_allies_text(room_id, uid)
                await bot.send_message(
                    chat_id=chat_id,
                    text=msg.ROLE_MAFIA.format(allies=allies),
                )
            elif role == ROLE_SHERIFF:
                await bot.send_message(chat_id=chat_id, text=msg.ROLE_SHERIFF)
            elif role == ROLE_DOCTOR:
                await bot.send_message(chat_id=chat_id, text=msg.ROLE_DOCTOR)
        except Exception as e:
            logger.warning("Failed to send role to %s: %s", uid, e)
        await asyncio.sleep(0.05)

    # Determine next phase
    room_updated = await storage.get_room(room_id)
    phase = room_updated.get("status", "")

    if phase == "zero_night":
        await _start_zero_night(bot, storage, room_id, gm, timer_service)
    else:
        await _start_night_phase(bot, storage, room_id, gm, timer_service)


# ──────────────────────────────────────────────
# Mafia night choice (m:)
# ──────────────────────────────────────────────


@router.callback_query(F.data.startswith("m:"))
async def on_mafia_choice(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Handle mafia's target selection during night."""
    storage = kwargs.get("game_storage")
    bot = kwargs.get("bot")
    timer_service = kwargs.get("timer_service")

    target_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    room_id = await storage.get_user_room(user_id)
    if not room_id:
        await callback.answer("Помилка")
        return

    room = await storage.get_room(room_id)
    if not room or room.get("status") != "night":
        await callback.answer("Зараз не нічна фаза")
        return

    night_num = int(room.get("day_number", "1"))

    # Record choice
    await storage.set_mafia_choice(room_id, night_num, user_id, target_id)
    await storage.refresh_room_ttl(room_id)

    # Get target display name
    target_player = await storage.get_player(room_id, target_id)
    target_name = get_display_name(
        target_player.get("username") if target_player else None, target_id
    )

    await callback.message.edit_text(
        msg.MAFIA_CHOICE_CONFIRMED.format(target=target_name)
    )
    await callback.answer()

    # Check if all alive mafia have voted
    gm = GameManager(storage)
    alive_mafia = await storage.get_alive_players_by_role(room_id, ROLE_DON)
    alive_mafia += await storage.get_alive_players_by_role(room_id, ROLE_MAFIA)

    choices = await storage.get_mafia_choices(room_id, night_num)
    alive_mafia_ids = {p["user_id"] for p in alive_mafia}

    all_voted = all(uid in choices for uid in alive_mafia_ids)

    if all_voted:
        # Cancel mafia timer
        if timer_service:
            await timer_service.cancel_timer(room_id, "mafia")

        # Check if all chose the same
        target_values = [
            choices[uid] for uid in alive_mafia_ids if uid in choices
        ]
        if len(set(target_values)) == 1:
            t_name = get_display_name(
                target_player.get("username") if target_player else None,
                int(target_values[0]),
            )
            for p in alive_mafia:
                try:
                    await bot.send_message(
                        chat_id=int(p["chat_id"]),
                        text=msg.MAFIA_ALL_CHOSE_SAME.format(target=t_name),
                    )
                except Exception:
                    pass

        # Proceed to next night phase
        await _continue_night_after_mafia(
            bot, storage, room_id, gm, timer_service
        )


# ──────────────────────────────────────────────
# Sheriff check (s:)
# ──────────────────────────────────────────────


@router.callback_query(F.data.startswith("s:"))
async def on_sheriff_check(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Handle sheriff's target selection during night."""
    storage = kwargs.get("game_storage")
    bot = kwargs.get("bot")
    timer_service = kwargs.get("timer_service")

    target_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    room_id = await storage.get_user_room(user_id)
    if not room_id:
        await callback.answer("Помилка")
        return

    room = await storage.get_room(room_id)
    night_num = int(room.get("day_number", "1"))

    await storage.set_sheriff_check(room_id, night_num, target_id)
    await storage.refresh_room_ttl(room_id)

    target_player = await storage.get_player(room_id, target_id)
    target_name = get_display_name(
        target_player.get("username") if target_player else None, target_id
    )

    await callback.message.edit_text(
        msg.SHERIFF_CHECK_CONFIRMED.format(target=target_name)
    )
    await callback.answer()

    # Cancel sheriff timer and proceed
    if timer_service:
        await timer_service.cancel_timer(room_id, "sheriff")

    gm = GameManager(storage)
    await _continue_night_after_sheriff(
        bot, storage, room_id, gm, timer_service
    )


# ──────────────────────────────────────────────
# Doctor save (d:)
# ──────────────────────────────────────────────


@router.callback_query(F.data.startswith("d:"))
async def on_doctor_save(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Handle doctor's target selection during night."""
    storage = kwargs.get("game_storage")
    bot = kwargs.get("bot")
    timer_service = kwargs.get("timer_service")

    target_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    room_id = await storage.get_user_room(user_id)
    if not room_id:
        await callback.answer("Помилка")
        return

    room = await storage.get_room(room_id)
    night_num = int(room.get("day_number", "1"))

    await storage.set_doctor_save(room_id, night_num, target_id)
    await storage.refresh_room_ttl(room_id)

    target_player = await storage.get_player(room_id, target_id)
    target_name = get_display_name(
        target_player.get("username") if target_player else None, target_id
    )

    await callback.message.edit_text(
        msg.DOCTOR_SAVE_CONFIRMED.format(target=target_name)
    )
    await callback.answer()

    # Cancel doctor timer and proceed to resolve night
    if timer_service:
        await timer_service.cancel_timer(room_id, "doctor")

    gm = GameManager(storage)
    await _resolve_and_announce_night(bot, storage, room_id, gm, timer_service)


# ──────────────────────────────────────────────
# Nomination (n:)
# ──────────────────────────────────────────────


@router.callback_query(F.data == "n:open")
async def on_nominate_open(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Open nomination selection."""
    storage = kwargs.get("game_storage")
    user_id = callback.from_user.id

    room_id = await storage.get_user_room(user_id)
    if not room_id:
        await callback.answer("Помилка")
        return

    room = await storage.get_room(room_id)
    if not room or room.get("status") not in ("day_speaking",):
        await callback.answer("Зараз не можна виставляти")
        return

    day_num = int(room.get("day_number", "1"))
    already_nominated = await storage.get_nominated(room_id, day_num)
    alive_players = await storage.get_alive_players(room_id)

    kb = nominate_keyboard(alive_players, already_nominated, user_id)

    await callback.message.edit_text(msg.NOMINATE_PROMPT, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("n:"))
async def on_nominate_player(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Handle nomination of a specific player."""
    if callback.data == "n:open":
        return  # handled above

    storage = kwargs.get("game_storage")
    bot = kwargs.get("bot")

    target_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    room_id = await storage.get_user_room(user_id)
    if not room_id:
        await callback.answer("Помилка")
        return

    room = await storage.get_room(room_id)
    day_num = int(room.get("day_number", "1"))

    # Check if already nominated
    already_nominated = await storage.get_nominated(room_id, day_num)
    if str(target_id) in already_nominated:
        await callback.answer("Цей гравець вже виставлений!")
        return

    # Check target is alive
    target_player = await storage.get_player(room_id, target_id)
    if not target_player or target_player.get("is_alive") != "true":
        await callback.answer("Не можна виставити мертвого гравця!")
        return

    # Record nomination
    await storage.add_nominated(room_id, day_num, target_id)
    await storage.set_nominated_by(room_id, day_num, target_id, user_id)
    await storage.refresh_room_ttl(room_id)

    target_name = get_display_name(target_player.get("username"), target_id)
    nominator_name = get_display_name(callback.from_user.username, user_id)

    await callback.message.edit_text(
        msg.NOMINATION_SUCCESS.format(target=target_name)
    )
    await callback.answer()

    # Build nominated list text
    updated_nominated = await storage.get_nominated(room_id, day_num)
    nominated_by = await storage.get_nominated_by(room_id, day_num)
    nominated_list = await _format_nominated_list(
        storage, room_id, updated_nominated, nominated_by
    )

    # Broadcast to all other players
    from services.room_manager import RoomManager

    rm = RoomManager(storage)
    other_chat_ids = await rm.get_other_player_chat_ids(room_id, user_id)
    await broadcast_message(
        bot,
        other_chat_ids,
        msg.NOMINATION_BROADCAST.format(
            nominator=nominator_name,
            target=target_name,
            nominated_list=nominated_list,
        ),
    )


# ──────────────────────────────────────────────
# Vote (v:)
# ──────────────────────────────────────────────


@router.callback_query(F.data.startswith("v:"))
async def on_vote(
    callback: CallbackQuery, state: FSMContext, **kwargs
) -> None:
    """Handle a vote for a nominated player."""
    storage = kwargs.get("game_storage")
    bot = kwargs.get("bot")
    timer_service = kwargs.get("timer_service")

    target_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    room_id = await storage.get_user_room(user_id)
    if not room_id:
        await callback.answer("Помилка")
        return

    room = await storage.get_room(room_id)
    if not room or room.get("status") not in ("day_voting", "revote"):
        await callback.answer("Зараз не фаза голосування")
        return

    # Check if player is alive
    player = await storage.get_player(room_id, user_id)
    if not player or player.get("is_alive") != "true":
        await callback.answer("Мертві не можуть голосувати")
        return

    day_num = int(room.get("day_number", "1"))

    # Check if already voted
    votes = await storage.get_votes(room_id, day_num)
    if str(user_id) in votes:
        await callback.answer("Ви вже проголосували!")
        return

    await storage.set_vote(room_id, day_num, user_id, target_id)
    await storage.refresh_room_ttl(room_id)

    target_player = await storage.get_player(room_id, target_id)
    target_name = get_display_name(
        target_player.get("username") if target_player else None, target_id
    )

    await callback.message.edit_text(
        msg.VOTE_CONFIRMED.format(target=target_name)
    )
    await callback.answer()

    # Check if all alive players have voted
    alive_players = await storage.get_alive_players(room_id)
    votes_updated = await storage.get_votes(room_id, day_num)
    alive_ids = {p["user_id"] for p in alive_players}
    all_voted = all(uid in votes_updated for uid in alive_ids)

    if all_voted:
        if timer_service:
            await timer_service.cancel_timer(room_id, "voting")
        await _resolve_voting(bot, storage, room_id, day_num, timer_service)


# ──────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────


async def _start_zero_night(bot, storage, room_id, gm, timer_service):
    """Start zero night: Don records strategy."""
    from services.room_manager import RoomManager

    rm = RoomManager(storage)

    # Send waiting message to everyone except Don
    don_players = await storage.get_players_by_role(room_id, ROLE_DON)

    if don_players:
        don = don_players[0]
        don_chat_id = int(don["chat_id"])
        don_user_id = int(don["user_id"])

        # Send prompt to Don
        await bot.send_message(
            chat_id=don_chat_id, text=msg.ZERO_NIGHT_DON_PROMPT
        )

        # Send waiting to everyone else
        other_chat_ids = await rm.get_other_player_chat_ids(room_id, don_user_id)
        await broadcast_message(bot, other_chat_ids, msg.ZERO_NIGHT_WAITING)

        # Start 90-second timer for Don
        if timer_service:

            async def on_don_timeout():
                await _handle_zero_night_timeout(
                    bot, storage, room_id, gm, timer_service
                )

            await timer_service.start_timer(
                room_id,
                don_chat_id,
                90,
                on_timeout=on_don_timeout,
                timer_suffix="zero_night",
            )


async def _handle_zero_night_timeout(bot, storage, room_id, gm, timer_service):
    """Handle Don's zero night timeout — skip and proceed."""
    don_players = await storage.get_players_by_role(room_id, ROLE_DON)
    if don_players:
        don_chat_id = int(don_players[0]["chat_id"])
        try:
            await bot.send_message(
                chat_id=don_chat_id, text=msg.ZERO_NIGHT_TIMEOUT
            )
        except Exception:
            pass

    # Transition to day
    await _transition_to_first_day(bot, storage, room_id, gm, timer_service)


async def _transition_to_first_day(bot, storage, room_id, gm, timer_service):
    """Transition from zero night to first day."""
    await gm.transition_to_day(room_id)

    from services.room_manager import RoomManager

    rm = RoomManager(storage)
    all_chat_ids = await rm.get_player_chat_ids(room_id)
    await broadcast_message(bot, all_chat_ids, msg.DAY_STARTED)

    await _start_day_speaking(bot, storage, room_id, gm, timer_service)


async def _start_night_phase(bot, storage, room_id, gm, timer_service):
    """Start the full night phase sequence: mafia -> sheriff -> doctor."""
    await gm.transition_to_night(room_id)


    # Send night message to civilians
    alive_players = await storage.get_alive_players(room_id)
    civilian_chat_ids = [
        int(p["chat_id"])
        for p in alive_players
        if p.get("role") not in MAFIA_ROLES
        and p.get("role") != ROLE_SHERIFF
        and p.get("role") != ROLE_DOCTOR
    ]
    dead_players = await storage.get_dead_players(room_id)
    dead_chat_ids = [int(p["chat_id"]) for p in dead_players]

    await broadcast_message(
        bot, civilian_chat_ids + dead_chat_ids, msg.NIGHT_WAITING
    )

    # Start mafia phase
    await _start_mafia_phase(bot, storage, room_id, gm, timer_service)


async def _start_mafia_phase(bot, storage, room_id, gm, timer_service):
    """Send mafia choice buttons to alive mafia members."""
    room = await storage.get_room(room_id)
    night_action_time = int(room.get("night_action_time", "15"))

    alive_mafia = await storage.get_alive_players_by_role(room_id, ROLE_DON)
    alive_mafia += await storage.get_alive_players_by_role(room_id, ROLE_MAFIA)

    if not alive_mafia:
        # No alive mafia — skip to sheriff
        await _continue_night_after_mafia(bot, storage, room_id, gm, timer_service)
        return

    # Get all alive players for target selection (mafia can target anyone)
    alive_players = await storage.get_alive_players(room_id)

    for p in alive_mafia:
        uid = int(p["user_id"])
        chat_id = int(p["chat_id"])

        allies = await gm.get_mafia_allies_text(room_id, uid)
        kb = player_select_keyboard(alive_players, "m:")

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=msg.NIGHT_MAFIA_PROMPT.format(allies=allies),
                reply_markup=kb,
            )
        except Exception as e:
            logger.warning("Failed to send mafia prompt to %s: %s", uid, e)

    # Timer for mafia
    if timer_service:

        async def on_mafia_timeout():
            await _continue_night_after_mafia(
                bot, storage, room_id, gm, timer_service
            )

        mafia_chat_id = int(alive_mafia[0]["chat_id"])
        await timer_service.start_timer(
            room_id,
            mafia_chat_id,
            night_action_time,
            on_timeout=on_mafia_timeout,
            timer_suffix="mafia",
        )


async def _continue_night_after_mafia(bot, storage, room_id, gm, timer_service):
    """After mafia phase, proceed to sheriff phase."""
    room = await storage.get_room(room_id)
    night_action_time = int(room.get("night_action_time", "15"))

    # Check if sheriff role exists in game and is alive
    has_sheriff_in_game = await gm.has_role_in_game(room_id, ROLE_SHERIFF)
    has_alive_sheriff = await gm.has_alive_role(room_id, ROLE_SHERIFF)

    if not has_sheriff_in_game:
        # Role doesn't exist in game (6-7 players) — skip entirely
        await _continue_night_after_sheriff(bot, storage, room_id, gm, timer_service)
        return

    if not has_alive_sheriff:
        # Sheriff is dead — still run the phase timer (to not reveal info),
        # but don't send any buttons
        await asyncio.sleep(night_action_time)
        await _continue_night_after_sheriff(bot, storage, room_id, gm, timer_service)
        return

    # Send sheriff prompt
    sheriff_players = await storage.get_alive_players_by_role(room_id, ROLE_SHERIFF)
    alive_players = await storage.get_alive_players(room_id)

    for p in sheriff_players:
        uid = int(p["user_id"])
        chat_id = int(p["chat_id"])
        kb = player_select_keyboard(alive_players, "s:", exclude_ids=[uid])

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=msg.NIGHT_SHERIFF_PROMPT,
                reply_markup=kb,
            )
        except Exception as e:
            logger.warning("Failed to send sheriff prompt to %s: %s", uid, e)

    if timer_service:

        async def on_sheriff_timeout():
            await _continue_night_after_sheriff(
                bot, storage, room_id, gm, timer_service
            )

        sheriff_chat_id = int(sheriff_players[0]["chat_id"])
        await timer_service.start_timer(
            room_id,
            sheriff_chat_id,
            night_action_time,
            on_timeout=on_sheriff_timeout,
            timer_suffix="sheriff",
        )


async def _continue_night_after_sheriff(bot, storage, room_id, gm, timer_service):
    """After sheriff phase, proceed to doctor phase."""
    room = await storage.get_room(room_id)
    night_action_time = int(room.get("night_action_time", "15"))
    night_num = int(room.get("day_number", "1"))

    has_doctor_in_game = await gm.has_role_in_game(room_id, ROLE_DOCTOR)
    has_alive_doctor = await gm.has_alive_role(room_id, ROLE_DOCTOR)

    if not has_doctor_in_game:
        # Doctor doesn't exist in game (e.g. 8 players) — skip
        await _resolve_and_announce_night(bot, storage, room_id, gm, timer_service)
        return

    if not has_alive_doctor:
        # Doctor is dead — wait the time period to not reveal info
        await asyncio.sleep(night_action_time)
        await _resolve_and_announce_night(bot, storage, room_id, gm, timer_service)
        return

    # Get previous night's doctor save (can't heal same target twice in a row)
    exclude_ids = []
    if night_num > 1:
        prev_save = await storage.get_doctor_save(room_id, night_num - 1)
        if prev_save:
            exclude_ids.append(int(prev_save))

    doctor_players = await storage.get_alive_players_by_role(room_id, ROLE_DOCTOR)
    alive_players = await storage.get_alive_players(room_id)

    for p in doctor_players:
        uid = int(p["user_id"])
        chat_id = int(p["chat_id"])
        # Doctor can heal self, but not same as last night
        kb = player_select_keyboard(alive_players, "d:", exclude_ids=exclude_ids)

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=msg.NIGHT_DOCTOR_PROMPT,
                reply_markup=kb,
            )
        except Exception as e:
            logger.warning("Failed to send doctor prompt to %s: %s", uid, e)

    if timer_service:

        async def on_doctor_timeout():
            await _resolve_and_announce_night(
                bot, storage, room_id, gm, timer_service
            )

        doctor_chat_id = int(doctor_players[0]["chat_id"])
        await timer_service.start_timer(
            room_id,
            doctor_chat_id,
            night_action_time,
            on_timeout=on_doctor_timeout,
            timer_suffix="doctor",
        )


async def _resolve_and_announce_night(bot, storage, room_id, gm, timer_service):
    """Resolve night actions and announce results."""
    room = await storage.get_room(room_id)
    night_num = int(room.get("day_number", "1"))

    result = await gm.resolve_night(room_id, night_num)

    from services.room_manager import RoomManager

    rm = RoomManager(storage)
    all_chat_ids = await rm.get_player_chat_ids(room_id)

    # Send sheriff result privately
    if result["sheriff_target"] and result["sheriff_result"]:
        sheriff_players = await storage.get_alive_players_by_role(room_id, ROLE_SHERIFF)
        for sp in sheriff_players:
            target_player = await storage.get_player(
                room_id, result["sheriff_target"]
            )
            target_name = get_display_name(
                target_player.get("username") if target_player else None,
                result["sheriff_target"],
            )
            if result["sheriff_result"] == "mafia":
                text = msg.SHERIFF_RESULT_MAFIA.format(target=target_name)
            else:
                text = msg.SHERIFF_RESULT_CIVILIAN.format(target=target_name)
            try:
                await bot.send_message(
                    chat_id=int(sp["chat_id"]), text=text
                )
            except Exception:
                pass

            # Record sheriff check accuracy for stats
            stats_svc = getattr(bot, "stats_service", None)
            if stats_svc:
                try:
                    was_correct = result["sheriff_result"] == "mafia"
                    await stats_svc.record_sheriff_check(
                        int(sp["user_id"]), was_correct
                    )
                except Exception:
                    pass

    # Record doctor save attempt for stats
    stats_svc = getattr(bot, "stats_service", None)
    if stats_svc:
        doctor_save_raw = await storage.get_doctor_save(room_id, night_num)
        if doctor_save_raw:
            doctor_players = await storage.get_alive_players_by_role(
                room_id, ROLE_DOCTOR
            )
            for dp in doctor_players:
                try:
                    await stats_svc.record_doctor_save(
                        int(dp["user_id"]), result["saved"]
                    )
                except Exception:
                    pass

    # Announce night results to everyone
    if result["killed"]:
        killed_player = await storage.get_player(room_id, result["killed"])
        killed_name = get_display_name(
            killed_player.get("username") if killed_player else None,
            result["killed"],
        )
        await broadcast_message(
            bot,
            all_chat_ids,
            msg.NIGHT_RESULT_KILLED.format(target=killed_name),
        )

        # Send death notification to killed player
        if killed_player:
            try:
                await bot.send_message(
                    chat_id=int(killed_player["chat_id"]),
                    text=msg.PLAYER_DIED_NOTIFICATION,
                )
            except Exception:
                pass
    else:
        await broadcast_message(bot, all_chat_ids, msg.NIGHT_RESULT_NOBODY_DIED)

    # Check win condition
    winner = await gm.check_win_condition(room_id)
    if winner:
        await _announce_game_over(bot, storage, room_id, gm, winner)
        return

    # Transition to day
    await gm.transition_to_day(room_id)
    await _start_day_speaking(bot, storage, room_id, gm, timer_service)


async def _start_day_speaking(bot, storage, room_id, gm, timer_service):
    """Start the day speaking phase."""
    order = await gm.get_speaking_order(room_id)
    if not order:
        return

    from services.room_manager import RoomManager

    rm = RoomManager(storage)
    all_chat_ids = await rm.get_player_chat_ids(room_id)

    # Build speaking order text
    lines = []
    for i, p in enumerate(order):
        uid = int(p["user_id"])
        name = get_display_name(p.get("username"), uid)
        slot = p.get("slot", str(i + 1))
        status = "зараз говорить" if i == 0 else "очікує"
        lines.append(f"{slot}️⃣ Слот {slot}: {name} - {status}")

    speaker_list = "\n".join(lines)
    await broadcast_message(
        bot,
        all_chat_ids,
        msg.DAY_SPEAKING_ORDER.format(speaker_list=speaker_list),
    )

    # Prompt the first speaker
    first_speaker = order[0]
    first_chat_id = int(first_speaker["chat_id"])

    from keyboards import nominate_button_keyboard

    await bot.send_message(
        chat_id=first_chat_id,
        text=msg.YOUR_TURN_TO_SPEAK,
        reply_markup=nominate_button_keyboard(),
    )

    # Start speech timer
    room = await storage.get_room(room_id)
    speech_time = int(room.get("speech_time", "90"))

    if timer_service:

        async def on_speech_timeout():
            await _handle_speech_timeout(
                bot, storage, room_id, gm, timer_service
            )

        await timer_service.start_timer(
            room_id,
            first_chat_id,
            speech_time,
            on_timeout=on_speech_timeout,
            timer_suffix="speech",
        )


async def _handle_speech_timeout(bot, storage, room_id, gm, timer_service):
    """Handle when a speaker's time expires."""
    from services.room_manager import RoomManager

    rm = RoomManager(storage)

    current = await gm.get_current_speaker(room_id)
    if not current:
        return

    speaker_name = get_display_name(
        current.get("username"), int(current["user_id"])
    )

    next_speaker = await gm.advance_speaker(room_id)

    if next_speaker:
        next_name = get_display_name(
            next_speaker.get("username"), int(next_speaker["user_id"])
        )
        all_chat_ids = await rm.get_player_chat_ids(room_id)
        await broadcast_message(
            bot,
            all_chat_ids,
            msg.SPEECH_TIMEOUT.format(speaker=speaker_name, next_speaker=next_name),
        )

        # Prompt next speaker
        from keyboards import nominate_button_keyboard

        next_chat_id = int(next_speaker["chat_id"])
        await bot.send_message(
            chat_id=next_chat_id,
            text=msg.YOUR_TURN_TO_SPEAK,
            reply_markup=nominate_button_keyboard(),
        )

        room = await storage.get_room(room_id)
        speech_time = int(room.get("speech_time", "90"))

        if timer_service:

            async def on_timeout():
                await _handle_speech_timeout(
                    bot, storage, room_id, gm, timer_service
                )

            await timer_service.start_timer(
                room_id,
                next_chat_id,
                speech_time,
                on_timeout=on_timeout,
                timer_suffix="speech",
            )
    else:
        # All speeches done
        all_chat_ids = await rm.get_player_chat_ids(room_id)
        await broadcast_message(
            bot,
            all_chat_ids,
            msg.SPEECH_TIMEOUT_LAST.format(speaker=speaker_name),
        )
        await _after_all_speeches(bot, storage, room_id, gm, timer_service)


async def _after_all_speeches(bot, storage, room_id, gm, timer_service):
    """Handle post-speaking phase: defense or voting."""
    room = await storage.get_room(room_id)
    day_num = int(room.get("day_number", "1"))
    nominated = await storage.get_nominated(room_id, day_num)

    from services.room_manager import RoomManager

    rm = RoomManager(storage)
    all_chat_ids = await rm.get_player_chat_ids(room_id)

    if not nominated:
        # No nominations — skip voting, go to night
        await broadcast_message(bot, all_chat_ids, msg.NO_NOMINATIONS)

        # Check win condition first
        winner = await gm.check_win_condition(room_id)
        if winner:
            await _announce_game_over(bot, storage, room_id, gm, winner)
            return

        await gm.increment_day(room_id)
        await _start_night_phase(bot, storage, room_id, gm, timer_service)
        return

    # Build nominated list
    nominated_by = await storage.get_nominated_by(room_id, day_num)
    nominated_list = await _format_nominated_list(
        storage, room_id, nominated, nominated_by
    )

    await broadcast_message(
        bot,
        all_chat_ids,
        msg.ALL_SPEECHES_DONE.format(nominated_list=nominated_list),
    )

    # Start defense phase
    await gm.transition_to_defense(room_id)
    await _start_defense_phase(
        bot, storage, room_id, gm, timer_service, nominated, 0
    )


async def _start_defense_phase(
    bot, storage, room_id, gm, timer_service, nominated, index
):
    """Start defense for the nominated player at given index."""
    if index >= len(nominated):
        # All defenses done
        await _after_all_defenses(bot, storage, room_id, gm, timer_service)
        return

    target_id = int(nominated[index])
    target_player = await storage.get_player(room_id, target_id)
    if not target_player:
        # Skip if player data not found
        await _start_defense_phase(
            bot, storage, room_id, gm, timer_service, nominated, index + 1
        )
        return

    target_chat_id = int(target_player["chat_id"])

    await bot.send_message(
        chat_id=target_chat_id, text=msg.DEFENSE_YOUR_TURN
    )

    room = await storage.get_room(room_id)
    speech_time = int(room.get("speech_time", "90"))

    if timer_service:

        async def on_defense_timeout():
            # Move to next defender
            await _start_defense_phase(
                bot, storage, room_id, gm, timer_service, nominated, index + 1
            )

        await timer_service.start_timer(
            room_id,
            target_chat_id,
            speech_time,
            on_timeout=on_defense_timeout,
            timer_suffix="defense",
        )

    # Store current defense index for the game handler to know who's defending
    await storage.update_room(room_id, current_speaker_index=str(index))


async def _after_all_defenses(bot, storage, room_id, gm, timer_service):
    """Handle post-defense: prep time then voting."""
    from services.room_manager import RoomManager

    rm = RoomManager(storage)
    all_chat_ids = await rm.get_player_chat_ids(room_id)

    await broadcast_message(bot, all_chat_ids, msg.ALL_DEFENSES_DONE)

    room = await storage.get_room(room_id)
    defense_prep_time = int(room.get("defense_prep_time", "30"))

    # Wait for prep time then start voting
    await asyncio.sleep(defense_prep_time)
    await _start_voting(bot, storage, room_id, gm, timer_service)


async def _start_voting(bot, storage, room_id, gm, timer_service, revote_players=None):
    """Start the voting phase."""
    await gm.transition_to_voting(room_id)

    room = await storage.get_room(room_id)
    day_num = int(room.get("day_number", "1"))
    voting_time = int(room.get("voting_time", "30"))

    # Get nominated players data
    if revote_players:
        nominated_ids = revote_players
    else:
        nominated_ids = await storage.get_nominated(room_id, day_num)

    nominated_data = []
    for nid in nominated_ids:
        p = await storage.get_player(room_id, int(nid))
        if p:
            nominated_data.append(p)

    nominated_list_text = "\n".join(
        f"• {get_display_name(p.get('username'), int(p['user_id']))}"
        for p in nominated_data
    )

    # Send voting buttons to alive players
    alive_players = await storage.get_alive_players(room_id)
    kb = voting_keyboard(nominated_data)

    for p in alive_players:
        chat_id = int(p["chat_id"])
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=msg.VOTING_PROMPT.format(nominated_list=nominated_list_text),
                reply_markup=kb,
            )
        except Exception as e:
            logger.warning("Failed to send voting to %s: %s", p["user_id"], e)

    # Send voting notification to dead players
    dead_players = await storage.get_dead_players(room_id)
    for p in dead_players:
        chat_id = int(p["chat_id"])
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=msg.VOTING_DEAD_PLAYER.format(
                    nominated_list=nominated_list_text
                ),
            )
        except Exception:
            pass

    # Start voting timer
    if timer_service:

        async def on_voting_timeout():
            await _handle_voting_timeout(
                bot, storage, room_id, gm, timer_service, nominated_ids
            )

        # Send timer to first alive player
        if alive_players:
            await timer_service.start_timer(
                room_id,
                int(alive_players[0]["chat_id"]),
                voting_time,
                on_timeout=on_voting_timeout,
                timer_suffix="voting",
            )


async def _handle_voting_timeout(
    bot, storage, room_id, gm, timer_service, nominated_ids
):
    """Auto-vote for players who didn't vote."""
    room = await storage.get_room(room_id)
    day_num = int(room.get("day_number", "1"))

    alive_players = await storage.get_alive_players(room_id)
    votes = await storage.get_votes(room_id, day_num)

    # Last nominated gets auto-votes
    last_nominated = int(nominated_ids[-1]) if nominated_ids else None

    for p in alive_players:
        uid = p["user_id"]
        if uid not in votes and last_nominated:
            await storage.set_auto_vote(room_id, day_num, int(uid), last_nominated)

            # Notify player
            target_player = await storage.get_player(room_id, last_nominated)
            target_name = get_display_name(
                target_player.get("username") if target_player else None,
                last_nominated,
            )
            try:
                await bot.send_message(
                    chat_id=int(p["chat_id"]),
                    text=msg.VOTE_TIMEOUT.format(target=target_name),
                )
            except Exception:
                pass

    await _resolve_voting(bot, storage, room_id, day_num, timer_service)


async def _resolve_voting(bot, storage, room_id, day_num, timer_service):
    """Resolve votes and announce results."""
    gm = GameManager(storage)
    result = await gm.resolve_votes(room_id, day_num)

    from services.room_manager import RoomManager

    rm = RoomManager(storage)
    all_chat_ids = await rm.get_player_chat_ids(room_id)

    # Format results
    results_lines = []
    for uid_str, count in sorted(
        result["vote_counts"].items(), key=lambda x: x[1], reverse=True
    ):
        p = await storage.get_player(room_id, int(uid_str))
        name = get_display_name(p.get("username") if p else None, int(uid_str))
        suffix = ""
        if result["eliminated"] and str(result["eliminated"]) == uid_str:
            suffix = " ⬅️ ВИГНАН"
        results_lines.append(f"• {name}: {count} голосів{suffix}")
    results_text = "\n".join(results_lines)

    # Format vote details
    vote_lines = []
    for voter_str, target_str in result["all_votes"].items():
        voter_p = await storage.get_player(room_id, int(voter_str))
        target_p = await storage.get_player(room_id, int(target_str))
        v_name = get_display_name(
            voter_p.get("username") if voter_p else None, int(voter_str)
        )
        t_name = get_display_name(
            target_p.get("username") if target_p else None, int(target_str)
        )
        auto = await storage.get_auto_votes(room_id, day_num)
        is_auto = voter_str in auto
        auto_label = " (автоматично)" if is_auto else ""
        vote_lines.append(f"• {v_name} → {t_name}{auto_label}")
    vote_details = "\n".join(vote_lines)

    if result["tie"]:
        # Tie — check if this is already a revote
        room = await storage.get_room(room_id)
        if room.get("status") == "revote":
            # Second tie — both stay
            await broadcast_message(
                bot,
                all_chat_ids,
                msg.REVOTE_STILL_TIE.format(results=results_text),
            )
            # Proceed to night
            winner = await gm.check_win_condition(room_id)
            if winner:
                await _announce_game_over(bot, storage, room_id, gm, winner)
                return
            await gm.increment_day(room_id)
            await _start_night_phase(bot, storage, room_id, gm, timer_service)
        else:
            # First tie — revote
            await gm.transition_to_revote(room_id)

            await broadcast_message(
                bot,
                all_chat_ids,
                msg.REVOTE_NEEDED.format(results=results_text),
            )

            # Clear votes for revote
            await storage.cleanup_day_keys(room_id, day_num)

            # Defense phase for tied players
            tied_ids = [str(uid) for uid in result["tied_players"]]
            await _start_defense_phase(
                bot, storage, room_id, gm, timer_service, tied_ids, 0
            )
    elif result["eliminated"]:
        eliminated_player = await storage.get_player(
            room_id, result["eliminated"]
        )
        eliminated_name = get_display_name(
            eliminated_player.get("username") if eliminated_player else None,
            result["eliminated"],
        )

        alive_count = len(await storage.get_alive_players(room_id))

        await broadcast_message(
            bot,
            all_chat_ids,
            msg.VOTING_RESULTS.format(
                results=results_text,
                vote_details=vote_details,
                eliminated=eliminated_name,
                remaining=alive_count,
            ),
        )

        # Last word phase
        winner = await gm.check_win_condition(room_id)
        if winner:
            await _announce_game_over(bot, storage, room_id, gm, winner)
            return

        await _start_last_word(
            bot, storage, room_id, gm, timer_service, result["eliminated"]
        )
    else:
        await broadcast_message(
            bot,
            all_chat_ids,
            msg.VOTING_RESULTS_NOBODY.format(results=results_text),
        )
        winner = await gm.check_win_condition(room_id)
        if winner:
            await _announce_game_over(bot, storage, room_id, gm, winner)
            return
        await gm.increment_day(room_id)
        await _start_night_phase(bot, storage, room_id, gm, timer_service)


async def _start_last_word(bot, storage, room_id, gm, timer_service, eliminated_id):
    """Start last word phase for eliminated player."""
    await gm.transition_to_last_word(room_id)

    eliminated_player = await storage.get_player(room_id, eliminated_id)
    if not eliminated_player:
        await _after_last_word(bot, storage, room_id, gm, timer_service)
        return

    eliminated_chat_id = int(eliminated_player["chat_id"])
    eliminated_name = get_display_name(
        eliminated_player.get("username"), eliminated_id
    )

    # Prompt the eliminated player
    await bot.send_message(
        chat_id=eliminated_chat_id, text=msg.LAST_WORD_PROMPT
    )

    # Notify others
    from services.room_manager import RoomManager

    rm = RoomManager(storage)
    other_chat_ids = await rm.get_other_player_chat_ids(room_id, eliminated_id)
    await broadcast_message(
        bot,
        other_chat_ids,
        msg.LAST_WORD_WAITING.format(player=eliminated_name),
    )

    room = await storage.get_room(room_id)
    last_word_time = int(room.get("last_word_time", "60"))

    # Store eliminated player id for game handler reference
    await storage.update_game_state(
        room_id, last_word_player=str(eliminated_id)
    )

    if timer_service:

        async def on_last_word_timeout():
            try:
                await bot.send_message(
                    chat_id=eliminated_chat_id, text=msg.LAST_WORD_TIMEOUT
                )
            except Exception:
                pass
            all_cids = await rm.get_player_chat_ids(room_id)
            await broadcast_message(
                bot, all_cids, msg.LAST_WORD_TIMEOUT
            )
            await _after_last_word(bot, storage, room_id, gm, timer_service)

        await timer_service.start_timer(
            room_id,
            eliminated_chat_id,
            last_word_time,
            on_timeout=on_last_word_timeout,
            timer_suffix="last_word",
        )


async def _after_last_word(bot, storage, room_id, gm, timer_service):
    """After last word, check win condition and proceed to night."""
    winner = await gm.check_win_condition(room_id)
    if winner:
        await _announce_game_over(bot, storage, room_id, gm, winner)
        return

    await gm.increment_day(room_id)
    await _start_night_phase(bot, storage, room_id, gm, timer_service)


async def _announce_game_over(bot, storage, room_id, gm, winner):
    """Announce game over."""
    # Collect stats BEFORE cleanup (data is still in Redis)
    stats_service = getattr(bot, "stats_service", None)
    if stats_service:
        try:
            players = await storage.get_all_players_data(room_id)
            room = await storage.get_room(room_id)
            day_count = int(room.get("day_number", "1")) if room else 1
            # started_at was stored as joined_at of first player; use None for now
            await stats_service.record_game(
                room_id=room_id,
                winner=winner,
                players=players,
                day_count=day_count,
            )
        except Exception as e:
            logger.warning("Failed to record game stats: %s", e)

    await gm.transition_to_finished(room_id)

    from services.room_manager import RoomManager

    rm = RoomManager(storage)
    all_chat_ids = await rm.get_player_chat_ids(room_id)

    mafia_list = await gm.get_mafia_list_text(room_id)

    if winner == "mafia":
        text = msg.GAME_OVER_MAFIA_WINS.format(mafia_list=mafia_list)
    else:
        text = msg.GAME_OVER_CIVILIANS_WIN.format(mafia_list=mafia_list)

    await broadcast_message(bot, all_chat_ids, text, reply_markup=MAIN_MENU_KB)

    # Cleanup: clear user-room mappings, FSM states
    player_ids = await storage.get_room_player_ids(room_id)
    for uid in player_ids:
        await storage.clear_user_room(int(uid))

    # Delete room data (or let TTL expire)
    await storage.delete_room(room_id)


async def _format_nominated_list(storage, room_id, nominated, nominated_by):
    """Format nominated list for display."""
    lines = []
    for nid in nominated:
        p = await storage.get_player(room_id, int(nid))
        name = get_display_name(
            p.get("username") if p else None, int(nid)
        )
        nominator_id = nominated_by.get(nid, "")
        if nominator_id:
            np = await storage.get_player(room_id, int(nominator_id))
            nom_name = get_display_name(
                np.get("username") if np else None, int(nominator_id)
            )
            lines.append(f"• {name} (виставлений {nom_name})")
        else:
            lines.append(f"• {name}")
    return "\n".join(lines)
