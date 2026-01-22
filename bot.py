import logging
import datetime
import uuid
from telegram import (
    Update, KeyboardButton, ReplyKeyboardMarkup,
    InlineKeyboardButton, InlineKeyboardMarkup, Contact
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# Custom Modules
import config
import geofence
import menus 
import languages
from keep_alive import keep_alive

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- STORAGE ---
# NOTE: On Render Free Tier, this resets if the bot restarts.
# For permanent storage, you need a database (MongoDB).
user_data = {}           # Stores cart, phone, lang
username_map = {}        # Maps username -> chat_id

ADMIN_USERNAME = "kanzedin"
SERVICE_MODE = 'AUTO' 

# --- HELPERS ---
def get_user_lang(chat_id):
    return user_data.get(chat_id, {}).get('lang', 'en')

def t(chat_id, key):
    lang = get_user_lang(chat_id)
    return languages.get_text(lang, key)

def is_admin(update: Update) -> bool:
    if not update.effective_user: return False
    return update.effective_user.username and update.effective_user.username.lower() == ADMIN_USERNAME.lower()

def is_open() -> bool:
    if SERVICE_MODE == 'OPEN': return True
    if SERVICE_MODE == 'CLOSED': return False
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=3)
    return config.OPEN_HOUR <= now.hour < config.CLOSE_HOUR

# --- HANDLERS ---

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    if user.username:
        username_map[user.username.lower()] = chat_id

    # Initialize profile if new
    if chat_id not in user_data:
        user_data[chat_id] = {
            'lang': None, 
            'phone': None, 
            'orders': {}, 
            'current_cafe': None,
            'location': None
        }

    # Step 1: Language Selection
    if not user_data[chat_id]['lang']:
        keyboard = ReplyKeyboardMarkup([['🇺🇸 English', '🇪🇹 አማርኛ']], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(languages.TEXTS['am']['choose_lang'], reply_markup=keyboard)
        return

    # Admin Panel
    if is_admin(update):
        await update.message.reply_text("👑 Admin: /open, /close, /auto, /dm, /broadcast, /stats")

    # Step 2: Check Open/Close
    if not is_open():
        await update.message.reply_text(t(chat_id, 'closed'))
        return

    # Step 3: Phone Number
    if not user_data[chat_id].get("phone"):
        btn_text = t(chat_id, 'btn_phone')
        kb = ReplyKeyboardMarkup([[KeyboardButton(btn_text, request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(t(chat_id, 'ask_phone'), reply_markup=kb)
        return

    await show_cafe_menu(update)

async def set_language(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text

    if "English" in text:
        user_data.setdefault(chat_id, {})['lang'] = 'en'
    elif "አማርኛ" in text:
        user_data.setdefault(chat_id, {})['lang'] = 'am'
    else:
        return # Ignore other text during setup

    await update.message.reply_text(t(chat_id, 'welcome'))
    await start(update, ctx) # Proceed to next step

async def contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.message.contact:
        user_data.setdefault(chat_id, {})['phone'] = update.message.contact.phone_number
        await update.message.reply_text(t(chat_id, 'phone_saved'))
        await start(update, ctx)

async def show_cafe_menu(update: Update):
    chat_id = update.effective_chat.id
    cafés = list(menus.CAFES.keys())
    # Arrange buttons
    kb = ReplyKeyboardMarkup([[c] for c in cafés], resize_keyboard=True)
    
    # Reset temp state
    user_data[chat_id]['current_cafe'] = None
    user_data[chat_id]['awaiting_location'] = False
        
    await update.message.reply_text(t(chat_id, 'choose_cafe'), reply_markup=kb)

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    
    # Handle Language Selection first
    if text in ['🇺🇸 English', '🇪🇹 አማርኛ']:
        await set_language(update, ctx)
        return

    # Ensure profile exists
    if chat_id not in user_data or not user_data[chat_id].get('lang'):
        await start(update, ctx)
        return

    lang = user_data[chat_id]['lang']
    data = user_data[chat_id]

    # Global Buttons
    if text == t(chat_id, 'btn_back'):
        await show_cafe_menu(update)
        return
    
    if text == t(chat_id, 'btn_cancel'):
        data['orders'] = {}
        data['current_cafe'] = None
        await update.message.reply_text(t(chat_id, 'order_cancelled'))
        await show_cafe_menu(update)
        return

    # Cafe Selection
    if not data.get("current_cafe"):
        if text in menus.CAFES:
            data['current_cafe'] = text
            await show_cafe_items(update, text)
        return

    # Finish Order
    if text == t(chat_id, 'btn_done'):
        if not data['orders']:
            await update.message.reply_text(t(chat_id, 'cart_empty'))
            return
        
        await request_location(update)
        return

    # Add Item
    try:
        if " — " not in text: return
        item, _, _ = text.partition(" — ")
        current_cafe = data['current_cafe']
        
        if current_cafe not in menus.CAFES or item not in menus.CAFES[current_cafe]: return
        if menus.CAFES[current_cafe][item] is None: return

        key = (current_cafe, item)
        data['orders'][key] = data['orders'].get(key, 0) + 1
        
        msg = t(chat_id, 'added_cart').format(item, data['orders'][key])
        await update.message.reply_text(msg)
    except:
        pass

async def show_cafe_items(update: Update, cafe_name: str):
    chat_id = update.effective_chat.id
    menu = menus.CAFES[cafe_name]
    keyboard = []
    
    for item, price in menu.items():
        if price is None:
            keyboard.append([item])
        else:
            keyboard.append([f"{item} — {price} ETB"])
    
    # Add navigation buttons in correct language
    keyboard += [[t(chat_id, 'btn_done')], [t(chat_id, 'btn_cancel')], [t(chat_id, 'btn_back')]]
    
    header = t(chat_id, 'menu_header').format(cafe_name)
    await update.message.reply_text(header, reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def request_location(update: Update):
    chat_id = update.effective_chat.id
    data = user_data[chat_id]
    
    # Calculate Total
    total = 39
    lines = []
    for (cafe, item), qty in data['orders'].items():
        price = menus.CAFES[cafe][item]
        total += price * qty
        lines.append(f"{item} x{qty}")

    summary = "\n".join(lines)
    txt_total = t(chat_id, 'total')
    txt_del = t(chat_id, 'delivery_fee')
    
    msg = f"{summary}\n\n{txt_del}: 39 ETB\n*{txt_total}: {total} ETB*"
    
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton(t(chat_id, 'btn_location'), request_location=True)], 
         [t(chat_id, 'btn_cancel'), t(chat_id, 'btn_back')]],
        resize_keyboard=True, one_time_keyboard=True
    )
    
    data['awaiting_location'] = True
    await update.message.reply_text(msg + "\n\n" + t(chat_id, 'ask_location'), reply_markup=kb, parse_mode="Markdown")

async def location(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    data = user_data.get(chat_id)
    if not data or not data.get("awaiting_location"): return

    data['awaiting_location'] = False
    
    # Check Geofence
    lat = update.message.location.latitude
    lon = update.message.location.longitude
    if not geofence.in_werabe(lat, lon):
        await update.message.reply_text(t(chat_id, 'location_error'))
        return

    # Store Location (Profile Update)
    data['location'] = {'lat': lat, 'lon': lon}

    # Generate Order
    order_id = str(uuid.uuid4())[:8].upper()
    
    # Admin Notification (Always in English/Standard format)
    mapslink = f"https://www.google.com/maps?q={lat},{lon}"
    cart_summary = "\n".join([f"{i} ({c})" for (c, i), q in data['orders'].items()])
    
    admin_msg = f"""📦 *NEW ORDER* `{order_id}`
👤 {update.effective_user.full_name} (@{update.effective_user.username})
📞 {data['phone']}
📍 [Map Location]({mapslink})
🛒 {cart_summary}
"""
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accept", callback_data=f"accept_{chat_id}_{order_id}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"decline_{chat_id}_{order_id}")
    ]])
    await ctx.bot.send_message(config.CHANNEL_ID, admin_msg, parse_mode='Markdown', reply_markup=kb)

    # User Confirmation
    await update.message.reply_text(t(chat_id, 'order_sent').format(order_id), parse_mode="Markdown")
    
    # Clear Cart
    data['orders'] = {}
    data['current_cafe'] = None
    await show_cafe_menu(update)

# --- ADMIN NOTIFICATIONS ---
async def admin_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    msg = update.message.text.replace("/broadcast", "").strip()
    if not msg: return
    
    count = 0
    for uid, udata in user_data.items():
        lang = udata.get('lang', 'en')
        prefix = languages.get_text(lang, 'admin_broadcast').format(msg)
        try:
            await ctx.bot.send_message(uid, prefix)
            count += 1
        except: pass
    await update.message.reply_text(f"Sent to {count} users.")

def main():
    keep_alive()
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", admin_broadcast))
    app.add_handler(MessageHandler(filters.CONTACT, contact))
    app.add_handler(MessageHandler(filters.LOCATION, location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    # Add callback handler for accept/decline here (same as previous code)
    
    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
