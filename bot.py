import logging
import datetime
import random
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
# Note: Resets on Render restart. Use Database for permanence.
user_data = {}           
username_map = {}        

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

    # Initialize Profile
    if chat_id not in user_data:
        user_data[chat_id] = {
            'lang': None, 'phone': None, 'orders': {}, 
            'current_cafe': None, 'location': None
        }

    # 1. Language Selection
    if not user_data[chat_id]['lang']:
        keyboard = ReplyKeyboardMarkup([['🇺🇸 English', '🇪🇹 አማርኛ']], resize_keyboard=True, one_time_keyboard=True)
        # Safe fallback if 'am' key is missing, defaults to English text
        msg = languages.TEXTS['am'].get('choose_lang', "Please select language:")
        await update.message.reply_text(msg, reply_markup=keyboard)
        return

    # Admin Help
    if is_admin(update):
        await update.message.reply_text("👑 Admin: /open, /close, /auto, /broadcast")

    # 2. Check Open/Closed
    if not is_open():
        await update.message.reply_text(t(chat_id, 'closed'))
        return

    # 3. Check Phone
    if not user_data[chat_id].get("phone"):
        btn_text = t(chat_id, 'btn_phone')
        kb = ReplyKeyboardMarkup([[KeyboardButton(btn_text, request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(t(chat_id, 'ask_phone'), reply_markup=kb)
        return

    await show_main_menu(update)

async def show_main_menu(update: Update):
    chat_id = update.effective_chat.id
    
    # Reset Order State
    user_data[chat_id]['current_cafe'] = None
    user_data[chat_id]['awaiting_location'] = False
    
    # Build Menu
    cafés = list(menus.CAFES.keys())
    menu_buttons = [[c] for c in cafés]
    menu_buttons.append([t(chat_id, 'btn_profile')]) 
    
    kb = ReplyKeyboardMarkup(menu_buttons, resize_keyboard=True)
    await update.message.reply_text(t(chat_id, 'choose_cafe'), reply_markup=kb)

async def show_profile(update: Update):
    chat_id = update.effective_chat.id
    data = user_data[chat_id]
    
    phone = data.get('phone', 'N/A')
    loc_status = t(chat_id, 'location_set') if data.get('location') else t(chat_id, 'location_not_set')
    lang_name = "English" if data.get('lang') == 'en' else "አማርኛ"
    
    # Using format directly with safeguards
    msg = t(chat_id, 'profile_header').replace("{}", str(phone), 1).replace("{}", str(loc_status), 1)
    
    kb = ReplyKeyboardMarkup([
        [t(chat_id, 'btn_switch_lang'), t(chat_id, 'btn_edit_phone')],
        [t(chat_id, 'btn_back')]
    ], resize_keyboard=True)
    
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=kb)

async def set_language(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text

    if "English" in text:
        user_data.setdefault(chat_id, {})['lang'] = 'en'
    elif "አማርኛ" in text:
        user_data.setdefault(chat_id, {})['lang'] = 'am'
    
    await update.message.reply_text(t(chat_id, 'welcome'))
    await show_main_menu(update)

async def contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.message.contact:
        user_data.setdefault(chat_id, {})['phone'] = update.message.contact.phone_number
        await update.message.reply_text(t(chat_id, 'phone_saved'))
        await show_main_menu(update)

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    
    # Language Setup
    if text in ['🇺🇸 English', '🇪🇹 አማርኛ']:
        await set_language(update, ctx)
        return

    # Safety Check
    if chat_id not in user_data or not user_data[chat_id].get('lang'):
        await start(update, ctx)
        return

    lang = user_data[chat_id]['lang']
    data = user_data[chat_id]

    # Navigation
    if text == t(chat_id, 'btn_back'):
        await show_main_menu(update)
        return
    
    if text == t(chat_id, 'btn_profile'):
        await show_profile(update)
        return

    if text == t(chat_id, 'btn_switch_lang'):
        data['lang'] = None
        await start(update, ctx)
        return

    if text == t(chat_id, 'btn_edit_phone'):
        data['phone'] = None
        await start(update, ctx)
        return

    if text == t(chat_id, 'btn_cancel'):
        data['orders'] = {}
        data['current_cafe'] = None
        await update.message.reply_text(t(chat_id, 'order_cancelled'))
        await show_main_menu(update)
        return

    # Ordering Logic
    if not data.get("current_cafe"):
        if text in menus.CAFES:
            data['current_cafe'] = text
            await show_cafe_items(update, text)
        return

    if text == t(chat_id, 'btn_done'):
        if not data['orders']:
            await update.message.reply_text(t(chat_id, 'cart_empty'))
            return
        await request_location(update)
        return

    # Add Item to Cart
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
        if price is None: keyboard.append([item])
        else: keyboard.append([f"{item} — {price} ETB"])
    
    keyboard += [[t(chat_id, 'btn_done')], [t(chat_id, 'btn_cancel')], [t(chat_id, 'btn_back')]]
    header = t(chat_id, 'menu_header').format(cafe_name)
    await update.message.reply_text(header, reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def request_location(update: Update):
    chat_id = update.effective_chat.id
    data = user_data[chat_id]
    
    total = 39
    lines = []
    for (cafe, item), qty in data['orders'].items():
        price = menus.CAFES[cafe][item]
        total += price * qty
        lines.append(f"{item} x{qty}")

    summary = "\n".join(lines)
    msg = f"{summary}\n\n{t(chat_id, 'delivery_fee')}: 39 ETB\n*{t(chat_id, 'total')}: {total} ETB*"
    
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
    lat = update.message.location.latitude
    lon = update.message.location.longitude

    # Geofence Check
    if not geofence.in_werabe(lat, lon):
        await update.message.reply_text(t(chat_id, 'location_error'))
        return

    data['location'] = {'lat': lat, 'lon': lon}

    # Generate 8-digit Order ID
    order_id = f"#{random.randint(10000000, 99999999)}"
    
    # Calculate Total
    total_price = 39
    cart_items = []
    for (cafe, item), qty in data['orders'].items():
        price = menus.CAFES.get(cafe, {}).get(item, 0)
        total_price += price * qty
        cart_items.append(f"• {item} x{qty} ({cafe})")
    
    cart_summary = "\n".join(cart_items)
    
    customer_info = (
        f"👤 {update.effective_user.full_name}\n"
        f"📞 {data.get('phone', 'No Phone')}\n"
        f"@{update.effective_user.username or 'NoUsername'}"
    )

    try:
        # 1. Send Map as VENUE (with Order ID title)
        await ctx.bot.send_venue(
            chat_id=config.CHANNEL_ID,
            latitude=lat,
            longitude=lon,
            title=f"Order {order_id}",
            address=f"Customer: {update.effective_user.full_name}"
        )

        # 2. Send Order Details
        admin_msg = f"""📦 *ORDER {order_id}*
    
{customer_info}

🛒 *ITEMS:*
{cart_summary}

💵 *Total:* {total_price} ETB
"""
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Accept", callback_data=f"accept_{chat_id}_{order_id}"),
            InlineKeyboardButton("❌ Decline", callback_data=f"decline_{chat_id}_{order_id}")
        ]])
        
        await ctx.bot.send_message(
            chat_id=config.CHANNEL_ID, 
            text=admin_msg, 
            parse_mode='Markdown', 
            reply_markup=kb
        )

        await update.message.reply_text(t(chat_id, 'order_sent').format(order_id), parse_mode="Markdown")
        
        # Reset
        data['orders'] = {}
        data['current_cafe'] = None
        await show_main_menu(update)

    except Exception as e:
        logger.error(f"FAILED TO SEND ORDER: {e}") 
        await update.message.reply_text("❌ System Error. Please contact support or try again.")

async def accept_or_decline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if not (data.startswith("accept_") or data.startswith("decline_")): return

    try:
        action, uid_str, order_id = data.split("_")
        uid = int(uid_str)
    except: return

    msg = query.message
    admin_name = query.from_user.full_name

    if "✅" in msg.text or "❌" in msg.text: return

    if action == "accept":
        new_text = msg.text + f"\n\n✅ Accepted by {admin_name}"
        await query.message.edit_text(new_text, reply_markup=None)
        await ctx.bot.send_message(uid, t(uid, 'order_accepted').format(order_id), parse_mode="Markdown")
        
    elif action == "decline":
        new_text = msg.text + f"\n\n❌ Declined by {admin_name}"
        await query.message.edit_text(new_text, reply_markup=None)
        await ctx.bot.send_message(uid, t(uid, 'order_declined').format(order_id), parse_mode="Markdown")

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

async def admin_control(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global SERVICE_MODE
    if not is_admin(update): return
    cmd = update.message.text
    if "/open" in cmd: SERVICE_MODE = 'OPEN'
    elif "/close" in cmd: SERVICE_MODE = 'CLOSED'
    elif "/auto" in cmd: SERVICE_MODE = 'AUTO'
    await update.message.reply_text(f"Service Mode: {SERVICE_MODE}")

def main():
    keep_alive()
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", admin_broadcast))
    app.add_handler(CommandHandler(["open", "close", "auto"], admin_control))
    
    app.add_handler(MessageHandler(filters.CONTACT, contact))
    app.add_handler(MessageHandler(filters.LOCATION, location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(accept_or_decline))
    
    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
