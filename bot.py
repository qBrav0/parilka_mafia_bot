import telebot
import random

from telebot import types
from telebot import custom_filters
from telebot.storage import StateMemoryStorage
from telebot.callback_data import CallbackData

from config import TOKEN
from utils import UserStates, generate_token, assign_roles_and_start_game, create_slots_inline_keyboard
from models import MafiaRoom                  
state_storage = StateMemoryStorage()
bot = telebot.TeleBot(TOKEN, state_storage=state_storage)

message_ids = {}

@bot.message_handler(commands=['start'])
def start(message):  
    bot.set_state(message.from_user.id, UserStates.main_menu, message.chat.id)
    bot.send_message(message.chat.id, f'Вотс Ап, {message.from_user.first_name}! Бот єбейшей парілки для мафії! ')
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    player_button = types.KeyboardButton('Я гравець')
    host_button = types.KeyboardButton('Я ведучий')
    keyboard.add(player_button, host_button)
    bot.send_message(message.chat.id, 'Обери хто ти на клавіатурі знизу', reply_markup=keyboard)

@bot.message_handler(state=UserStates.main_menu)
def main_menu(message):
    '''Гловне меню'''
    if message.text == 'Я ведучий':
        room_token = generate_token()
        bot.add_data(message.from_user.id, message.chat.id, room_token = room_token)
        MafiaRoom.create_host(room_token=room_token, player_name=message.from_user.first_name, player_chat_id=message.from_user.id)
        bot.send_message(message.chat.id, f"Токен кімнати: {room_token}")
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        start_game_button = types.KeyboardButton('Почати гру')
        keyboard.add(start_game_button)
        bot.send_message(message.chat.id, "Очікуй всіх гравців, після чого натисни - розпочати гру", reply_markup=keyboard)
        bot.set_state(message.from_user.id, UserStates.host_waiting, message.chat.id)
        
        

    if message.text == 'Я гравець':
        bot.send_message(message.chat.id, "Введіть токен від ведучого")
        bot.set_state(message.from_user.id, UserStates.awaiting_token, message.chat.id)

@bot.message_handler(state=UserStates.awaiting_token)
def awaiting_token(message):
    room_token = message.text
    player_added = MafiaRoom.add_player(room_token=room_token, player_name=message.from_user.first_name, player_chat_id=message.chat.id)
    if player_added:
        bot.send_message(message.chat.id, "Ви успішно приєдналися до кімнати!")
        bot.set_state(message.from_user.id, UserStates.player_waiting, message.chat.id)

        host = MafiaRoom.get(MafiaRoom.room_token == room_token, MafiaRoom.host == True)
        players_count = MafiaRoom.get_players_count(room_token)
        bot.send_message(host.player_chat_id, f"До кімнати приєднався новий гравець: {message.from_user.first_name}. Кількість гравців у кімнаті: {players_count}")

    else:
        bot.send_message(message.chat.id, "Невірний токен. Спробуйте ще раз.")


@bot.message_handler(state=UserStates.host_waiting)
def host_waiting(message):
    with bot.retrieve_data(message.chat.id) as data:
        room_token = data['room_token']

    keyboard = create_slots_inline_keyboard(room_token)
    for chat_id in MafiaRoom.get_player_chat_ids(room_token):
        message_ids[chat_id] = bot.send_message(chat_id, "Обери свій слот", reply_markup=keyboard).message_id


    if message.text == 'Почати гру':
        players_count = MafiaRoom.get_players_count(room_token)
        bot.send_message(message.chat.id, f"У кімнаті {players_count} гравців.")
        bot.send_message(message.chat.id, "Скільки мафій буде в грі?")
        bot.set_state(message.from_user.id, UserStates.awaiting_mafia_count, message.chat.id)

@bot.message_handler(state=UserStates.awaiting_mafia_count)
def awaiting_mafia_count(message):
    try:
        mafia_count = int(message.text)
        bot.send_message(message.chat.id, "Чи потрібно додати доктора? (Так/Ні)")
        bot.set_state(message.from_user.id, UserStates.awaiting_doctor, message.chat.id)
        bot.add_data(message.from_user.id, message.chat.id, mafia_count = mafia_count)
    except ValueError:
        bot.send_message(message.chat.id, "Будь ласка, введіть число.")

@bot.message_handler(state=UserStates.awaiting_doctor)
def awaiting_doctor(message):
    doctor = message.text.lower()
    if doctor in ['так', 'ні']:
        with bot.retrieve_data(message.chat.id) as data:
            mafia_count = data['mafia_count']
            room_token = data['room_token']
            
        doctor_needed = (doctor == 'так')
        bot.send_message(message.chat.id, f"Гра починається з {mafia_count} мафіями та {'з' if doctor_needed else 'без'} доктором.")
        players_roles = assign_roles_and_start_game(room_token, mafia_count, doctor_needed)

        for player_chat_id, role in players_roles.items():
            MafiaRoom.assign_role(room_token, player_chat_id, role)
            bot.send_message(player_chat_id, f"Твоя роль у грі: {role}")

        players = MafiaRoom.get_players(room_token)
        roles_message = []
        for player_name, player_chat_id in players.items():
            player_slot = MafiaRoom.get_player_number(room_token, player_chat_id)
            roles_message.append(f"{player_name} (Слот {player_slot}): {players_roles[player_chat_id]}")
        roles_message_text = '\n'.join(roles_message)
        
        bot.send_message(MafiaRoom.get_host_chat_id(room_token), f"Ролі гравців:\n{roles_message_text}")
        bot.set_state(message.from_user.id, UserStates.game_started, message.chat.id)
        
    else:
        bot.send_message(message.chat.id, "Будь ласка, відповідайте 'Так' або 'Ні'.")

# Обробник початку гри
@bot.callback_query_handler(func=lambda call: call.data.startswith('choose_number'))
def choose_number_callback(call):
    
    _, room_token, chosen_number = call.data.split(':')
    player_chat_id = call.message.chat.id
    # Перевіряємо, чи номер ще вільний
    if chosen_number.startswith('❌'):
        bot.answer_callback_query(call.id, "Цей номер вже зайнятий!")
    else:
        # Опрацьовуємо вибір номеру
        MafiaRoom.assign_player_number(room_token, player_chat_id, chosen_number)
        bot.answer_callback_query(call.id, f"Ви обрали номер {chosen_number}")

        # Відправляємо оновлену клавіатуру всім гравцям
        for player_chat_id in MafiaRoom.get_player_chat_ids(room_token):
            bot.edit_message_text("Оберіть вільний номер:", player_chat_id, message_ids[player_chat_id],  reply_markup=create_slots_inline_keyboard(room_token))

bot.add_custom_filter(custom_filters.StateFilter(bot))
bot.infinity_polling()
