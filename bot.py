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
# Make sure these files exist in your folder
import config
import geofence
import menus 
from keep_alive import keep_alive

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- GLOBAL VARIABLES ---
user_data = {}           # Stores cart, phone, etc.
username_map = {}        # Maps "username" -> chat_id for DMs
ADMIN_USERNAME = "kanzedin"

# Service Status: 'AUTO' (time based), 'OPEN' (force open), 'CLOSED' (force closed)
SERVICE_MODE = 'AUTO' 

# --- HELPER FUNCTIONS ---

def is_admin(update: Update) -> bool:
    """Check if the user is the admin"""
    if not update.effective_user:
        return False
    username = update.effective_user.username
    return username and username.lower() == ADMIN_USERNAME.lower()

def update_user_info(update: Update):
    """Save chat_id and username for reverse lookup"""
    user = update.effective_user
    chat_id = update.effective_chat.id
    if user and user.username:
        # Save username without @ (lowercase for consistency)
        clean_name = user.username.lower().replace("@", "")
        username_map[clean_name] = chat_id

def is_open() -> bool:
    """Check if shop is open based on Manual Mode or Time"""
    global SERVICE_MODE
    
    if SERVICE_MODE == 'OPEN':
        return True
    elif SERVICE_MODE == 'CLOSED':
        return False
    else:
        # AUTO mode: Check time (UTC+3 for EAT)
        now = datetime.datetime.utcnow() + datetime.timedelta(hours=3)
        return config.OPEN_HOUR <= now.hour < config.CLOSE_HOUR

# --- ADMIN COMMANDS ---

async def admin_dm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Usage: /dm @username Your message here"""
    if not is_admin(update):
        return

    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text("❌ Usage: /dm <username> <message>")
        return

    target_username = ctx.args[0].lower().replace("@", "")
    message_text = " ".join(ctx.args[1:])

    target_chat_id = username_map.get(target_username)

    if not target_chat_id:
        await update.message.reply_text(f"❌ User @{target_username} has not started the bot yet.")
        return

    try:
        await ctx.bot.send_message(target_chat_id, f"🔔 *Notification:*\n\n{message_text}", parse_mode="Markdown")
        await update.message.reply_text(f"✅ Message sent to @{target_username}")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to send: {e}")

async def set_service_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/open, /close, or /auto"""
    if not is_admin(update):
        return

    global SERVICE_MODE
    command = update.message.text.lower()

    if "/open" in command:
        SERVICE_MODE = 'OPEN'
        await update.message.reply_text("🟢 Service is now FORCED OPEN.")
    elif "/close" in command:
        SERVICE_MODE = 'CLOSED'
        await update.message.reply_text("🔴 Service is now FORCED CLOSED.")
    elif "/auto" in command:
        SERVICE_MODE = 'AUTO'
        await update.message.reply_text("🕒 Service set to AUTO (Time-based).")

# --- USER HANDLERS ---

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    update_user_info(update) # Save username
    chat_id = update.effective_chat.id
    user_data.setdefault(chat_id, {
        'orders': {}, 'phone': None, 'current_cafe': None, 'awaiting_location': False
    })
    
    if is_admin(update):
        await update.message.reply_text(
            "👑 *Admin Panel*\n\n"
            "🎮 *Controls:*\n"
            "/open - Force Open\n"
            "/close - Force Close\n"
            "/auto - Use Time Schedule\n"
            "/dm @user msg - Send Direct Message\n"
            "/broadcast msg - Send to ALL\n"
            "/stats - View Users",
            parse_mode="Markdown"
        )

    if not is_open():
        msg = "⛔ Currently Closed."
        if SERVICE_MODE == 'AUTO':
            msg += " Open daily 12 AM–12 PM."
        await update.message.reply_text(msg)
        return

    if not user_data[chat_id].get("phone"):
        await update.message.reply_text(
            "📞 Please share your phone number to continue:",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("Share Phone Number", request_contact=True)]],
                one_time_keyboard=True,
                resize_keyboard=True
            )
        )
        return

    await show_cafe_menu(update)

async def show_cafe_menu(update: Update):
    cafés = list(menus.CAFES.keys())
    keyboard_buttons = [[c] for c in cafés]
    keyboard = ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True)
    
    chat_id = update.effective_chat.id
    if chat_id in user_data:
        user_data[chat_id]['current_cafe'] = None
        user_data[chat_id]['awaiting_location'] = False
        
    await update.message.reply_text("Choose a café:", reply_markup=keyboard)

async def contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    update_user_info(update)
    if not update.message.contact:
        return
        
    contact: Contact = update.message.contact
    chat_id = update.effective_chat.id
    user_data.setdefault(chat_id, {'orders': {}, 'current_cafe': None})
    user_data[chat_id]['phone'] = contact.phone_number
    await update.message.reply_text("✅ Phone number saved!")
    await show_cafe_menu(update)

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    update_user_info(update) # Always update username mapping on text
    chat_id = update.effective_chat.id
    text = update.message.text
    data = user_data.setdefault(chat_id, {
        'orders': {}, 'phone': None, 'current_cafe': None, 'awaiting_location': False
    })

    # Check status (Allow admins to test even if closed)
    if not is_open() and not is_admin(update):
        await update.message.reply_text("⛔ Sorry, we are currently closed.")
        return

    if not data.get("phone"):
        await update.message.reply_text(
            "📞 Please share your phone number to continue:",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("Share Phone Number", request_contact=True)]],
                one_time_keyboard=True,
                resize_keyboard=True
            )
        )
        return

    if text == "🔙 Back":
        if data.get('current_cafe'):
            data['current_cafe'] = None
            await show_cafe_menu(update)
        else:
            await show_cafe_menu(update)
        return

    if text == "❌ Cancel Order":
        data['orders'] = {}
        data['current_cafe'] = None
        data['awaiting_location'] = False
        await update.message.reply_text("❌ Order cancelled.")
        await show_cafe_menu(update)
        return

    if not data.get("current_cafe"):
        if text in menus.CAFES:
            data['current_cafe'] = text
            await show_cafe_items(update, text)
        return

    if text == "✅️ Done":
        if not data['orders']:
            await update.message.reply_text("❗ Cart is empty.")
            return

        total = 39
        summary_by_cafe = {}
        for (cafe, item), qty in data['orders'].items():
            price = menus.CAFES[cafe].get(item)
            if price is None: continue
            subtotal = price * qty
            total += subtotal
            summary_by_cafe.setdefault(cafe, []).append(f"{item} × {qty} = {subtotal} ETB")

        summary_lines = []
        for cafe, lines in summary_by_cafe.items():
            summary_lines.append(f"🧾 *{cafe}*\n" + "\n".join(lines) + "\n")

        summary = "\n".join(summary_lines)
        await update.message.reply_text(
            f"{summary}💵 *Total: {total} ETB*\n🚚 *Delivery fee: 39 ETB*\n\n📍 Share location to finalize:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("Share location", request_location=True)], ["❌ Cancel Order", "🔙 Back"]],
                resize_keyboard=True, one_time_keyboard=True
            )
        )
        data['awaiting_location'] = True
        return

    try:
        if " — " not in text:
            await update.message.reply_text("❌ Select an item from the menu.")
            return

        item, _, _ = text.partition(" — ")
        current_cafe = data['current_cafe']
        
        if current_cafe not in menus.CAFES or item not in menus.CAFES[current_cafe]:
            await update.message.reply_text("❌ Item not found.")
            return

        if menus.CAFES[current_cafe][item] is None:
             await update.message.reply_text("❌ That is a category header.")
             return

        key = (current_cafe, item)
        data['orders'][key] = data['orders'].get(key, 0) + 1
        await update.message.reply_text(f"🛒 Added: {item} × {data['orders'][key]}\n✅️ Press 'Done' when ready.")
    except Exception:
        logger.error("Error processing item", exc_info=True)
        await update.message.reply_text("❌ Error processing item.")

async def show_cafe_items(update: Update, cafe_name: str):
    menu = menus.CAFES[cafe_name]
    keyboard = []
    for item, price in menu.items():
        if price is None:
            keyboard.append([item])
        else:
            keyboard.append([f"{item} — {price} ETB"])
    keyboard += [["✅️ Done"], ["❌ Cancel Order"], ["🔙 Back"]]
    await update.message.reply_text(f"Menu for {cafe_name}:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

async def location(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    update_user_info(update)
    uid = update.effective_chat.id
    data = user_data.get(uid)
    if not data or not data.get("awaiting_location"):
        return

    data['awaiting_location'] = False

    if not is_open() and not is_admin(update):
        await update.message.reply_text("⛔ Sorry, we closed while you were ordering.")
        return

    if not geofence.in_werabe(update.message.location.latitude, update.message.location.longitude):
        await update.message.reply_text("❌ Delivery only within Werabe city.")
        return

    order_id = str(uuid.uuid4())[:8].upper()
    total = 39
    summary_by_cafe = {}

    for (cafe, item), qty in data['orders'].items():
        price = menus.CAFES[cafe].get(item)
        if price is None: continue
        subtotal = price * qty
        total += subtotal
        summary_by_cafe.setdefault(cafe, []).append(f"{item} × {qty} = {subtotal} ETB")

    summary_lines = [f"🧾 *{cafe}*\n" + "\n".join(lines) + "\n" for cafe, lines in summary_by_cafe.items()]
    summary = "\n".join(summary_lines)
    
    customer = update.effective_user.full_name
    uname = update.effective_user.username or "N/A"
    phone = data.get("phone", "N/A")
    mapslink = f"https://www.google.com/maps/search/?api=1&query={update.message.location.latitude},{update.message.location.longitude}"

    await ctx.bot.send_message(
        config.CHANNEL_ID,
        f"📍 Customer location for *Order ID:* `{order_id}`: [Open Map]({mapslink})",
        parse_mode='Markdown'
    )

    msg = f"""📦 *New order!*
Order ID: `{order_id}`
Customer: {customer} (@{uname})
Phone: {phone}
Total: {total} ETB

{summary}
"""
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accept", callback_data=f"accept_{uid}_{order_id}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"decline_{uid}_{order_id}")
    ]])
    await ctx.bot.send_message(config.CHANNEL_ID, msg, parse_mode='Markdown', reply_markup=keyboard)

    await update.message.reply_text(
        f"✅ Order Sent!\nID: `{order_id}`\n\n{summary}💵 Total: {total} ETB",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True, one_time_keyboard=True)
    )
    data['orders'] = {}
    data['current_cafe'] = None

async def accept_or_decline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if not (data.startswith("accept_") or data.startswith("decline_")):
        return

    try:
        action, uid_str, order_id = data.split("_")
        uid = int(uid_str)
    except:
        return

    msg = query.message
    admin_name = query.from_user.full_name

    if "✅ Accepted" in msg.text or "❌ Declined" in msg.text:
        await query.answer("Already processed.")
        return

    if action == "accept":
        updated = msg.text + f"\n\n✅ Accepted by {admin_name}"
        await query.message.edit_text(updated, reply_markup=None)
        await ctx.bot.send_message(uid, f"✅ Order `{order_id}` accepted! 🚚", parse_mode="Markdown")
    elif action == "decline":
        updated = msg.text + f"\n\n❌ Declined by {admin_name}"
        await query.message.edit_text(updated, reply_markup=None)
        await ctx.bot.send_message(uid, f"😔 Order `{order_id}` declined.", parse_mode="Markdown")

async def broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    
    message_text = update.message.text.replace("/broadcast", "").strip()
    if not message_text:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    
    success, failed = 0, 0
    await update.message.reply_text(f"📤 Sending to {len(user_data)} users...")
    
    for chat_id in list(user_data.keys()):
        try:
            await ctx.bot.send_message(chat_id, f"📢 *Announcement*\n\n{message_text}", parse_mode="Markdown")
            success += 1
        except:
            failed += 1
    
    await update.message.reply_text(f"✅ Sent: {success} | ❌ Failed: {failed}")

async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await update.message.reply_text(
        f"📊 *Stats*\nUsers: {len(user_data)}\nPhones Saved: {sum(1 for d in user_data.values() if d.get('phone'))}",
        parse_mode="Markdown"
    )

def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {ctx.error}", exc_info=True)

def main():
    keep_alive() # Start Web Server
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()

    # Admin Commands
    app.add_handler(CommandHandler("open", set_service_mode))
    app.add_handler(CommandHandler("close", set_service_mode))
    app.add_handler(CommandHandler("auto", set_service_mode))
    app.add_handler(CommandHandler("dm", admin_dm))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("stats", stats))
    
    # User Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.CONTACT, contact))
    app.add_handler(MessageHandler(filters.LOCATION, location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(accept_or_decline))
    app.add_error_handler(error_handler)

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()

    
