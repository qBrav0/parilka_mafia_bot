from telebot.handler_backends import State, StatesGroup 
from models import MafiaRoom
from telebot import types


class UserStates(StatesGroup):

    '''Class for defining states'''

    main_menu = State()
    host_waiting = State()
    awaiting_token = State()
    awaiting_mafia_count = State()
    awaiting_doctor = State()
    game_started = State()
    player_waiting = State()

    
import random
import string

def generate_token(length=5):
    characters = string.ascii_uppercase + string.digits
    token = ''.join(random.choice(characters) for _ in range(length))
    return token

def assign_roles_and_start_game(room_token, mafia_count, doctor_needed):

    players_dict = MafiaRoom.get_players(room_token)

    roles = ['Мафія'] * mafia_count + ['Комісар'] + (['Доктор'] if doctor_needed else [])
    roles += ['Мирний'] * (len(players_dict) - len(roles))
    random.shuffle(roles)

    mafia_indices = [i for i, role in enumerate(roles) if role == 'Мафія']
    if mafia_indices:
        don_index = random.choice(mafia_indices)
        roles[don_index] = 'Дон Мафії'

    players_roles = {}
    players_chat_id = list(players_dict.keys())
    random.shuffle(players_chat_id)
    for player_name, role in zip(players_chat_id, roles):   
        player_chat_id = players_dict[player_name]     
        players_roles[player_chat_id] = role
    return players_roles

def create_slots_inline_keyboard(room_token):
    players_slots = MafiaRoom.get_players_slots(room_token)
    players_chat_id = MafiaRoom.get_player_chat_ids(room_token)
    inline_keyboard = types.InlineKeyboardMarkup(row_width=3)
    for number in range(1, len(players_chat_id) + 1):
        button_text = f"{number}"
        if number in players_slots.values():
            button_text = f"❌{number}" 
        button = types.InlineKeyboardButton(button_text, callback_data=f"choose_number:{room_token}:{number}")
        inline_keyboard.add(button)
    return inline_keyboard 