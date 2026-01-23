from flask import Flask, request, jsonify
from threading import Thread
import os
import requests
import config

app = Flask('')

# Store bot application reference (will be set by bot.py)
bot_app = None

def set_bot_app(application):
    """Set the bot application reference for webhook callbacks"""
    global bot_app
    bot_app = application

@app.route('/')
def home():
    return "I am alive and running!"

@app.route('/chapa-webhook', methods=['POST'])
def chapa_webhook():
    """Handle Chapa payment webhook"""
    try:
        data = request.get_json()
        tx_ref = data.get('tx_ref')
        status = data.get('status')
        
        if not tx_ref:
            return jsonify({'error': 'Missing tx_ref'}), 400
        
        # Import here to avoid circular imports
        from bot import pending_payments, verify_payment, complete_order_after_payment
        
        if tx_ref not in pending_payments:
            return jsonify({'error': 'Transaction not found'}), 404
        
        # Mark payment as verified (bot will check periodically)
        if status == 'success':
            # Store verification status
            pending_payments[tx_ref]['verified'] = True
            pending_payments[tx_ref]['webhook_received'] = True
            
            # Try to complete order if bot is available
            if bot_app and bot_app.bot:
                async def process_payment():
                    await complete_order_after_payment(tx_ref, bot_app.bot)
                
                # Run async function
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                except:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                
                if loop.is_running():
                    # If loop is running, schedule the coroutine
                    asyncio.create_task(process_payment())
                else:
                    loop.run_until_complete(process_payment())
            
            return jsonify({'status': 'success', 'message': 'Payment processed'}), 200
        else:
            return jsonify({'status': 'pending', 'message': 'Payment not yet confirmed'}), 200
            
    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({'error': str(e)}), 500

def run():
    # Render assigns a random port to the PORT environment variable
    # We must listen on that specific port
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()
