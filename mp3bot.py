import os
import logging
import sqlite3
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Токен бота Telegram
TOKEN = ""  # Замените на ваш токен

# ID администратора
ADMIN_ID = ''

# Директория для временного хранения файлов
TEMP_DIR = "temp_files"
os.makedirs(TEMP_DIR, exist_ok=True)

# Путь к базе данных SQLite
DB_PATH = "voice_messages.db"


# Проверка существования базы данных
def check_database_exists():
    return os.path.exists(DB_PATH)


# Миграция и инициализация базы данных
def init_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Таблица для голосовых сообщений
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS voice_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        file_id TEXT NOT NULL,
        original_filename TEXT NOT NULL,
        created_at TEXT NOT NULL,
        file_data BLOB NOT NULL
    )
    ''')

    # Таблица для логирования чата
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS chat_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        username TEXT,
        first_name TEXT,
        message_type TEXT NOT NULL,
        message_text TEXT,
        message_data TEXT,
        error_flag BOOLEAN DEFAULT 0,
        timestamp TEXT NOT NULL
    )
    ''')

    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")


# Функция миграции базы данных
def migrate_database():
    # Проверяем, существует ли база данных
    if not check_database_exists():
        # Если базы нет, просто создаем новую
        init_database()
        return

    # Если база уже существует, проверяем структуру
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Проверяем наличие таблицы voice_messages
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='voice_messages'")
    if not cursor.fetchone():
        # Создаем таблицу если не существует
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS voice_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            file_id TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            created_at TEXT NOT NULL,
            file_data BLOB NOT NULL
        )
        ''')
        logger.info("Создана таблица voice_messages")
    else:
        # Проверяем наличие столбца file_data
        cursor.execute("PRAGMA table_info(voice_messages)")
        columns = [column[1] for column in cursor.fetchall()]

        if "file_data" not in columns:
            logger.info("Миграция базы данных: добавление столбца file_data")

            # Создаем новую таблицу с нужной структурой
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS voice_messages_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                file_id TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                created_at TEXT NOT NULL,
                file_data BLOB
            )
            ''')

            # Копируем данные из старой таблицы (без file_data)
            cursor.execute('''
            INSERT INTO voice_messages_new (id, user_id, file_id, original_filename, created_at)
            SELECT id, user_id, file_id, original_filename, created_at FROM voice_messages
            ''')

            # Удаляем старую таблицу и переименовываем новую
            cursor.execute('DROP TABLE voice_messages')
            cursor.execute('ALTER TABLE voice_messages_new RENAME TO voice_messages')

            logger.info("Миграция таблицы voice_messages завершена успешно")

    # Проверяем наличие таблицы chat_logs
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chat_logs'")
    if not cursor.fetchone():
        # Создаем таблицу для логирования чата если не существует
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT,
            message_type TEXT NOT NULL,
            message_text TEXT,
            message_data TEXT,
            error_flag BOOLEAN DEFAULT 0,
            timestamp TEXT NOT NULL
        )
        ''')
        logger.info("Создана таблица chat_logs")
    else:
        # Проверяем наличие столбца error_flag
        cursor.execute("PRAGMA table_info(chat_logs)")
        columns = [column[1] for column in cursor.fetchall()]

        if "error_flag" not in columns:
            logger.info("Миграция базы данных: добавление столбца error_flag")
            cursor.execute('ALTER TABLE chat_logs ADD COLUMN error_flag BOOLEAN DEFAULT 0')
            logger.info("Миграция таблицы chat_logs завершена успешно")

    conn.commit()
    conn.close()


# Функция для логирования сообщений и действий пользователя
def log_message(user_id, username, first_name, message_type, message_text=None, message_data=None, error_flag=False):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Если message_data - словарь или другой сложный объект, преобразуем в JSON
        if message_data and not isinstance(message_data, str):
            message_data = json.dumps(message_data, ensure_ascii=False)

        cursor.execute(
            "INSERT INTO chat_logs (user_id, username, first_name, message_type, message_text, message_data, error_flag, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, username, first_name, message_type, message_text, message_data, 1 if error_flag else 0, timestamp)
        )
        conn.commit()
        conn.close()
        logger.debug(f"Лог сохранен: user_id={user_id}, type={message_type}, error_flag={error_flag}")
    except Exception as e:
        logger.error(f"Ошибка при логировании сообщения: {e}")


# Получение логов для администратора
def get_all_logs(limit=50, user_id=None, error_only=False):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    query = "SELECT id, user_id, username, first_name, message_type, message_text, message_data, error_flag, timestamp FROM chat_logs"
    params = []

    conditions = []
    if user_id:
        conditions.append("user_id = ?")
        params.append(user_id)

    if error_only:
        conditions.append("error_flag = 1")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    logs = cursor.fetchall()
    conn.close()
    return logs


# Получение пользователей бота
def get_bot_users():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT user_id, username, first_name, 
               (SELECT COUNT(*) FROM chat_logs cl WHERE cl.user_id = main.user_id) as message_count, 
               MAX(timestamp) as last_activity
        FROM chat_logs main
        GROUP BY user_id
        ORDER BY last_activity DESC
    """)
    users = cursor.fetchall()
    conn.close()
    return users


# Сохранение голосового сообщения в базу данных
def save_voice_message(user_id, file_id, original_filename, file_data):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "INSERT INTO voice_messages (user_id, file_id, original_filename, created_at, file_data) VALUES (?, ?, ?, ?, ?)",
        (user_id, file_id, original_filename, created_at, file_data)
    )
    voice_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return voice_id


# Получение списка голосовых сообщений пользователя
def get_user_voice_messages(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, original_filename, created_at FROM voice_messages WHERE user_id = ? ORDER BY created_at DESC LIMIT 10",
        (user_id,)
    )
    messages = cursor.fetchall()
    conn.close()
    return messages


# Получение информации о голосовом сообщении по ID
def get_voice_message(message_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, user_id, file_id, original_filename, created_at, file_data FROM voice_messages WHERE id = ?",
        (message_id,))
    message = cursor.fetchone()
    conn.close()
    return message


# Создание основного меню (с учетом прав администратора)
def get_main_menu(user_id):
    keyboard = [
        [InlineKeyboardButton("🎤 Создать новое голосовое", callback_data="new_voice")],
        [InlineKeyboardButton("📋 Мои голосовые сообщения", callback_data="list_voices")],
        [InlineKeyboardButton("🔄 Перезапустить бота", callback_data="restart")]
    ]

    # Добавляем кнопки администратора
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("📊 Просмотр логов", callback_data="admin_logs")])
        keyboard.append([InlineKeyboardButton("👥 Пользователи бота", callback_data="admin_users")])

    return InlineKeyboardMarkup(keyboard)


# Создание меню со списком голосовых сообщений
def get_voices_menu(user_id):
    messages = get_user_voice_messages(user_id)
    keyboard = []

    for msg_id, filename, created_at in messages:
        # Обрезаем имя файла, если оно слишком длинное
        display_name = filename if len(filename) < 30 else filename[:27] + "..."
        keyboard.append([InlineKeyboardButton(f"{display_name} ({created_at})", callback_data=f"voice_{msg_id}")])

    # Добавляем кнопку возврата в главное меню
    keyboard.append([InlineKeyboardButton("⬅️ Назад в главное меню", callback_data="main_menu")])

    return InlineKeyboardMarkup(keyboard)


# Создание меню для просмотра логов администратором
def get_admin_logs_menu():
    keyboard = [
        [InlineKeyboardButton("📝 Все логи (50)", callback_data="logs_all_50")],
        [InlineKeyboardButton("⚠️ Только ошибки", callback_data="logs_errors")],
        [InlineKeyboardButton("⬅️ Назад в главное меню", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


# Создание меню для выбора пользователя администратором
def get_admin_users_menu():
    users = get_bot_users()
    keyboard = []

    for user_id, username, first_name, msg_count, last_activity in users:
        display_name = username or first_name or f"User {user_id}"
        keyboard.append([
            InlineKeyboardButton(
                f"{display_name} ({msg_count} сообщ.)",
                callback_data=f"user_logs_{user_id}"
            )
        ])

    # Добавляем кнопку возврата в главное меню
    keyboard.append([InlineKeyboardButton("⬅️ Назад в главное меню", callback_data="main_menu")])

    return InlineKeyboardMarkup(keyboard)


# Форматирование логов для отображения
def format_logs_for_display(logs):
    if not logs:
        return "Логи не найдены."

    result = "📋 Журнал действий:\n\n"

    for log in logs:
        log_id, user_id, username, first_name, message_type, message_text, message_data, error_flag, timestamp = log

        # Формируем имя пользователя для отображения
        user_display = username or first_name or f"User {user_id}"

        # Добавляем эмодзи для различных типов сообщений
        type_emoji = "🔄"  # По умолчанию
        if message_type == "text":
            type_emoji = "💬"
        elif message_type == "command":
            type_emoji = "🔧"
        elif message_type == "button_click":
            type_emoji = "👆"
        elif message_type == "mp3_upload":
            type_emoji = "🎵"
        elif message_type == "voice_created":
            type_emoji = "🎤"
        elif message_type == "voice_sent":
            type_emoji = "📤"
        elif message_type == "error" or "error" in message_type:
            type_emoji = "❗️"

        # Добавляем восклицательный знак для ошибок
        if error_flag:
            type_emoji = "❗️"

        # Формируем запись лога
        log_entry = f"{timestamp} | {type_emoji} {user_display}: {message_type}"

        if message_text:
            # Обрезаем текст, если он слишком длинный
            if len(message_text) > 30:
                message_text = message_text[:27] + "..."
            log_entry += f" | {message_text}"

        result += log_entry + "\n"

        # Разделяем логи пустой строкой для лучшей читаемости
        result += "-" * 30 + "\n"

    return result


# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user

    # Логирование команды
    log_message(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        message_type="command",
        message_text="/start"
    )

    await update.message.reply_text(
        f"Привет, {user.first_name}! Я бот для преобразования MP3 файлов в голосовые сообщения.\n\n"
        "Используйте кнопки меню ниже для управления:",
        reply_markup=get_main_menu(user.id)
    )


# Обработчик команды /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user

    # Логирование команды
    log_message(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        message_type="command",
        message_text="/help"
    )

    await update.message.reply_text(
        "🔹 Для создания нового голосового сообщения отправьте MP3 файл или используйте кнопку 'Создать новое голосовое'.\n"
        "🔹 Чтобы просмотреть ранее созданные голосовые, нажмите 'Мои голосовые сообщения'.\n"
        "🔹 Для перезапуска бота нажмите 'Перезапустить бота' или введите /start.",
        reply_markup=get_main_menu(user.id)
    )


# Обработчик для колбэков от кнопок меню
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    user = query.from_user

    # Логирование нажатия кнопки
    log_message(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        message_type="button_click",
        message_text=query.data
    )

    # Обязательно отвечаем на callback_query, чтобы убрать "часики" у кнопки
    await query.answer()

    # Обработка основного меню
    if query.data == "main_menu":
        await query.edit_message_text(
            text="Выберите действие из меню:",
            reply_markup=get_main_menu(user_id)
        )

    elif query.data == "new_voice":
        await query.edit_message_text(
            text="Отправьте мне MP3 файл, и я преобразую его в голосовое сообщение.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад в меню", callback_data="main_menu")]])
        )

    elif query.data == "list_voices":
        messages = get_user_voice_messages(user_id)
        if messages:
            await query.edit_message_text(
                text="Ваши сохраненные голосовые сообщения:",
                reply_markup=get_voices_menu(user_id)
            )
        else:
            await query.edit_message_text(
                text="У вас пока нет сохраненных голосовых сообщений. Отправьте MP3 файл, чтобы создать первое!",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ Назад в меню", callback_data="main_menu")]])
            )

    elif query.data == "restart":
        await query.edit_message_text(
            text="Бот перезапущен! Выберите действие из меню:",
            reply_markup=get_main_menu(user_id)
        )

    # Обработка функций администратора
    elif query.data == "admin_logs" and user_id == ADMIN_ID:
        await query.edit_message_text(
            text="Выберите тип логов для просмотра:",
            reply_markup=get_admin_logs_menu()
        )

    elif query.data == "admin_users" and user_id == ADMIN_ID:
        await query.edit_message_text(
            text="Выберите пользователя для просмотра логов:",
            reply_markup=get_admin_users_menu()
        )

    elif query.data == "logs_all_50" and user_id == ADMIN_ID:
        logs = get_all_logs(limit=50)
        logs_text = format_logs_for_display(logs)

        await query.edit_message_text(
            text=logs_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin_logs")]])
        )

    elif query.data == "logs_errors" and user_id == ADMIN_ID:
        logs = get_all_logs(limit=50, error_only=True)
        logs_text = format_logs_for_display(logs)

        await query.edit_message_text(
            text=logs_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin_logs")]])
        )

    elif query.data.startswith("user_logs_") and user_id == ADMIN_ID:
        # Извлекаем ID пользователя из колбэка
        selected_user_id = int(query.data.split("_")[2])
        logs = get_all_logs(limit=30, user_id=selected_user_id)
        logs_text = format_logs_for_display(logs)

        await query.edit_message_text(
            text=logs_text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin_users")]])
        )

    # Обработка воспроизведения голосовых сообщений
    elif query.data.startswith("voice_"):
        # Извлекаем ID голосового сообщения
        voice_id = int(query.data.split("_")[1])
        voice_info = get_voice_message(voice_id)

        if voice_info:
            try:
                # Логируем запрос на воспроизведение голосового
                log_message(
                    user_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                    message_type="play_voice",
                    message_data={"voice_id": voice_id}
                )

                # Извлекаем данные файла из базы данных
                _, _, _, original_filename, created_at, file_data = voice_info

                # Создаем временный файл
                temp_file = os.path.join(TEMP_DIR, f"temp_{original_filename}")
                with open(temp_file, 'wb') as f:
                    f.write(file_data)

                # Отправляем голосовое сообщение
                with open(temp_file, 'rb') as voice_file:
                    sent_message = await context.bot.send_voice(
                        chat_id=user_id,
                        voice=voice_file
                    )

                # Логируем успешную отправку голосового
                log_message(
                    user_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                    message_type="voice_sent",
                    message_data={"voice_id": voice_id, "sent_message_id": sent_message.message_id}
                )

                # Удаляем временный файл
                if os.path.exists(temp_file):
                    os.remove(temp_file)

                # Возвращаемся к списку
                await query.edit_message_text(
                    text="Ваши сохраненные голосовые сообщения:",
                    reply_markup=get_voices_menu(user_id)
                )
            except Exception as e:
                logger.error(f"Ошибка при отправке сохраненного голосового: {e}")

                # Логируем ошибку
                log_message(
                    user_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                    message_type="voice_error",
                    message_data={"voice_id": voice_id, "error": str(e)},
                    error_flag=True
                )

                await query.edit_message_text(
                    text=f"Произошла ошибка при отправке голосового сообщения: {e}",
                    reply_markup=get_voices_menu(user_id)
                )
        else:
            # Логируем ошибку "голосовое не найдено"
            log_message(
                user_id=user.id,
                username=user.username,
                first_name=user.first_name,
                message_type="voice_not_found",
                message_data={"voice_id": voice_id},
                error_flag=True
            )

            await query.edit_message_text(
                text="Голосовое сообщение не найдено. Возможно, оно было удалено.",
                reply_markup=get_voices_menu(user_id)
            )


# Обработчик MP3 файлов
async def handle_mp3(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.message.from_user
    user_id = user.id
    logger.info(f"Пользователь {user.username} отправил MP3 файл")

    # Получаем информацию о файле
    audio_file = update.message.audio or update.message.document

    # Логируем получение файла
    file_info = {
        "file_id": audio_file.file_id,
        "file_name": getattr(audio_file, 'file_name', "unknown.mp3"),
        "file_size": getattr(audio_file, 'file_size', 0),
        "mime_type": getattr(audio_file, 'mime_type', "unknown")
    }

    log_message(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        message_type="mp3_upload",
        message_data=file_info
    )

    if not audio_file:
        # Логируем ошибку "не MP3 файл" с флагом ошибки
        log_message(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            message_type="error",
            message_text="Отправлен не аудио файл",
            error_flag=True
        )

        await update.message.reply_text(
            "Пожалуйста, отправьте MP3 файл.",
            reply_markup=get_main_menu(user_id)
        )
        return

    # Проверяем, что это MP3 файл
    file_name = audio_file.file_name if hasattr(audio_file, 'file_name') else "audio.mp3"
    if not file_name.lower().endswith('.mp3') and (
            update.message.document and not audio_file.mime_type == "audio/mpeg"):
        # Логируем ошибку "не MP3 формат" с флагом ошибки
        log_message(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            message_type="error",
            message_text=f"Файл {file_name} не в формате MP3",
            error_flag=True
        )

        await update.message.reply_text(
            "Пожалуйста, отправьте файл в формате MP3.",
            reply_markup=get_main_menu(user_id)
        )
        return

    # Сообщаем пользователю, что начали обработку
    process_msg = await update.message.reply_text("Обрабатываю ваш MP3 файл...")

    # Скачиваем файл
    new_file = await context.bot.get_file(audio_file.file_id)

    # Создаем имя файла с временной меткой
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Путь к временному файлу MP3
    temp_mp3_path = os.path.join(TEMP_DIR, f"{timestamp}_{file_name}")

    # Скачиваем MP3 файл
    await new_file.download_to_drive(temp_mp3_path)

    try:
        # Логируем начало обработки
        log_message(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            message_type="processing_started",
            message_data={"file_path": temp_mp3_path}
        )

        # Читаем содержимое MP3 файла для сохранения в базе
        with open(temp_mp3_path, 'rb') as mp3_file:
            file_data = mp3_file.read()

        # Отправляем файл как голосовое сообщение
        with open(temp_mp3_path, 'rb') as voice_file:
            voice_message = await update.message.reply_voice(
                voice=voice_file,
                filename=file_name
            )

        # Сохраняем информацию о голосовом сообщении в базу данных
        voice_id = save_voice_message(
            user_id=user_id,
            file_id=voice_message.voice.file_id,
            original_filename=file_name,
            file_data=file_data
        )

        # Логируем успешное создание голосового
        log_message(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            message_type="voice_created",
            message_data={"voice_id": voice_id, "original_filename": file_name}
        )

        logger.info(f"Файл успешно отправлен пользователю {user.username} и сохранен в базу (ID: {voice_id})")

        # Удаляем сообщение о обработке и отправляем сообщение с главным меню
        await process_msg.delete()
        await update.message.reply_text(
            "Голосовое сообщение успешно создано и сохранено!",
            reply_markup=get_main_menu(user_id)
        )

    except Exception as e:
        logger.error(f"Ошибка при обработке файла: {e}")

        # Логируем ошибку с флагом ошибки
        log_message(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            message_type="error",
            message_text=f"Ошибка при обработке файла: {e}",
            error_flag=True
        )

        await process_msg.delete()
        await update.message.reply_text(
            f"Произошла ошибка при обработке файла: {e}",
            reply_markup=get_main_menu(user_id)
        )
    finally:
        # Удаляем временный файл
        if os.path.exists(temp_mp3_path):
            os.remove(temp_mp3_path)


# Обработчик для прочих сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id

    # Логируем текстовое сообщение
    if update.message.text:
        log_message(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            message_type="text",
            message_text=update.message.text
        )
    else:
        # Логируем другие типы сообщений как ошибки (не MP3)
        message_type = "unknown"
        if update.message.photo:
            message_type = "photo"
        elif update.message.video:
            message_type = "video"
        elif update.message.sticker:
            message_type = "sticker"

        log_message(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            message_type=message_type,
            error_flag=True
        )

    await update.message.reply_text(
        "Отправьте мне MP3 файл, чтобы преобразовать его в голосовое сообщение, или воспользуйтесь меню:",
        reply_markup=get_main_menu(user_id)
    )


# Функция для просмотра логов (админ-функция)
async def view_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id

    # Проверяем, что команду вызвал администратор
    if user_id != ADMIN_ID:
        return

    # Получаем последние 20 записей из лога
    logs = get_all_logs(limit=20)
    logs_text = format_logs_for_display(logs)

    await update.message.reply_text(
        logs_text,
        reply_markup=get_admin_logs_menu()
    )


def main() -> None:
    """Запуск бота"""
    # Проверяем и мигрируем базу данных при необходимости
    migrate_database()

    # Создаем бота и обработчики
    application = Application.builder().token(TOKEN).build()

    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("logs", view_logs))  # Админ-команда для просмотра логов

    # Обработка MP3 файлов
    application.add_handler(MessageHandler(filters.AUDIO | filters.Document.AUDIO, handle_mp3))

    # Обработка кнопок меню
    application.add_handler(CallbackQueryHandler(button_callback))

    # Ловим все остальные сообщения
    application.add_handler(MessageHandler(filters.ALL, handle_message))

    # Запускаем бота
    logger.info("Бот запущен")
    application.run_polling()


if __name__ == "__main__":
    main()