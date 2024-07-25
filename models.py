from re import S
import peewee as pw

db = pw.SqliteDatabase('mafia_bot.db')
class BaseModel(pw.Model):
    class Meta:
        database = db

class MafiaRoom(BaseModel):

    room_token = pw.TextField(null=False)
    player_name = pw.TextField(null=False)
    player_chat_id = pw.IntegerField(null=False)
    role = pw.TextField(null=True)
    player_number = pw.IntegerField(null=True)
    host = pw.BooleanField(default=False)

    @classmethod
    def create_host(cls, room_token, player_name, player_chat_id):
        """
        Створює нового хоста кімнати.
        """

        return cls.create(room_token=room_token, player_name=player_name, player_chat_id=player_chat_id, host=True)
    
    @classmethod
    def get_player_number(cls, room_token, player_chat_id):
        """
        Повертає номер (слот) гравця за його чат айді у кімнаті.
        """
        try:
            player = cls.get(cls.room_token == room_token, cls.player_chat_id == player_chat_id)
            return player.player_number
        except cls.DoesNotExist:
            return None
        
    @classmethod
    def get_players_numbers(cls, room_token):
        """
        Повертає список номерів (слотів) гравців у кімнаті.
        """
        players = cls.select(cls.player_number).where(cls.room_token == room_token, cls.host == False, cls.player_number.is_null(False))
        return [player.player_number for player in players]
    
    @classmethod
    def add_player(cls, room_token, player_name, player_chat_id, role=None):
        """
        Додає нового гравця до кімнати.
        """
        try:
            host = cls.get(cls.room_token == room_token, cls.host == True)
            if host:
                return cls.create(room_token=room_token, player_name=player_name, player_chat_id=player_chat_id, host=False, role=role)
        except:
            return False

    @classmethod
    def get_players_count(cls, room_token):
        """
        Повертає кількість гравців у кімнаті.
        """
        return cls.select().where(cls.room_token == room_token).count() - 1  # Виключаємо хоста
    
    @classmethod
    def get_players(cls, room_token):
        """
        Повертає словник з іменами гравців та їхніми чат айді.
        """
        players_dict = {}
        players = cls.select().where(cls.room_token == room_token, cls.host == False)
        for player in players:
            players_dict[player.player_name] = player.player_chat_id
        return players_dict
    
    @classmethod
    def get_player_chat_ids(cls, room_token):
        """
        Повертає чат айді всіх гравців у кімнаті.
        """
        players = cls.select().where(cls.room_token == room_token, cls.host == False)
        chat_ids = []
        for player in players:
            chat_ids.append(player.player_chat_id)
        return chat_ids

    @classmethod
    def assign_role(cls, room_token, player_chat_id, role):
        """
        Призначає роль гравцю.
        """
        try:
            player = cls.get(cls.room_token == room_token, cls.player_chat_id == player_chat_id)
            player.role = role
            player.save()
            return True
        except cls.DoesNotExist:
            return False
        
    @classmethod
    def assign_player_number(cls, room_token, player_chat_id, player_number):
        """
        Ставить гравцю у кімнаті певний номер (слот).
        """
        try:
            player = cls.get(cls.room_token == room_token, cls.player_chat_id == player_chat_id)
            player.player_number = player_number
            player.save()
            return True
        except cls.DoesNotExist:
            return False
        
    @classmethod
    def get_player_name(cls, room_token, player_chat_id):
        """
        Повертає ім'я гравця за його player_chat_id.
        """
        try:
            player = cls.get(cls.room_token == room_token, cls.player_chat_id == player_chat_id)
            return player.player_name
        except cls.DoesNotExist:
            return None
        
    @classmethod
    def get_players_slots(cls, room_token):
        """
        Повертає словник з іменами гравців та їхніми слотами.
        """
        players_dict = {}
        players = cls.select().where(cls.room_token == room_token, cls.host == False)
        for player in players:
            players_dict[player.player_name] = player.player_number
        return players_dict

    @classmethod
    def get_host_chat_id(cls, room_token):
        """
        Повертає чат айді хоста за room_token.
        """
        try:
            host = cls.get(cls.room_token == room_token, cls.host == True)
            return host.player_chat_id
        except cls.DoesNotExist:
            return None



def create_tables():
    with db:
        db.create_tables([MafiaRoom])


# create_tables()