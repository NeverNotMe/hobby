import telebot
import threading
import time
import os
from telebot import types
from solana.rpc.api import Client
from solders.keypair import Keypair
from dotenv import load_dotenv

# 1. Load Environment Variables from .env file
load_dotenv()

# 2. Get Token
BOT_TOKEN = os.getenv("NEW_TELEGRAM_BOT_TOKEN")

# Check if token exists
if not BOT_TOKEN:
    print("❌ CRITICAL ERROR: TELEGRAM_BOT_TOKEN not found.")
    print("Make sure you have a .env file with TELEGRAM_BOT_TOKEN=your_token_here")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# Dictionary to keep track of each user's scanning state
user_sessions = {}

# --- MENU BUILDERS ---

def get_main_menu():
    """Creates the persistent bottom keyboard with control buttons"""
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    btn_start = types.KeyboardButton('🚀 Start Scanning')
    btn_status = types.KeyboardButton('📊 Status')
    btn_stop = types.KeyboardButton('🛑 Stop')
    markup.add(btn_start, btn_status, btn_stop)
    return markup

def get_rpc_menu():
    """Menu for choosing RPC connection"""
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    btn_public = types.KeyboardButton('🌐 Use Public Mainnet')
    btn_cancel = types.KeyboardButton('🔙 Cancel')
    markup.add(btn_public, btn_cancel)
    return markup

# --- SCANNER WORKER ---

def scanner_worker(chat_id):
    """The background thread that runs the scanning loop"""
    session = user_sessions.get(chat_id)
    if not session: return

    rpc_url = session['rpc']
    
    try:
        # Initialize connection
        client = Client(rpc_url)
        # Quick health check
        client.get_epoch_info()
    except Exception as e:
        bot.send_message(
            chat_id, 
            f"⚠️ **RPC Error:** Could not connect.\n`{str(e)}`\nScanning stopped.", 
            parse_mode='Markdown',
            reply_markup=get_main_menu()
        )
        if chat_id in user_sessions:
            user_sessions[chat_id]['running'] = False
        return

    bot.send_message(chat_id, "✅ **RPC Connected!** Hunting for active wallets...", parse_mode='Markdown')

    while session.get('running', False):
        session['attempts'] += 1
        
        try:
            # 1. Generate Wallet
            kp = Keypair()
            pubkey = kp.pubkey()
            
            # 2. Check Balance
            resp = client.get_balance(pubkey)
            balance = resp.value

            if balance > 0:
                sol_balance = balance / 1_000_000_000
                secret_bytes = kp.secret()
                
                msg = (
                    f"🚨 <b>FOUND FUNDED WALLET!</b> 🚨\n\n"
                    f"💰 <b>Balance:</b> {sol_balance} SOL\n"
                    f"🔑 <b>Address:</b> <code>{pubkey}</code>\n"
                    f"🔐 <b>Secret:</b> <code>{secret_bytes}</code>"
                )
                bot.send_message(chat_id, msg, parse_mode='HTML')

        except Exception:
            # RPC failures (rate limits), just wait a bit
            time.sleep(2)

        # Rate limit protection (adjust as needed)
        time.sleep(0.05) 

# --- BOT COMMANDS & BUTTON HANDLERS ---

@bot.message_handler(commands=['start'])
def start_command(message):
    """Entry point for the bot"""
    bot.send_message(
        message.chat.id, 
        "👋 **Solana Sweeper Bot Online**\n\nUse the buttons below to control the scanner.", 
        parse_mode='Markdown',
        reply_markup=get_main_menu()
    )

@bot.message_handler(func=lambda message: message.text == '🚀 Start Scanning')
def request_rpc(message):
    chat_id = message.chat.id
    
    # Don't start if already running
    if chat_id in user_sessions and user_sessions[chat_id].get('running'):
        bot.send_message(chat_id, "⚠️ Scanner is already running!", reply_markup=get_main_menu())
        return

    msg = bot.send_message(
        chat_id, 
        "🔗 **Select RPC Connection:**\n\nPaste a custom HTTPs URL, or click the button below for the public node.", 
        parse_mode='Markdown',
        reply_markup=get_rpc_menu()
    )
    bot.register_next_step_handler(msg, process_rpc_input)

def process_rpc_input(message):
    chat_id = message.chat.id
    text = message.text.strip()

    if text == '🔙 Cancel':
        bot.send_message(chat_id, "Action cancelled.", reply_markup=get_main_menu())
        return

    # Determine URL
    if text == '🌐 Use Public Mainnet':
        rpc_url = "https://api.mainnet-beta.solana.com"
    elif text.startswith("http"):
        rpc_url = text
    else:
        msg = bot.send_message(chat_id, "❌ Invalid URL. Please try again or Cancel:", reply_markup=get_rpc_menu())
        bot.register_next_step_handler(msg, process_rpc_input)
        return

    # Initialize Session
    user_sessions[chat_id] = {
        'running': True,
        'attempts': 0,
        'rpc': rpc_url
    }

    # Start Thread
    threading.Thread(target=scanner_worker, args=(chat_id,), daemon=True).start()

    bot.send_message(
        chat_id, 
        f"🚀 **Scanner Initialized!**\nTargeting: `{rpc_url}`", 
        parse_mode='Markdown',
        reply_markup=get_main_menu()
    )

@bot.message_handler(func=lambda message: message.text == '📊 Status')
def status_handler(message):
    chat_id = message.chat.id
    session = user_sessions.get(chat_id)

    if not session or not session.get('running'):
        bot.send_message(chat_id, "😴 **Status:** Idle (Not scanning)", reply_markup=get_main_menu())
        return

    text = (
        f"🤖 **STATUS REPORT**\n"
        f"✅ System: Online\n"
        f"🔄 Scanned: `{session['attempts']}` wallets\n"
        f"🌐 Connection: Active"
    )
    bot.send_message(chat_id, text, parse_mode='Markdown', reply_markup=get_main_menu())

@bot.message_handler(func=lambda message: message.text == '🛑 Stop')
def stop_handler(message):
    chat_id = message.chat.id
    session = user_sessions.get(chat_id)

    if session and session.get('running'):
        session['running'] = False
        bot.send_message(chat_id, "🛑 **Scanner Stopped.**", reply_markup=get_main_menu())
    else:
        bot.send_message(chat_id, "⚠️ No active scan to stop.", reply_markup=get_main_menu())

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    print("🤖 Bot started...")
    try:
        bot.remove_webhook()
        bot.infinity_polling()
    except Exception as e:
        print(f"❌ Critical Error: {e}")
