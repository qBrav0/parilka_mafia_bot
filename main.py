import telebot
import random

from telebot import types
from telebot import custom_filters
from telebot.storage import StateMemoryStorage

from config import TOKEN  
from utils import UserStates

state_storage = StateMemoryStorage()
bot = telebot.TeleBot(TOKEN, state_storage=state_storage)

players = {}
host_id = None

@bot.message_handler(commands=['start'])
def start(message):  
    bot.set_state(message.from_user.id, UserStates.main_menu, message.chat.id)
    bot.send_message(message.chat.id, f'Привіт, {message.from_user.first_name}! Ласкаво просимо до гри Мафія!')
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    player_button = types.KeyboardButton('Я гравець')
    host_button = types.KeyboardButton('Я ведучий')
    keyboard.add(player_button, host_button)
    bot.send_message(message.chat.id, 'Обери свою роль:', reply_markup=keyboard)

@bot.message_handler(state=UserStates.main_menu)
def main_menu(message):
    global host_id
    if message.text == 'Я ведучий':
        host_id = message.from_user.id
        bot.send_message(message.chat.id, "Ви обрали роль ведучого.")
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        start_game_button = types.KeyboardButton('Почати гру')
        keyboard.add(start_game_button)
        bot.send_message(message.chat.id, "Натисніть кнопку, щоб почати гру", reply_markup=keyboard)
        bot.set_state(message.from_user.id, UserStates.host_waiting, message.chat.id)
        players.clear()

    elif message.text == 'Я гравець':
        players[message.chat.id] = message.from_user.first_name
        bot.send_message(message.chat.id, "Ви приєдналися до гри як гравець.")
        bot.set_state(message.from_user.id, UserStates.player_waiting, message.chat.id)
        if host_id:
            bot.send_message(host_id, f"Новий гравець: {message.from_user.first_name}\nЗагальна кількість гравців: {len(players)}")

@bot.message_handler(state=UserStates.host_waiting)
def host_waiting(message):
    if message.text == 'Почати гру':
        bot.send_message(message.chat.id, "Скільки мафій буде в грі?")
        bot.set_state(message.from_user.id, UserStates.awaiting_mafia_count, message.chat.id)

@bot.message_handler(state=UserStates.awaiting_mafia_count)
def awaiting_mafia_count(message):
    try:
        mafia_count = int(message.text)
        bot.add_data(message.from_user.id, message.chat.id, mafia_count = mafia_count)
        bot.send_message(message.chat.id, "Чи потрібно додати доктора? (Так/Ні)")
        bot.set_state(message.from_user.id, UserStates.awaiting_doctor, message.chat.id)
    except ValueError:
        bot.send_message(message.chat.id, "Будь ласка, введіть число.")

@bot.message_handler(state=UserStates.awaiting_doctor)
def awaiting_doctor(message):
    doctor = message.text.lower()
    if doctor in ['так', 'ні']:
        doctor_needed = (doctor == 'так')
        bot.add_data(message.from_user.id, message.chat.id, doctor_needed = doctor_needed)
        bot.send_message(message.chat.id, "Гра починається з налаштуванням ролей.")
        assign_roles_and_start_game(message)
        bot.set_state(message.from_user.id, UserStates.game_started, message.chat.id)
    else:
        bot.send_message(message.chat.id, "Будь ласка, відповідайте 'Так' або 'Ні'.")

def assign_roles_and_start_game(message):
    with bot.retrieve_data(message.chat.id) as host_data:
        mafia_count = host_data['mafia_count']
        doctor_needed = host_data['doctor_needed']

    player_chat_ids = list(players.keys())
    random.shuffle(player_chat_ids)

    roles = ['Мафія'] * mafia_count + ['Комісар'] + (['Доктор'] if doctor_needed else [])
    roles += ['Мирний'] * (len(player_chat_ids) - len(roles))
    random.shuffle(roles)

    # Assign a random Don among the mafia
    mafia_indices = [i for i, role in enumerate(roles) if role == 'Мафія']
    if mafia_indices:
        don_index = random.choice(mafia_indices)
        roles[don_index] = 'Дон Мафії'

    players_roles = {players[player_id]: role for player_id, role in zip(player_chat_ids, roles)}
    for player_id, role in zip(player_chat_ids, roles):
        bot.send_message(player_id, f"Ваша роль у грі: {role}")

    roles_message = '\n'.join([f"{player}: {role}" for player, role in players_roles.items()])
    bot.send_message(host_id, f"Ролі гравців:\n{roles_message}")

bot.add_custom_filter(custom_filters.StateFilter(bot))
bot.infinity_polling()
