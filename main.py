import os
import json
import logging
import asyncio
import base64
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Файли для зберігання даних
MESSAGES_FILE = 'messages.json'
GROUPS_FILE = 'groups.json'
ADMINS_FILE = 'admins.json'

class SimpleBroadcastBot:
    def __init__(self, token):
        self.token = token
        self.application = Application.builder().token(token).build()
        self.scheduler = AsyncIOScheduler()
        self.setup_handlers()
        self.load_data()
        self.broadcast_in_progress = False
        self.auto_broadcast_active = False
        self.current_message_index = 0
        
    def setup_handlers(self):
        """Налаштування обробників команд"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("add_message", self.add_message))
        self.application.add_handler(CommandHandler("list_messages", self.list_messages))
        self.application.add_handler(CommandHandler("delete_message", self.delete_message))
        self.application.add_handler(CommandHandler("broadcast", self.broadcast))
        self.application.add_handler(CommandHandler("start_auto", self.start_auto))
        self.application.add_handler(CommandHandler("stop_auto", self.stop_auto))
        self.application.add_handler(CommandHandler("add_admin", self.add_admin))
        self.application.add_handler(CommandHandler("status", self.status))
        self.application.add_handler(CommandHandler("skip_photo", self.skip_photo))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        
    def load_data(self):
        """Завантаження даних з файлів"""
        try:
            # Повідомлення
            if os.path.exists(MESSAGES_FILE):
                with open(MESSAGES_FILE, 'r', encoding='utf-8') as f:
                    self.messages = json.load(f)
                logger.info(f"Завантажено {len(self.messages)} повідомлень")
            else:
                self.messages = []
                logger.info("Файл повідомлень не знайдено, створено новий список")
                
            # Групи
            if os.path.exists(GROUPS_FILE):
                with open(GROUPS_FILE, 'r', encoding='utf-8') as f:
                    self.groups = json.load(f)
                logger.info(f"Завантажено {len(self.groups)} груп")
            else:
                self.groups = []
                logger.info("Файл груп не знайдено, створено новий список")
                
            # Адміни
            if os.path.exists(ADMINS_FILE):
                with open(ADMINS_FILE, 'r', encoding='utf-8') as f:
                    admins_data = json.load(f)
                    logger.info(f"Завантажено адмінів: {admins_data} (тип: {type(admins_data)})")
                    
                if isinstance(admins_data, list):
                    self.admins = [str(admin) for admin in admins_data]
                elif isinstance(admins_data, (int, str)):
                    self.admins = [str(admins_data)]
                else:
                    self.admins = []
                    logger.warning("Невідомий тип даних для адмінів, створено порожній список")
            else:
                self.admins = []
                logger.info("Файл адмінів не знайдено, створено новий список")
                
            logger.info(f"Підсумковий список адмінів: {self.admins}")
                
        except Exception as e:
            logger.error(f"Помилка завантаження даних: {e}")
            self.messages = []
            self.groups = []
            self.admins = []
            
    def save_data(self, data_type):
        """Збереження даних у файли"""
        try:
            if data_type == 'messages':
                with open(MESSAGES_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self.messages, f, ensure_ascii=False, indent=2)
            elif data_type == 'groups':
                with open(GROUPS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self.groups, f, ensure_ascii=False, indent=2)
            elif data_type == 'admins':
                with open(ADMINS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self.admins, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Помилка збереження даних: {e}")
    
    def is_admin(self, user_id):
        """Перевірка, чи є користувач адміном"""
        try:
            user_id_str = str(user_id)
            
            if not isinstance(self.admins, list):
                logger.error(f"self.admins не є списком! Тип: {type(self.admins)}, значення: {self.admins}")
                if isinstance(self.admins, (int, str)):
                    self.admins = [str(self.admins)]
                else:
                    self.admins = []
                self.save_data('admins')
                
            return user_id_str in self.admins
        except Exception as e:
            logger.error(f"Помилка в is_admin: {e}")
            return False

    async def start_auto_broadcast(self):
        """Запуск автоматичної розсилки"""
        if self.auto_broadcast_active:
            logger.info("Авто-розсилка вже активна")
            return
            
        self.auto_broadcast_active = True
        
        # Додаємо завдання кожну хвилину
        trigger = IntervalTrigger(minutes=1)
        self.scheduler.add_job(
            self.single_auto_broadcast,
            trigger=trigger,
            id='auto_broadcast',
            replace_existing=True
        )
        
        if not self.scheduler.running:
            self.scheduler.start()
            
        logger.info("⏰ Авто-розсилка запущена - кожну хвилину")

    async def single_auto_broadcast(self):
        """Одна автоматична розсилка одного повідомлення"""
        try:
            if not self.auto_broadcast_active or not self.messages or not self.groups:
                return
            
            bot = self.application.bot
            
            # Отримуємо поточне повідомлення
            if self.current_message_index >= len(self.messages):
                self.current_message_index = 0
            
            message_data = self.messages[self.current_message_index]
            
            success_count = 0
            total_groups = len(self.groups)
            
            logger.info(f"🤖 Авто-розсилка повідомлення {self.current_message_index + 1}/{len(self.messages)}")
            
            # Розсилаємо поточне повідомлення
            for group in self.groups:
                try:
                    if message_data.get('has_photo') and message_data.get('photo_base64'):
                        # Декодуємо фото з base64
                        photo_data = base64.b64decode(message_data['photo_base64'])
                        
                        await bot.send_photo(
                            chat_id=group['chat_id'],
                            photo=photo_data,
                            caption=message_data['text']
                        )
                    else:
                        await bot.send_message(
                            chat_id=group['chat_id'],
                            text=message_data['text']
                        )
                    success_count += 1
                    
                    # Невелика затримка між відправками
                    await asyncio.sleep(0.5)
                    
                except Exception as e:
                    logger.error(f"❌ Помилка авто-відправки в групу {group['title']}: {e}")
            
            # Оновлюємо індекс для наступного повідомлення
            self.current_message_index = (self.current_message_index + 1) % len(self.messages)
            
            logger.info(f"✅ Авто-розсилка завершена. Успішно: {success_count}/{total_groups}")
            
        except Exception as e:
            logger.error(f"💥 Помилка в single_auto_broadcast: {e}")

    async def start_auto(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Запуск автоматичної розсилки"""
        try:
            user_id = update.effective_user.id
            
            if not self.is_admin(user_id):
                await update.message.reply_text("❌ У вас немає прав для цієї команди")
                return
            
            if not self.messages:
                await update.message.reply_text(
                    "❌ Немає повідомлень для розсилки!\n"
                    "Спочатку додайте повідомлення командою /add_message"
                )
                return
                
            if not self.groups:
                await update.message.reply_text(
                    "❌ Немає груп для розсилки!\n"
                    "Додайте бота в групу та надішліть /start в цій групі"
                )
                return
            
            if self.auto_broadcast_active:
                await update.message.reply_text("ℹ️ Авто-розсилка вже активна")
                return
            
            await self.start_auto_broadcast()
            
            await update.message.reply_text(
                f"✅ Авто-розсилка запущена!\n\n"
                f"📊 Статистика:\n"
                f"• Повідомлень: {len(self.messages)}\n"
                f"• Груп: {len(self.groups)}\n"
                f"• Інтервал: кожну хвилину\n\n"
                f"🤖 Тепер бот автоматично розсилатиме повідомлення по черзі.\n"
                f"⏹️ Зупинити: /stop_auto"
            )
        except Exception as e:
            logger.error(f"Помилка в start_auto: {e}")
            await update.message.reply_text("❌ Помилка при запуску авто-розсилки")

    async def stop_auto(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Зупинка автоматичної розсилки"""
        try:
            user_id = update.effective_user.id
            
            if not self.is_admin(user_id):
                await update.message.reply_text("❌ У вас немає прав для цієї команди")
                return
            
            if not self.auto_broadcast_active:
                await update.message.reply_text("ℹ️ Авто-розсилка вже зупинена")
                return
            
            self.auto_broadcast_active = False
            self.scheduler.remove_job('auto_broadcast')
            
            await update.message.reply_text(
                "🛑 Авто-розсилка зупинена!\n"
                "Для повторного запуску використайте /start_auto\n"
                "Для разової розсилки: /broadcast"
            )
        except Exception as e:
            logger.error(f"Помилка в stop_auto: {e}")
            await update.message.reply_text("❌ Помилка при зупинці авто-розсилки")
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обробка команди /start"""
        try:
            user_id = update.effective_user.id
            
            if update.message.chat.type in ['group', 'supergroup']:
                chat_id = update.message.chat.id
                chat_title = update.message.chat.title
                
                group_info = {
                    'chat_id': chat_id,
                    'title': chat_title,
                    'added_date': datetime.now().isoformat()
                }
                
                if not any(g.get('chat_id') == chat_id for g in self.groups):
                    self.groups.append(group_info)
                    self.save_data('groups')
                    await update.message.reply_text(
                        f"✅ Групу '{chat_title}' додано для розсилки!\n"
                        f"ID групи: {chat_id}\n\n"
                        f"Тепер адміни можуть робити розсилку в цю групу."
                    )
                else:
                    await update.message.reply_text("ℹ️ Ця група вже додана для розсилки")
                    
            else:
                if not self.admins:
                    self.admins = [str(user_id)]
                    self.save_data('admins')
                    await update.message.reply_text(
                        f"👋 Вітаю! Ви перший користувач, тому тепер ви адмін!\n"
                        f"Ваш user_id: {user_id}\n\n"
                        f"Тепер ви можете додавати повідомлення для розсилки."
                    )
                    return
                
                if self.is_admin(user_id):
                    auto_status = "🟢 УВІМКНЕНА" if self.auto_broadcast_active else "🔴 ВИМКНЕНА"
                    
                    await update.message.reply_text(
                        f"👋 Вітаю, адміне!\n\n"
                        f"📊 Статистика:\n"
                        f"• Повідомлень: {len(self.messages)}\n"
                        f"• Груп: {len(self.groups)}\n"
                        f"• Авто-розсилка: {auto_status}\n\n"
                        "📋 Доступні команди:\n"
                        "/add_message - додати повідомлення (текст + фото)\n"
                        "/list_messages - список повідомлень\n"
                        "/delete_message [id] - видалити повідомлення\n"
                        "/broadcast - зробити разову розсилку всіх повідомлень\n"
                        "/start_auto - увімкнути авто-розсилку (кожну хвилину)\n"
                        "/stop_auto - вимкнути авто-розсилку\n"
                        "/add_admin [user_id] - додати адміна\n"
                        "/status - статус бота\n\n"
                        "📝 Як додати повідомлення:\n"
                        "1. Використайте /add_message\n"
                        "2. Надішліть текст повідомлення\n"
                        "3. Надішліть фото (опціонально)\n"
                        "4. Повідомлення збережеться для майбутніх розсилок"
                    )
                else:
                    await update.message.reply_text(
                        f"❌ У вас немає прав адміністратора\n"
                        f"Ваш user_id: {user_id}\n"
                        f"Поточні адміни: {', '.join(self.admins)}\n"
                        f"Зверніться до адміна для додавання."
                    )
        except Exception as e:
            logger.error(f"Помилка в команді /start: {e}")
            await update.message.reply_text("❌ Сталася помилка. Спробуйте ще раз.")
    
    async def add_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Додавання повідомлення"""
        try:
            user_id = update.effective_user.id
            
            if not self.is_admin(user_id):
                await update.message.reply_text("❌ У вас немає прав для цієї команди")
                return
            
            context.user_data['adding_message'] = True
            context.user_data['message_step'] = 'text'
            await update.message.reply_text(
                "📝 Надішліть текст повідомлення для розсилки:\n\n"
                "ℹ️ Після тексту ви зможете додати фото (необов'язково)"
            )
            
        except Exception as e:
            logger.error(f"Помилка в add_message: {e}")
            await update.message.reply_text("❌ Помилка при додаванні повідомлення")
    
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обробка текстового повідомлення"""
        try:
            user_id = update.effective_user.id
            
            if not self.is_admin(user_id) or update.message.chat.type in ['group', 'supergroup']:
                return
            
            if context.user_data.get('adding_message') and context.user_data.get('message_step') == 'text':
                text = update.message.text
                context.user_data['pending_text'] = text
                context.user_data['message_step'] = 'photo'
                
                await update.message.reply_text(
                    f"✅ Текст збережено!\n\n"
                    f"📝 Текст: {text}\n\n"
                    f"Тепер надішліть ФОТО для цього повідомлення.\n"
                    f"Якщо не хочете додавати фото, надішліть /skip_photo"
                )
                
        except Exception as e:
            logger.error(f"Помилка в handle_text: {e}")
    
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обробка фото"""
        try:
            user_id = update.effective_user.id
            
            if not self.is_admin(user_id) or update.message.chat.type in ['group', 'supergroup']:
                return
            
            if context.user_data.get('adding_message') and context.user_data.get('message_step') == 'photo':
                text = context.user_data.get('pending_text', '')
                
                if not text:
                    await update.message.reply_text("❌ Спочатку надішліть текст повідомлення!")
                    return
                
                # Зберігаємо фото в base64
                photo_file = await update.message.photo[-1].get_file()
                photo_bytes = await photo_file.download_as_bytearray()
                photo_base64 = base64.b64encode(photo_bytes).decode('utf-8')
                
                # Зберігаємо повідомлення
                message_data = {
                    'id': len(self.messages) + 1,
                    'text': text,
                    'photo_base64': photo_base64,
                    'has_photo': True,
                    'created_date': datetime.now().isoformat(),
                    'created_by': user_id
                }
                
                self.messages.append(message_data)
                self.save_data('messages')
                
                # Очищаємо тимчасові дані
                context.user_data.pop('adding_message', None)
                context.user_data.pop('message_step', None)
                context.user_data.pop('pending_text', None)
                
                await update.message.reply_text(
                    f"✅ Повідомлення з фото додано!\n\n"
                    f"📝 Текст: {text}\n"
                    f"🖼️ Фото: додано\n"
                    f"📊 ID: {message_data['id']}\n\n"
                    f"Тепер ви можете зробити розсилку командою /broadcast"
                )
                
        except Exception as e:
            logger.error(f"Помилка в handle_photo: {e}")
            await update.message.reply_text("❌ Помилка при додаванні фото")
    
    async def skip_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Пропуск додавання фото"""
        try:
            user_id = update.effective_user.id
            
            if not self.is_admin(user_id):
                await update.message.reply_text("❌ У вас немає прав для цієї команди")
                return
            
            if context.user_data.get('adding_message') and context.user_data.get('message_step') == 'photo':
                text = context.user_data.get('pending_text', '')
                
                if not text:
                    await update.message.reply_text("❌ Спочатку надішліть текст повідомлення!")
                    return
                
                # Зберігаємо повідомлення без фото
                message_data = {
                    'id': len(self.messages) + 1,
                    'text': text,
                    'photo_base64': None,
                    'has_photo': False,
                    'created_date': datetime.now().isoformat(),
                    'created_by': user_id
                }
                
                self.messages.append(message_data)
                self.save_data('messages')
                
                # Очищаємо тимчасові дані
                context.user_data.pop('adding_message', None)
                context.user_data.pop('message_step', None)
                context.user_data.pop('pending_text', None)
                
                await update.message.reply_text(
                    f"✅ Повідомлення додано (без фото)!\n\n"
                    f"📝 Текст: {text}\n"
                    f"🖼️ Фото: відсутнє\n"
                    f"📊 ID: {message_data['id']}\n\n"
                    f"Тепер ви можете зробити розсилку командою /broadcast"
                )
            else:
                await update.message.reply_text("❌ Не знайдено активного процесу додавання повідомлення")
                
        except Exception as e:
            logger.error(f"Помилка в skip_photo: {e}")
            await update.message.reply_text("❌ Помилка при додаванні повідомлення")
    
    async def list_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Список всіх повідомлень"""
        try:
            user_id = update.effective_user.id
            
            if not self.is_admin(user_id):
                await update.message.reply_text("❌ У вас немає прав для цієї команди")
                return
                
            if not self.messages:
                await update.message.reply_text("📭 Немає збережених повідомлень")
                return
                
            response = "📋 Список повідомлень для розсилки:\n\n"
            for msg in self.messages:
                has_photo = "✅" if msg.get('has_photo') else "❌"
                response += f"🔹 ID: {msg['id']}\n"
                response += f"📝 Текст: {msg['text'][:80]}...\n"
                response += f"🖼️ Фото: {has_photo}\n"
                response += f"📅 Дата: {msg['created_date'][:10]}\n"
                response += "─" * 30 + "\n"
                
            response += f"\n🗑️ Для видалення: /delete_message [id]"
            response += f"\n📤 Для розсилки: /broadcast"
            response += f"\n🤖 Для авто-розсилки: /start_auto"
                
            await update.message.reply_text(response)
        except Exception as e:
            logger.error(f"Помилка в list_messages: {e}")
            await update.message.reply_text("❌ Помилка при отриманні списку повідомлень")
    
    async def delete_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Видалення повідомлення"""
        try:
            user_id = update.effective_user.id
            
            if not self.is_admin(user_id):
                await update.message.reply_text("❌ У вас немає прав для цієї команди")
                return
                
            if not context.args:
                await update.message.reply_text(
                    "❌ Вкажіть ID повідомлення: /delete_message [id]\n"
                    "Список повідомлень: /list_messages"
                )
                return
                
            try:
                message_id = int(context.args[0])
                message_to_delete = None
                
                for msg in self.messages:
                    if msg['id'] == message_id:
                        message_to_delete = msg
                        break
                
                if message_to_delete:
                    self.messages.remove(message_to_delete)
                    self.save_data('messages')
                    await update.message.reply_text(f"✅ Повідомлення ID {message_id} видалено!")
                else:
                    await update.message.reply_text(f"❌ Повідомлення з ID {message_id} не знайдено")
                    
            except ValueError:
                await update.message.reply_text("❌ ID повинен бути числом")
                
        except Exception as e:
            logger.error(f"Помилка в delete_message: {e}")
            await update.message.reply_text("❌ Помилка при видаленні повідомлення")
    
    async def broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Разова розсилка всіх повідомлень"""
        try:
            user_id = update.effective_user.id
            
            if not self.is_admin(user_id):
                await update.message.reply_text("❌ У вас немає прав для цієї команди")
                return
            
            if self.broadcast_in_progress:
                await update.message.reply_text("⏳ Розсилка вже виконується. Зачекайте...")
                return
            
            if not self.messages:
                await update.message.reply_text(
                    "❌ Немає повідомлень для розсилки!\n"
                    "Спочатку додайте повідомлення командою /add_message"
                )
                return
                
            if not self.groups:
                await update.message.reply_text(
                    "❌ Немає груп для розсилки!\n"
                    "Додайте бота в групу та надішліть /start в цій групі"
                )
                return
            
            self.broadcast_in_progress = True
            progress_msg = await update.message.reply_text("🔄 Початок розсилки...")
            
            bot = self.application.bot
            success_count = 0
            total_groups = len(self.groups)
            
            # Розсилаємо всі повідомлення по черзі
            for message_index, message_data in enumerate(self.messages, 1):
                message_success = 0
                
                for group_index, group in enumerate(self.groups, 1):
                    try:
                        if message_data.get('has_photo') and message_data.get('photo_base64'):
                            # Декодуємо фото з base64
                            photo_data = base64.b64decode(message_data['photo_base64'])
                            
                            await bot.send_photo(
                                chat_id=group['chat_id'],
                                photo=photo_data,
                                caption=message_data['text']
                            )
                        else:
                            await bot.send_message(
                                chat_id=group['chat_id'],
                                text=message_data['text']
                            )
                        message_success += 1
                        logger.info(f"✅ Повідомлення {message_index} відправлено в {group['title']} ({message_success}/{total_groups})")
                        
                        await asyncio.sleep(0.5)
                        
                    except Exception as e:
                        logger.error(f"❌ Помилка відправки в групу {group['title']}: {e}")
                
                success_count += message_success
                
                await progress_msg.edit_text(
                    f"📤 Розсилка...\n"
                    f"Повідомлення: {message_index}/{len(self.messages)}\n"
                    f"Успішних відправок: {success_count}/{(message_index) * total_groups}"
                )
            
            total_attempts = len(self.messages) * total_groups
            await progress_msg.edit_text(
                f"✅ Розсилка завершена!\n\n"
                f"📊 Результати:\n"
                f"• Повідомлень розіслано: {len(self.messages)}\n"
                f"• Груп отримувачів: {total_groups}\n"
                f"• Успішних відправок: {success_count}/{total_attempts}\n"
                f"• Невдалих: {total_attempts - success_count}\n\n"
                f"🔄 Щоб зробити ще одну розсилку, використайте /broadcast"
            )
            
            self.broadcast_in_progress = False
            
        except Exception as e:
            logger.error(f"Помилка в broadcast: {e}")
            await update.message.reply_text("❌ Помилка при розсилці")
            self.broadcast_in_progress = False
    
    async def add_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Додавання адміністратора"""
        try:
            user_id = update.effective_user.id
            
            if not self.is_admin(user_id):
                await update.message.reply_text("❌ У вас немає прав для цієї команди")
                return
                
            if not context.args:
                await update.message.reply_text("❌ Вкажіть ID користувача: /add_admin [user_id]")
                return
                
            new_admin_id = context.args[0]
            
            if not isinstance(self.admins, list):
                self.admins = []
                
            if new_admin_id not in self.admins:
                self.admins.append(new_admin_id)
                self.save_data('admins')
                await update.message.reply_text(f"✅ Користувача {new_admin_id} додано як адміна")
            else:
                await update.message.reply_text("ℹ️ Цей користувач вже є адміном")
        except Exception as e:
            logger.error(f"Помилка в add_admin: {e}")
            await update.message.reply_text("❌ Помилка при додаванні адміна")
    
    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Статус бота"""
        try:
            user_id = update.effective_user.id
            
            if not self.is_admin(user_id):
                await update.message.reply_text("❌ У вас немає прав для цієї команди")
                return
            
            status_text = "🟢 АКТИВНА" if self.broadcast_in_progress else "🔴 НЕАКТИВНА"
            auto_status = "🟢 УВІМКНЕНА" if self.auto_broadcast_active else "🔴 ВИМКНЕНА"
            
            messages_with_photo = sum(1 for msg in self.messages if msg.get('has_photo'))
            
            await update.message.reply_text(
                f"📊 Статус бота:\n\n"
                f"🔄 Розсилка: {status_text}\n"
                f"🤖 Авто-розсилка: {auto_status}\n"
                f"⏱️ Інтервал: кожну хвилину\n"
                f"📝 Повідомлень: {len(self.messages)}\n"
                f"🖼️ З фото: {messages_with_photo}\n"
                f"📍 Поточне: {self.current_message_index + 1}/{len(self.messages)}\n"
                f"👥 Груп: {len(self.groups)}\n"
                f"👮 Адмінів: {len(self.admins)}\n\n"
                f"{('▶️ Для розсилки: /broadcast' if not self.broadcast_in_progress else '⏳ Розсилка виконується...')}\n"
                f"{('▶️ Для авто-розсилки: /start_auto' if not self.auto_broadcast_active else '⏹️ Зупинити авто: /stop_auto')}"
            )
        except Exception as e:
            logger.error(f"Помилка в status: {e}")
            await update.message.reply_text("❌ Помилка при отриманні статусу")
    
    def run(self):
        """Запуск бота"""
        logger.info("Бот запущено!")
        logger.info(f"Початкові адміни: {self.admins}")
        self.application.run_polling()

# Запуск бота
if __name__ == "__main__":
    BOT_TOKEN = "8499995319:AAHBRnfL_KBgX_GthW1Yn0tFG-WRq1oiNw8"
    
    bot = SimpleBroadcastBot(BOT_TOKEN)
    bot.run()