import subprocess
import sys
import os
import time
import threading
import base58
from datetime import datetime
# --- IMPORTS ---
import telebot
from telebot import types
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solana.transaction import Transaction
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("NEW_TELEGRAM_BOT_TOKEN")

if not BOT_TOKEN:
    print("❌ ERROR: TELEGRAM_BOT_TOKEN not found in .env")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# Store user states
# Structure: { chat_id: { 'mode': 'sweep'/'vanity', 'sweep_running': bool, 'vanity_running': bool } }
user_sessions = {}

# RPC URL (Public Mainnet)
RPC_URL = os.getenv("CUSTOM_RPC")
if not RPC_URL:
    print("error in RPC. using default")
    RPC_URL = "https://api.mainnet-beta.solana.com"
client = Client(RPC_URL)

# --- MENUS ---
def get_main_menu():
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    btn_sweep = types.KeyboardButton('🧹 Wallet Sweeper')
    btn_vanity = types.KeyboardButton('💎 Vanity Address')
    btn_stop = types.KeyboardButton('🛑 Stop All Tasks')
    markup.add(btn_sweep, btn_vanity, btn_stop)
    return markup

def get_cancel_menu():
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    markup.add(types.KeyboardButton('🔙 Cancel'))
    return markup

# --- WORKER: WALLET SWEEPER ---
def sweeper_worker(chat_id, private_key_str, dest_address):
    session = user_sessions.get(chat_id)
    if not session: return

    try:
        # Decode private key (supports Base58 string or list of ints)
        if "[" in private_key_str:
            import json
            pk_bytes = bytes(json.loads(private_key_str))
            sender_kp = Keypair.from_bytes(pk_bytes)
        else:
            sender_kp = Keypair.from_base58_string(private_key_str)
            
        dest_pubkey = Pubkey.from_string(dest_address)
        sender_pubkey = sender_kp.pubkey()
        
        bot.send_message(chat_id, f"👀 **Sweeper Active!**\nWatching: `{sender_pubkey}`\nForwarding to: `{dest_pubkey}`", parse_mode='Markdown')
        
    except Exception as e:
        bot.send_message(chat_id, f"❌ Key Error: {e}")
        session['sweep_running'] = False
        return

    while session.get('sweep_running'):
        try:
            # Check Balance
            balance_resp = client.get_balance(sender_pubkey)
            balance = balance_resp.value
            
            # Fee buffer (5000 lamports for sig + wiggle room)
            FEE_BUFFER = 5000 

            if balance > FEE_BUFFER:
                amount_to_send = balance - FEE_BUFFER
                
                # Create Transaction
                ix = transfer(TransferParams(
                    from_pubkey=sender_pubkey,
                    to_pubkey=dest_pubkey,
                    lamports=amount_to_send
                ))
                
                txn = Transaction().add(ix)
                recent_blockhash = client.get_latest_blockhash().value.blockhash
                txn.recent_blockhash = recent_blockhash
                
                # Send
                resp = client.send_transaction(txn, sender_kp)
                
                sol_amt = amount_to_send / 1_000_000_000
                bot.send_message(chat_id, f"🧹 **SWEPT!**\nMoved {sol_amt} SOL\nSig: `{resp.value}`", parse_mode='Markdown')
                
                # Optional: Sleep longer after success
                time.sleep(10)
            
        except Exception as e:
            print(f"Sweep Error: {e}")
            time.sleep(2)
            
        time.sleep(1) # Check every second

# --- WORKER: VANITY GENERATOR ---
def vanity_worker(chat_id, prefix):
    session = user_sessions.get(chat_id)
    session['vanity_running'] = True
    
    bot.send_message(chat_id, f"🔨 **Mining Vanity Address...**\nPrefix: `{prefix}`\n(This may take time)", parse_mode='Markdown')
    
    attempts = 0
    start_time = time.time()
    
    while session.get('vanity_running'):
        attempts += 1
        kp = Keypair()
        addr = str(kp.pubkey())
        
        # Check matching prefix (Case Sensitive)
        if addr.startswith(prefix):
            duration = round(time.time() - start_time, 2)
            msg = (
                f"💎 **VANITY FOUND!** ({duration}s)\n\n"
                f"🔑 Address: `{addr}`\n"
                f"🔐 Secret: `{kp.secret()}`"
            )
            bot.send_message(chat_id, msg, parse_mode='Markdown')
            session['vanity_running'] = False
            return
            
        # UI Update every 10k attempts
        if attempts % 10000 == 0:
            # print(f"User {chat_id}: {attempts} attempts...")
            pass

# --- HANDLERS ---

@bot.message_handler(commands=['start'])
def start_command(message):
    bot.send_message(message.chat.id, "🤖 **Solana Utility Bot**\nSelect a tool:", reply_markup=get_main_menu())

# --- SWEEPER FLOW ---
@bot.message_handler(func=lambda m: m.text == '🧹 Wallet Sweeper')
def sweep_ask_key(message):
    msg = bot.send_message(message.chat.id, "🔐 Enter the **Source Private Key** (Base58 or Array) to drain from:", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, sweep_get_dest)

def sweep_get_dest(message):
    if message.text == '🔙 Cancel':
        return start_command(message)
        
    src_key = message.text.strip()
    msg = bot.send_message(message.chat.id, "🏦 Enter the **Destination Address** (Where to send funds):", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, lambda m: sweep_start(m, src_key))

def sweep_start(message, src_key):
    if message.text == '🔙 Cancel':
        return start_command(message)
    
    dest_addr = message.text.strip()
    chat_id = message.chat.id
    
    if chat_id not in user_sessions: user_sessions[chat_id] = {}
    user_sessions[chat_id]['sweep_running'] = True
    
    threading.Thread(target=sweeper_worker, args=(chat_id, src_key, dest_addr), daemon=True).start()
    bot.send_message(chat_id, "✅ Sweeper thread started.", reply_markup=get_main_menu())

# --- VANITY FLOW ---
@bot.message_handler(func=lambda m: m.text == '💎 Vanity Address')
def vanity_ask_prefix(message):
    msg = bot.send_message(
        message.chat.id, 
        "🔤 Enter desired **Prefix** (Case Sensitive).\n\n"
        "⚠️ _Warning: 1-3 chars is fast. 4+ chars may take very long._\n"
        "Ex: `cool` or `Sol`", 
        parse_mode='Markdown',
        reply_markup=get_cancel_menu()
    )
    bot.register_next_step_handler(msg, vanity_start)

def vanity_start(message):
    if message.text == '🔙 Cancel':
        return start_command(message)
        
    prefix = message.text.strip()
    chat_id = message.chat.id
    
    # Validation
    allowed_chars = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    if not all(c in allowed_chars for c in prefix):
        bot.send_message(chat_id, "❌ Invalid Base58 characters (0, O, I, l are not allowed).", reply_markup=get_main_menu())
        return
        
    if len(prefix) > 5:
        bot.send_message(chat_id, "❌ Prefix too long (Max 5 chars for cloud bots).", reply_markup=get_main_menu())
        return

    if chat_id not in user_sessions: user_sessions[chat_id] = {}
    threading.Thread(target=vanity_worker, args=(chat_id, prefix), daemon=True).start()

# --- GLOBAL STOP ---
@bot.message_handler(func=lambda m: m.text == '🛑 Stop All Tasks')
def stop_all(message):
    chat_id = message.chat.id
    if chat_id in user_sessions:
        user_sessions[chat_id]['sweep_running'] = False
        user_sessions[chat_id]['vanity_running'] = False
        bot.send_message(chat_id, "🛑 All background tasks stopped.", reply_markup=get_main_menu())
    else:
        bot.send_message(chat_id, "⚠️ Nothing running.", reply_markup=get_main_menu())

if __name__ == "__main__":
    print("🤖 Bot Started...")
    bot.infinity_polling()
