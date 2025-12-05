import subprocess
import sys
import os
import time
import threading
import base58
import json
from datetime import datetime

# --- IMPORTS ---
import telebot
from telebot import types
from solana.rpc.api import Client
from solana.rpc.commitment import Confirmed
from solana.rpc.types import TxOpts 
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer, ID as SYSTEM_PROGRAM_ID
from solders.transaction import Transaction 
from solders.message import Message 
from solders.compute_budget import set_compute_unit_price
from solders.instruction import Instruction, AccountMeta
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("NEW_TELEGRAM_BOT_TOKEN")

if not BOT_TOKEN:
    print("❌ ERROR: TELEGRAM_BOT_TOKEN not found in .env")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)
user_sessions = {}

# RPC URL
RPC_URL = "https://api.mainnet-beta.solana.com"
client = Client(RPC_URL, commitment=Confirmed)

# CONSTANTS
TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")

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
        # Decode private key
        if "[" in private_key_str:
            pk_bytes = bytes(json.loads(private_key_str))
            sender_kp = Keypair.from_bytes(pk_bytes)
        else:
            sender_kp = Keypair.from_base58_string(private_key_str)
            
        dest_pubkey = Pubkey.from_string(dest_address)
        sender_pubkey = sender_kp.pubkey()
        
        # 1. SMART CONTRACT CHECK
        acc_info = client.get_account_info(sender_pubkey)
        is_token_acc = False
        
        if acc_info.value:
            owner = acc_info.value.owner
            
            if owner == TOKEN_PROGRAM_ID:
                is_token_acc = True
                bot.send_message(chat_id, "⚠️ **Token Account Detected!**\nSwitching to 'Close Account' mode to reclaim rent.", parse_mode='Markdown')
            elif owner != SYSTEM_PROGRAM_ID:
                bot.send_message(
                    chat_id, 
                    f"⚠️ **WARNING: Smart Contract Detected**\n"
                    f"Owner: `{owner}`\n"
                    f"Only the program can move these funds. Sweeping might fail.", 
                    parse_mode='Markdown'
                )

        # Initial Balance
        init_bal = client.get_balance(sender_pubkey).value / 1000000000
        
        bot.send_message(
            chat_id, 
            f"👀 **Sweeper Active!**\n"
            f"🔑 Key resolves to: `{sender_pubkey}`\n"
            f"💰 Current Balance: `{init_bal} SOL`\n"
            f"🎯 Forwarding to: `{dest_pubkey}`", 
            parse_mode='Markdown'
        )
        
    except Exception as e:
        bot.send_message(chat_id, f"❌ Key Error: {e}")
        session['sweep_running'] = False
        return

    while session.get('sweep_running'):
        try:
            # Check Balance
            balance_resp = client.get_balance(sender_pubkey)
            balance = balance_resp.value
            
            # --- STRATEGY 1: CLOSE TOKEN ACCOUNT (Recover Rent) ---
            if is_token_acc and balance > 0:
                print(f"Attempting to close Token Account: {sender_pubkey}")
                
                # Instruction: CloseAccount (Index 9)
                # Keys: [Account, Dest, Owner]
                # We assume the keypair provided is the Owner (Authority)
                ix_close = Instruction(
                    TOKEN_PROGRAM_ID,
                    bytes([9]), # 9 = CloseAccount
                    [
                        AccountMeta(sender_pubkey, is_signer=False, is_writable=True),
                        AccountMeta(dest_pubkey, is_signer=False, is_writable=True),
                        AccountMeta(sender_pubkey, is_signer=True, is_writable=False) # Sign with itself
                    ]
                )
                
                recent_blockhash = client.get_latest_blockhash().value.blockhash
                msg = Message([ix_close], sender_pubkey)
                txn = Transaction([sender_kp], msg, recent_blockhash)
                
                resp = client.send_transaction(txn, opts=TxOpts(skip_preflight=True))
                
                sol_recovered = balance / 1_000_000_000
                bot.send_message(chat_id, f"♻️ **ACCOUNT CLOSED!**\nRecovered {sol_recovered} SOL Rent\nSig: `{resp.value}`", parse_mode='Markdown')
                
                # Stop after closing (account is gone)
                session['sweep_running'] = False
                return

            # --- STRATEGY 2: STANDARD TRANSFER (System Program) ---
            FEE = 5000 
            if not is_token_acc and balance > FEE:
                amount_to_send = balance - FEE
                print(f"Sweeping: {amount_to_send} lamports")

                ix = transfer(TransferParams(
                    from_pubkey=sender_pubkey,
                    to_pubkey=dest_pubkey,
                    lamports=amount_to_send
                ))
                
                recent_blockhash = client.get_latest_blockhash().value.blockhash
                msg = Message([ix], sender_pubkey)
                txn = Transaction([sender_kp], msg, recent_blockhash)
                
                resp = client.send_transaction(txn, opts=TxOpts(skip_preflight=True))
                
                sol_amt = amount_to_send / 1_000_000_000
                bot.send_message(chat_id, f"🧹 **SWEPT!**\nMoved {sol_amt} SOL\nSig: `{resp.value}`", parse_mode='Markdown')
                time.sleep(10)
            
        except Exception as e:
            if "InvalidAccountForFee" in str(e):
                pass 
            else:
                print(f"Sweep Error: {e}")
            time.sleep(2)
            
        time.sleep(1) 

# --- WORKER: VANITY GENERATOR ---
def vanity_worker(chat_id, prefix):
    session = user_sessions.get(chat_id)
    session['vanity_running'] = True
    
    bot.send_message(chat_id, f"🔨 **Mining Vanity Address...**\nPrefix: `{prefix}`", parse_mode='Markdown')
    
    attempts = 0
    start_time = time.time()
    
    while session.get('vanity_running'):
        attempts += 1
        kp = Keypair()
        addr = str(kp.pubkey())
        
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
            
        if attempts % 50000 == 0:
            pass # Keep alive

# --- HANDLERS ---

@bot.message_handler(commands=['start'])
def start_command(message):
    bot.send_message(message.chat.id, "🤖 **Solana Utility Bot**", reply_markup=get_main_menu())

@bot.message_handler(func=lambda m: m.text == '🧹 Wallet Sweeper')
def sweep_ask_key(message):
    msg = bot.send_message(message.chat.id, "🔐 Enter **Source Private Key** (Base58 or Array):", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, sweep_get_dest)

def sweep_get_dest(message):
    if message.text == '🔙 Cancel': return start_command(message)
    src_key = message.text.strip()
    msg = bot.send_message(message.chat.id, "🏦 Enter **Destination Address**:", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, lambda m: sweep_start(m, src_key))

def sweep_start(message, src_key):
    if message.text == '🔙 Cancel': return start_command(message)
    dest_addr = message.text.strip()
    chat_id = message.chat.id
    
    if chat_id not in user_sessions: user_sessions[chat_id] = {}
    user_sessions[chat_id]['sweep_running'] = True
    
    threading.Thread(target=sweeper_worker, args=(chat_id, src_key, dest_addr), daemon=True).start()
    bot.send_message(chat_id, "✅ Sweeper started.", reply_markup=get_main_menu())

@bot.message_handler(func=lambda m: m.text == '💎 Vanity Address')
def vanity_ask_prefix(message):
    msg = bot.send_message(message.chat.id, "🔤 Enter **Prefix** (Case Sensitive, 1-4 chars):", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, vanity_start)

def vanity_start(message):
    if message.text == '🔙 Cancel': return start_command(message)
    prefix = message.text.strip()
    chat_id = message.chat.id
    
    if chat_id not in user_sessions: user_sessions[chat_id] = {}
    threading.Thread(target=vanity_worker, args=(chat_id, prefix), daemon=True).start()

@bot.message_handler(func=lambda m: m.text == '🛑 Stop All Tasks')
def stop_all(message):
    chat_id = message.chat.id
    if chat_id in user_sessions:
        user_sessions[chat_id]['sweep_running'] = False
        user_sessions[chat_id]['vanity_running'] = False
        bot.send_message(chat_id, "🛑 Stopped.", reply_markup=get_main_menu())

if __name__ == "__main__":
    print("🤖 Bot Started...")
    bot.infinity_polling()
