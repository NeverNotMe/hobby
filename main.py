import telebot
import threading
import time
import os
from telebot import types
from solana.rpc.api import Client
from solders.keypair import Keypair

# ⚙️ Load Token from Replit Secrets
# Make sure to add TELEGRAM_BOT_TOKEN in the Secrets tab
BOT_TOKEN = "6426964193:AAEIKz4EPSTvHNj2oMGwmp2_XQIHDyArO04"

# Fallback if secret is missing (Not recommended for production)
if not BOT_TOKEN:
    print("❌ Error: TELEGRAM_BOT_TOKEN not found in Secrets.")
    # You can paste your token here for testing if not using Secrets:
    # BOT_TOKEN = "YOUR_ACTUAL_TOKEN_HERE"

bot = telebot.TeleBot(BOT_TOKEN)

# Dictionary to keep track of each user's scanning state
user_sessions = {}

# --- HELPER FUNCTIONS ---

def get_main_keyboard():
    """Creates the keyboard with Status and Stop buttons"""
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    btn_status = types.KeyboardButton('📊 Status')
    btn_stop = types.KeyboardButton('🛑 Stop')
    markup.add(btn_status, btn_stop)
    return markup

def scanner_worker(chat_id):
    """The background thread that actually scans wallets"""
    session = user_sessions.get(chat_id)
    if not session: return

    rpc_url = session['rpc']
    
    try:
        # Initialize Solana Client
        client = Client(rpc_url)
        # Simple health check (get epoch info or similar)
        client.get_epoch_info()
    except Exception as e:
        bot.send_message(chat_id, f"⚠️ **RPC Connection Error:**\n`{str(e)}`\n\nStopping scan.", parse_mode='Markdown')
        session['running'] = False
        return

    bot.send_message(chat_id, "✅ **RPC Connected!** Scanning started...", parse_mode='Markdown')

    while session['running']:
        session['attempts'] += 1
        
        try:
            # 1. Generate Wallet
            kp = Keypair()
            pubkey = kp.pubkey()
            
            # 2. Check Balance
            # Note: get_balance returns a response object, accessing .value gives lamports
            resp = client.get_balance(pubkey)
            balance = resp.value

            if balance > 0:
                sol_balance = balance / 1_000_000_000
                secret_bytes = kp.secret() # byte array
                
                msg = (
                    f"🚨 <b>FOUND FUNDED WALLET!</b> 🚨\n\n"
                    f"💰 <b>Balance:</b> {sol_balance} SOL\n"
                    f"🔑 <b>Address:</b> <code>{pubkey}</code>\n"
                    f"🔐 <b>Secret:</b> <code>{secret_bytes}</code>"
                )
                bot.send_message(chat_id, msg, parse_mode='HTML')
                
                # Optional: Stop after finding one?
                # session['running'] = False 

        except Exception as e:
            # Handle rate limits or connection errors
            # print(f"RPC Error: {e}") # Optional logging
            time.sleep(2) # Wait longer on error

        # Rate limit protection (adjust based on RPC limits)
        # 0.05 = ~20 RPS max (theoretical), RPCs usually lower
        time.sleep(0.05) 

# --- BOT HANDLERS ---

@bot.message_handler(commands=['start'])
def start_command(message):
    chat_id = message.chat.id
    
    # Check if already running
    if chat_id in user_sessions and user_sessions[chat_id]['running']:
        bot.send_message(chat_id, "⚠️ Scanner is already running. Use '🛑 Stop' first.")
        return

    text = (
        "👋 **Welcome to the Solana Scanner Bot!**\n\n"
        "To start scanning, please send me a valid **Solana RPC URL**.\n"
        "_(Example: https://api.mainnet-beta.solana.com)_"
    )
    
    msg = bot.send_message(chat_id, text, parse_mode='Markdown')
    bot.register_next_step_handler(msg, process_rpc_input)

def process_rpc_input(message):
    chat_id = message.chat.id
    rpc_url = message.text.strip()

    # Basic validation
    if not rpc_url.startswith("http"):
        msg = bot.send_message(chat_id, "❌ Invalid URL. Please send a valid RPC link (starting with http/https):")
        bot.register_next_step_handler(msg, process_rpc_input)
        return

    # Initialize Session
    user_sessions[chat_id] = {
        'running': True,
        'attempts': 0,
        'rpc': rpc_url
    }

    # Start the Scanner Thread
    threading.Thread(target=scanner_worker, args=(chat_id,), daemon=True).start()

    bot.send_message(
        chat_id, 
        f"🚀 **Scanner Initialized!**\nRPC: `{rpc_url}`\n\nConnecting...", 
        parse_mode='Markdown',
        reply_markup=get_main_keyboard()
    )

@bot.message_handler(func=lambda message: message.text == '📊 Status')
def status_handler(message):
    chat_id = message.chat.id
    session = user_sessions.get(chat_id)

    if not session or not session.get('running'):
        bot.send_message(chat_id, "⚠️ No active scan running. Type /start to begin.", reply_markup=types.ReplyKeyboardRemove())
        return

    text = (
        f"🤖 **Bot Status: ONLINE**\n"
        f"🔄 Iterations: `{session['attempts']}`\n"
        f"🌐 RPC: Connected"
    )
    bot.send_message(chat_id, text, parse_mode='Markdown')

@bot.message_handler(func=lambda message: message.text == '🛑 Stop')
def stop_handler(message):
    chat_id = message.chat.id
    session = user_sessions.get(chat_id)

    if session and session['running']:
        session['running'] = False
        bot.send_message(chat_id, "🛑 **Scanner Stopped.**", reply_markup=types.ReplyKeyboardRemove())
        # Clean up session
        del user_sessions[chat_id]
    else:
        bot.send_message(chat_id, "⚠️ No scan is currently running.", reply_markup=types.ReplyKeyboardRemove())

def main():
    if not BOT_TOKEN:
        print("Please set TELEGRAM_BOT_TOKEN in Replit Secrets.")
        return

    print("🤖 Bot is running...")
    # remove_webhook is useful if switching from webhook to polling
    bot.remove_webhook()
    bot.infinity_polling()

if __name__ == "__main__":
    main()
