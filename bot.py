import logging
import datetime
import uuid
import requests
from telegram import (
    Update, KeyboardButton, ReplyKeyboardMarkup,
    InlineKeyboardButton, InlineKeyboardMarkup, Contact
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

import config
import geofence
import menus 
import languages
from keep_alive import keep_alive, set_bot_app
from chapa import Chapa

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- STORAGE ---
user_data = {}           
username_map = {}
pending_payments = {}  # {tx_ref: {chat_id, order_id, order_data}}

# --- CHAPA INITIALIZATION ---
chapa = Chapa(config.CHAPA_SECRET_KEY)        

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
    # Timezone: EAT (UTC+3)
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=3)
    return config.OPEN_HOUR <= now.hour < config.CLOSE_HOUR

def track_username(update: Update):
    """Updates the map of usernames to chat_ids on every interaction"""
    user = update.effective_user
    chat_id = update.effective_chat.id
    if user and user.username:
        # Store as lowercase for case-insensitive lookup
        username_map[user.username.lower()] = chat_id

async def check_is_closed(update, chat_id):
    """Returns True if closed, and sends message."""
    if not is_open():
        await update.message.reply_text(t(chat_id, 'closed'))
        return True
    return False

async def ask_for_phone(update, chat_id):
    btn_text = t(chat_id, 'btn_phone')
    kb = ReplyKeyboardMarkup([[KeyboardButton(btn_text, request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(t(chat_id, 'ask_phone'), reply_markup=kb)

async def check_user_exists(update, chat_id):
    if chat_id not in user_data:
        user_data[chat_id] = {
            'lang': None, 'phone': None, 'orders': {}, 
            'current_cafe': None, 'location': None
        }
        return False 
    return True 

# --- ADMIN HANDLERS ---

async def admin_dm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Usage: /dm @username Your message here
    """
    if not is_admin(update): return

    # Check arguments
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text("❌ Usage: /dm @username <message>")
        return

    # Parse target and message
    target_handle = ctx.args[0].replace("@", "").lower()
    message_body = " ".join(ctx.args[1:])

    # Lookup Chat ID
    target_chat_id = username_map.get(target_handle)

    if not target_chat_id:
        await update.message.reply_text(f"❌ User @{target_handle} not found in bot memory.\n(They must interact with the bot after the last server restart).")
        return

    # Send Message
    try:
        # Get user lang for a localized header, or default to English/Neutral
        user_lang = user_data.get(target_chat_id, {}).get('lang', 'en')
        header = languages.TEXTS[user_lang].get('admin_dm', "🔔 Notification:\n\n{}").format(message_body)
        
        await ctx.bot.send_message(target_chat_id, header)
        await update.message.reply_text(f"✅ Message sent to @{target_handle}")
    except Exception as e:
        logger.error(f"DM Failed: {e}")
        await update.message.reply_text(f"❌ Failed to send: {e}")

async def admin_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    msg = update.message.text.replace("/broadcast", "").strip()
    if not msg: 
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    
    count = 0
    await update.message.reply_text(f"📤 Sending to {len(user_data)} users...")
    
    for uid, udata in user_data.items():
        lang = udata.get('lang', 'en')
        prefix = languages.get_text(lang, 'admin_broadcast').format(msg)
        try:
            await ctx.bot.send_message(uid, prefix)
            count += 1
        except: pass
    await update.message.reply_text(f"✅ Sent to {count} users.")

async def admin_control(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global SERVICE_MODE
    if not is_admin(update): return
    cmd = update.message.text
    if "/open" in cmd: SERVICE_MODE = 'OPEN'
    elif "/close" in cmd: SERVICE_MODE = 'CLOSED'
    elif "/auto" in cmd: SERVICE_MODE = 'AUTO'
    await update.message.reply_text(f"Service Mode: {SERVICE_MODE}")

# --- USER HANDLERS ---

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    track_username(update) # Track user
    chat_id = update.effective_chat.id
    
    await check_user_exists(update, chat_id)

    # 1. Language Selection (Always First)
    if not user_data[chat_id]['lang']:
        keyboard = ReplyKeyboardMarkup([['🇺🇸 English', '🇪🇹 አማርኛ']], resize_keyboard=True, one_time_keyboard=True)
        msg = languages.TEXTS['am'].get('choose_lang', "Please select language:")
        await update.message.reply_text(msg, reply_markup=keyboard)
        return

    # Admin Help
    if is_admin(update):
        await update.message.reply_text("👑 Admin: /open, /close, /auto, /broadcast, /dm")

    # 2. Check Time
    if await check_is_closed(update, chat_id): return

    # 3. Check Phone
    if not user_data[chat_id].get("phone"):
        await ask_for_phone(update, chat_id)
        return

    await show_main_menu(update)

async def set_language(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    track_username(update)
    chat_id = update.effective_chat.id
    text = update.message.text

    await check_user_exists(update, chat_id)

    if "English" in text:
        user_data[chat_id]['lang'] = 'en'
    elif "አማርኛ" in text:
        user_data[chat_id]['lang'] = 'am'
    
    # Check Time before proceeding
    if await check_is_closed(update, chat_id): return

    await update.message.reply_text(t(chat_id, 'welcome'))
    
    if not user_data[chat_id].get("phone"):
        await ask_for_phone(update, chat_id)
    else:
        await show_main_menu(update)

async def contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    track_username(update)
    chat_id = update.effective_chat.id
    await check_user_exists(update, chat_id)
    
    if await check_is_closed(update, chat_id): return
    
    if update.message.contact:
        user_data[chat_id]['phone'] = update.message.contact.phone_number
        await update.message.reply_text(t(chat_id, 'phone_saved'))
        await show_main_menu(update)

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    track_username(update)
    chat_id = update.effective_chat.id
    text = update.message.text
    
    # 1. CRITICAL: Check if profile exists. 
    exists = await check_user_exists(update, chat_id)
    if not exists:
        await start(update, ctx)
        return

    # 2. Handle Language Setup
    if text in ['🇺🇸 English', '🇪🇹 አማርኛ']:
        await set_language(update, ctx)
        return

    # 3. Safety: Ensure Lang is set
    if not user_data[chat_id].get('lang'):
        await start(update, ctx)
        return

    # 4. STRICT TIME CHECK
    if await check_is_closed(update, chat_id): return

    # 5. STRICT PHONE CHECK
    if not user_data[chat_id].get("phone"):
        await ask_for_phone(update, chat_id)
        return

    # --- Proceed (Profile, Time, and Phone are all valid) ---

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
        
        # Validation checks
        if current_cafe not in menus.CAFES or item not in menus.CAFES[current_cafe]: return
        if menus.CAFES[current_cafe][item] is None: return

        key = (current_cafe, item)
        data['orders'][key] = data['orders'].get(key, 0) + 1
        msg = t(chat_id, 'added_cart').format(item, data['orders'][key])
        await update.message.reply_text(msg)
    except:
        pass

async def show_main_menu(update: Update):
    chat_id = update.effective_chat.id
    
    user_data[chat_id]['current_cafe'] = None
    user_data[chat_id]['awaiting_location'] = False
    
    cafés = list(menus.CAFES.keys())
    menu_buttons = [[c] for c in cafés]
    menu_buttons.append([t(chat_id, 'btn_profile')]) 
    
    kb = ReplyKeyboardMarkup(menu_buttons, resize_keyboard=True)
    await update.message.reply_text(t(chat_id, 'choose_cafe'), reply_markup=kb)

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
    
    if not data.get("phone"):
        await ask_for_phone(update, chat_id)
        return

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

async def show_profile(update: Update):
    chat_id = update.effective_chat.id
    data = user_data[chat_id]
    
    phone = data.get('phone', 'N/A')
    loc_status = t(chat_id, 'location_set') if data.get('location') else t(chat_id, 'location_not_set')
    
    msg = t(chat_id, 'profile_header').replace("{}", str(phone), 1).replace("{}", str(loc_status), 1)
    
    kb = ReplyKeyboardMarkup([
        [t(chat_id, 'btn_switch_lang'), t(chat_id, 'btn_edit_phone')],
        [t(chat_id, 'btn_back')]
    ], resize_keyboard=True)
    
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=kb)

async def initialize_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, order_id: str, total_price: float, order_data: dict):
    """Initialize Chapa payment for an order"""
    try:
        user = update.effective_user
        data = user_data[chat_id]
        
        # Generate unique transaction reference
        tx_ref = f"werabe_{order_id.replace('#', '')}_{uuid.uuid4().hex[:8]}"
        
        # Prepare customer info
        first_name = user.first_name or "Customer"
        last_name = user.last_name or ""
        email = f"{chat_id}@telegram.user"  # Placeholder email
        
        # Initialize payment with Chapa
        # Amount should be in ETB (Ethiopian Birr)
        response = chapa.initialize(
            email=email,
            amount=float(total_price),
            first_name=first_name,
            last_name=last_name,
            tx_ref=tx_ref,
            callback_url=config.CHAPA_WEBHOOK_URL if config.CHAPA_WEBHOOK_URL else None,
            currency="ETB"
        )
        
        if response and response.get('status') == 'success':
            checkout_url = response.get('data', {}).get('checkout_url')
            
            # Store pending payment info
            pending_payments[tx_ref] = {
                'chat_id': chat_id,
                'order_id': order_id,
                'order_data': order_data,
                'total_price': total_price,
                'timestamp': datetime.datetime.now()
            }
            
            # Send payment link to user
            payment_msg = f"{t(chat_id, 'payment_init')}\n\n🔗 {checkout_url}\n\n{t(chat_id, 'payment_pending')}"
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(t(chat_id, 'btn_pay_now'), url=checkout_url)
            ], [
                InlineKeyboardButton("✅ Check Payment Status", callback_data=f"check_payment_{tx_ref}"),
                InlineKeyboardButton(t(chat_id, 'btn_cancel_payment'), callback_data=f"cancel_pay_{tx_ref}")
            ]])
            
            await update.message.reply_text(payment_msg, reply_markup=kb, parse_mode="Markdown")
            
            # Store tx_ref in user data for verification
            data['pending_tx_ref'] = tx_ref
            data['pending_order_id'] = order_id
            
            return True
        else:
            error_msg = response.get('message', 'Unknown error') if response else 'No response from payment gateway'
            logger.error(f"Chapa payment initialization failed: {error_msg} - {response}")
            await update.message.reply_text(f"{t(chat_id, 'payment_failed')}\n\nError: {error_msg}")
            return False
            
    except Exception as e:
        logger.error(f"Payment initialization error: {e}")
        await update.message.reply_text(t(chat_id, 'payment_failed'))
        return False

async def verify_payment(tx_ref: str, ctx: ContextTypes.DEFAULT_TYPE = None):
    """Verify payment status with Chapa"""
    try:
        # Verify payment using Chapa API
        headers = {
            'Authorization': f'Bearer {config.CHAPA_SECRET_KEY}'
        }
        response = requests.get(
            f'https://api.chapa.co/v1/transaction/verify/{tx_ref}',
            headers=headers
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 'success' and data.get('data', {}).get('status') == 'successful':
                return True
        return False
    except Exception as e:
        logger.error(f"Payment verification error: {e}")
        return False

async def complete_order_after_payment(tx_ref: str, bot_instance):
    """Complete order after successful payment"""
    if tx_ref not in pending_payments:
        return False
    
    payment_info = pending_payments[tx_ref]
    chat_id = payment_info['chat_id']
    order_id = payment_info['order_id']
    order_data = payment_info['order_data']
    total_price = payment_info['total_price']
    
    try:
        data = user_data[chat_id]
        lat = data['location']['lat']
        lon = data['location']['lon']
        
        cart_items = []
        for (cafe, item), qty in order_data.items():
            cart_items.append(f"• {item} x{qty} ({cafe})")
        
        cart_summary = "\n".join(cart_items)
        
        # Get user info
        try:
            user_chat = await bot_instance.get_chat(chat_id)
            user_name = user_chat.full_name or 'Customer'
        except:
            user_name = 'Customer'
        
        customer_info = (
            f"👤 {user_name}\n"
            f"📞 {data['phone']}\n"
            f"💳 Payment: ✅ Verified (Ref: {tx_ref})"
        )

        mapslink = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
        map_msg = f"📍 Customer location for Order ID: `{order_id}`: [Open Map]({mapslink})"
        
        await bot_instance.send_message(
            chat_id=config.CHANNEL_ID,
            text=map_msg,
            parse_mode='Markdown',
            disable_web_page_preview=False 
        )

        admin_msg = f"""📦 *ORDER DETAILS {order_id}* (PAID ✅)
    
{customer_info}

🛒 *ITEMS:*
{cart_summary}

💵 *Total:* {total_price} ETB
💳 *Payment Status:* ✅ Paid
"""
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Accept", callback_data=f"accept_{chat_id}_{order_id}"),
            InlineKeyboardButton("❌ Decline", callback_data=f"decline_{chat_id}_{order_id}")
        ]])
        
        await bot_instance.send_message(
            chat_id=config.CHANNEL_ID, 
            text=admin_msg, 
            parse_mode='Markdown', 
            reply_markup=kb
        )

        await bot_instance.send_message(
            chat_id=chat_id,
            text=t(chat_id, 'payment_success') + "\n\n" + t(chat_id, 'order_sent').format(order_id),
            parse_mode="Markdown"
        )
        
        # Clear order data
        data['orders'] = {}
        data['current_cafe'] = None
        data.pop('pending_tx_ref', None)
        data.pop('pending_order_id', None)
        
        # Remove from pending payments
        del pending_payments[tx_ref]
        
        return True
    except Exception as e:
        logger.error(f"Failed to complete order after payment: {e}")
        return False

async def location(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    track_username(update)
    chat_id = update.effective_chat.id
    
    # 1. Check Profile
    exists = await check_user_exists(update, chat_id)
    if not exists:
        await start(update, ctx)
        return

    # 2. Check Time
    if await check_is_closed(update, chat_id): return

    data = user_data.get(chat_id)
    
    # 3. Check Phone
    if not data.get("phone"):
        await ask_for_phone(update, chat_id)
        return

    if not data.get("awaiting_location"): return

    data['awaiting_location'] = False
    lat = update.message.location.latitude
    lon = update.message.location.longitude

    if not geofence.in_werabe(lat, lon):
        await update.message.reply_text(t(chat_id, 'location_error'))
        return

    data['location'] = {'lat': lat, 'lon': lon}

    order_id = f"#{uuid.uuid4().hex[:8].upper()}"
    
    total_price = 39
    cart_items = []
    order_data_copy = {}  # Copy for payment
    for (cafe, item), qty in data['orders'].items():
        price = menus.CAFES.get(cafe, {}).get(item, 0)
        total_price += price * qty
        cart_items.append(f"• {item} x{qty} ({cafe})")
        order_data_copy[(cafe, item)] = qty
    
    cart_summary = "\n".join(cart_items)
    
    # Show order summary and initiate payment
    summary_msg = f"{cart_summary}\n\n{t(chat_id, 'delivery_fee')}: 39 ETB\n*{t(chat_id, 'total')}: {total_price} ETB*"
    await update.message.reply_text(summary_msg, parse_mode="Markdown")
    
    # Initialize payment
    await initialize_payment(update, ctx, chat_id, order_id, total_price, order_data_copy)

    # Note: Order will be sent to admin only after payment verification

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

async def handle_payment_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle payment-related callbacks"""
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.from_user.id
    
    # Handle payment cancellation
    if data.startswith("cancel_pay_"):
        tx_ref = data.replace("cancel_pay_", "")
        if tx_ref in pending_payments:
            user_data[chat_id]['orders'] = {}
            user_data[chat_id]['current_cafe'] = None
            user_data[chat_id].pop('pending_tx_ref', None)
            user_data[chat_id].pop('pending_order_id', None)
            del pending_payments[tx_ref]
            await query.message.edit_text(t(chat_id, 'payment_cancelled'))
            await show_main_menu(update)
    
    # Handle payment verification check
    elif data.startswith("check_payment_"):
        tx_ref = data.replace("check_payment_", "")
        if await verify_payment(tx_ref, ctx):
            await complete_order_after_payment(tx_ref, ctx.bot)
            await query.message.edit_text(t(chat_id, 'payment_success'))
        else:
            await query.answer("Payment not yet confirmed. Please wait...", show_alert=True)

async def check_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Command to manually check payment status"""
    chat_id = update.effective_chat.id
    data = user_data.get(chat_id, {})
    
    tx_ref = data.get('pending_tx_ref')
    if not tx_ref:
        await update.message.reply_text("❌ No pending payment found.")
        return
    
    if tx_ref not in pending_payments:
        await update.message.reply_text("❌ Payment reference not found.")
        return
    
    # Verify payment
    if await verify_payment(tx_ref, ctx):
        await complete_order_after_payment(tx_ref, ctx.bot)
    else:
        await update.message.reply_text(t(chat_id, 'payment_pending'))

def main():
    keep_alive()
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()
    
    # Set bot app reference for webhook
    set_bot_app(app)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", admin_broadcast))
    app.add_handler(CommandHandler("dm", admin_dm)) # <-- Added DM Handler
    app.add_handler(CommandHandler(["open", "close", "auto"], admin_control))
    app.add_handler(CommandHandler("checkpayment", check_payment))  # Manual payment check
    
    app.add_handler(MessageHandler(filters.CONTACT, contact))
    app.add_handler(MessageHandler(filters.LOCATION, location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Payment and order callbacks
    app.add_handler(CallbackQueryHandler(handle_payment_callback, pattern="^(cancel_pay_|check_payment_)"))
    app.add_handler(CallbackQueryHandler(accept_or_decline))
    
    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
