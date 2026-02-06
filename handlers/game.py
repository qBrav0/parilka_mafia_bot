"""Game message handlers.

Handles:
- Voice/video_note messages during game phases
- Text messages during game (rejection)
- Shout (out-of-turn) voice messages
- Don's zero night strategy recording
- Defense phase voice messages
- Last word voice messages
"""

import asyncio
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import messages as msg
from keyboards import IN_GAME_KB, MAIN_MENU_KB, nominate_button_keyboard
from services.audio_handler import get_media_info, validate_audio_duration
from services.game_manager import (
    MAFIA_ROLES,
    ROLE_DON,
    ROLE_MAFIA,
    GameManager,
)
from states import GameStates
from utils import (
    broadcast_media,
    broadcast_message,
    get_display_name,
)

logger = logging.getLogger(__name__)
router = Router(name="game")


@router.message(F.voice | F.video_note)
async def handle_voice_or_video_note(
    message: Message, state: FSMContext, **kwargs
) -> None:
    """Handle all voice/video_note messages during game.

    Routes to appropriate handler based on current game phase.
    """
    storage = kwargs.get("game_storage")
    bot = kwargs.get("bot")
    timer_service = kwargs.get("timer_service")

    user_id = message.from_user.id
    room_id = await storage.get_user_room(user_id)

    if not room_id:
        return  # Not in a game, ignore

    room = await storage.get_room(room_id)
    if not room:
        return

    status = room.get("status", "")

    # Extract media info
    media = get_media_info(message)
    if not media:
        return

    gm = GameManager(storage)

    if status == "zero_night":
        await _handle_zero_night_voice(
            message, storage, bot, room_id, media, gm, timer_service
        )
    elif status == "day_speaking":
        await _handle_day_speaking_voice(
            message, storage, bot, room_id, media, gm, timer_service
        )
    elif status == "defense_phase":
        await _handle_defense_voice(
            message, storage, bot, room_id, media, gm, timer_service
        )
    elif status == "last_word":
        await _handle_last_word_voice(
            message, storage, bot, room_id, media, gm, timer_service
        )
    elif status in ("day_voting", "revote"):
        # During voting — this is a shout
        await _handle_shout(message, storage, bot, room_id, media, gm)
    elif status == "night":
        # Night — no shouts allowed
        await message.answer(msg.SHOUT_NIGHT_CANNOT)
    else:
        pass  # Ignore in other phases


@router.message(F.text)
async def handle_text_during_game(
    message: Message, state: FSMContext, **kwargs
) -> None:
    """Handle text messages during game phases (reject with instruction).

    Only applies when user is in an active game and it's their turn.
    """
    storage = kwargs.get("game_storage")

    if not storage:
        return

    user_id = message.from_user.id
    room_id = await storage.get_user_room(user_id)

    if not room_id:
        return

    room = await storage.get_room(room_id)
    if not room:
        return

    status = room.get("status", "")

    # Only show error during speaking phases
    if status in ("day_speaking", "defense_phase", "last_word", "zero_night"):
        # Check if it's this player's turn
        gm = GameManager(storage)
        current = await gm.get_current_speaker(room_id)

        if status == "zero_night":
            don_players = await storage.get_players_by_role(room_id, ROLE_DON)
            if don_players and int(don_players[0]["user_id"]) == user_id:
                await message.answer(msg.TEXT_NOT_ALLOWED)
                return
        elif status == "last_word":
            game_state = await storage.get_game_state(room_id)
            if game_state:
                lw_player = game_state.get("last_word_player", "")
                if lw_player and int(lw_player) == user_id:
                    await message.answer(msg.TEXT_NOT_ALLOWED)
                    return
        elif current and int(current["user_id"]) == user_id:
            await message.answer(msg.TEXT_NOT_ALLOWED)
            return

    # For day phases (not current speaker) - check if shout
    if status in ("day_speaking", "defense_phase", "day_voting"):
        player = await storage.get_player(room_id, user_id)
        if player and player.get("is_alive") == "true":
            # Text messages are not shouts — only voice/video_note
            pass


# ──────────────────────────────────────────────
# Internal voice handlers
# ──────────────────────────────────────────────


async def _handle_zero_night_voice(
    message, storage, bot, room_id, media, gm, timer_service
):
    """Handle Don's strategy voice in zero night."""
    user_id = message.from_user.id

    # Only Don can send voice during zero night
    don_players = await storage.get_players_by_role(room_id, ROLE_DON)
    if not don_players or int(don_players[0]["user_id"]) != user_id:
        return  # Not Don, ignore

    # Validate duration
    if not validate_audio_duration(media["duration"], 60):
        await message.answer(
            msg.AUDIO_TOO_LONG.format(
                max_duration=60, duration=media["duration"]
            )
        )
        return

    # Save strategy
    await storage.set_don_strategy(
        room_id, media["file_id"], media["type"], media["duration"]
    )

    # Cancel timer
    if timer_service:
        await timer_service.cancel_timer(room_id, "zero_night")

    # Confirm to Don
    await message.answer(msg.DON_STRATEGY_SENT)

    # Send strategy to all mafia (except Don)
    mafia_players = await storage.get_players_by_role(room_id, ROLE_MAFIA)
    mafia_chat_ids = [int(p["chat_id"]) for p in mafia_players]

    if mafia_chat_ids:
        await broadcast_message(
            bot, mafia_chat_ids, msg.DON_STRATEGY_TO_MAFIA
        )
        await broadcast_media(
            bot, mafia_chat_ids, media["file_id"], media["type"]
        )
        await broadcast_message(bot, mafia_chat_ids, msg.DON_STRATEGY_LISTEN)

    # Transition to day
    from handlers.callbacks import _transition_to_first_day

    await _transition_to_first_day(bot, storage, room_id, gm, timer_service)


async def _handle_day_speaking_voice(
    message, storage, bot, room_id, media, gm, timer_service
):
    """Handle voice during day speaking phase."""
    user_id = message.from_user.id

    # Check if it's this player's turn
    current = await gm.get_current_speaker(room_id)
    if not current:
        return

    if int(current["user_id"]) == user_id:
        # It's their turn — process speech
        room = await storage.get_room(room_id)
        max_duration = int(room.get("max_audio_duration", "60"))

        if not validate_audio_duration(media["duration"], max_duration):
            await message.answer(
                msg.AUDIO_TOO_LONG.format(
                    max_duration=max_duration, duration=media["duration"]
                )
            )
            return

        # Cancel speech timer
        if timer_service:
            await timer_service.cancel_timer(room_id, "speech")

        speaker_name = get_display_name(
            current.get("username"), int(current["user_id"])
        )

        # Confirm to speaker
        await message.answer(
            msg.SPEECH_ACCEPTED.format(duration=media["duration"])
        )

        # Advance to next speaker
        next_speaker = await gm.advance_speaker(room_id)

        # Broadcast speech to all other players
        from services.room_manager import RoomManager

        rm = RoomManager(storage)
        other_chat_ids = await rm.get_other_player_chat_ids(room_id, user_id)

        if next_speaker:
            next_name = get_display_name(
                next_speaker.get("username"), int(next_speaker["user_id"])
            )
            caption = msg.SPEECH_BROADCAST.format(
                speaker=speaker_name,
                duration=media["duration"],
                next_speaker=next_name,
            )
        else:
            caption = msg.SPEECH_BROADCAST_LAST.format(
                speaker=speaker_name, duration=media["duration"]
            )

        # Send media first, then caption
        if media["type"] == "video_note":
            from utils import broadcast_video_note

            await broadcast_video_note(bot, other_chat_ids, media["file_id"])
            await broadcast_message(bot, other_chat_ids, caption)
        else:
            from utils import broadcast_voice

            await broadcast_voice(
                bot, other_chat_ids, media["file_id"], caption=caption
            )

        if next_speaker:
            # Prompt next speaker
            next_chat_id = int(next_speaker["chat_id"])
            await bot.send_message(
                chat_id=next_chat_id,
                text=msg.YOUR_TURN_TO_SPEAK,
                reply_markup=nominate_button_keyboard(),
            )

            speech_time = int(room.get("speech_time", "90"))
            if timer_service:

                async def on_timeout():
                    from handlers.callbacks import _handle_speech_timeout

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
            from handlers.callbacks import _after_all_speeches

            await _after_all_speeches(bot, storage, room_id, gm, timer_service)
    else:
        # Not their turn — this is a shout
        player = await storage.get_player(room_id, user_id)
        if player and player.get("is_alive") == "true":
            await _handle_shout(message, storage, bot, room_id, media, gm)
        elif player and player.get("is_alive") == "false":
            await message.answer(msg.SHOUT_DEAD_CANNOT)


async def _handle_defense_voice(
    message, storage, bot, room_id, media, gm, timer_service
):
    """Handle voice during defense phase."""
    user_id = message.from_user.id

    room = await storage.get_room(room_id)
    day_num = int(room.get("day_number", "1"))
    current_idx = int(room.get("current_speaker_index", "0"))
    nominated = await storage.get_nominated(room_id, day_num)

    if current_idx >= len(nominated):
        return

    defender_id = int(nominated[current_idx])

    if defender_id != user_id:
        # Not the current defender — treat as shout
        player = await storage.get_player(room_id, user_id)
        if player and player.get("is_alive") == "true":
            await _handle_shout(message, storage, bot, room_id, media, gm)
        return

    max_duration = int(room.get("max_audio_duration", "60"))
    if not validate_audio_duration(media["duration"], max_duration):
        await message.answer(
            msg.AUDIO_TOO_LONG.format(
                max_duration=max_duration, duration=media["duration"]
            )
        )
        return

    # Cancel defense timer
    if timer_service:
        await timer_service.cancel_timer(room_id, "defense")

    # Confirm
    await message.answer(
        msg.SPEECH_ACCEPTED.format(duration=media["duration"])
    )

    # Broadcast to all
    from services.room_manager import RoomManager

    rm = RoomManager(storage)
    other_chat_ids = await rm.get_other_player_chat_ids(room_id, user_id)

    defender_name = get_display_name(message.from_user.username, user_id)
    caption = f"🛡️ Захист від {defender_name}:"

    if media["type"] == "video_note":
        from utils import broadcast_video_note

        await broadcast_video_note(bot, other_chat_ids, media["file_id"])
        await broadcast_message(bot, other_chat_ids, caption)
    else:
        from utils import broadcast_voice

        await broadcast_voice(
            bot, other_chat_ids, media["file_id"], caption=caption
        )

    # Move to next defender
    from handlers.callbacks import _start_defense_phase

    await _start_defense_phase(
        bot, storage, room_id, gm, timer_service, nominated, current_idx + 1
    )


async def _handle_last_word_voice(
    message, storage, bot, room_id, media, gm, timer_service
):
    """Handle voice during last word phase."""
    user_id = message.from_user.id

    game_state = await storage.get_game_state(room_id)
    if not game_state:
        return

    lw_player = game_state.get("last_word_player", "")
    if not lw_player or int(lw_player) != user_id:
        return  # Not the eliminated player

    room = await storage.get_room(room_id)
    last_word_max = int(room.get("last_word_max_audio", "45"))

    if not validate_audio_duration(media["duration"], last_word_max):
        await message.answer(
            msg.AUDIO_TOO_LONG.format(
                max_duration=last_word_max, duration=media["duration"]
            )
        )
        return

    # Cancel timer
    if timer_service:
        await timer_service.cancel_timer(room_id, "last_word")

    # Confirm
    await message.answer(msg.LAST_WORD_SENT)

    # Broadcast to all
    from services.room_manager import RoomManager

    rm = RoomManager(storage)
    other_chat_ids = await rm.get_other_player_chat_ids(room_id, user_id)

    player_name = get_display_name(message.from_user.username, user_id)

    if media["type"] == "video_note":
        from utils import broadcast_video_note

        await broadcast_video_note(bot, other_chat_ids, media["file_id"])
        await broadcast_message(
            bot,
            other_chat_ids,
            msg.LAST_WORD_BROADCAST.format(
                player=player_name, duration=media["duration"]
            ),
        )
    else:
        from utils import broadcast_voice

        await broadcast_voice(
            bot,
            other_chat_ids,
            media["file_id"],
            caption=msg.LAST_WORD_BROADCAST.format(
                player=player_name, duration=media["duration"]
            ),
        )

    # Proceed after last word
    from handlers.callbacks import _after_last_word

    await _after_last_word(bot, storage, room_id, gm, timer_service)


async def _handle_shout(message, storage, bot, room_id, media, gm):
    """Handle an out-of-turn voice message (shout)."""
    user_id = message.from_user.id

    player = await storage.get_player(room_id, user_id)
    if not player:
        return

    if player.get("is_alive") != "true":
        await message.answer(msg.SHOUT_DEAD_CANNOT)
        return

    room = await storage.get_room(room_id)
    status = room.get("status", "")

    # Shouts only during day phases
    if status not in (
        "day_speaking",
        "defense_phase",
        "day_voting",
        "revote",
        "last_word",
    ):
        await message.answer(msg.SHOUT_NIGHT_CANNOT)
        return

    penalties, killed = await gm.process_shout(room_id, user_id)

    player_name = get_display_name(message.from_user.username, user_id)

    from services.room_manager import RoomManager

    rm = RoomManager(storage)

    if killed:
        # 3rd penalty — auto-kill
        await message.answer(msg.SHOUT_KILLED)

        other_chat_ids = await rm.get_other_player_chat_ids(room_id, user_id)
        await broadcast_media(
            bot, other_chat_ids, media["file_id"], media["type"]
        )
        await broadcast_message(
            bot,
            other_chat_ids,
            msg.SHOUT_KILLED_BROADCAST.format(player=player_name),
        )

        # Check win condition
        winner = await gm.check_win_condition(room_id)
        if winner:
            from handlers.callbacks import _announce_game_over

            await _announce_game_over(bot, storage, room_id, gm, winner)
    else:
        # Penalty warning
        if penalties == 2:
            await message.answer(msg.SHOUT_WARNING.format(penalties=penalties))
        else:
            await message.answer(
                msg.SHOUT_CONFIRMED.format(penalties=penalties)
            )

        # Broadcast shout to everyone else
        other_chat_ids = await rm.get_other_player_chat_ids(room_id, user_id)
        await broadcast_media(
            bot, other_chat_ids, media["file_id"], media["type"]
        )
        await broadcast_message(
            bot,
            other_chat_ids,
            msg.SHOUT_BROADCAST.format(
                player=player_name, penalties=penalties
            ),
        )
