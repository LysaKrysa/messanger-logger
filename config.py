import os

USE_TELEGRAM = os.environ.get('USE_TELEGRAM', 'false').lower() == 'true'
if USE_TELEGRAM:
    import telepot
    bot = telepot.Bot(os.environ['TELEGRAM_BOT_TOKEN'])
    bot_chat_id = os.environ['TELEGRAM_CHAT_ID']
