import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')

# Chapa Payment Gateway
CHAPA_SECRET_KEY = os.getenv('Test_Secret_key')
CHAPA_PUBLIC_KEY = os.getenv('Test_Public_key')
CHAPA_WEBHOOK_URL = os.getenv('CHAPA_WEBHOOK_URL', '')  # Optional: Your webhook URL

# Werabe location boundaries
MIN_LAT, MAX_LAT = 7.8500, 8.0000
MIN_LON, MAX_LON = 38.0000, 38.2000

# Time window (UTC+3)
OPEN_HOUR, CLOSE_HOUR = 6, 22