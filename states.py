"""FSM states for the Mafia bot.

States are stored in Redis for persistence (via aiogram RedisStorage).
"""

from aiogram.fsm.state import State, StatesGroup


class MenuStates(StatesGroup):
    """States for the main menu and room management."""

    waiting_custom_max_players = State()  # Очікування введення власного значення max_players
    waiting_room_code = State()  # Очікування введення коду кімнати


class GameStates(StatesGroup):
    """States for active game phases."""

    waiting_game_start = State()  # В лобі, очікування старту
    zero_night_don_speech = State()  # Дон записує стратегію
    night_waiting = State()  # Мирні чекають вночі
    night_mafia_choice = State()  # Мафія обирає жертву
    night_sheriff_check = State()  # Комісар перевіряє гравця
    night_doctor_save = State()  # Лікар рятує гравця
    day_speaking = State()  # Черга говорити
    day_listening = State()  # Слухає інших
    day_nominating = State()  # Можливість виставити на голосування
    defense_phase = State()  # Фаза захисту виставлених
    defense_listening = State()  # Слухає захист
    day_voting = State()  # Голосування
    last_word_phase = State()  # Останнє слово вигнаного
    last_word_waiting = State()  # Чекає останнє слово
    revote_phase = State()  # Переголосування
    game_over = State()  # Гра завершена
