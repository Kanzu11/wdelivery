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
# Make sure these modules exist in your folder
import config
import geofence
import menus 
from keep_alive import keep_alive  # Import the web server

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

user_data = {}
ADMIN_USERNAME = "kanzedin"  # Admin username

def is_admin(update: Update) -> bool:
    """Check if the user is the admin"""
    if not update.effective_user:
        return False
    username = update.effective_user.username
    return username and username.lower() == ADMIN_USERNAME.lower()

def is_open() -> bool:
    # Adjust UTC time to EAT (UTC+3)
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=3)
    return config.OPEN_HOUR <= now.hour < config.CLOSE_HOUR

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_data.setdefault(chat_id, {
        'orders': {}, 'phone': None, 'current_cafe': None, 'awaiting_location': False
    })
    
    # Track user for admin stats
    if is_admin(update):
        await update.message.reply_text(
            "👑 Admin mode activated!\n\n"
            "Available commands:\n"
            "/broadcast - Send message to all users\n"
            "/stats - See total number of users"
        )

    if not is_open():
        await update.message.reply_text("Sorry, we're closed. Open daily 12 AM–12 PM .")
        return

    # Check if we already have the phone number
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
    # Arrange buttons in rows of 2 for better look
    keyboard_buttons = [[c] for c in cafés]
    keyboard = ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True)
    
    chat_id = update.effective_chat.id
    # Reset current cafe selection
    if chat_id in user_data:
        user_data[chat_id]['current_cafe'] = None
        user_data[chat_id]['awaiting_location'] = False
        
    await update.message.reply_text("Choose a café:", reply_markup=keyboard)

async def contact(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message.contact:
        return
        
    contact: Contact = update.message.contact
    chat_id = update.effective_chat.id
    user_data.setdefault(chat_id, {'orders': {}, 'current_cafe': None})
    user_data[chat_id]['phone'] = contact.phone_number
    await update.message.reply_text("✅ Phone number saved!")
    await show_cafe_menu(update)

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    data = user_data.setdefault(chat_id, {
        'orders': {}, 'phone': None, 'current_cafe': None, 'awaiting_location': False
    })

    if not is_open():
        await update.message.reply_text("⏰ Sorry, we're currently closed. Open daily 6 AM–6 PM EAT.")
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
            # If inside a cafe menu, go back to cafe list
            data['current_cafe'] = None
            await show_cafe_menu(update)
        else:
            # If at cafe list, just show list again (or main menu if you had one)
            await show_cafe_menu(update)
        return

    if text == "❌ Cancel Order":
        data['orders'] = {}
        data['current_cafe'] = None
        data['awaiting_location'] = False
        await update.message.reply_text("❌ Order cancelled.")
        await show_cafe_menu(update)
        return

    # If user selects a Cafe
    if not data.get("current_cafe"):
        if text in menus.CAFES:
            data['current_cafe'] = text
            await show_cafe_items(update, text)
        return

    # If user presses "Done"
    if text == "✅️ Done":
        if not data['orders']:
            await update.message.reply_text("❗ You haven't added any items yet.")
            return

        total = 39
        summary_by_cafe = {}
        for (cafe, item), qty in data['orders'].items():
            price = menus.CAFES[cafe].get(item)
            if price is None:
                continue
            subtotal = price * qty
            total += subtotal
            summary_by_cafe.setdefault(cafe, []).append(f"{item} × {qty} = {subtotal} ETB")

        summary_lines = []
        for cafe, lines in summary_by_cafe.items():
            summary_lines.append(f"🧾 *{cafe}*\n" + "\n".join(lines) + "\n")

        summary = "\n".join(summary_lines)
        await update.message.reply_text(
            f"{summary}💵 *Total: {total} ETB*\n🚚 *Delivery fee: 39 ETB*\n\n📍 Please share your location to finalize the order:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("Share location", request_location=True)], ["❌ Cancel Order", "🔙 Back"]],
                resize_keyboard=True,
                one_time_keyboard=True
            )
        )
        data['awaiting_location'] = True
        return

    # If user selects an Item
    try:
        if " — " not in text:
            await update.message.reply_text("❌ This item can't be selected.")
            return

        item, _, _ = text.partition(" — ")
        current_cafe = data['current_cafe']
        
        # Verify item belongs to current cafe
        if current_cafe not in menus.CAFES or item not in menus.CAFES[current_cafe]:
            await update.message.reply_text("❌ Item not found in this café.")
            return
            
        if menus.CAFES[current_cafe][item] is None:
             await update.message.reply_text("❌ This is a header, not an item.")
             return

        key = (current_cafe, item)
        data['orders'][key] = data['orders'].get(key, 0) + 1
        await update.message.reply_text(f"🛒 Added to cart: {item} × {data['orders'][key]}.\n✅️ Press 'Done' when ready.")
    except Exception:
        logger.error("Error processing item", exc_info=True)
        await update.message.reply_text("❌ Unexpected format.")

async def show_cafe_items(update: Update, cafe_name: str):
    menu = menus.CAFES[cafe_name]
    keyboard = []

    for item, price in menu.items():
        if price is None:
            keyboard.append([item])  # header
        else:
            keyboard.append([f"{item} — {price} ETB"])

    keyboard += [["✅️ Done"], ["❌ Cancel Order"], ["🔙 Back"]]

    await update.message.reply_text(f"Menu for {cafe_name}:", reply_markup=ReplyKeyboardMarkup(
        keyboard, resize_keyboard=True
    ))

async def location(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    data = user_data.get(uid)
    if not data or not data.get("awaiting_location"):
        return

    data['awaiting_location'] = False

    if not is_open():
        await update.message.reply_text("⏰ Sorry, we're currently closed. Open daily 6 AM–6 PM EAT.")
        return

    if not geofence.in_werabe(update.message.location.latitude, update.message.location.longitude):
        await update.message.reply_text("❌ Delivery only within Werabe city.")
        return

    order_id = str(uuid.uuid4())[:8].upper()
    total = 39
    summary_by_cafe = {}

    for (cafe, item), qty in data['orders'].items():
        price = menus.CAFES[cafe].get(item)
        if price is None:
            continue
        subtotal = price * qty
        total += subtotal
        summary_by_cafe.setdefault(cafe, []).append(f"{item} × {qty} = {subtotal} ETB")

    summary_lines = []
    for cafe, lines in summary_by_cafe.items():
        summary_lines.append(f"🧾 *{cafe}*\n" + "\n".join(lines) + "\n")

    summary = "\n".join(summary_lines)
    customer = update.effective_user.full_name
    uname = update.effective_user.username or "N/A"
    phone = data.get("phone", "N/A")
    # Updated Maps link for better compatibility
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
        f"✅ Your order has been sent!\nPlease wait for confirmation.\n\n📦 *Order ID:* `{order_id}`\n\n🧾 *Order Summary*\n{summary}💵 Total: {total} ETB",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            [["🔙 Back"]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
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
        logger.error(f"Invalid callback data: {data}")
        return

    msg = query.message
    admin_name = query.from_user.full_name

    if "✅ Accepted" in msg.text or "❌ Declined" in msg.text:
        await query.answer("Already processed.")
        return

    if action == "accept":
        updated = msg.text + f"\n\n✅ Accepted by {admin_name}"
        await query.message.edit_text(updated, reply_markup=None)
        await ctx.bot.send_message(uid, f"✅ Your order `{order_id}` has been accepted and is on its way! 🚚", parse_mode="Markdown")
    elif action == "decline":
        updated = msg.text + f"\n\n❌ Declined by {admin_name}"
        await query.message.edit_text(updated, reply_markup=None)
        await ctx.bot.send_message(uid, f"😔 Sorry, your order `{order_id}` has been declined by the café.", parse_mode="Markdown")

async def broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin command to broadcast message to all users"""
    if not is_admin(update):
        await update.message.reply_text("❌ You don't have permission to use this command.")
        return
    
    # Get the message to broadcast
    message_text = update.message.text
    if message_text.startswith("/broadcast"):
        broadcast_msg = message_text.replace("/broadcast", "").strip()
        if not broadcast_msg:
            await update.message.reply_text(
                "📢 Usage: /broadcast <your message>\n\n"
                "Example: /broadcast Hello everyone! We have a special offer today."
            )
            return
    
    total_users = len(user_data)
    success_count = 0
    failed_count = 0
    
    await update.message.reply_text(f"📤 Broadcasting to {total_users} users...")
    
    for chat_id in list(user_data.keys()):
        try:
            await ctx.bot.send_message(chat_id, f"📢 *Announcement*\n\n{broadcast_msg}", parse_mode="Markdown")
            success_count += 1
        except Exception as e:
            logger.error(f"Failed to send message to {chat_id}: {e}")
            failed_count += 1
    
    await update.message.reply_text(
        f"✅ Broadcast completed!\n"
        f"✅ Success: {success_count}\n"
        f"❌ Failed: {failed_count}\n"
        f"📊 Total: {total_users}"
    )

async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin command to see total number of users"""
    if not is_admin(update):
        await update.message.reply_text("❌ You don't have permission to use this command.")
        return
    
    total_users = len(user_data)
    users_with_phone = sum(1 for data in user_data.values() if data.get("phone"))
    
    await update.message.reply_text(
        f"📊 *Bot Statistics*\n\n"
        f"👥 Total Users: {total_users}\n"
        f"📞 Users with Phone: {users_with_phone}\n"
        f"📱 Users without Phone: {total_users - users_with_phone}",
        parse_mode="Markdown"
    )

def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {ctx.error}", exc_info=True)

def main():
    # 1. Start the Flask keep-alive server in a separate thread
    keep_alive()

    # 2. Build the Telegram Application
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()

    # 3. Add Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(MessageHandler(filters.CONTACT, contact))
    app.add_handler(MessageHandler(filters.LOCATION, location))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(accept_or_decline))
    
    # 4. Error handling
    app.add_error_handler(error_handler)

    # 5. Start Polling
    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()

