#!/usr/bin/env python3
"""letify ☀️ — твой путь к лету · трекер веса, воды, питания и тренировок"""

import subprocess, sys
def _pip(pkg): subprocess.check_call([sys.executable,"-m","pip","install",pkg,"-q"])
try:    import aiogram
except: _pip("aiogram")
try:    import apscheduler
except: _pip("apscheduler")
try:    import zoneinfo; zoneinfo.ZoneInfo("Europe/Moscow")
except: _pip("tzdata")

import asyncio, logging, os, sqlite3, re, json, calendar as _cal_module
from datetime import datetime, timedelta, date as dt_date, time as dt_time

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN не задан")
DB_PATH = "letify.db"
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── ЧАСОВОЙ ПОЯС ────────────────────────────────────────────────────
# Бот всегда работает в московском времени (UTC+3)
from zoneinfo import ZoneInfo
TZ_MSK = ZoneInfo("Europe/Moscow")

def now_msk() -> datetime:
    """Текущее время в МСК (naive datetime для сравнения с хранимыми значениями)."""
    return datetime.now(TZ_MSK).replace(tzinfo=None)

def today_msk() -> dt_date:
    """Текущая дата в МСК."""
    return now_msk().date()

def datetime_now_sql() -> str:
    """Текущее datetime МСК в формате ISO для INSERT в БД."""
    return now_msk().strftime("%Y-%m-%d %H:%M:%S")
bot       = Bot(token=TOKEN)
dp        = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

DAYS_RU   = ["пн","вт","ср","чт","пт","сб","вс"]
DAYS_CRON = ["mon","tue","wed","thu","fri","sat","sun"]

ACTS = {
    "run":   ("🏃", "бег"),
    "walk":  ("🚶", "прогулка"),
    "bike":  ("🚴", "велосипед"),
    "gym":   ("💪", "зал"),
    "yoga":  ("🧘", "йога"),
    "swim":  ("🏊", "плавание"),
    "other": ("☀️",  "другое"),
}
def aico(t): return ACTS.get(t,("✦",""))[0]
def anam(t): return ACTS.get(t,("✦",t))[1]

MEALS = {"breakfast":("🌅","завтрак"),"lunch":("☀️","обед"),
         "dinner":("🌙","ужин"),"snack":("🍑","перекус"),"other":("🌿","другое")}
MEAL_ORDER = ["breakfast","lunch","dinner","snack","other"]
def mico(k): return MEALS.get(k,("✦",""))[0]
def mnam(k): return MEALS.get(k,("✦",k))[1]

# ── ДЕФОЛТНЫЕ ПРОДУКТЫ ──────────────────────────────────────────────
DEFAULT_PRODUCTS = [
    ("банан",           89,  1.1, 0.3, 23.0),
    ("яблоко",          52,  0.3, 0.2, 14.0),
    ("куриная грудка", 165, 31.0, 3.6,  0.0),
    ("яйцо",           155, 13.0,11.0,  1.1),
    ("творог 5%",      121, 17.0, 5.0,  3.0),
    ("гречка варёная",  92,  3.4, 1.0, 20.0),
    ("рис варёный",    130,  2.7, 0.3, 28.0),
    ("хлеб пшен.",     265,  9.0, 3.0, 53.0),
    ("молоко",          52,  3.3, 2.5,  4.8),
    ("творог 0%",       71, 16.0, 0.1,  3.3),
    ("авокадо",        160,  2.0,15.0,  9.0),
    ("греч. йогурт",    59, 10.0, 0.4,  3.6),
]

# ── АКТИВНЫЕ СЕССИИ ──────────────────────────────────────────────────
card_sessions: dict = {}          # uid -> {card_list, card_idx, msg_id}
water_remind_msgs: dict = {}      # uid -> msg_id  (для редактирования уведомления)
workout_timer_msgs: dict = {}     # uid -> msg_id  (для экрана таймера)


# ── FSM СОСТОЯНИЯ ───────────────────────────────────────────────────
class St(StatesGroup):
    # Вес, вода, калории
    weight      = State()
    water_custom= State()
    water_goal  = State()
    goal_weight = State()
    cal_goal    = State()
    calories    = State()
    # Профиль
    pname=State(); pheight=State(); page_age=State()
    # Тренировки
    act_name=State(); act_date=State(); act_timerange=State(); act_remind=State()
    plan_num_input=State()
    plan_upload=State(); plan_upload_days=State()
    # 😴 Сон
    sleep_hours  = State()
    sleep_quality= State()
    # 🍎 Быстрые продукты
    qp_name = State()
    qp_cal  = State()
    qp_prot = State()
    qp_fat  = State()
    qp_carb = State()
    # 🧮 КБЖУ
    kbzhu_grams = State()
    # 🍽 Логирование еды
    food_grams = State()
    # 🔔 Напоминания
    remind_water_interval = State()
    remind_water_manual   = State()
    remind_weight_time    = State()
    remind_report_time    = State()
    remind_report_day     = State()


# ── ИНИЦИАЛИЗАЦИЯ БД ────────────────────────────────────────────────
def db():
    c = sqlite3.connect(DB_PATH); c.row_factory = sqlite3.Row; return c

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, name TEXT DEFAULT '',
            height REAL DEFAULT 0, age INTEGER DEFAULT 0,
            start_weight REAL DEFAULT 0, goal_weight REAL DEFAULT 0,
            water_goal INTEGER DEFAULT 2000, cal_goal INTEGER DEFAULT 2000,
            gender TEXT DEFAULT 'male'
        );
        CREATE TABLE IF NOT EXISTS weight_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            weight REAL, logged_at TEXT DEFAULT (datetime('now','+3 hours'))
        );
        CREATE TABLE IF NOT EXISTS water_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            amount INTEGER, logged_at TEXT DEFAULT (datetime('now','+3 hours'))
        );
        CREATE TABLE IF NOT EXISTS calories_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            amount INTEGER, description TEXT DEFAULT '',
            logged_at TEXT DEFAULT (datetime('now','+3 hours'))
        );
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            name TEXT, type TEXT DEFAULT 'other', scheduled_at TEXT,
            duration INTEGER DEFAULT 30, completed INTEGER DEFAULT 0,
            started_at TEXT, ended_at TEXT, days_of_week TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            show_weight INTEGER DEFAULT 1,
            show_water INTEGER DEFAULT 1,
            show_calories INTEGER DEFAULT 1,
            show_sleep INTEGER DEFAULT 1,
            show_upcoming INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS sleep_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            hours REAL NOT NULL, quality INTEGER DEFAULT 3,
            note TEXT DEFAULT '',
            logged_at TEXT DEFAULT (datetime('now','+3 hours'))
        );
        CREATE TABLE IF NOT EXISTS quick_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            name TEXT, calories INTEGER,
            protein REAL DEFAULT 0, fat REAL DEFAULT 0, carbs REAL DEFAULT 0,
            is_default INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS reminders (
            user_id INTEGER, type TEXT,
            enabled INTEGER DEFAULT 0,
            schedule TEXT DEFAULT '[]',
            interval_hours INTEGER DEFAULT 3,
            report_day INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, type)
        );
        CREATE TABLE IF NOT EXISTS workout_timers (
            user_id INTEGER PRIMARY KEY,
            act_id INTEGER, act_name TEXT,
            started_at TEXT,
            duration_planned INTEGER DEFAULT 30,
            is_active INTEGER DEFAULT 1
        );
        """)
        # ── Миграции ─────────────────────────────────────────────────
        ex = {r[1] for r in c.execute("PRAGMA table_info(activities)")}
        for col, defn in [("days_of_week","TEXT DEFAULT ''"),("started_at","TEXT"),("ended_at","TEXT")]:
            if col not in ex:
                c.execute("ALTER TABLE activities ADD COLUMN {} {}".format(col, defn))
        ux = {r[1] for r in c.execute("PRAGMA table_info(users)")}
        for col, defn in [("cal_goal","INTEGER DEFAULT 2000"),("gender","TEXT DEFAULT 'male'")]:
            if col not in ux:
                c.execute("ALTER TABLE users ADD COLUMN {} {}".format(col, defn))
        rx = {r[1] for r in c.execute("PRAGMA table_info(reminders)")}
        if "report_day" not in rx:
            c.execute("ALTER TABLE reminders ADD COLUMN report_day INTEGER DEFAULT 0")
        sx = {r[1] for r in c.execute("PRAGMA table_info(user_settings)")}
        if "show_sleep" not in sx:
            c.execute("ALTER TABLE user_settings ADD COLUMN show_sleep INTEGER DEFAULT 1")
        if "bar_style" not in sx:
            c.execute("ALTER TABLE user_settings ADD COLUMN bar_style INTEGER DEFAULT 0")
        cx = {r[1] for r in c.execute("PRAGMA table_info(calories_log)")}
        if "meal_type" not in cx:
            c.execute("ALTER TABLE calories_log ADD COLUMN meal_type TEXT DEFAULT 'other'")
        qx = {r[1] for r in c.execute("PRAGMA table_info(quick_products)")}
        if "last_used" not in qx:
            c.execute("ALTER TABLE quick_products ADD COLUMN last_used TEXT DEFAULT ''")

def ensure_defaults(uid):
    """Добавить дефолтные продукты и напоминания для нового пользователя."""
    with db() as c:
        cnt = c.execute("SELECT COUNT(*) FROM quick_products WHERE user_id=?", (uid,)).fetchone()[0]
        if cnt == 0:
            for name, cal, prot, fat, carbs in DEFAULT_PRODUCTS:
                c.execute(
                    "INSERT INTO quick_products (user_id,name,calories,protein,fat,carbs,is_default) VALUES (?,?,?,?,?,?,1)",
                    (uid, name, cal, prot, fat, carbs))
        for rtype in ("water", "weight", "report"):
            c.execute(
                "INSERT OR IGNORE INTO reminders (user_id,type,enabled,schedule,interval_hours) VALUES (?,?,0,'[]',3)",
                (uid, rtype))


# ── HELPERS: ПОЛЬЗОВАТЕЛЬ ───────────────────────────────────────────
def upsert(uid, name=""):
    with db() as c:
        c.execute("INSERT OR IGNORE INTO users (user_id,name) VALUES (?,?)", (uid, name))
        c.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (uid,))
    ensure_defaults(uid)

def guser(uid):
    with db() as c: return c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()

def upd_user(uid, **kw):
    s = ", ".join("{}=?".format(k) for k in kw)
    with db() as c: c.execute("UPDATE users SET {} WHERE user_id=?".format(s), list(kw.values())+[uid])

def gsett(uid):
    with db() as c:
        r = c.execute("SELECT * FROM user_settings WHERE user_id=?", (uid,)).fetchone()
        if not r:
            c.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (uid,))
            r = c.execute("SELECT * FROM user_settings WHERE user_id=?", (uid,)).fetchone()
        return r

def toggle_sett(uid, field):
    with db() as c:
        c.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (uid,))
        cur = c.execute("SELECT {} FROM user_settings WHERE user_id=?".format(field), (uid,)).fetchone()
        val = 0 if (cur[0] if cur else 1) else 1
        c.execute("UPDATE user_settings SET {}=? WHERE user_id=?".format(field), (val, uid))
    return val


# ── HELPERS: ВЕС / ВОДА / КАЛОРИИ ───────────────────────────────────
def log_w(uid, w):
    with db() as c:
        c.execute("INSERT INTO weight_log (user_id,weight) VALUES (?,?)", (uid, w))
        u = c.execute("SELECT start_weight FROM users WHERE user_id=?", (uid,)).fetchone()
        if u and not u["start_weight"]:
            c.execute("UPDATE users SET start_weight=? WHERE user_id=?", (w, uid))

def del_last_w(uid):
    with db() as c:
        r = c.execute("SELECT id FROM weight_log WHERE user_id=? ORDER BY logged_at DESC LIMIT 1", (uid,)).fetchone()
        if r: c.execute("DELETE FROM weight_log WHERE id=?", (r["id"],))

def reset_w(uid):
    with db() as c: c.execute("DELETE FROM weight_log WHERE user_id=?", (uid,))

def weight_hist(uid, n=None):
    with db() as c:
        if n: return c.execute("SELECT * FROM weight_log WHERE user_id=? ORDER BY logged_at DESC LIMIT ?", (uid,n)).fetchall()
        return c.execute("SELECT * FROM weight_log WHERE user_id=? ORDER BY logged_at DESC", (uid,)).fetchall()

def log_water(uid, a):
    with db() as c: c.execute("INSERT INTO water_log (user_id,amount) VALUES (?,?)", (uid, a))

def del_last_water(uid):
    with db() as c:
        r = c.execute("SELECT id FROM water_log WHERE user_id=? ORDER BY logged_at DESC LIMIT 1", (uid,)).fetchone()
        if r: c.execute("DELETE FROM water_log WHERE id=?", (r["id"],))

def reset_water(uid):
    with db() as c: c.execute("DELETE FROM water_log WHERE user_id=? AND date(logged_at)=date('now','+3 hours')", (uid,))

def today_water(uid):
    with db() as c:
        r = c.execute("SELECT COALESCE(SUM(amount),0) t FROM water_log WHERE user_id=? AND date(logged_at)=date('now','+3 hours')", (uid,)).fetchone()
        return r["t"] if r else 0

def log_cal(uid, a, desc="", meal_type="other"):
    with db() as c: c.execute("INSERT INTO calories_log (user_id,amount,description,meal_type) VALUES (?,?,?,?)", (uid, a, desc, meal_type))

def today_cal_by_meal(uid):
    with db() as c:
        rows=c.execute(
            "SELECT meal_type,SUM(amount) s,COUNT(*) n FROM calories_log WHERE user_id=? AND date(logged_at)=date('now','+3 hours') GROUP BY meal_type",
            (uid,)).fetchall()
    return {r["meal_type"]:(r["s"],r["n"]) for r in rows}

def cal_entries_by_meal(uid, meal_type):
    with db() as c:
        return c.execute(
            "SELECT id,amount,description,logged_at FROM calories_log WHERE user_id=? AND meal_type=? AND date(logged_at)=date('now','+3 hours') ORDER BY logged_at",
            (uid, meal_type)).fetchall()

def get_recent_products(uid, n=8):
    with db() as c:
        return c.execute(
            "SELECT * FROM quick_products WHERE user_id=? AND last_used!='' ORDER BY last_used DESC LIMIT ?",
            (uid, n)).fetchall()

def get_recent_products_paged(uid, limit=100):
    with db() as c:
        return c.execute(
            "SELECT * FROM quick_products WHERE user_id=? AND last_used!='' ORDER BY last_used DESC LIMIT ?",
            (uid, limit)).fetchall()

def clear_recent_products(uid):
    with db() as c:
        c.execute("UPDATE quick_products SET last_used='' WHERE user_id=?", (uid,))

def today_cal_by_meal_for_date(uid, date_str):
    with db() as c:
        rows = c.execute(
            "SELECT meal_type,SUM(amount) s,COUNT(*) n FROM calories_log WHERE user_id=? AND date(logged_at)=? GROUP BY meal_type",
            (uid, date_str)).fetchall()
    return {r["meal_type"]: (r["s"], r["n"]) for r in rows}

def cal_meal_entries_for_date(uid, date_str, meal_type):
    with db() as c:
        return c.execute(
            "SELECT id,amount,description,logged_at FROM calories_log WHERE user_id=? AND meal_type=? AND date(logged_at)=? ORDER BY logged_at",
            (uid, meal_type, date_str)).fetchall()

def get_days_with_calories(uid, year, month):
    month_str = "{:04d}-{:02d}".format(year, month)
    with db() as c:
        rows = c.execute(
            "SELECT DISTINCT date(logged_at) d FROM calories_log WHERE user_id=? AND strftime('%Y-%m',logged_at)=?",
            (uid, month_str)).fetchall()
    return {int(r["d"].split("-")[2]) for r in rows}

def mark_product_used(pid):
    with db() as c:
        c.execute("UPDATE quick_products SET last_used=datetime('now','+3 hours') WHERE id=?", (pid,))

def del_last_cal(uid):
    with db() as c:
        r = c.execute("SELECT id FROM calories_log WHERE user_id=? ORDER BY logged_at DESC LIMIT 1", (uid,)).fetchone()
        if r: c.execute("DELETE FROM calories_log WHERE id=?", (r["id"],))

def reset_cal(uid):
    with db() as c: c.execute("DELETE FROM calories_log WHERE user_id=? AND date(logged_at)=date('now','+3 hours')", (uid,))

def today_cal(uid):
    with db() as c:
        r = c.execute("SELECT COALESCE(SUM(amount),0) t FROM calories_log WHERE user_id=? AND date(logged_at)=date('now','+3 hours')", (uid,)).fetchone()
        return r["t"] if r else 0

def water_streak(uid):
    with db() as c:
        goal = (c.execute("SELECT water_goal FROM users WHERE user_id=?", (uid,)).fetchone() or {"water_goal": 2000})["water_goal"]
        rows = c.execute(
            "SELECT date(logged_at) d, SUM(amount) s FROM water_log WHERE user_id=? GROUP BY date(logged_at) ORDER BY date(logged_at) DESC",
            (uid,)).fetchall()
    streak = 0; check = today_msk()
    for r in rows:
        d = dt_date.fromisoformat(r["d"])
        if d != check: break
        if r["s"] >= goal: streak += 1
        else: break
        check -= timedelta(days=1)
    return streak


# ── HELPERS: ТРЕНИРОВКИ ─────────────────────────────────────────────
def get_act(aid):
    with db() as c:
        r = c.execute("SELECT * FROM activities WHERE id=?", (aid,)).fetchone()
        return dict(r) if r else None

def add_act(uid, name, atype, sched, dur, dow):
    with db() as c:
        cur = c.execute(
            "INSERT INTO activities (user_id,name,type,scheduled_at,duration,days_of_week) VALUES (?,?,?,?,?,?)",
            (uid, name, atype, sched, dur, dow))
        return cur.lastrowid

def del_act(aid):
    with db() as c: c.execute("DELETE FROM activities WHERE id=?", (aid,))

def complete_act(aid):
    with db() as c:
        c.execute("UPDATE activities SET completed=1,ended_at=datetime('now','+3 hours') WHERE id=?", (aid,))

def start_act(aid):
    with db() as c:
        c.execute("UPDATE activities SET started_at=datetime('now','+3 hours') WHERE id=?", (aid,))

def acts_for_day(uid, check_date):
    dow = check_date.weekday(); ds = check_date.strftime("%Y-%m-%d")
    with db() as c:
        one = c.execute(
            "SELECT * FROM activities WHERE user_id=? AND days_of_week='' AND date(scheduled_at)=? ORDER BY scheduled_at",
            (uid, ds)).fetchall()
        rec = c.execute("SELECT * FROM activities WHERE user_id=? AND days_of_week!=''", (uid,)).fetchall()
    result = [dict(r) for r in one]
    for r in rec:
        days = [int(d) for d in r["days_of_week"].split(",") if d.strip()]
        if dow in days:
            try:
                t = datetime.fromisoformat(r["scheduled_at"]).time()
                row = dict(r)
                row["scheduled_at"] = datetime.combine(check_date, t).isoformat()
                if row.get("ended_at"):
                    try: row["completed"] = 1 if datetime.fromisoformat(row["ended_at"]).date() == check_date else 0
                    except: row["completed"] = 0
                result.append(row)
            except: result.append(dict(r))
    result.sort(key=lambda x: x["scheduled_at"]); return result

def get_today_card_list(uid):
    return [a["id"] for a in acts_for_day(uid, today_msk())]

def get_smart_card_idx(card_list):
    for i, aid in enumerate(card_list):
        a = get_act(aid)
        if a and not a.get("completed"): return i
    return max(0, len(card_list) - 1)


# ── HELPERS: СОН ────────────────────────────────────────────────────
def log_sleep(uid, hours, quality=3, note=""):
    with db() as c:
        c.execute("INSERT INTO sleep_log (user_id,hours,quality,note) VALUES (?,?,?,?)", (uid, hours, quality, note))

def del_last_sleep(uid):
    with db() as c:
        r = c.execute("SELECT id FROM sleep_log WHERE user_id=? ORDER BY logged_at DESC LIMIT 1", (uid,)).fetchone()
        if r: c.execute("DELETE FROM sleep_log WHERE id=?", (r["id"],))

def reset_sleep(uid):
    with db() as c: c.execute("DELETE FROM sleep_log WHERE user_id=?", (uid,))

def sleep_hist(uid, n=7):
    with db() as c:
        return c.execute("SELECT * FROM sleep_log WHERE user_id=? ORDER BY logged_at DESC LIMIT ?", (uid, n)).fetchall()


# ── HELPERS: БЫСТРЫЕ ПРОДУКТЫ ────────────────────────────────────────
def get_products(uid):
    with db() as c:
        return c.execute("SELECT * FROM quick_products WHERE user_id=? ORDER BY name", (uid,)).fetchall()

def add_product(uid, name, cal, prot=0.0, fat=0.0, carbs=0.0):
    with db() as c:
        c.execute(
            "INSERT INTO quick_products (user_id,name,calories,protein,fat,carbs) VALUES (?,?,?,?,?,?)",
            (uid, name, cal, prot, fat, carbs))

def del_product(pid):
    with db() as c: c.execute("DELETE FROM quick_products WHERE id=?", (pid,))

def get_product(pid):
    with db() as c:
        r = c.execute("SELECT * FROM quick_products WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None


# ── HELPERS: НАПОМИНАНИЯ ────────────────────────────────────────────
def get_reminder(uid, rtype):
    with db() as c:
        r = c.execute("SELECT * FROM reminders WHERE user_id=? AND type=?", (uid, rtype)).fetchone()
        if not r:
            c.execute(
                "INSERT OR IGNORE INTO reminders (user_id,type,enabled,schedule,interval_hours) VALUES (?,?,0,'[]',3)",
                (uid, rtype))
            r = c.execute("SELECT * FROM reminders WHERE user_id=? AND type=?", (uid, rtype)).fetchone()
        return dict(r) if r else {"enabled":0,"schedule":"[]","interval_hours":3,"report_day":0}

def set_reminder(uid, rtype, **kw):
    with db() as c:
        c.execute("INSERT OR IGNORE INTO reminders (user_id,type) VALUES (?,?)", (uid, rtype))
        for k, v in kw.items():
            c.execute("UPDATE reminders SET {}=? WHERE user_id=? AND type=?".format(k), (v, uid, rtype))

def get_all_users():
    with db() as c:
        return [r[0] for r in c.execute("SELECT user_id FROM users").fetchall()]


# ── HELPERS: ТАЙМЕР ТРЕНИРОВКИ ───────────────────────────────────────
def start_wt(uid, act_id, act_name, duration_planned=30):
    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO workout_timers (user_id,act_id,act_name,started_at,duration_planned,is_active) VALUES (?,?,?,datetime('now','+3 hours'),?,1)",
            (uid, act_id, act_name, duration_planned))

def get_wt(uid):
    with db() as c:
        r = c.execute("SELECT * FROM workout_timers WHERE user_id=? AND is_active=1", (uid,)).fetchone()
        return dict(r) if r else None

def stop_wt(uid):
    with db() as c:
        c.execute("UPDATE workout_timers SET is_active=0 WHERE user_id=?", (uid,))


# ── УТИЛИТЫ ─────────────────────────────────────────────────────────
def parse_timerange(raw):
    raw = raw.strip()
    m = re.match(r'^(\d{1,2}(?::\d{2})?)\s*[-–—]\s*(\d{1,2}(?::\d{2})?)$', raw)
    if not m: raise ValueError
    def norm(s): return datetime.strptime(s, "%H:%M") if ':' in s else datetime.strptime(s, "%H")
    t1 = norm(m.group(1)); t2 = norm(m.group(2))
    dur = int((t2-t1).total_seconds()/60)
    if dur <= 0: dur += 1440
    if not (1 <= dur <= 1440): raise ValueError
    return t1.strftime("%H:%M"), t2.strftime("%H:%M"), dur

def parse_time_hm(raw):
    """Парсит 'HH:MM' или 'H:MM'. Возвращает (hour, minute)."""
    raw = raw.strip()
    m = re.match(r'^(\d{1,2}):(\d{2})$', raw)
    if not m: raise ValueError("bad time")
    h, mn = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mn <= 59): raise ValueError("out of range")
    return h, mn

def pbar(pct, n=8, on="🟩", off="⬜"): return on*int(pct/100*n)+off*(n-int(pct/100*n))
def pbar_block(pct, n=10): return "[" + "█"*int(pct/100*n) + "░"*(n-int(pct/100*n)) + "]"
def get_bar_style(uid):
    with db() as c:
        r=c.execute("SELECT bar_style FROM user_settings WHERE user_id=?",(uid,)).fetchone()
        return (r["bar_style"] or 0) if r else 0
def wbar(p,uid=None): return pbar_block(p) if uid and get_bar_style(uid) else pbar(p,8,"🟦","⬜")
def cbar(p,uid=None): return pbar_block(p) if uid and get_bar_style(uid) else pbar(p,8,"🟨","⬜")
def gbar(p,uid=None): return pbar_block(p) if uid and get_bar_style(uid) else pbar(p,8,"🟩","⬜")
def sbar(q,uid=None): return pbar_block(q*20) if uid and get_bar_style(uid) else pbar(q*20,8,"🟣","⬜")
def bq(t): return "<blockquote>{}</blockquote>".format(t)
def strike(t): return "<s>{}</s>".format(t)

def fmt_eta(diff_min):
    if diff_min <= 0: return "сейчас"
    if diff_min < 60: return "{}м".format(int(diff_min))
    h = int(diff_min//60); m = int(diff_min%60)
    return "{}ч{}".format(h, " {}м".format(m) if m else "")

def fmt_dur(minutes):
    minutes = int(minutes)
    if minutes < 60: return "{}м".format(minutes)
    return "{}ч {}м".format(minutes//60, minutes%60) if minutes%60 else "{}ч".format(minutes//60)

def fmt_log_water(rows):
    if not rows: return "<i>пусто</i>"
    lines = "".join("{:5}  +{} мл\n".format(
        datetime.fromisoformat(e["logged_at"]).strftime("%H:%M"), e["amount"]) for e in rows)
    return "<code>{}</code>".format(lines.rstrip())

def fmt_log_cal(rows):
    if not rows: return "<i>пусто</i>"
    lines = ""
    for e in rows:
        t = datetime.fromisoformat(e["logged_at"]).strftime("%H:%M")
        d = "  "+e["description"][:12] if e["description"] else ""
        lines += "{:5}  +{} ккал{}\n".format(t, e["amount"], d)
    return "<code>{}</code>".format(lines.rstrip())

def fmt_log_weight(rows):
    if not rows: return "<i>пусто</i>"
    lines = "".join("{:10}  {} кг\n".format(
        datetime.fromisoformat(r["logged_at"]).strftime("%d.%m.%Y"), r["weight"]) for r in rows)
    return "<code>{}</code>".format(lines.rstrip())

def quality_icon(q):
    return {1:"😫",2:"😴",3:"😑",4:"🙂",5:"🌟"}.get(q,"😑")

def days_to_summer() -> int:
    """Дней до 1 июня текущего / следующего года."""
    today = today_msk()
    summer = dt_date(today.year, 6, 1)
    if summer <= today:
        summer = dt_date(today.year + 1, 6, 1)
    return (summer - today).days

def summer_emoji() -> str:
    """Эмодзи в зависимости от расстояния до лета."""
    d = days_to_summer()
    if d <= 0:   return "🏖️"
    if d <= 7:   return "🌊"
    if d <= 30:  return "🌤"
    if d <= 60:  return "🌸"
    if d <= 90:  return "🌱"
    return "❄️"

def summer_line() -> str:
    d = days_to_summer()
    if d <= 0:   return "лето уже здесь 🏖️"
    if d == 1:   return "завтра лето! 🌊"
    return "до лета  <b>{}</b>  {}".format(d, summer_emoji())

def parse_plan_text(text: str) -> list:
    results = []
    for line in text.splitlines():
        line = line.strip()
        if not line: continue
        m = re.match(r'^(\d{1,2}(?::\d{2})?)\s*[-–—]\s*(\d{1,2}(?::\d{2})?)\s+(.+)$', line)
        if not m: continue
        try:
            def norm(s): return datetime.strptime(s, "%H:%M") if ':' in s else datetime.strptime(s, "%H")
            t1 = norm(m.group(1)); t2 = norm(m.group(2))
            dur = int((t2 - t1).total_seconds() / 60)
            if dur <= 0: dur += 1440
            if not (1 <= dur <= 1440): continue
            results.append({
                "time": t1.strftime("%H:%M"), "time_end": t2.strftime("%H:%M"),
                "duration": dur, "name": m.group(3).strip()[:80], "type": "other",
            })
        except: continue
    return results

def fmt_upload_preview(tasks: list) -> str:
    if not tasks: return "<i>задач не распознано</i>"
    lines = ["{}–{}  {}".format(t["time"], t["time_end"], t["name"]) for t in tasks]
    return "<blockquote expandable>{}</blockquote>".format("\n".join(lines))


# ── КНОПКИ ──────────────────────────────────────────────────────────
def B(text, cb): return InlineKeyboardButton(text=text, callback_data=cb)
def KB(*rows): return InlineKeyboardMarkup(inline_keyboard=[[B(t,d) for t,d in r] for r in rows])
def kb_x(d="main"):    return KB([("✕", d)])
def kb_back(d="main"): return KB([("< назад", d)])

def kb_main(uid):
    acts  = acts_for_day(uid, today_msk())
    total = len(acts); done = sum(1 for a in acts if a.get("completed"))
    plan_label = "📋  план  {}/{}".format(done, total) if total else "📋  план"
    return KB(
        [(plan_label, "plan_cards")],
        [("⚖️", "weight"), ("🌊", "water"), ("🍋", "nutrition")],
        [("🌙", "sleep"),  ("⏱️", "workout_timer"), ("📊", "progress")],
        [("🏖️ профиль", "profile"), ("⚙️ настройки", "settings")],
    )

def kb_weight():
    return KB(
        [("вся история","weight_hist_all"), ("30 дней","weight_hist30")],
        [("↩ удалить последнее","weight_del")],
        [("< назад","main")],
    )

def kb_water():
    return KB(
        [("150 мл","w150"),("200 мл","w200"),("250 мл","w250"),("500 мл","w500")],
        [("своё кол-во","water_custom"), ("↩ удалить","water_del")],
        [("< назад","main")],
    )

def kb_cal():
    return KB(
        [("100","c100"),("200","c200"),("300","c300"),("500","c500")],
        [("700","c700"),("1000","c1000"),("своё","cal_custom")],
        [("↩ удалить","cal_del"),("< назад","nutrition")],
    )

def kb_nutrition():
    return KB(
        [("➕ добавить еду", "food_add")],
        [("📓 дневник",      "food_diary"),  ("🌿 продукты", "quick_products")],
        [("🧮 кбжу",         "kbzhu"),       ("< назад",     "main")],
    )

# ── выбор продукта при логировании ─────────────────────────────────
def kb_food_add(uid):
    recent = get_recent_products(uid, 4)
    rows = []
    if recent:
        rows.append([B("🕐 недавние", "recent_prods")])
    rows.append([B("📋 все продукты","food_all_0"), B("➕ новый","food_new")])
    rows.append([B("< питание","nutrition")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_food_all(uid, page=0):
    prods=get_products(uid); ps=6
    total_p=max(1,(len(prods)+ps-1)//ps); page=max(0,min(page,total_p-1))
    chunk=prods[page*ps:(page+1)*ps]
    rows=[]
    for p in chunk:
        rows.append([B(p["name"],"food_pick_{}".format(p["id"]))])
    if total_p>1:
        rows.append([B("←","food_all_{}".format(page-1) if page>0 else "noop"),
                     B("{} из {}".format(page+1,total_p),"noop"),
                     B("→","food_all_{}".format(page+1) if page<total_p-1 else "noop")])
    rows.append([B("< назад","food_add")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_food_grams(pid):
    return KB(
        [("50г","fg_{}_50".format(pid)), ("100г","fg_{}_100".format(pid)),
         ("150г","fg_{}_150".format(pid)),("200г","fg_{}_200".format(pid))],
        [("250г","fg_{}_250".format(pid)),("300г","fg_{}_300".format(pid)),
         ("400г","fg_{}_400".format(pid)),("500г","fg_{}_500".format(pid))],
        [("✏️ свой вес","fgc_{}".format(pid))],
        [("< назад","food_add")],
    )

def kb_food_meal(pid, grams):
    pg="{}_{}".format(pid,grams)
    return KB(
        [("☀️ завтрак","fs_{}_breakfast".format(pg)),
         ("🌤 обед",   "fs_{}_lunch".format(pg))],
        [("🌙 ужин",   "fs_{}_dinner".format(pg)),
         ("🍫 перекус","fs_{}_snack".format(pg))],
        [("✦ другое",  "fs_{}_other".format(pg))],
        [("← граммы", "food_pick_{}".format(pid))],
    )

def kb_goals():
    return KB(
        [("⚖️ цель по весу","goal_weight"), ("💧 норма воды","water_goal_set")],
        [("🔥 цель по ккал","cal_goal_set")],
        [("📐 идеальный вес","ideal_weight")],
        [("< назад","settings")],
    )

def kb_profile():
    return KB(
        [("👤 имя","pname"),("📏 рост","pheight"),("🎂 возраст","page_age")],
        [("< назад","main")],
    )

def kb_progress():
    return KB(
        [("📅 неделя","week_stats"),("📋 история веса","weight_hist30")],
        [("< назад","main")],
    )

def kb_settings():
    return KB(
        [("📋 план",        "plan_manage"),      ("📤 загрузить план","plan_upload_start")],
        [("🎯 цели и нормы","goals"),            ("🔔 напоминания",   "reminders")],
        [("🏠 экран",       "sett_display"),     ("🗑 сбросить",      "sett_reset")],
        [("< меню",        "main")],
    )

def kb_sett_display(uid):
    s=gsett(uid)
    def ch(v): return "✅" if v else "☐"
    sl=s["show_sleep"] if "show_sleep" in s.keys() else 1
    bs=s["bar_style"]  if "bar_style"  in s.keys() else 0
    bar_lbl="[██░░] блочная" if bs else "🟩⬜ эмодзи"
    return KB(
        [("{} ⚖️ вес".format(ch(s["show_weight"])),      "stog_weight"),
         ("{} 💧 вода".format(ch(s["show_water"])),      "stog_water")],
        [("{} 🔥 калории".format(ch(s["show_calories"])), "stog_calories"),
         ("{} 😴 сон".format(ch(sl)),                    "stog_sleep")],
        [("{} полоса: {}".format(ch(bs),bar_lbl),        "stog_bar_style")],
        [("< настройки","settings")],
    )

def kb_sett_reset():
    return KB(
        [("💧 вода за день","reset_water"),   ("🔥 ккал за день","reset_cal")],
        [("⚖️ история веса","reset_weight"),  ("😴 история сна","reset_sleep")],
        [("< настройки","settings")],
    )

def kb_days(sel: set):
    r1 = [B(("● " if i in sel else "○ ")+DAYS_RU[i], "nday_{}".format(i)) for i in range(4)]
    r2 = [B(("● " if i in sel else "○ ")+DAYS_RU[i], "nday_{}".format(i)) for i in range(4,7)]
    return InlineKeyboardMarkup(inline_keyboard=[
        r1, r2,
        [B("все","nday_all"), B("сбросить","nday_none")],
        [B("далее >","nday_done")],
        [B("✕","plan_manage")],
    ])

def kb_remind_act():
    return KB(
        [("10м","rem_10"),("15м","rem_15"),("30м","rem_30"),("1ч","rem_60")],
        [("2ч","rem_120"),("без напоминания","rem_0")],
        [("✕","plan_manage")],
    )

def kb_upload_days(sel: set):
    r1 = [B(("● " if i in sel else "○ ")+DAYS_RU[i], "upday_{}".format(i)) for i in range(4)]
    r2 = [B(("● " if i in sel else "○ ")+DAYS_RU[i], "upday_{}".format(i)) for i in range(4,7)]
    return InlineKeyboardMarkup(inline_keyboard=[
        r1, r2,
        [B("все","upday_all"), B("сегодня","upday_today"), B("сбросить","upday_none")],
        [B("✅ сохранить план","upday_save")],
        [B("✕ отмена","plan_manage")],
    ])

# ── Клавиатура напоминаний ──────────────────────────────────────────
def kb_reminders(uid):
    wr  = get_reminder(uid, "water")
    wgr = get_reminder(uid, "weight")
    rep = get_reminder(uid, "report")
    def st(r): return "🔔" if r["enabled"] else "🔕"
    def sch_s(r, rtype):
        if not r["enabled"]: return "выкл"
        sch = json.loads(r.get("schedule") or "[]")
        if rtype == "water":
            if r.get("interval_hours"):
                return "каждые {}ч".format(r["interval_hours"])
            return ", ".join(sch[:2]) + ("…" if len(sch)>2 else "")
        return sch[0] if sch else "—"
    return KB(
        [("{} 💧 вода  {}".format(st(wr),  sch_s(wr, "water")),  "remind_water")],
        [("{} ⚖️ взвеситься  {}".format(st(wgr), sch_s(wgr,"weight")), "remind_weight")],
        [("{} 📅 отчёт  {}".format(st(rep), sch_s(rep,"report")), "remind_report")],
        [("< настройки","settings")],
    )

def kb_water_remind_setup(uid):
    wr = get_reminder(uid, "water")
    en = wr["enabled"]
    return KB(
        [("{} вкл/выкл".format("✅" if en else "☐"), "wr_toggle")],
        [("⚡ авто (каждые N часов)", "wr_auto")],
        [("🕐 вручную (выбрать время)", "wr_manual")],
        [("< напоминания","reminders")],
    )

def kb_water_interval():
    return KB(
        [("каждый час","wri_1"), ("каждые 2ч","wri_2"), ("каждые 3ч","wri_3")],
        [("каждые 4ч","wri_4"),  ("каждые 6ч","wri_6")],
        [("< назад","remind_water")],
    )

# ── Клавиатура уведомления о воде (приходит в чат) ─────────────────
def kb_water_notif():
    return KB(
        [("🌊 150","wrlog_150"),("🌊 200","wrlog_200"),("🌊 250","wrlog_250"),("🌊 500","wrlog_500")],
        [("пропустить","wrlog_skip")],
    )

# ── Клавиатура сна ─────────────────────────────────────────────────
def kb_sleep():
    return KB(
        [("5ч","sl_5"),("5.5ч","sl_5.5"),("6ч","sl_6"),("6.5ч","sl_6.5")],
        [("7ч","sl_7"),("7.5ч","sl_7.5"),("8ч","sl_8"),("9ч","sl_9")],
        [("✏️ своё","sl_custom"), ("↩ удалить","sleep_del")],
        [("📋 история","sleep_hist"), ("< назад","main")],
    )

def kb_sleep_quality(hours_str):
    safe = hours_str.replace(".", "d")
    return InlineKeyboardMarkup(inline_keyboard=[
        [B("😫 1","sq_{}_1".format(safe)), B("😴 2","sq_{}_2".format(safe)),
         B("😑 3","sq_{}_3".format(safe)), B("🙂 4","sq_{}_4".format(safe)),
         B("🌟 5","sq_{}_5".format(safe))],
        [B("✕ отмена","sleep")],
    ])

# ── Клавиатура быстрых продуктов ────────────────────────────────────
def kb_quick_products(uid, page=0):
    prods = get_products(uid); ps = 4
    total_p = max(1,(len(prods)+ps-1)//ps)
    page = max(0,min(page,total_p-1))
    chunk = prods[page*ps:(page+1)*ps]
    rows = []
    for p in chunk:
        rows.append([B(p["name"], "qp_log_{}".format(p["id"]))])
    nav = []
    if page > 0: nav.append(B("<","qp_page_{}".format(page-1)))
    nav.append(B("{}/{}".format(page+1,total_p),"noop"))
    if page < total_p-1: nav.append(B(">","qp_page_{}".format(page+1)))
    if nav: rows.append(nav)
    rows.append([B("➕ добавить","qp_add"), B("🗑 удалить","qp_del_mode")])
    rows.append([B("< назад","nutrition")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_qp_delete_mode(uid, page=0):
    prods = get_products(uid); ps = 4
    total_p = max(1,(len(prods)+ps-1)//ps)
    page = max(0,min(page,total_p-1))
    chunk = prods[page*ps:(page+1)*ps]
    rows = []
    for p in chunk:
        rows.append([B("🗑 {}".format(p["name"]), "qp_dodel_{}".format(p["id"]))])
    nav = []
    if page > 0: nav.append(B("<","qpdm_{}".format(page-1)))
    nav.append(B("{}/{}".format(page+1,total_p),"noop"))
    if page < total_p-1: nav.append(B(">","qpdm_{}".format(page+1)))
    rows.append(nav)
    rows.append([B("< назад","quick_products")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ── Клавиатура КБЖУ-калькулятора ────────────────────────────────────
def kb_kbzhu(uid, page=0):
    prods = get_products(uid); ps = 5
    total_p = max(1,(len(prods)+ps-1)//ps)
    page = max(0,min(page,total_p-1))
    chunk = prods[page*ps:(page+1)*ps]
    rows = []
    for p in chunk:
        rows.append([B(p["name"], "kbzhu_pick_{}".format(p["id"]))])
    nav = []
    if page > 0: nav.append(B("<","kbzhu_page_{}".format(page-1)))
    nav.append(B("{}/{}".format(page+1,total_p),"noop"))
    if page < total_p-1: nav.append(B(">","kbzhu_page_{}".format(page+1)))
    if nav: rows.append(nav)
    rows.append([B("< назад","nutrition")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ── Клавиатура таймера ──────────────────────────────────────────────
def kb_workout_timer_active():
    return KB(
        [("🔄 обновить","wt_refresh")],
        [("✅ завершить","wt_finish"), ("❌ отменить","wt_cancel")],
        [("< назад","main")],
    )

def kb_workout_timer_empty():
    return KB(
        [("📋 к задачам","plan_cards")],
        [("< назад","main")],
    )

# ── Клавиатура календаря дневника ────────────────────────────────────
_MONTH_NAMES = ["","январь","февраль","март","апрель","май","июнь",
                "июль","август","сентябрь","октябрь","ноябрь","декабрь"]

def kb_diary_cal(uid, year, month):
    days_with = get_days_with_calories(uid, year, month)
    prev_m = month-1; prev_y = year
    if prev_m < 1: prev_m = 12; prev_y = year-1
    next_m = month+1; next_y = year
    if next_m > 12: next_m = 1; next_y = year+1
    rows = []
    rows.append([
        B("← " + _MONTH_NAMES[prev_m], "diary_cal_{}_{}".format(prev_y, prev_m)),
        B("{} {}".format(_MONTH_NAMES[month], year), "noop"),
        B(_MONTH_NAMES[next_m] + " →", "diary_cal_{}_{}".format(next_y, next_m)),
    ])
    rows.append([B(d,"noop") for d in ["пн","вт","ср","чт","пт","сб","вс"]])
    today = today_msk()
    for week in _cal_module.monthcalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append(B(" ","noop"))
            else:
                d = dt_date(year, month, day)
                if d > today:
                    row.append(B(str(day),"noop"))
                else:
                    marker = "•" if day in days_with else ""
                    ds = "{:04d}-{:02d}-{:02d}".format(year, month, day)
                    row.append(B("{}{}".format(day, marker), "diary_date_{}".format(ds)))
        rows.append(row)
    rows.append([B("< назад к дневнику","food_diary")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ── Клавиатура недавних продуктов ────────────────────────────────────
def kb_recent_products(uid, page=0):
    recent = get_recent_products_paged(uid, 100)
    ps = 4; total_p = max(1,(len(recent)+ps-1)//ps)
    page = max(0,min(page,total_p-1))
    chunk = recent[page*ps:(page+1)*ps]
    rows = []
    for p in chunk:
        rows.append([B(p["name"], "food_pick_{}".format(p["id"]))])
    nav = []
    if page > 0: nav.append(B("<","recent_page_{}".format(page-1)))
    nav.append(B("{}/{}".format(page+1,total_p),"noop"))
    if page < total_p-1: nav.append(B(">","recent_page_{}".format(page+1)))
    if nav: rows.append(nav)
    rows.append([B("🗑 очистить недавние","recent_clear")])
    rows.append([B("< назад","food_add")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ── Клавиатура карточки ─────────────────────────────────────────────
def kb_card(idx, card_list, aid):
    rows = []
    a = get_act(aid)
    now = now_msk()
    if a and not a.get("completed"):
        started = bool(a.get("started_at"))
        try:
            dt_a = datetime.fromisoformat(a["scheduled_at"])
            diff_min = (dt_a - now).total_seconds() / 60
            if started:
                rows.append([B("✅ завершить","card_complete_{}".format(aid)),
                             B("⏱ таймер","wt_from_card_{}".format(aid))])
            elif diff_min <= 5:
                rows.append([B("▶️ начать","card_start_{}".format(aid)),
                             B("⏱ таймер","wt_from_card_{}".format(aid))])
        except:
            if started:
                rows.append([B("✅ завершить","card_complete_{}".format(aid))])
    nav = []
    nav.append(B("<","card_nav_{}".format(idx-1)) if idx>0 else B("·","noop"))
    nav.append(B(">","card_nav_{}".format(idx+1)) if idx<len(card_list)-1 else B("·","noop"))
    rows.append(nav)
    rows.append([B("< назад","main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── ТЕКСТ КАРТОЧКИ ───────────────────────────────────────────────────
def build_card_text(uid, idx, card_list):
    if not card_list:
        return "📋  <b>нет задач на сегодня</b>\n\nдобавь через ⚙️ → управление планом"
    idx = max(0, min(idx, len(card_list)-1))
    aid = card_list[idx]
    a   = get_act(aid)
    if not a: return "❌ задача не найдена"

    now       = now_msk()
    completed = bool(a.get("completed"))
    started   = bool(a.get("started_at")) and not completed
    remaining = sum(1 for c in card_list if not (get_act(c) or {}).get("completed"))

    name_s = "{}  <b>{}</b>".format(aico(a["type"]), a["name"])
    if completed: name_s = strike(name_s) + "  ✅"

    pos_s = "<i>{} / {}</i>".format(idx+1, len(card_list))
    time_line = ""; status_bq = ""
    try:
        dt_a = datetime.fromisoformat(a["scheduled_at"])
        dur  = a.get("duration") or 30
        dt_e = dt_a + timedelta(minutes=dur)
        diff = (dt_a - now).total_seconds() / 60
        t_s  = dt_a.strftime("%H:%M"); t_e = dt_e.strftime("%H:%M")
        if not completed and not started and diff > 0:
            time_line = "{} – {}  ·  через <b>{}</b>".format(t_s, t_e, fmt_eta(diff))
        else:
            time_line = "{} – {}".format(t_s, t_e)
        if not completed:
            if started:
                rem = int((dt_e - now).total_seconds() / 60)
                msg = "до конца {} мин".format(rem) if rem > 0 else "время вышло"
                status_bq = "<blockquote>▶️  выполняется  ·  {}</blockquote>".format(msg)
            elif diff < 0:
                status_bq = "<blockquote>⚠️  просрочено на {} мин</blockquote>".format(int(-diff))
    except: pass

    upcoming_lines = []
    for c in card_list:
        if c == aid: continue
        ca = get_act(c)
        if ca and not ca.get("completed"):
            try:
                u_dt = datetime.fromisoformat(ca["scheduled_at"])
                u_e  = (u_dt + timedelta(minutes=(ca.get("duration") or 30))).strftime("%H:%M")
                upcoming_lines.append("{}  {}  {}–{}".format(
                    aico(ca["type"]), ca["name"], u_dt.strftime("%H:%M"), u_e))
            except:
                upcoming_lines.append("{}  {}".format(aico(ca["type"]), ca["name"]))
            if len(upcoming_lines) == 2: break

    lines = [pos_s, "", name_s]
    if time_line: lines.append(time_line)
    lines.append("осталось: <b>{}</b>".format(remaining))
    if status_bq: lines += ["", status_bq]
    if upcoming_lines: lines += ["", "<i>" + "\n".join(upcoming_lines) + "</i>"]
    return "\n".join(lines)


# ── ЭКРАНЫ ──────────────────────────────────────────────────────────
def scr_main(uid):
    u = guser(uid); s = gsett(uid)
    water=today_water(uid); cal=today_cal(uid)
    wg=u["water_goal"] or 2000; cg=u["cal_goal"] or 2000
    wp=min(100,int(water/wg*100)); cp=min(100,int(cal/cg*100))
    lw=weight_hist(uid,1)
    w_s="{:.1f}".format(lw[0]["weight"]) if lw else "—"
    g_s="{:.1f}".format(u["goal_weight"]) if u["goal_weight"] else "—"
    streak=water_streak(uid); now=now_msk()
    name=u["name"] or "привет"

    parts=["<b>{}</b>  <i>{}</i>".format(name, now.strftime("%d.%m  %H:%M")),""]
    parts.append("☀️  "+summer_line()); parts.append("")

    if s["show_weight"]:
        parts.append("⚖️  <b>{} → {} кг</b>".format(w_s,g_s)); parts.append("")

    if s["show_water"]:
        st_s="  🔥 {}д".format(streak) if streak>=2 else ""
        parts.append("🌊  <b>{} / {} мл</b>{}".format(water,wg,st_s))
        parts.append(wbar(wp,uid)+"  {}%".format(wp)); parts.append("")

    if s["show_calories"]:
        parts.append("🍋  <b>{} / {} ккал</b>".format(cal,cg))
        parts.append(cbar(cp,uid)+"  {}%".format(cp)); parts.append("")

    sl_flag = s["show_sleep"] if "show_sleep" in s.keys() else 1
    if sl_flag:
        sl_rows=sleep_hist(uid,1)
        if sl_rows:
            sl_r=sl_rows[0]; sl_h=sl_r["hours"]; sl_q=sl_r["quality"] or 3
            parts.append("🌙  <b>{:.1f}ч</b>  {}  {}".format(sl_h,quality_icon(sl_q),sbar(sl_q,uid)))
            parts.append("")

    acts=acts_for_day(uid,today_msk())
    if acts:
        plan_lines=[]
        for a in acts:
            completed=bool(a.get("completed")); started=bool(a.get("started_at")) and not completed
            try:
                dt_a=datetime.fromisoformat(a["scheduled_at"])
                diff=(dt_a-now).total_seconds()/60
                t_s=dt_a.strftime("%H:%M"); t_e=(dt_a+timedelta(minutes=(a.get("duration") or 30))).strftime("%H:%M")
                time_s="{}–{}".format(t_s,t_e)
            except: time_s=""; diff=9999
            row="{}  {}  <i>{}</i>".format(aico(a["type"]),a["name"],time_s)
            if completed:   row=strike(row)+" ✅"
            elif started:   row=row+" ▶️"
            elif diff<0:    row=row+" ⚠️"
            plan_lines.append(row)
        while parts and parts[-1]=="": parts.pop()
        parts.append("")
        parts.append("<blockquote expandable>{}</blockquote>".format("\n".join(plan_lines)))

    while parts and parts[-1]=="": parts.pop()
    return "\n".join(parts), kb_main(uid)

def scr_weight(uid):
    hist=weight_hist(uid,7); u=guser(uid)
    sw,gw=u["start_weight"],u["goal_weight"]
    prog=""
    if sw and gw and hist:
        cw=hist[0]["weight"]; lost=sw-cw; need=max(0,cw-gw); tot=sw-gw
        pct=max(0,min(100,int(lost/tot*100))) if tot else 0
        all_h=weight_hist(uid)
        rate=0.0; rate_s=""
        if len(all_h)>=2:
            try:
                d1=datetime.fromisoformat(all_h[-1]["logged_at"])
                d2=datetime.fromisoformat(all_h[0]["logged_at"])
                days=(d2-d1).days or 1
                rate=(all_h[-1]["weight"]-all_h[0]["weight"])/days*7
                rate_s="  <i>{:+.2f} кг/нед</i>".format(rate)
            except: pass
        forecast=""
        if need>0 and abs(rate)>0.01:
            try:
                days_left=int(need/abs(rate)*7)
                eta_d=(today_msk()+timedelta(days=days_left)).strftime("%d.%m.%Y")
                forecast="\nпрогноз  <b>{}</b>  ☀️".format(eta_d)
            except: pass
        prog="\n\n{} {}%{}{}".format(gbar(pct,uid),pct,rate_s,forecast)
    total=len(weight_hist(uid))
    log_block=bq(fmt_log_weight(hist)) if hist else "<i>нет записей</i>"
    text="⚖️  <b>вес</b>  <i>последние 7</i>\n\n{}{}\n\n<i>всего {} записей · отправь число</i>".format(log_block,prog,total)
    return text, kb_weight()

def scr_weight_hist(uid, page=0, ps=20):
    all_h=weight_hist(uid); total=len(all_h)
    pages=max(1,(total+ps-1)//ps); page=max(0,min(page,pages-1))
    chunk=all_h[page*ps:(page+1)*ps]
    nav=[]
    if page>0: nav.append(B("<","wh_p{}".format(page-1)))
    nav.append(B("{}/{}".format(page+1,pages),"noop"))
    if page<pages-1: nav.append(B(">","wh_p{}".format(page+1)))
    kb=InlineKeyboardMarkup(inline_keyboard=[nav,[B("< назад","weight")]])
    return "⚖️  <b>история</b>  <i>{} записей</i>\n\n{}".format(total,bq(fmt_log_weight(chunk))), kb

def scr_water(uid):
    u=guser(uid); today=today_water(uid); goal=u["water_goal"] or 2000
    pct=min(100,int(today/goal*100)); streak=water_streak(uid)
    with db() as c:
        rows=c.execute(
            "SELECT amount,logged_at FROM water_log WHERE user_id=? AND date(logged_at)=date('now','+3 hours') ORDER BY logged_at DESC LIMIT 10",
            (uid,)).fetchall()
    text="🌊  <b>вода</b>\n\n<b>{} / {} мл</b>{}\n{} {}%\n\n{}".format(
        today,goal,"  🔥{}д".format(streak) if streak>=2 else "",
        wbar(pct,uid),pct, bq(fmt_log_water(rows)) if rows else "<i>пусто</i>")
    return text, kb_water()

def scr_cal(uid):
    u=guser(uid); today=today_cal(uid); goal=u["cal_goal"] or 2000
    pct=min(100,int(today/goal*100))
    with db() as c:
        rows=c.execute(
            "SELECT amount,description,logged_at FROM calories_log WHERE user_id=? AND date(logged_at)=date('now','+3 hours') ORDER BY logged_at DESC LIMIT 10",
            (uid,)).fetchall()
    text="🍋  <b>калории</b>\n\n<b>{} / {} ккал</b>\n{} {}%\n\n{}".format(
        today,goal,cbar(pct,uid),pct,
        bq(fmt_log_cal(rows)) if rows else "<i>пусто</i>")
    return text, kb_cal()

def scr_goals(uid):
    u=guser(uid); lw=weight_hist(uid,1)
    cw=lw[0]["weight"] if lw else None; gw=u["goal_weight"]; sw=u["start_weight"]
    prog=""; forecast_s=""
    if cw and gw and sw:
        lost=abs(sw-cw); need=max(0,cw-gw) if gw<cw else max(0,gw-cw)
        tot=abs(sw-gw); pct=max(0,min(100,int(lost/tot*100))) if tot else 0
        prog="\n\n{} {}%  ·  ещё {:.1f} кг".format(gbar(pct,uid),pct,need)
        all_h=weight_hist(uid)
        if len(all_h)>=2 and need>0:
            try:
                d1=datetime.fromisoformat(all_h[-1]["logged_at"])
                d2=datetime.fromisoformat(all_h[0]["logged_at"])
                days=(d2-d1).days or 1
                rate=abs(all_h[-1]["weight"]-all_h[0]["weight"])/days
                if rate>0.001:
                    days_left=int(need/rate)
                    eta_d=(today_msk()+timedelta(days=days_left)).strftime("%d.%m.%Y")
                    forecast_s="\nпри текущем темпе → <b>{}</b>  ☀️".format(eta_d)
            except: pass
    tbl="старт    {}\nсейчас   {}\nцель     {}\nвода     {} мл/д\nккал     {} ккал/д".format(
        "{:.1f} кг".format(sw) if sw else "—",
        "{:.1f} кг".format(cw) if cw else "—",
        "{:.1f} кг".format(gw) if gw else "—",
        u["water_goal"] or 2000, u["cal_goal"] or 2000)
    return "🎯  <b>цели · путь к лету</b>\n\n<code>{}</code>{}{}".format(tbl,prog,forecast_s), kb_goals()

def scr_profile(uid):
    u=guser(uid); lw=weight_hist(uid,1)
    extra=""
    if u["height"] and lw:
        w=lw[0]["weight"]; h=u["height"]/100; bmi=w/(h*h)
        cat="недовес" if bmi<18.5 else "норма" if bmi<25 else "избыток" if bmi<30 else "ожирение"
        extra+="\nИМТ      {:.1f}  ({})".format(bmi,cat)
        extra+="\nидеал    {:.1f} кг".format((u["height"]-100)*0.9)
    if u["height"] and u["age"] and lw:
        w=lw[0]["weight"]
        # Mifflin-St Jeor (пол учитываем)
        gender=u.get("gender","male") or "male"
        if gender=="female":
            bmr=10*w+6.25*u["height"]-5*u["age"]-161
        else:
            bmr=10*w+6.25*u["height"]-5*u["age"]+5
        tdee=bmr*1.4
        extra+="\nBMR      {:.0f} ккал".format(bmr)
        extra+="\nTDEE     {:.0f} ккал".format(tdee)
        extra+="\nвода     {} мл/д".format(int(w*35))
        if u["goal_weight"] and u["goal_weight"]<w:
            extra+="\nдля цели {} ккал/д".format(int(tdee-500))
    tbl="имя      {}\nрост     {}\nвозраст  {}{}".format(
        (u["name"] or "—")[:14],
        "{:.0f} см".format(u["height"]) if u["height"] else "—",
        "{} лет".format(u["age"]) if u["age"] else "—",
        extra)
    return "🏖️  <b>профиль</b>\n\n<code>{}</code>".format(tbl), kb_profile()

def scr_progress(uid):
    with db() as c:
        ta=c.execute("SELECT COUNT(*) cnt FROM activities WHERE user_id=? AND completed=1",(uid,)).fetchone()["cnt"]
        tw=c.execute("SELECT COALESCE(SUM(amount),0) t FROM water_log WHERE user_id=?",(uid,)).fetchone()["t"]
        w7=c.execute("SELECT COALESCE(SUM(amount),0) t FROM water_log WHERE user_id=? AND date(logged_at)>=date('now','+3 hours','-7 days')",(uid,)).fetchone()["t"]
        c7=c.execute("SELECT COALESCE(SUM(amount),0) t FROM calories_log WHERE user_id=? AND date(logged_at)>=date('now','+3 hours','-7 days')",(uid,)).fetchone()["t"]
        d7=c.execute("SELECT COUNT(DISTINCT date(logged_at)) cnt FROM water_log WHERE user_id=? AND date(logged_at)>=date('now','+3 hours','-7 days')",(uid,)).fetchone()["cnt"] or 1
        sl=c.execute("SELECT AVG(hours) a FROM sleep_log WHERE user_id=? AND date(logged_at)>=date('now','+3 hours','-7 days')",(uid,)).fetchone()["a"]
    streak=water_streak(uid)
    sleep_s="{:.1f}ч".format(sl) if sl else "—"
    tbl="тренировок   {}\nвода всего   {:.1f} л\nсерия вода   {} дн\n─────────────────\nср вода/день {} мл\nср ккал/день {}\nср сон/ночь  {}\n─────────────────\n{}".format(
        ta,tw/1000,streak,w7//d7,c7//d7,sleep_s,summer_line())
    return "📊  <b>путь к лету</b>\n\n<code>{}</code>".format(tbl), kb_progress()

def scr_week_stats(uid):
    with db() as c:
        water7=c.execute("SELECT COALESCE(SUM(amount),0) t FROM water_log WHERE user_id=? AND date(logged_at)>=date('now','+3 hours','-7 days')",(uid,)).fetchone()["t"]
        cal7=c.execute("SELECT COALESCE(SUM(amount),0) t FROM calories_log WHERE user_id=? AND date(logged_at)>=date('now','+3 hours','-7 days')",(uid,)).fetchone()["t"]
        acts7=c.execute("SELECT COUNT(*) cnt FROM activities WHERE user_id=? AND completed=1 AND date(ended_at)>=date('now','+3 hours','-7 days')",(uid,)).fetchone()["cnt"]
        sl7=c.execute("SELECT AVG(hours) a, COUNT(*) cnt FROM sleep_log WHERE user_id=? AND date(logged_at)>=date('now','+3 hours','-7 days')",(uid,)).fetchone()
        lw=c.execute("SELECT weight FROM weight_log WHERE user_id=? AND date(logged_at)>=date('now','+3 hours','-7 days') ORDER BY logged_at",(uid,)).fetchall()
    u=guser(uid); wgoal=(u["water_goal"] or 2000)*7
    wp=min(100,int(water7/wgoal*100))
    wdelta=""
    if len(lw)>=2: wdelta="  Δ{:+.1f}кг".format(lw[-1]["weight"]-lw[0]["weight"])
    sleep_s="{:.1f}ч".format(sl7["a"]) if sl7["a"] else "—"
    tbl="вода      {} / {} мл\nккал      {} ккал\nтренировок  {}\nсон (ср)   {}{}".format(
        water7,wgoal,cal7,acts7,sleep_s,wdelta)
    return "📅  <b>неделя</b>\n\n<code>{}</code>\n\n{} {}%\n\n<i>{}</i>".format(tbl,wbar(wp,uid),wp,summer_line()), KB([("< статистика","progress")])

def scr_settings(uid):
    acts=acts_for_day(uid,today_msk())
    total=len(acts); done=sum(1 for a in acts if a.get("completed"))
    plan_s="нет задач" if not total else "{} из {} выполнено".format(done,total)
    return "⚙️  <b>настройки</b>\n\nплан сегодня: <i>{}</i>\n\n<i>{}</i>".format(plan_s, summer_line()), kb_settings()

def scr_sett_display(uid):
    s=gsett(uid)
    def ch(v): return "✅" if v else "☐"
    sl=s["show_sleep"] if "show_sleep" in s.keys() else 1
    bs=s["bar_style"]  if "bar_style"  in s.keys() else 0
    bar_lbl="[██░░] блочная" if bs else "🟩⬜ эмодзи"
    lines=[
        "{} ⚖️ вес".format(ch(s["show_weight"])),
        "{} 💧 вода".format(ch(s["show_water"])),
        "{} 🔥 калории".format(ch(s["show_calories"])),
        "{} 😴 сон".format(ch(sl)),
        "",
        "{} полоса: {}".format(ch(bs),bar_lbl),
    ]
    return "🏠  <b>главная</b>\n\n{}\n\n<i>нажми чтобы переключить</i>".format("\n".join(lines)), kb_sett_display(uid)

def scr_sett_reset():
    return "🗑  <b>сброс данных</b>\n\nчто сбросить?", kb_sett_reset()

def scr_plan_manage(uid, day_offset=0):
    day_offset=day_offset%7
    today=today_msk()
    sel=today-timedelta(days=today.weekday())+timedelta(days=day_offset)
    acts=acts_for_day(uid,sel)
    dlabel="сегодня" if sel==today else "завтра" if sel==today+timedelta(days=1) else sel.strftime("%d.%m")
    lines=[]
    for i,a in enumerate(acts,1):
        try:
            dt_a=datetime.fromisoformat(a["scheduled_at"])
            t_s=dt_a.strftime("%H:%M"); dur=a.get("duration") or 30
            t_e=(dt_a+timedelta(minutes=dur)).strftime("%H:%M")
            time_s="{}–{}".format(t_s,t_e)
        except: time_s="--:--"
        done=bool(a.get("completed"))
        task="{}  {}  {}".format(aico(a["type"]),a["name"],time_s)
        lines.append("{}. ".format(i)+(strike(task)+" ✅" if done else task))
    block="<blockquote>{}\n<i>отправь номер для управления</i></blockquote>".format(
        "\n".join(lines)) if lines else "<blockquote><i>нет задач</i></blockquote>"
    text="📋  <b>{}</b>  {}\n{}".format(DAYS_RU[day_offset],dlabel,block)
    pd=(day_offset-1)%7; nd=(day_offset+1)%7
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [B("< "+DAYS_RU[pd],"pman_d{}".format(pd)), B(DAYS_RU[nd]+" >","pman_d{}".format(nd))],
        [B("< настройки","settings")],
    ])
    return text, kb

def scr_plan_intro(uid):
    acts=acts_for_day(uid,today_msk())
    total=len(acts); done=sum(1 for a in acts if a.get("completed")); now=now_msk()
    if total==0:
        text="📋  <b>план на сегодня</b>\n\n<i>задач пока нет</i>\n\nдобавь через ⚙️ → управление планом\n\n<i>{}</i>".format(summer_line())
        kb=KB([("⚙️ настройки","settings"),("< назад","main")])
        return text, kb
    lines=[]
    for a in acts:
        try:
            dt_a=datetime.fromisoformat(a["scheduled_at"])
            t_s=dt_a.strftime("%H:%M"); dur=a.get("duration") or 30
            t_e=(dt_a+timedelta(minutes=dur)).strftime("%H:%M"); time_s="{}–{}".format(t_s,t_e)
        except: time_s=""
        completed=bool(a.get("completed"))
        name_s=(strike("{}  {}  {}".format(aico(a["type"]),a["name"],time_s))+" ✅") if completed \
               else "{}  {}  <i>{}</i>".format(aico(a["type"]),a["name"],time_s)
        lines.append(name_s)
    remaining=total-done
    if done==0:     status_s="всё впереди — начнём! 🌱";  action_btn=("▶️ начать","plan_cards")
    elif done==total: status_s="всё выполнено — ты молодец! 🏆☀️"; action_btn=("📋 посмотреть","plan_cards")
    else:           status_s="осталось {} из {}  💪".format(remaining,total); action_btn=("▶️ продолжить","plan_cards")
    block="<blockquote expandable>{}</blockquote>".format("\n".join(lines))
    text="📋  <b>план на сегодня</b>\n\n{}\n<i>{}</i>".format(block,status_s)
    return text, KB([action_btn],[("< назад","main")])

def scr_card_open(uid, idx=None):
    card_list=get_today_card_list(uid)
    if not card_list:
        return build_card_text(uid,0,[]), kb_back("main")
    if idx is None: idx=get_smart_card_idx(card_list)
    idx=max(0,min(idx,len(card_list)-1))
    return build_card_text(uid,idx,card_list), kb_card(idx,card_list,card_list[idx]), card_list, idx

def ideal_weight_text(uid):
    u=guser(uid); lw=weight_hist(uid,1)
    if not u["height"]: return "⚠️ сначала укажи рост в профиле"
    h=u["height"]; cw=lw[0]["weight"] if lw else None
    lorentz=h-100-(h-150)/4; broca=(h-100)*0.9
    devine_m=50+2.3*((h/2.54)-60); devine_f=45.5+2.3*((h/2.54)-60)
    avg=(lorentz+broca+devine_m+devine_f)/4
    h_m=h/100; bmi_lo=round(18.5*h_m*h_m,1); bmi_hi=round(24.9*h_m*h_m,1)
    def diff(v): return "  ({:+.1f})".format(cw-v) if cw else ""
    tbl=("рост       {:.0f} см\n─────────────────\n"
         "лоренц     {:.1f} кг{}\nброка      {:.1f} кг{}\n"
         "девайн ♂   {:.1f} кг{}\nдевайн ♀   {:.1f} кг{}\n"
         "среднее    {:.1f} кг{}\n─────────────────\n"
         "иmt 18.5   {:.1f} кг\nимт 24.9   {:.1f} кг").format(
        h,lorentz,diff(lorentz),broca,diff(broca),
        devine_m,diff(devine_m),devine_f,diff(devine_f),
        avg,diff(avg),bmi_lo,bmi_hi)
    note="\n\n<i>в скобках — разница с текущим весом</i>" if cw else ""
    return "📐  <b>идеальный вес</b>\n\n<code>{}</code>{}".format(tbl,note)

# ── ЭКРАН: СОН ──────────────────────────────────────────────────────
def scr_sleep(uid):
    rows=sleep_hist(uid,7)
    lines=[]
    for r in rows:
        d=datetime.fromisoformat(r["logged_at"]).strftime("%d.%m")
        q=r["quality"] or 3
        lines.append("{}  {}ч  {}".format(d,r["hours"],quality_icon(q)))
    avg_s=""
    if rows:
        avg_h=sum(r["hours"] for r in rows)/len(rows)
        avg_q=sum((r["quality"] or 3) for r in rows)/len(rows)
        avg_s="\n\n<b>среднее</b>  {:.1f}ч  {}".format(avg_h,sbar(avg_q,uid))
    block=bq("\n".join(lines)) if lines else "<i>нет записей</i>"
    return "🌙  <b>сон</b>  <i>последние 7</i>\n\n{}{}\n\n<i>хороший сон — путь к лету 🌟 выбери часов</i>".format(block,avg_s), kb_sleep()

def scr_sleep_hist(uid):
    rows=sleep_hist(uid,30)
    if not rows: return "🌙  <b>история сна</b>\n\n<i>нет записей</i>", KB([("< назад","sleep")])
    lines=[]
    for r in rows:
        d=datetime.fromisoformat(r["logged_at"]).strftime("%d.%m")
        lines.append("{}  {}ч  {}".format(d,r["hours"],quality_icon(r["quality"] or 3)))
    avg_h=sum(r["hours"] for r in rows)/len(rows)
    return "🌙  <b>история сна</b>  <i>30 дней</i>\n\nсреднее: <b>{:.1f}ч</b>\n\n{}".format(
        avg_h,bq("\n".join(lines))), KB([("< назад","sleep")])

# ── ЭКРАН: ПИТАНИЕ ─────────────────────────────────────────────────
# ── ЭКРАНЫ: ПИТАНИЕ ────────────────────────────────────────────────
def scr_nutrition(uid):
    cal=today_cal(uid); u=guser(uid); goal=u["cal_goal"] or 2000
    pct=min(100,int(cal/goal*100))
    by_meal=today_cal_by_meal(uid)
    rows=[]
    for mk in MEAL_ORDER:
        if mk in by_meal:
            kcal,cnt=by_meal[mk]
            rows.append("{}  {}  —  {} ккал  <i>×{}</i>".format(mico(mk),mnam(mk),kcal,cnt))
    meal_s="\n".join(rows) if rows else "<i>сегодня пусто — нажми «добавить еду»</i>"
    return ("🍋  <b>питание</b>\n\n"
            "<b>{} / {} ккал</b>\n{} {}%\n\n{}".format(
            cal,goal,cbar(pct,uid),pct,meal_s)), kb_nutrition()

def scr_food_add(uid):
    recent = get_recent_products(uid, 4)
    sub = "  <i>есть недавние — нажми кнопку ниже</i>" if recent else ""
    return "➕  <b>добавить еду</b>\n\n<i>выбери продукт из базы или добавь новый{}</i>".format(sub), kb_food_add(uid)

def scr_food_all(uid, page=0):
    prods = get_products(uid); ps = 6
    total_p = max(1,(len(prods)+ps-1)//ps); page = max(0,min(page,total_p-1))
    chunk = prods[page*ps:(page+1)*ps]
    lines = []
    for i,p in enumerate(chunk, page*ps+1):
        lines.append("{}. <b>{}</b>  —  {} ккал  Б{}  Ж{}  У{}".format(
            i, p["name"], p["calories"],
            round(p["protein"],1), round(p["fat"],1), round(p["carbs"],1)))
    page_s = "  <i>{} из {}</i>".format(page+1,total_p) if total_p > 1 else ""
    text = ("📋  <b>все продукты</b>  <i>{} шт</i>{}\n\n"
            "{}\n\n<i>выбери продукт</i>").format(
        len(prods), page_s, "\n".join(lines) if lines else "<i>пусто</i>")
    return text, kb_food_all(uid, page)

def scr_food_grams(pid):
    p=get_product(pid)
    if not p: return "❌ продукт не найден", kb_back("food_add")
    return ("🌿  <b>{}</b>\n\n"
            "<code>{}ккал  |  Б{}  Ж{}  У{}</code>  на 100г\n\n"
            "<i>сколько грамм съел?</i>".format(
            p["name"],p["calories"],
            round(p["protein"],1),round(p["fat"],1),round(p["carbs"],1))), kb_food_grams(pid)

def scr_food_meal(pid, grams):
    p=get_product(pid)
    if not p: return "❌ продукт не найден", kb_back("food_add")
    kcal=int(p["calories"]*grams/100)
    prot=round(p["protein"]*grams/100,1)
    fat =round(p["fat"]*grams/100,1)
    carb=round(p["carbs"]*grams/100,1)
    return ("🌿  <b>{}</b>  {}г\n\n"
            "<code>ккал   {}\n"
            "белки  {}\n"
            "жиры   {}\n"
            "углев. {}</code>\n\n"
            "<i>к какому приёму отнести?</i>".format(
            p["name"],grams,kcal,prot,fat,carb)), kb_food_meal(pid,grams)

def scr_food_diary(uid, date_str=None):
    if date_str is None:
        date_str = today_msk().isoformat()
    try:
        d = dt_date.fromisoformat(date_str)
    except:
        d = today_msk(); date_str = d.isoformat()
    is_today = (d == today_msk())
    date_label = "сегодня" if is_today else d.strftime("%d.%m.%Y")
    by_meal = today_cal_by_meal_for_date(uid, date_str)
    sections = []
    for mk in MEAL_ORDER:
        if mk not in by_meal: continue
        meal_total, _ = by_meal[mk]
        entries = cal_meal_entries_for_date(uid, date_str, mk)
        rows_ = []
        for e in entries:
            t_s = datetime.fromisoformat(e["logged_at"]).strftime("%H:%M")
            d_s = "  {}".format(e["description"][:22]) if e["description"] else ""
            rows_.append("  {}  {} ккал{}".format(t_s, e["amount"], d_s))
        block = "<blockquote expandable>{}</blockquote>".format("\n".join(rows_)) if rows_ else ""
        sections.append("{}  <b>{}</b>  <i>{} ккал</i>\n{}".format(
            mico(mk), mnam(mk), meal_total, block))
    diary = "\n\n".join(sections) if sections else "<i>записей нет</i>"
    kb_rows = []
    if is_today:
        kb_rows.append([B("↩ удалить последнее","food_del_last")])
    kb_rows.append([B("📅 посмотреть другой день","food_diary_cal")])
    kb_rows.append([B("< питание","nutrition")])
    return ("📓  <b>дневник питания</b>  <i>{}</i>\n\n{}".format(date_label, diary),
            InlineKeyboardMarkup(inline_keyboard=kb_rows))

# ── ЭКРАН: БЫСТРЫЕ ПРОДУКТЫ ─────────────────────────────────────────
def scr_quick_products(uid, page=0):
    prods = get_products(uid); ps = 4
    total_p = max(1,(len(prods)+ps-1)//ps)
    page = max(0,min(page,total_p-1))
    chunk = prods[page*ps:(page+1)*ps]
    lines = []
    for i,p in enumerate(chunk, page*ps+1):
        lines.append("{}. <b>{}</b>  —  {} ккал  Б{}  Ж{}  У{}".format(
            i, p["name"], p["calories"],
            round(p["protein"],1), round(p["fat"],1), round(p["carbs"],1)))
    page_s = "  <i>стр. {}/{}</i>".format(page+1,total_p) if total_p > 1 else ""
    products_block = "<blockquote expandable>{}</blockquote>".format(
        "\n".join(lines)) if lines else "<i>нет продуктов</i>"
    text = ("🌿  <b>продукты</b>  <i>{} шт</i>  стр. {}/{}\n\n"
            "{}\n\n"
            "<i>нажми → логировать  ·  КБЖУ на 100г</i>").format(len(prods), page+1, total_p, products_block)
    return text, kb_quick_products(uid, page)

# ── ЭКРАН: НЕДАВНИЕ ПРОДУКТЫ ──────────────────────────────────────────
def scr_recent_products(uid, page=0):
    recent = get_recent_products_paged(uid, 100)
    if not recent:
        return "🕐  <b>недавние продукты</b>\n\n<i>пока нет недавних</i>", KB([("< назад","food_add")])
    ps = 4; total_p = max(1,(len(recent)+ps-1)//ps)
    page = max(0,min(page,total_p-1))
    chunk = recent[page*ps:(page+1)*ps]
    lines = []
    for p in chunk:
        lines.append("  <b>{}</b>  —  {} ккал  Б{}  Ж{}  У{}".format(
            p["name"], p["calories"],
            round(p["protein"],1), round(p["fat"],1), round(p["carbs"],1)))
    page_s = "  <i>стр. {}/{}</i>".format(page+1,total_p) if total_p > 1 else ""
    text = ("🕐  <b>недавние</b>  <i>{} шт</i>{}\n\n"
            "{}\n\n"
            "<i>нажми → добавить снова</i>").format(len(recent), page_s, "\n".join(lines))
    return text, kb_recent_products(uid, page)

def scr_kbzhu(uid, page=0):
    prods = get_products(uid); ps = 5
    total_p = max(1,(len(prods)+ps-1)//ps)
    page = max(0,min(page,total_p-1))
    chunk = prods[page*ps:(page+1)*ps]
    lines = []
    for i,p in enumerate(chunk, page*ps+1):
        lines.append("{}. <b>{}</b>  —  {} ккал/100г  Б{}  Ж{}  У{}".format(
            i, p["name"], p["calories"],
            round(p["protein"],1), round(p["fat"],1), round(p["carbs"],1)))
    page_s = "  <i>стр. {}/{}</i>".format(page+1,total_p) if total_p > 1 else ""
    text = "🧮  <b>КБЖУ-калькулятор</b>{}\n\n{}\n\n<i>выбери продукт из списка</i>".format(
        page_s, "\n".join(lines) if lines else "<i>нет продуктов</i>")
    return text, kb_kbzhu(uid, page)

# ── ЭКРАН: ТАЙМЕР ТРЕНИРОВКИ ─────────────────────────────────────────
def scr_workout_timer(uid):
    wt=get_wt(uid)
    t=None
    if not wt:
        return "⏱  <b>таймер</b>\n\n<i>нет активной тренировки</i>\n\nзапусти из карточки задачи 🌱", kb_workout_timer_empty()
    try:
        started=datetime.fromisoformat(wt["started_at"])
        elapsed=int((now_msk()-started).total_seconds()/60)
        dur=wt["duration_planned"] or 30
        rem=dur-elapsed
        pct=min(100,int(elapsed/dur*100))
        bar=pbar_block(pct) if get_bar_style(uid) else pbar(pct,10,"🟨","⬜")
        name=wt.get("act_name","тренировка")
        if rem>0:
            status="осталось  <b>{}</b>  💪".format(fmt_dur(rem))
        else:
            status="⚠️  время вышло  (+{})  — ты превзошёл план! ☀️".format(fmt_dur(-rem))
        text="⏱  <b>{}</b>\n\nпрошло  <b>{}</b>  из  {}\n{} {}%\n\n{}".format(
            name,fmt_dur(elapsed),fmt_dur(dur),bar,pct,status)
    except Exception as e:
        text="⏱  <b>таймер</b>\n\n⚠️ ошибка: {}".format(e)
    return text, kb_workout_timer_active()

# ── ЭКРАН: НАПОМИНАНИЯ ───────────────────────────────────────────────
def scr_reminders(uid):
    wr=get_reminder(uid,"water"); wgr=get_reminder(uid,"weight"); rep=get_reminder(uid,"report")
    def en(r): return "вкл ✅" if r["enabled"] else "выкл"
    def info_w(r):
        if not r["enabled"]: return ""
        sch=json.loads(r.get("schedule") or "[]")
        if r.get("interval_hours"):
            return "каждые {}ч  8:00–22:00".format(r["interval_hours"])
        return ", ".join(sch)
    def info_t(r):
        if not r["enabled"]: return ""
        sch=json.loads(r.get("schedule") or "[]")
        return sch[0] if sch else "—"
    def info_rep(r):
        if not r["enabled"]: return ""
        sch=json.loads(r.get("schedule") or "[]")
        day=r.get("report_day",0); t=sch[0] if sch else "09:00"
        return "{}  {}".format(DAYS_RU[day],t)
    lines=[
        "🌊 вода  {}  {}".format(en(wr),info_w(wr)),
        "⚖️ вес   {}  {}".format(en(wgr),info_t(wgr)),
        "📅 отчёт {}  {}".format(en(rep),info_rep(rep)),
    ]
    return "🔔  <b>напоминания</b>\n\n<code>{}</code>\n\n<i>умные уведомления на пути к лету ☀️</i>".format("\n".join(lines)), kb_reminders(uid)






# ── СЛУЖЕБНОЕ ───────────────────────────────────────────────────────
async def safe_del(cid, mid):
    try: await bot.delete_message(cid, mid)
    except: pass

async def show(uid, state, text, markup):
    if len(text) > 4000: text = text[:3997] + "…"
    data=await state.get_data(); mid=data.get("msg_id")
    if mid:
        try:
            await bot.edit_message_text(chat_id=uid,message_id=mid,text=text,
                                        reply_markup=markup,parse_mode="HTML")
            return
        except Exception as e:
            err=str(e).lower()
            if "message is not modified" in err: return
            # сообщение удалено или недоступно — сбрасываем msg_id
            await state.update_data(msg_id=None)
            try: await bot.delete_message(uid,mid)
            except: pass
    try:
        m=await bot.send_message(uid,text,reply_markup=markup,parse_mode="HTML")
        await state.update_data(msg_id=m.message_id)
    except Exception as e:
        log.warning("show send error uid=%s: %s", uid, e)

async def show_card(uid, state, idx, card_list):
    text=build_card_text(uid,idx,card_list)
    kb=kb_card(idx,card_list,card_list[idx])
    await show(uid,state,text,kb)
    data=await state.get_data(); mid=data.get("msg_id")
    card_sessions[uid]={"card_list":card_list,"card_idx":idx,"msg_id":mid}

async def save_act(uid, state, na):
    rep=na.get("repeat_days",[]); dow=",".join(str(d) for d in rep)
    dur=na.get("duration",30); time_=na.get("time","00:00")
    name_=na.get("name",""); type_=na.get("type","other")
    tr=na.get("time_range",time_)
    date_=today_msk().strftime("%Y-%m-%d") if rep else na.get("date",today_msk().strftime("%Y-%m-%d"))
    sched="{} {}:00".format(date_,time_)
    add_act(uid,name_,type_,sched,dur,dow)
    reps=" · ".join(DAYS_RU[d] for d in rep) if rep else "одноразово"
    await show(uid,state,
        "✅  <b>{}</b> добавлено\n\n<code>{} {}  {}  {}</code>".format(
            name_,aico(type_),tr,reps,""),
        KB([("📋 план","plan_manage"),("< меню","main")]))
    await state.update_data(new_act={})


# ── ПЛАНИРОВЩИК: ТИКИ КАРТОЧЕК ──────────────────────────────────────
async def tick_cards():
    for uid,sess in list(card_sessions.items()):
        try:
            card_list=sess["card_list"]; idx=sess["card_idx"]; mid=sess["msg_id"]
            if not card_list or not mid: card_sessions.pop(uid,None); continue
            aid=card_list[idx]; a=get_act(aid)
            if not a: card_sessions.pop(uid,None); continue
            text=build_card_text(uid,idx,card_list)
            kb=kb_card(idx,card_list,aid)
            await bot.edit_message_text(chat_id=uid,message_id=mid,text=text,reply_markup=kb,parse_mode="HTML")
        except Exception as e:
            if "message is not modified" not in str(e).lower():
                log.debug("tick_cards uid=%s: %s",uid,e)


# ── ПЛАНИРОВЩИК: НАПОМИНАНИЯ О ВОДЕ ────────────────────────────────
async def check_water_reminders():
    now=now_msk()
    now_hm=now.strftime("%H:%M"); now_h=now.hour
    for uid in get_all_users():
        try:
            r=get_reminder(uid,"water")
            if not r["enabled"]: continue
            sch=json.loads(r.get("schedule") or "[]")
            interval=r.get("interval_hours",0)
            fire=False
            if interval:
                # авто-режим: каждые N часов с 8:00 до 22:00
                if 8<=now_h<=21 and now.minute==0 and now_h%interval==0:
                    fire=True
            else:
                # ручной режим: конкретное время
                if now_hm in sch:
                    fire=True
            if fire:
                today=today_water(uid); u=guser(uid)
                goal=u["water_goal"] if u else 2000
                pct=min(100,int(today/(goal or 2000)*100))
                text="🌊  <b>пора пить воду!</b>\n\n<b>{} / {} мл</b>  {}%\n\n<i>твоё лето ближе с каждым глотком 💙</i>".format(
                    today,goal,pct)
                m=await bot.send_message(uid,text,reply_markup=kb_water_notif(),parse_mode="HTML")
                water_remind_msgs[uid]=m.message_id
        except Exception as e:
            log.debug("water_remind uid=%s: %s",uid,e)


# ── ПЛАНИРОВЩИК: НАПОМИНАНИЕ ВЗВЕСИТЬСЯ ────────────────────────────
async def check_weight_reminders():
    now=now_msk(); now_hm=now.strftime("%H:%M")
    for uid in get_all_users():
        try:
            r=get_reminder(uid,"weight")
            if not r["enabled"]: continue
            sch=json.loads(r.get("schedule") or "[]")
            if now_hm in sch:
                lw=weight_hist(uid,1)
                last_s=""
                if lw:
                    ld=datetime.fromisoformat(lw[0]["logged_at"])
                    if (now_msk()-ld).days<1: continue  # уже взвешивался сегодня
                    last_s="\n<i>последний раз: {} ({} кг)</i>".format(
                        ld.strftime("%d.%m"),lw[0]["weight"])
                await bot.send_message(
                    uid,"⚖️  <b>привет! пора взвеситься</b>{}\n\nкаждый килограмм ближе к летней мечте ☀️\n\nотправь своё текущее значение".format(last_s),
                    parse_mode="HTML",
                    reply_markup=KB([("⚖️ открыть вес","weight")]))
        except Exception as e:
            log.debug("weight_remind uid=%s: %s",uid,e)


# ── ПЛАНИРОВЩИК: ЕЖЕНЕДЕЛЬНЫЙ ОТЧЁТ ────────────────────────────────
async def check_weekly_report():
    now=now_msk(); now_hm=now.strftime("%H:%M"); now_dow=now.weekday()
    for uid in get_all_users():
        try:
            r=get_reminder(uid,"report")
            if not r["enabled"]: continue
            sch=json.loads(r.get("schedule") or "[]")
            rep_day=r.get("report_day",0)
            if now_dow!=rep_day or now_hm not in sch: continue
            # Собираем данные за неделю
            with db() as c:
                water7=c.execute("SELECT COALESCE(SUM(amount),0) t FROM water_log WHERE user_id=? AND date(logged_at)>=date('now','+3 hours','-7 days')",(uid,)).fetchone()["t"]
                cal7=c.execute("SELECT COALESCE(SUM(amount),0) t FROM calories_log WHERE user_id=? AND date(logged_at)>=date('now','+3 hours','-7 days')",(uid,)).fetchone()["t"]
                d7=c.execute("SELECT COUNT(DISTINCT date(logged_at)) cnt FROM calories_log WHERE user_id=? AND date(logged_at)>=date('now','+3 hours','-7 days')",(uid,)).fetchone()["cnt"] or 1
                acts7=c.execute("SELECT COUNT(*) cnt FROM activities WHERE user_id=? AND completed=1 AND date(ended_at)>=date('now','+3 hours','-7 days')",(uid,)).fetchone()["cnt"]
                sl7=c.execute("SELECT AVG(hours) a FROM sleep_log WHERE user_id=? AND date(logged_at)>=date('now','+3 hours','-7 days')",(uid,)).fetchone()["a"]
                lw7=c.execute("SELECT weight FROM weight_log WHERE user_id=? AND date(logged_at)>=date('now','+3 hours','-7 days') ORDER BY logged_at",(uid,)).fetchall()
            u=guser(uid); wgoal=(u["water_goal"] if u else 2000)*7
            wp=min(100,int(water7/wgoal*100))
            wdelta=""; wline=""
            if len(lw7)>=2:
                wdelta="{:+.1f} кг".format(lw7[-1]["weight"]-lw7[0]["weight"])
                wline="\n⚖️ вес за неделю  <b>{}</b>".format(wdelta)
            sleep_s="  ср. {:.1f}ч/ночь".format(sl7) if sl7 else ""
            d_start=(today_msk()-timedelta(days=6)).strftime("%d.%m")
            d_end=today_msk().strftime("%d.%m")
            text="🌤  <b>летний отчёт  {} – {}</b>{}\n\n".format(d_start,d_end,wline)
            text+="🌊 вода   {:.1f} / {:.1f} л  ({}%)\n".format(water7/1000,wgoal/1000,wp)
            text+="🍋 ккал   {} ккал  (ср. {}/день)\n".format(cal7,cal7//d7)
            text+="💪 трен-к  {} выполнено\n".format(acts7)
            text+="🌙 сон   {}\n".format(sleep_s.strip() or "нет данных")
            text+="\n{}  {}%\n\n<i>{}</i>".format(wbar(wp,uid),wp,summer_line())
            await bot.send_message(uid,text,parse_mode="HTML",
                                   reply_markup=KB([("📊 открыть статистику","progress")]))
        except Exception as e:
            log.debug("report uid=%s: %s",uid,e)


# ── /start ──────────────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    uid=msg.from_user.id
    name=msg.from_user.first_name or ""
    upsert(uid, name)
    await state.clear()
    card_sessions.pop(uid,None)
    await safe_del(msg.chat.id,msg.message_id)
    # Первый запуск — приветствие
    u=guser(uid)
    if not u["name"] or not u["start_weight"]:
        welcome=(
            "привет{} ☀️  я <b>letify</b>\n\n"
            "помогу встретить лето лёгким и свободным 🌊\n\n"
            "<i>{}</i>".format(
                ", <b>{}</b>".format(name) if name else "",
                summer_line()
            )
        )
        sent=await bot.send_message(uid,welcome,parse_mode="HTML",
            reply_markup=KB([("☀️ начать","main")]))
        await state.update_data(msg_id=sent.message_id)
    else:
        t,m=scr_main(uid)
        sent=await bot.send_message(uid,t,reply_markup=m,parse_mode="HTML")
        await state.update_data(msg_id=sent.message_id)


# ── CALLBACKS ───────────────────────────────────────────────────────
@dp.callback_query()
async def on_cb(call: CallbackQuery, state: FSMContext):
    uid=call.from_user.id; data=call.data
    upsert(uid, call.from_user.first_name or "")
    await state.update_data(msg_id=call.message.message_id)
    try: await call.answer()
    except: pass
    async def s(t, m): await show(uid, state, t, m)

    if data=="noop": return

    # ── МЕНЮ ──────────────────────────────────────────────────────
    if data=="main":
        await state.set_state(None)
        card_sessions.pop(uid,None)
        t,m=scr_main(uid); await s(t,m); return

    # ── ПЛАН ──────────────────────────────────────────────────────
    if data=="plan_open":
        t,m=scr_plan_intro(uid); await s(t,m); return

    if data=="plan_cards":
        result=scr_card_open(uid)
        if len(result)==2:
            await s(result[0],result[1]); return
        text,kb,card_list,idx=result
        await show(uid,state,text,kb)
        data_st=await state.get_data(); mid=data_st.get("msg_id")
        card_sessions[uid]={"card_list":card_list,"card_idx":idx,"msg_id":mid}; return

    if data.startswith("card_nav_"):
        idx=int(data[9:])
        sess=card_sessions.get(uid)
        if sess:
            card_list=sess["card_list"]; idx=max(0,min(idx,len(card_list)-1))
            sess["card_idx"]=idx; await show_card(uid,state,idx,card_list)
        else:
            result=scr_card_open(uid,idx)
            if len(result)==2: await s(result[0],result[1]); return
            text,kb,card_list,idx=result
            await show(uid,state,text,kb)
            data_st=await state.get_data(); mid=data_st.get("msg_id")
            card_sessions[uid]={"card_list":card_list,"card_idx":idx,"msg_id":mid}
        return

    if data.startswith("card_start_"):
        aid=int(data[11:]); start_act(aid)
        sess=card_sessions.get(uid)
        card_list=sess["card_list"] if sess else get_today_card_list(uid)
        idx=sess["card_idx"] if sess else 0
        await show_card(uid,state,idx,card_list); return

    if data.startswith("card_complete_"):
        aid=int(data[14:]); complete_act(aid)
        sess=card_sessions.get(uid)
        card_list=sess["card_list"] if sess else get_today_card_list(uid)
        idx=get_smart_card_idx(card_list)
        if sess: sess["card_idx"]=idx
        await show_card(uid,state,idx,card_list); return

    if data.startswith("card_del_"):
        aid=int(data[9:]); del_act(aid)
        sess=card_sessions.get(uid)
        if sess:
            card_list=[c for c in sess["card_list"] if c!=aid]
            sess["card_list"]=card_list; idx=get_smart_card_idx(card_list) if card_list else 0
            sess["card_idx"]=idx
        else:
            card_list=get_today_card_list(uid); idx=0
        if not card_list:
            card_sessions.pop(uid,None); t,m=scr_main(uid); await s("🗑 удалено\n\n"+t,m)
        else:
            await show_card(uid,state,idx,card_list)
        return

    # ── ВЕС ───────────────────────────────────────────────────────
    if data=="weight":
        await state.set_state(St.weight); t,m=scr_weight(uid); await s(t,m); return
    if data=="weight_del":
        del_last_w(uid); t,m=scr_weight(uid); await s("↩ удалено\n\n"+t,m); return
    if data=="weight_hist30":
        t,m=scr_weight_hist(uid,0,30); await s(t,m); return
    if data=="weight_hist_all":
        t,m=scr_weight_hist(uid,0); await s(t,m); return
    if data.startswith("wh_p"):
        t,m=scr_weight_hist(uid,int(data[4:])); await s(t,m); return

    # ── ВОДА ──────────────────────────────────────────────────────
    if data=="water":
        t,m=scr_water(uid); await s(t,m); return
    _wm={"w150":150,"w200":200,"w250":250,"w500":500}
    if data in _wm:
        log_water(uid,_wm[data]); t,m=scr_water(uid); await s("+{} мл\n\n".format(_wm[data])+t,m); return
    if data=="water_custom":
        await state.set_state(St.water_custom); await s("🌊 сколько мл выпил?",kb_x("water")); return
    if data=="water_goal_set":
        await state.set_state(St.water_goal); await s("дневная норма (мл):",kb_x("settings")); return
    if data=="water_del":
        del_last_water(uid); t,m=scr_water(uid); await s("↩ удалено\n\n"+t,m); return

    # ── УВЕДОМЛЕНИЕ О ВОДЕ (inline кнопки в самом уведомлении) ────
    if data.startswith("wrlog_"):
        cmd=data[6:]
        mid_notif=water_remind_msgs.get(uid)
        if cmd=="skip":
            if mid_notif:
                try: await bot.edit_message_text(chat_id=uid,message_id=mid_notif,
                    text="💧  пропущено",parse_mode="HTML",reply_markup=None)
                except: pass
            return
        try:
            amt=int(cmd)
            log_water(uid,amt)
            today=today_water(uid); u=guser(uid); goal=u["water_goal"] if u else 2000
            pct=min(100,int(today/(goal or 2000)*100))
            if mid_notif:
                try:
                    await bot.edit_message_text(
                        chat_id=uid,message_id=mid_notif,
                        text="✅  <b>+{} мл</b> записано!\n\n🌊 сегодня: <b>{} / {} мл</b>  {}%\n\n<i>каждый глоток ближе к лету ☀️</i>".format(
                            amt,today,goal,pct),
                        parse_mode="HTML",reply_markup=None)
                except: pass
            water_remind_msgs.pop(uid,None)
        except: pass
        return

    # ── ПИТАНИЕ ───────────────────────────────────────────────────
    if data=="nutrition":
        t,m=scr_nutrition(uid); await s(t,m); return

    if data=="food_diary":
        t,m=scr_food_diary(uid); await s(t,m); return

    if data=="food_del_last":
        del_last_cal(uid); t,m=scr_food_diary(uid); await s("↩ удалено\n\n"+t,m); return

    if data=="food_diary_cal":
        today=today_msk()
        month_label="{} {}".format(_MONTH_NAMES[today.month],today.year)
        await s("📅  <b>выбери день</b>  <i>{}</i>\n\n<i>•  — есть записи</i>".format(month_label),
                kb_diary_cal(uid,today.year,today.month)); return

    if data.startswith("diary_cal_"):
        parts_=data[10:].split("_")
        if len(parts_)==2:
            try:
                y,m_=int(parts_[0]),int(parts_[1])
                month_label="{} {}".format(_MONTH_NAMES[m_],y)
                await s("📅  <b>выбери день</b>  <i>{}</i>\n\n<i>•  — есть записи</i>".format(month_label),
                        kb_diary_cal(uid,y,m_)); return
            except: pass
        t,m=scr_food_diary(uid); await s(t,m); return

    if data.startswith("diary_date_"):
        date_str=data[11:]; t,m=scr_food_diary(uid,date_str); await s(t,m); return

    # ── НЕДАВНИЕ ПРОДУКТЫ ─────────────────────────────────────────
    if data=="recent_prods":
        t,m=scr_recent_products(uid); await s(t,m); return

    if data.startswith("recent_page_"):
        page=int(data[12:]); t,m=scr_recent_products(uid,page); await s(t,m); return

    if data=="recent_clear":
        clear_recent_products(uid); t,m=scr_food_add(uid)
        await s("🗑 недавние очищены\n\n"+t,m); return

    # добавить еду: выбор продукта
    if data=="food_add":
        t,m=scr_food_add(uid); await s(t,m); return

    if data=="food_new":
        await state.set_state(St.qp_name)
        await state.update_data(qp_ctx="food")
        await s("➕  <b>новый продукт</b>\n\nшаг 1 — название:",kb_x("food_add")); return

    if data.startswith("food_all_"):
        page=int(data[9:]); t,m=scr_food_all(uid,page); await s(t,m); return

    # food_pick_{pid} — выбрали продукт → граммы
    if data.startswith("food_pick_"):
        pid=int(data[10:]); t,m=scr_food_grams(pid); await s(t,m); return

    # fg_{pid}_{grams} — быстрые граммы → выбор приёма
    if data.startswith("fg_"):
        parts_=data[3:].rsplit("_",1)
        if len(parts_)==2:
            pid=int(parts_[0]); grams=int(parts_[1])
            t,m=scr_food_meal(pid,grams); await s(t,m)
        return

    # fgc_{pid} — ввод своих граммов
    if data.startswith("fgc_"):
        pid=int(data[4:]); p=get_product(pid)
        if not p: t,m=scr_food_add(uid); await s("❌ продукт не найден\n\n"+t,m); return
        await state.set_state(St.food_grams)
        await state.update_data(food_pid=pid)
        await s("🍎  <b>{}</b>\n{}ккал/100г\n\n<i>введи граммы:</i>".format(
            p["name"],p["calories"]), kb_x("food_pick_{}".format(pid))); return

    # fs_{pid}_{grams}_{meal} — сохраняем запись
    if data.startswith("fs_"):
        tail=data[3:].rsplit("_",1)
        if len(tail)==2:
            pid_g,mt=tail
            pg=pid_g.rsplit("_",1)
            if len(pg)==2:
                pid=int(pg[0]); grams=int(pg[1]); p=get_product(pid)
                if p:
                    kcal=int(p["calories"]*grams/100)
                    mark_product_used(pid)
                    log_cal(uid,kcal,desc="{} {}г".format(p["name"],grams),meal_type=mt)
                    t,m=scr_nutrition(uid)
                    await s("✅  {} {}  —  {} {}г  +{} ккал\n\n".format(
                        mico(mt),mnam(mt),p["name"],grams,kcal)+t,m); return
        t,m=scr_nutrition(uid); await s(t,m); return

    # ── КАЛОРИИ legacy ───────────────────────────────────────────
    if data=="calories":
        t,m=scr_nutrition(uid); await s(t,m); return
    if data=="cal_goal_set":
        await state.set_state(St.cal_goal); await s("дневная цель (ккал):",kb_x("settings")); return
    if data=="cal_del":
        del_last_cal(uid); t,m=scr_nutrition(uid); await s("↩ удалено\n\n"+t,m); return

    # ── ЦЕЛИ ──────────────────────────────────────────────────────
    if data=="goals":
        t,m=scr_goals(uid); await s(t,m); return
    if data=="goal_weight":
        await state.set_state(St.goal_weight); await s("🏖️ какой вес хочешь к лету? (кг)",kb_x("goals")); return
    if data=="ideal_weight":
        await s(ideal_weight_text(uid),KB([("< назад","goals")])); return

    # ── ПРОФИЛЬ ───────────────────────────────────────────────────
    if data=="profile":
        t,m=scr_profile(uid); await s(t,m); return
    if data=="pname":
        await state.set_state(St.pname); await s("✏️ как тебя зовут?",kb_x("profile")); return
    if data=="pheight":
        await state.set_state(St.pheight); await s("📏 рост (см):",kb_x("profile")); return
    if data=="page_age":
        await state.set_state(St.page_age); await s("🎂 сколько лет?",kb_x("profile")); return

    # ── СТАТИСТИКА ────────────────────────────────────────────────
    if data=="progress":
        t,m=scr_progress(uid); await s(t,m); return
    if data=="week_stats":
        t,m=scr_week_stats(uid); await s(t,m); return

    # ── НАСТРОЙКИ ─────────────────────────────────────────────────
    if data=="settings":
        t,m=scr_settings(uid); await s(t,m); return
    if data=="sett_display":
        t,m=scr_sett_display(uid); await s(t,m); return
    if data=="sett_reset":
        t,m=scr_sett_reset(); await s(t,m); return
    if data=="reset_water":
        reset_water(uid); await s("💧 вода за день сброшена",kb_sett_reset()); return
    if data=="reset_cal":
        reset_cal(uid); await s("🔥 калории за день сброшены",kb_sett_reset()); return
    if data=="reset_weight":
        reset_w(uid); await s("⚖️ история веса сброшена",kb_sett_reset()); return
    if data=="reset_sleep":
        reset_sleep(uid); await s("😴 история сна сброшена",kb_sett_reset()); return

    _tog={"stog_weight":"show_weight","stog_water":"show_water","stog_calories":"show_calories",
           "stog_sleep":"show_sleep","stog_bar_style":"bar_style"}
    if data in _tog:
        toggle_sett(uid,_tog[data]); t,m=scr_sett_display(uid); await s(t,m); return

    # ── УПРАВЛЕНИЕ ПЛАНОМ ─────────────────────────────────────────
    if data=="plan_manage":
        await state.set_state(St.plan_num_input)
        await state.update_data(plan_day=0)
        t,m=scr_plan_manage(uid,0); await s(t,m); return

    if data.startswith("pman_del_"):
        aid=int(data[9:]); del_act(aid)
        sd=await state.get_data(); off=sd.get("plan_day",0)
        await state.set_state(St.plan_num_input)
        t,m=scr_plan_manage(uid,off); await s("🗑 удалено\n\n"+t.split("\n",1)[-1],m); return

    if data.startswith("pman_d"):
        off=int(data[6:]); await state.set_state(St.plan_num_input)
        await state.update_data(plan_day=off); t,m=scr_plan_manage(uid,off); await s(t,m); return

    # ── ЗАГРУЗКА ПЛАНА ТЕКСТОМ ────────────────────────────────────
    if data=="plan_upload_start":
        await state.set_state(St.plan_upload); await state.update_data(upload_tasks=[])
        await s("📤  <b>загрузка плана</b>\n\nотправляй задачи:\n<code>10:00-10:30 прогулка\n20:00-21 купить апельсины</code>\n\n<i>можно несколько сообщений</i>",
            KB([("✅ готово","plan_upload_done"),("✕ отмена","plan_manage")])); return

    if data=="plan_upload_done":
        sd=await state.get_data(); tasks=sd.get("upload_tasks",[])
        if not tasks:
            await s("ничего не распознано — отправь задачи в формате\n<code>10:00-11:00 название</code>",
                KB([("✕ отмена","plan_manage")])); return
        await state.set_state(St.plan_upload_days); await state.update_data(upload_sel=[])
        await s("📋  <b>распознано: {}</b>\n\n{}\n\nвыбери дни:".format(
            len(tasks),fmt_upload_preview(tasks)),kb_upload_days(set())); return

    if data.startswith("upday_"):
        sd=await state.get_data(); tasks=sd.get("upload_tasks",[]); sel=set(sd.get("upload_sel",[]))
        cmd=data[6:]
        if cmd=="all":   sel=set(range(7))
        elif cmd=="none": sel=set()
        elif cmd=="today": sel={today_msk().weekday()}
        elif cmd=="save":
            if not sel:
                await s("выбери хотя бы один день",kb_upload_days(sel)); return
            today=today_msk(); dow=",".join(str(d) for d in sorted(sel)); saved=0
            for task in tasks:
                sched="{} {}:00".format(today.strftime("%Y-%m-%d"),task["time"])
                add_act(uid,task["name"],task["type"],sched,task["duration"],dow); saved+=1
            await state.set_state(None); await state.update_data(upload_tasks=[],upload_sel=[])
            days_s=" · ".join(DAYS_RU[d] for d in sorted(sel))
            t,m=scr_plan_manage(uid,0)
            await s("✅  сохранено {}  дни: {}\n\n".format(saved,days_s)+t.split("\n\n",1)[-1],m); return
        else:
            try:
                i=int(cmd)
                if i in sel: sel.remove(i)
                else: sel.add(i)
            except: pass
        await state.update_data(upload_sel=list(sel))
        await s("📋  <b>распознано: {}</b>\n\n{}\n\nвыбери дни:".format(
            len(tasks),fmt_upload_preview(tasks)),kb_upload_days(sel)); return

    # ── ДОБАВЛЕНИЕ ЗАДАЧИ ──────────────────────────────────────────
    if data=="act_add":
        await state.set_state(St.act_name); await state.update_data(new_act={})
        await s("<b>новая задача</b>\n\nшаг 1 — название:",kb_x("plan_manage")); return

    if data.startswith("atype_"):
        atype=data[6:]; sd=await state.get_data(); na=sd.get("new_act",{})
        na["type"]=atype; await state.update_data(new_act=na)
        sel=set(na.get("repeat_days",[]))
        await s("<b>{}</b>  {} {}\n\nшаг 3 — дни повтора\n<i>«далее» — для разовой</i>".format(
            na.get("name",""),aico(atype),anam(atype)),kb_days(sel)); return

    if data.startswith("nday_"):
        sd=await state.get_data(); na=sd.get("new_act",{}); sel=set(na.get("repeat_days",[])); cmd=data[5:]
        if cmd=="all": sel=set(range(7))
        elif cmd=="none": sel=set()
        elif cmd=="done":
            na["repeat_days"]=sorted(sel); await state.update_data(new_act=na)
            if not sel:
                await state.set_state(St.act_date)
                await s("шаг 4 — дата\n<code>ДД.ММ.ГГГГ</code>  или  сегодня / завтра",kb_x("plan_manage"))
            else:
                dt_s=" · ".join(DAYS_RU[d] for d in sorted(sel)); await state.set_state(St.act_timerange)
                await s("повтор: <b>{}</b>\n\nшаг 4 — время\n<code>16:00-19:00</code>".format(dt_s),kb_x("plan_manage"))
            return
        else:
            i=int(cmd)
            if i in sel: sel.remove(i)
            else: sel.add(i)
        na["repeat_days"]=sorted(sel); await state.update_data(new_act=na)
        chosen=" · ".join(DAYS_RU[d] for d in sorted(sel)) if sel else "<i>не выбрано</i>"
        await s("дни: {}\n\nотметь нужные → нажми «далее»".format(chosen),kb_days(sel)); return

    if data.startswith("rem_"):
        sd=await state.get_data(); na=sd.get("new_act",{})
        await state.set_state(None); await save_act(uid,state,na); return

    # ── СОН ───────────────────────────────────────────────────────
    if data=="sleep":
        t,m=scr_sleep(uid); await s(t,m); return

    if data.startswith("sl_"):
        hours_s=data[3:]
        if hours_s=="custom":
            await state.set_state(St.sleep_hours); await s("введи часов сна (например 7.5):",kb_x("sleep")); return
        try:
            hours=float(hours_s); assert 1<=hours<=24
            await s("качество сна?",kb_sleep_quality(hours_s))
        except: await s("❌ ошибка",kb_x("sleep"))
        return

    if data.startswith("sq_"):
        parts_=data[3:].rsplit("_",1)
        if len(parts_)==2:
            hours_s=parts_[0].replace("d","."); q=int(parts_[1])
            try:
                hours=float(hours_s)
                log_sleep(uid,hours,q)
                t,m=scr_sleep(uid)
                await s("✅  {}ч  {}  записано\n\n".format(hours,quality_icon(q))+t,m)
            except: await s("❌ ошибка",kb_x("sleep"))
        return

    if data=="sleep_del":
        del_last_sleep(uid); t,m=scr_sleep(uid); await s("↩ удалено\n\n"+t,m); return

    if data=="sleep_hist":
        t,m=scr_sleep_hist(uid); await s(t,m); return

    # ── БЫСТРЫЕ ПРОДУКТЫ ──────────────────────────────────────────
    if data=="quick_products":
        t,m=scr_quick_products(uid); await s(t,m); return

    if data.startswith("qp_page_"):
        page=int(data[8:]); t,m=scr_quick_products(uid,page); await s(t,m); return

    if data.startswith("qp_log_"):
        pid=int(data[7:]); p=get_product(pid)
        if not p:
            t,m=scr_quick_products(uid); await s("❌ продукт не найден\n\n"+t,m); return
        # Спрашиваем граммы — через FSM
        await state.set_state(St.kbzhu_grams)
        await state.update_data(kbzhu_pid=pid,kbzhu_mode="log")
        await s("🍎  <b>{}</b>\n{}ккал / 100г\n\nсколько грамм?".format(p["name"],p["calories"]),
            kb_x("quick_products")); return

    if data=="qp_add":
        await state.set_state(St.qp_name)
        await s("➕  <b>новый продукт</b>\n\nшаг 1 — название:",kb_x("quick_products")); return

    if data=="qp_del_mode":
        if not get_products(uid):
            t,m=scr_quick_products(uid); await s("список пуст\n\n"+t,m); return
        prods=get_products(uid); total_p=max(1,(len(prods)+3)//4)
        await s("🗑  <b>удалить продукт</b>\n\nвыбери:  <i>стр. 1/{}</i>".format(total_p),kb_qp_delete_mode(uid)); return

    if data.startswith("qpdm_"):
        page=int(data[5:])
        prods=get_products(uid); total_p=max(1,(len(prods)+3)//4)
        page=max(0,min(page,total_p-1))
        await s("🗑  <b>удалить продукт</b>\n\nвыбери:  <i>стр. {}/{}</i>".format(page+1,total_p),kb_qp_delete_mode(uid,page)); return

    if data.startswith("qp_dodel_"):
        pid=int(data[9:]); del_product(pid)
        t,m=scr_quick_products(uid); await s("🗑 продукт удалён\n\n"+t,m); return

    # ── КБЖУ КАЛЬКУЛЯТОР ──────────────────────────────────────────
    if data=="kbzhu":
        t,m=scr_kbzhu(uid); await s(t,m); return

    if data.startswith("kbzhu_page_"):
        page=int(data[11:]); t,m=scr_kbzhu(uid,page); await s(t,m); return

    if data.startswith("kbzhu_pick_"):
        pid=int(data[11:]); p=get_product(pid)
        if not p:
            t,m=scr_kbzhu(uid); await s("❌ продукт не найден",kb_x("kbzhu")); return
        await state.set_state(St.kbzhu_grams)
        await state.update_data(kbzhu_pid=pid,kbzhu_mode="calc")
        await s("🧮  <b>{}</b>\n{}ккал / 100г  |  Б{} Ж{} У{}\n\nсколько грамм?".format(
            p["name"],p["calories"],
            round(p["protein"],1),round(p["fat"],1),round(p["carbs"],1)),
            kb_x("kbzhu")); return

    if data.startswith("kbzhu_log_"):
        # Логировать из результата КБЖУ
        parts_=data[10:].split("_")
        pid=int(parts_[0]); grams=int(parts_[1])
        p=get_product(pid)
        if p:
            kcal=int(p["calories"]*grams/100)
            mark_product_used(p["id"])
            log_cal(uid,kcal,desc="{} {}г".format(p["name"],grams))
            t,m=scr_nutrition(uid); await s("+{} ккал ({} {}г)\n\n".format(kcal,p["name"],grams)+t,m)
        return

    # ── ТАЙМЕР ТРЕНИРОВКИ ─────────────────────────────────────────
    if data=="workout_timer":
        t,m=scr_workout_timer(uid); await s(t,m); return

    if data.startswith("wt_from_card_"):
        aid=int(data[13:]); a=get_act(aid)
        if a:
            start_wt(uid,aid,a.get("name","тренировка"),a.get("duration",30))
            start_act(aid)
        t,m=scr_workout_timer(uid); await s(t,m); return

    if data=="wt_refresh":
        t,m=scr_workout_timer(uid); await s(t,m); return

    if data=="wt_finish":
        t=get_wt(uid)
        if t:
            complete_act(t["act_id"])
            stop_wt(uid)
            started=datetime.fromisoformat(t["started_at"])
            elapsed=int((now_msk()-started).total_seconds()/60)
            await s("✅  <b>тренировка завершена!</b>\n\nвремя: <b>{}</b>\n\nкаждая тренировка — шаг к лету ☀️".format(fmt_dur(elapsed)),
                KB([("< назад","main")])); return
        t2,m=scr_main(uid); await s(t2,m); return

    if data=="wt_cancel":
        stop_wt(uid); t,m=scr_main(uid); await s("❌ таймер отменён\n\n"+t,m); return

    # ── НАПОМИНАНИЯ ───────────────────────────────────────────────
    if data=="reminders":
        t,m=scr_reminders(uid); await s(t,m); return

    if data=="remind_water":
        t,m=scr_reminders(uid)
        await s("🔔  <b>напоминание о воде</b>",kb_water_remind_setup(uid)); return

    if data=="wr_toggle":
        r=get_reminder(uid,"water"); new_en=0 if r["enabled"] else 1
        set_reminder(uid,"water",enabled=new_en)
        await s("🔔  <b>напоминание о воде</b>",kb_water_remind_setup(uid)); return

    if data=="wr_auto":
        await s("⚡  <b>авто-режим</b>\n\nбот будет напоминать о воде в промежутке 8:00–22:00\nвыбери интервал:",
            kb_water_interval()); return

    if data.startswith("wri_"):
        interval=int(data[4:])
        set_reminder(uid,"water",enabled=1,interval_hours=interval,schedule="[]")
        t,m=scr_reminders(uid)
        await s("✅  напоминание каждые {}ч  с 8:00 до 22:00\n\n".format(interval)+t,m); return

    if data=="wr_manual":
        await state.set_state(St.remind_water_manual)
        await s("🕐  <b>ручной режим</b>\n\nвведи время через запятую:\n<code>8:00, 12:00, 16:00, 20:00</code>",
            kb_x("remind_water")); return

    if data=="remind_weight":
        r=get_reminder(uid,"weight")
        sch=json.loads(r.get("schedule") or "[]"); t_s=sch[0] if sch else "—"
        en="✅ вкл" if r["enabled"] else "☐ выкл"
        await s("⚖️  <b>напоминание взвеситься</b>\n\nстатус: {}\nвремя: <b>{}</b>".format(en,t_s),
            KB([("🔔 вкл/выкл","rw_toggle"),("🕐 изменить время","rw_set_time")],[("< назад","reminders")])); return

    if data=="rw_toggle":
        r=get_reminder(uid,"weight"); new_en=0 if r["enabled"] else 1
        set_reminder(uid,"weight",enabled=new_en)
        t,m=scr_reminders(uid); await s("обновлено\n\n"+t,m); return

    if data=="rw_set_time":
        await state.set_state(St.remind_weight_time)
        await s("введи время для напоминания взвеситься:\n<code>08:00</code>",kb_x("remind_weight")); return

    if data=="remind_report":
        r=get_reminder(uid,"report"); sch=json.loads(r.get("schedule") or "[]")
        t_s=sch[0] if sch else "—"; day=r.get("report_day",0)
        en="✅ вкл" if r["enabled"] else "☐ выкл"
        await s("📅  <b>еженедельный отчёт</b>\n\nстатус: {}\nдень: <b>{}</b>  время: <b>{}</b>".format(
            en,DAYS_RU[day],t_s),
            KB([("🔔 вкл/выкл","rep_toggle"),("📅 день и время","rep_set")],[("< назад","reminders")])); return

    if data=="rep_toggle":
        r=get_reminder(uid,"report"); new_en=0 if r["enabled"] else 1
        set_reminder(uid,"report",enabled=new_en); t,m=scr_reminders(uid); await s("обновлено\n\n"+t,m); return

    if data=="rep_set":
        # Выбор дня отчёта
        rows_=[[B(DAYS_RU[i],"repday_{}".format(i)) for i in range(4)]]
        rows_.append([B(DAYS_RU[i],"repday_{}".format(i)) for i in range(4,7)])
        rows_.append([B("✕ отмена","remind_report")])
        await s("📅  выбери день отчёта:",InlineKeyboardMarkup(inline_keyboard=rows_)); return

    if data.startswith("repday_"):
        day=int(data[7:]); set_reminder(uid,"report",report_day=day)
        await state.set_state(St.remind_report_time)
        await state.update_data(report_day=day)
        await s("выбран {}  ·  теперь введи время:\n<code>09:00</code>".format(DAYS_RU[day]),
            kb_x("remind_report")); return



# ── FSM ХЕНДЛЕРЫ ────────────────────────────────────────────────────
async def _del(msg): await safe_del(msg.chat.id, msg.message_id)

@dp.message(St.plan_upload)
async def fh_plan_upload(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    new_tasks=parse_plan_text(msg.text or "")
    sd=await state.get_data(); tasks=sd.get("upload_tasks",[])+new_tasks
    tasks.sort(key=lambda t: t["time"])
    await state.update_data(upload_tasks=tasks)
    await show(uid,state,
        "📤  <b>загрузка плана</b>\n\nраспознано: <b>{}</b>\n\n{}\n\n<i>продолжай или нажми «готово»</i>".format(
            len(tasks),fmt_upload_preview(tasks)),
        KB([("✅ готово","plan_upload_done"),("✕ отмена","plan_manage")]))

# BUG FIX: после успешного ввода веса — сбрасываем состояние
@dp.message(St.weight)
async def fh_w(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    try:
        w=float(msg.text.replace(",",".")); assert 20<=w<=300
        log_w(uid,w)
        await state.set_state(None)   # ← ИСПРАВЛЕНО
        t,m=scr_weight(uid)
        await show(uid,state,"✓ {:.1f} кг\n\n".format(w)+t,m)
    except: await show(uid,state,"❌ введи число 20–300, например <code>72.5</code>",kb_x("weight"))

@dp.message(St.water_custom)
async def fh_wc(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    try:
        a=int(float(msg.text.replace(",","."))); assert 10<=a<=5000
        log_water(uid,a); await state.set_state(None); t,m=scr_water(uid)
        await show(uid,state,"+{} мл\n\n".format(a)+t,m)
    except: await show(uid,state,"❌ введи число 10–5000",kb_x("water"))

@dp.message(St.water_goal)
async def fh_wg(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    try:
        g=int(float(msg.text.replace(",","."))); assert 500<=g<=10000
        upd_user(uid,water_goal=g); await state.set_state(None); t,m=scr_water(uid)
        await show(uid,state,"норма: {} мл/день\n\n".format(g)+t,m)
    except: await show(uid,state,"❌ введи число 500–10000",kb_x("settings"))

@dp.message(St.goal_weight)
async def fh_gw(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    try:
        gw=float(msg.text.replace(",",".")); assert 20<=gw<=300
        upd_user(uid,goal_weight=gw); await state.set_state(None); t,m=scr_goals(uid)
        await show(uid,state,"цель: {:.1f} кг\n\n".format(gw)+t,m)
    except: await show(uid,state,"❌ введи число 20–300",kb_x("goals"))

@dp.message(St.cal_goal)
async def fh_cg(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    try:
        g=int(float(msg.text.replace(",","."))); assert 500<=g<=10000
        upd_user(uid,cal_goal=g); await state.set_state(None); t,m=scr_cal(uid)
        await show(uid,state,"цель: {} ккал/день\n\n".format(g)+t,m)
    except: await show(uid,state,"❌ введи число 500–10000",kb_x("settings"))

@dp.message(St.calories)
async def fh_cal(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    try:
        c=int(float(msg.text.replace(",","."))); assert 1<=c<=10000
        log_cal(uid,c); await state.set_state(None); t,m=scr_cal(uid)
        await show(uid,state,"+{} ккал\n\n".format(c)+t,m)
    except: await show(uid,state,"❌ введи число 1–10000",kb_x("calories"))

@dp.message(St.pname)
async def fh_pn(msg: Message, state: FSMContext):
    uid=msg.from_user.id; name=msg.text.strip()[:50]; await _del(msg)
    upd_user(uid,name=name); await state.set_state(None); t,m=scr_profile(uid)
    await show(uid,state,"имя: <b>{}</b>\n\n".format(name)+t,m)

@dp.message(St.pheight)
async def fh_ph(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    try:
        h=float(msg.text.replace(",",".")); assert 100<=h<=250
        upd_user(uid,height=h); await state.set_state(None); t,m=scr_profile(uid)
        await show(uid,state,"рост: {:.0f} см\n\n".format(h)+t,m)
    except: await show(uid,state,"❌ введи число 100–250",kb_x("profile"))

@dp.message(St.page_age)
async def fh_pa(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    try:
        age=int(msg.text); assert 5<=age<=120
        upd_user(uid,age=age); await state.set_state(None); t,m=scr_profile(uid)
        await show(uid,state,"возраст: {} лет\n\n".format(age)+t,m)
    except: await show(uid,state,"❌ введи число 5–120",kb_x("profile"))

@dp.message(St.act_name)
async def fh_an(msg: Message, state: FSMContext):
    uid=msg.from_user.id; name=msg.text.strip()[:80]; await _del(msg)
    sd=await state.get_data(); na=sd.get("new_act",{}); na["name"]=name
    await state.update_data(new_act=na); await state.set_state(None)
    items=list(ACTS.items()); rows=[]
    for i in range(0,len(items),2):
        row=[B("{} {}".format(ico,lbl),"atype_{}".format(k)) for k,(ico,lbl) in items[i:i+2]]
        rows.append(row)
    rows.append([B("✕","plan_manage")])
    await show(uid,state,"<b>{}</b>\n\nшаг 2 — тип активности:".format(name),
               InlineKeyboardMarkup(inline_keyboard=rows))

@dp.message(St.act_date)
async def fh_ad(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    try:
        tl=msg.text.strip().lower(); td=today_msk()
        d=td if tl in ("сегодня","today") else \
          (td+timedelta(days=1) if tl in ("завтра","tomorrow") else \
           datetime.strptime(msg.text.strip(),"%d.%m.%Y").date())
        sd=await state.get_data(); na=sd.get("new_act",{})
        na["date"]=d.strftime("%Y-%m-%d"); await state.update_data(new_act=na)
        await state.set_state(St.act_timerange)
        await show(uid,state,"дата: <b>{}</b>\n\nшаг 5 — время\n<code>16:00-19:00</code>  или  <code>16-19</code>".format(
            d.strftime("%d.%m.%Y")),kb_x("plan_manage"))
    except: await show(uid,state,"❌ формат: <code>ДД.ММ.ГГГГ</code>, сегодня или завтра",kb_x("plan_manage"))

@dp.message(St.act_timerange)
async def fh_atr(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    try:
        s_,e_,dur=parse_timerange(msg.text)
        sd=await state.get_data(); na=sd.get("new_act",{})
        na["time"]=s_; na["time_range"]="{}-{}".format(s_,e_); na["duration"]=dur
        await state.update_data(new_act=na); await state.set_state(None)
        await show(uid,state,"время: <b>{} – {}</b>  ({} мин)\n\nзадача готова, сохранить?".format(s_,e_,dur),
            KB([("✅ сохранить","rem_0"),("✕","plan_manage")]))
    except: await show(uid,state,"❌ формат: <code>16:00-19:00</code>  или  <code>16-19</code>",kb_x("plan_manage"))

# ── FSM: НОМЕР ЗАДАЧИ В ПЛАНЕ ───────────────────────────────────────
@dp.message(St.plan_num_input)
async def fh_plan_num(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    text=msg.text.strip()
    if not text.isdigit():
        sd=await state.get_data(); off=sd.get("plan_day",0)
        t,m=scr_plan_manage(uid,off); await show(uid,state,t,m); return
    n=int(text)
    sd=await state.get_data(); off=sd.get("plan_day",0)
    today=today_msk()
    sel=today-timedelta(days=today.weekday())+timedelta(days=off)
    acts=acts_for_day(uid,sel)
    if n<1 or n>len(acts):
        t,m=scr_plan_manage(uid,off); await show(uid,state,"❌ номер {} не существует\n\n".format(n)+t,m); return
    a=acts[n-1]; aid=a["id"]; done=bool(a.get("completed"))
    try:
        dt_a=datetime.fromisoformat(a["scheduled_at"]); t_s=dt_a.strftime("%H:%M")
        dur=a.get("duration") or 30; t_e=(dt_a+timedelta(minutes=dur)).strftime("%H:%M")
        time_s="{} – {}".format(t_s,t_e)
    except: time_s=""
    status="✅ выполнено" if done else "⏳ ожидает"
    info_text="{}  <b>{}</b>  <i>{}</i>\n{}".format(aico(a["type"]),a["name"],time_s,status)
    kb_=InlineKeyboardMarkup(inline_keyboard=[
        [B("🗑 удалить","pman_del_{}".format(aid))],
        [B("< назад к плану","plan_manage")],
    ])
    await show(uid,state,info_text,kb_)

# ── FSM: СОН ─────────────────────────────────────────────────────────
@dp.message(St.sleep_hours)
async def fh_sleep_h(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    try:
        h=float(msg.text.replace(",",".")); assert 1<=h<=24
        hours_s=str(h).replace(".","d")
        await state.set_state(None)
        await show(uid,state,"{}ч — качество сна?".format(h),kb_sleep_quality(hours_s))
    except: await show(uid,state,"❌ введи число 1–24 (например 7.5)",kb_x("sleep"))

# ── FSM: БЫСТРЫЕ ПРОДУКТЫ — добавление ──────────────────────────────
@dp.message(St.qp_name)
async def fh_qp_name(msg: Message, state: FSMContext):
    uid=msg.from_user.id; name=msg.text.strip()[:40]; await _del(msg)
    await state.update_data(qp_name=name)
    await state.set_state(St.qp_cal)
    await show(uid,state,"<b>{}</b>\n\nсколько ккал на 100г?".format(name),kb_x("quick_products"))

@dp.message(St.qp_cal)
async def fh_qp_cal(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    try:
        cal=int(float(msg.text.replace(",","."))); assert 0<=cal<=900
        await state.update_data(qp_cal=cal)
        await state.set_state(St.qp_prot)
        await show(uid,state,"белки на 100г (г):\n<i>или 0 если не знаешь</i>",kb_x("quick_products"))
    except: await show(uid,state,"❌ введи число 0–900",kb_x("quick_products"))

@dp.message(St.qp_prot)
async def fh_qp_prot(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    try:
        p=float(msg.text.replace(",",".")); assert 0<=p<=100
        await state.update_data(qp_prot=p); await state.set_state(St.qp_fat)
        await show(uid,state,"жиры на 100г (г):",kb_x("quick_products"))
    except: await show(uid,state,"❌ введи число 0–100",kb_x("quick_products"))

@dp.message(St.qp_fat)
async def fh_qp_fat(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    try:
        f=float(msg.text.replace(",",".")); assert 0<=f<=100
        await state.update_data(qp_fat=f); await state.set_state(St.qp_carb)
        await show(uid,state,"углеводы на 100г (г):",kb_x("quick_products"))
    except: await show(uid,state,"❌ введи число 0–100",kb_x("quick_products"))

@dp.message(St.qp_carb)
async def fh_qp_carb(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    try:
        c=float(msg.text.replace(",",".")); assert 0<=c<=100
        sd=await state.get_data()
        add_product(uid,sd["qp_name"],sd["qp_cal"],sd.get("qp_prot",0),sd.get("qp_fat",0),c)
        ctx=sd.get("qp_ctx","")
        await state.set_state(None)
        await state.update_data(qp_name=None,qp_cal=None,qp_prot=None,qp_fat=None,qp_ctx=None)
        with db() as _c:
            new_p=_c.execute("SELECT * FROM quick_products WHERE user_id=? ORDER BY id DESC LIMIT 1",(uid,)).fetchone()
        if ctx=="food" and new_p:
            t,m=scr_food_grams(new_p["id"])
            await show(uid,state,"✅  <b>{}</b> сохранён\n\n".format(sd["qp_name"])+t,m)
        else:
            t,m=scr_quick_products(uid)
            await show(uid,state,"✅  <b>{}</b> добавлен\n\n".format(sd["qp_name"])+t,m)
    except: await show(uid,state,"❌ введи число 0–100",kb_x("quick_products"))

# ── FSM: КБЖУ ГРАММЫ ─────────────────────────────────────────────────
@dp.message(St.kbzhu_grams)
async def fh_kbzhu(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    try:
        grams=int(float(msg.text.replace(",","."))); assert 1<=grams<=5000
        sd=await state.get_data(); pid=sd.get("kbzhu_pid"); mode=sd.get("kbzhu_mode","calc")
        p=get_product(pid)
        if not p: await show(uid,state,"❌ продукт не найден",kb_x("quick_products")); return
        kcal=int(p["calories"]*grams/100)
        prot=round(p["protein"]*grams/100,1)
        fat =round(p["fat"]*grams/100,1)
        carb=round(p["carbs"]*grams/100,1)
        text="🧮  <b>{}</b>  {}г\n\n<code>ккал    {}\nбелки   {}\nжиры    {}\nуглев.  {}</code>".format(
            p["name"],grams,kcal,prot,fat,carb)
        await state.set_state(None)
        if mode=="log":
            mark_product_used(p["id"])
            log_cal(uid,kcal,desc="{} {}г".format(p["name"],grams))
            t,m=scr_nutrition(uid)
            await show(uid,state,text+"\n\n✅  <b>+{} ккал</b> записано\n\n".format(kcal)+t,m)
        else:
            kb_=KB([("✅ записать {} ккал".format(kcal),"kbzhu_log_{}_{}".format(pid,grams))],
                   [("< калькулятор","kbzhu")])
            await show(uid,state,text,kb_)
    except: await show(uid,state,"❌ введи количество грамм (например 150)",kb_x("quick_products"))

# ── FSM: НАПОМИНАНИЯ ─────────────────────────────────────────────────
@dp.message(St.remind_water_manual)
async def fh_remind_water(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    try:
        parts_=[p.strip() for p in msg.text.split(",")]
        valid=[]
        for p_ in parts_:
            h,mn=parse_time_hm(p_); valid.append("{:02d}:{:02d}".format(h,mn))
        if not valid: raise ValueError
        set_reminder(uid,"water",enabled=1,schedule=json.dumps(valid),interval_hours=0)
        await state.set_state(None); t,m=scr_reminders(uid)
        await show(uid,state,"✅  напоминания: {}\n\n".format(", ".join(valid))+t,m)
    except: await show(uid,state,"❌ формат: <code>8:00, 12:00, 18:00</code>",kb_x("reminders"))

@dp.message(St.remind_weight_time)
async def fh_remind_weight(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    try:
        h,mn=parse_time_hm(msg.text.strip()); t_s="{:02d}:{:02d}".format(h,mn)
        set_reminder(uid,"weight",enabled=1,schedule=json.dumps([t_s]))
        await state.set_state(None); t,m=scr_reminders(uid)
        await show(uid,state,"✅  напоминание взвеситься в {}\n\n".format(t_s)+t,m)
    except: await show(uid,state,"❌ формат: <code>08:00</code>",kb_x("reminders"))

@dp.message(St.remind_report_time)
async def fh_remind_report(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    try:
        h,mn=parse_time_hm(msg.text.strip()); t_s="{:02d}:{:02d}".format(h,mn)
        sd=await state.get_data(); day=sd.get("report_day",0)
        set_reminder(uid,"report",enabled=1,schedule=json.dumps([t_s]),report_day=day)
        await state.set_state(None); t,m=scr_reminders(uid)
        await show(uid,state,"✅  отчёт {}  в {}\n\n".format(DAYS_RU[day],t_s)+t,m)
    except: await show(uid,state,"❌ формат: <code>09:00</code>",kb_x("reminders"))


# ── FSM: ГРАММЫ ПРИ ДОБАВЛЕНИИ ────────────────────────────────────
@dp.message(St.food_grams)
async def fh_food_grams(msg: Message, state: FSMContext):
    uid=msg.from_user.id; await _del(msg)
    try:
        grams=int(float(msg.text.replace(",","."))); assert 1<=grams<=5000
        sd=await state.get_data(); pid=sd.get("food_pid")
        await state.set_state(None)
        t,m=scr_food_meal(pid,grams); await show(uid,state,t,m)
    except: await show(uid,state,"❌ введи количество граммов (например 150)",kb_x("food_add"))

# ── FALLBACK ────────────────────────────────────────────────────────
@dp.message(F.text)
async def fallback(msg: Message, state: FSMContext):
    uid=msg.from_user.id
    upsert(uid,msg.from_user.first_name or "")
    await _del(msg)
    text=msg.text.strip()
    # Навигация по карточкам цифрой
    sess=card_sessions.get(uid)
    if sess and text.isdigit():
        n=int(text); card_list=sess["card_list"]; idx=n-1
        if 0<=idx<len(card_list):
            sess["card_idx"]=idx; await show_card(uid,state,idx,card_list)
        else:
            card_text=build_card_text(uid,sess["card_idx"],card_list)
            kb=kb_card(sess["card_idx"],card_list,card_list[sess["card_idx"]])
            await show(uid,state,"❌ нет карточки {}\n\n".format(n)+card_text,kb)
        return
    card_sessions.pop(uid,None)
    t,m=scr_main(uid); await show(uid,state,t,m)


# ── FLASK API — запуск в отдельном потоке ───────────────────────────
def start_flask():
    """Запускает Flask API-сервер параллельно с ботом."""
    try:
        import subprocess, sys as _sys
        # Устанавливаем flask и flask-cors если их нет
        for pkg in ("flask", "flask-cors"):
            try:
                __import__(pkg.replace("-","_"))
            except ImportError:
                subprocess.check_call([_sys.executable, "-m", "pip", "install", pkg, "-q"])

        from flask import Flask, request, jsonify, send_from_directory
        from flask_cors import CORS
        import threading, sqlite3 as _sqlite3, os as _os
        from datetime import timedelta, date as _dt_date

        flask_app = Flask(__name__, static_folder="static")
        CORS(flask_app)

        def _db():
            c = _sqlite3.connect(DB_PATH)
            c.row_factory = _sqlite3.Row
            return c

        def _today():
            return now_msk().date().isoformat()

        def _r2d(r):
            return dict(r) if r else {}

        def _rs(rows):
            return [dict(r) for r in rows]

        # ── Отдаём HTML ───────────────────────────────────────────────
        @flask_app.route("/")
        def _index():
            return send_from_directory("static", "letify.html")

        # ── Полные данные пользователя ────────────────────────────────
        @flask_app.route("/api/data")
        def _get_data():
            uid = request.args.get("uid", type=int)
            if not uid:
                return jsonify({"error": "uid required"}), 400
            today = _today()
            with _db() as c:
                user    = _r2d(c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone())
                setts   = _r2d(c.execute("SELECT * FROM user_settings WHERE user_id=?", (uid,)).fetchone())
                w_rows  = _rs(c.execute("SELECT amount,logged_at FROM water_log WHERE user_id=? AND date(logged_at)=?", (uid, today)).fetchall())
                w_total = sum(r["amount"] for r in w_rows)
                cal_rows= c.execute("SELECT meal_type,SUM(amount) s FROM calories_log WHERE user_id=? AND date(logged_at)=? GROUP BY meal_type",(uid,today)).fetchall()
                cal_day = {r["meal_type"]: r["s"] for r in cal_rows}
                wlog    = _rs(c.execute("SELECT weight,logged_at FROM weight_log WHERE user_id=? ORDER BY logged_at DESC LIMIT 30",(uid,)).fetchall())
                slog    = _rs(c.execute("SELECT hours,quality,logged_at FROM sleep_log WHERE user_id=? ORDER BY logged_at DESC LIMIT 14",(uid,)).fetchall())
                acts1   = _rs(c.execute("SELECT * FROM activities WHERE user_id=? AND days_of_week='' AND date(scheduled_at)=? ORDER BY scheduled_at",(uid,today)).fetchall())
                acts_r  = _rs(c.execute("SELECT * FROM activities WHERE user_id=? AND days_of_week!=''",(uid,)).fetchall())
                prods   = _rs(c.execute("SELECT id,name,calories,protein,fat,carbs FROM quick_products WHERE user_id=? ORDER BY name",(uid,)).fetchall())
                s_rows  = c.execute("SELECT date(logged_at) d,SUM(amount) s FROM water_log WHERE user_id=? GROUP BY date(logged_at) ORDER BY date(logged_at) DESC",(uid,)).fetchall()
            # Convert Python weekday (0=Mon..6=Sun) to JS getDay convention (0=Sun,1=Mon..6=Sat)
            py_dow = now_msk().weekday()  # 0=Mon..6=Sun
            dow = (py_dow + 1) % 7        # 0=Sun,1=Mon..6=Sat (matches JS getDay & HTML buttons)
            acts = list(acts1)
            for a in acts_r:
                days = [int(d) for d in a["days_of_week"].split(",") if d.strip()]
                if dow in days:
                    acts.append(a)
            acts.sort(key=lambda x: x.get("scheduled_at",""))
            goal_w = user.get("water_goal", 2000) or 2000
            streak = 0
            check  = now_msk().date()
            for r in s_rows:
                d = _dt_date.fromisoformat(r["d"])
                if d != check: break
                if r["s"] >= goal_w: streak += 1
                else: break
                check -= timedelta(days=1)
            return jsonify({"user":user,"settings":setts,"water":{"total":w_total,"entries":[r["amount"] for r in w_rows],"streak":streak},"calories":cal_day,"weight_log":wlog,"sleep_log":slog,"acts_today":acts,"products":prods})

        # ── Вода ──────────────────────────────────────────────────────
        @flask_app.route("/api/water", methods=["POST"])
        def _add_water():
            d = request.json or {}
            uid, amt = d.get("uid"), d.get("amount")
            if not uid or not amt: return jsonify({"error":"uid and amount required"}),400
            with _db() as c: c.execute("INSERT INTO water_log (user_id,amount) VALUES (?,?)",(uid,amt))
            return jsonify({"ok":True})

        @flask_app.route("/api/water/undo", methods=["POST"])
        def _undo_water():
            uid = (request.json or {}).get("uid")
            if not uid: return jsonify({"error":"uid required"}),400
            with _db() as c:
                r = c.execute("SELECT id FROM water_log WHERE user_id=? ORDER BY logged_at DESC LIMIT 1",(uid,)).fetchone()
                if r: c.execute("DELETE FROM water_log WHERE id=?",(r["id"],))
            return jsonify({"ok":True})

        # ── Сон ───────────────────────────────────────────────────────
        @flask_app.route("/api/sleep", methods=["POST"])
        def _add_sleep():
            d = request.json or {}
            uid, hrs, q = d.get("uid"), d.get("hours"), d.get("quality",3)
            if not uid or hrs is None: return jsonify({"error":"uid and hours required"}),400
            with _db() as c: c.execute("INSERT INTO sleep_log (user_id,hours,quality) VALUES (?,?,?)",(uid,hrs,q))
            return jsonify({"ok":True})

        # ── Вес ───────────────────────────────────────────────────────
        @flask_app.route("/api/weight", methods=["POST"])
        def _add_weight():
            d = request.json or {}
            uid, w = d.get("uid"), d.get("weight")
            if not uid or w is None: return jsonify({"error":"uid and weight required"}),400
            with _db() as c:
                # Only insert a new log entry — NEVER auto-update start_weight here.
                # start_weight is set ONLY via /api/profile (pp-start-weight screen).
                c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
                c.execute("INSERT INTO weight_log (user_id,weight) VALUES (?,?)",(uid,w))
            return jsonify({"ok":True})

        # ── Активности ────────────────────────────────────────────────
        @flask_app.route("/api/activity/complete", methods=["POST"])
        def _complete_act():
            d = request.json or {}
            uid, aid = d.get("uid"), d.get("act_id")
            if not uid or not aid: return jsonify({"error":"uid and act_id required"}),400
            with _db() as c: c.execute("UPDATE activities SET completed=1,ended_at=datetime('now','+3 hours') WHERE id=? AND user_id=?",(aid,uid))
            return jsonify({"ok":True})

        @flask_app.route("/api/activity/uncomplete", methods=["POST"])
        def _uncomplete_act():
            d = request.json or {}
            uid, aid = d.get("uid"), d.get("act_id")
            if not uid or not aid: return jsonify({"error":"uid and act_id required"}),400
            with _db() as c: c.execute("UPDATE activities SET completed=0,ended_at=NULL WHERE id=? AND user_id=?",(aid,uid))
            return jsonify({"ok":True})

        @flask_app.route("/api/activity/delete", methods=["POST"])
        def _delete_act():
            d = request.json or {}
            uid, aid = d.get("uid"), d.get("act_id")
            if not uid or not aid: return jsonify({"error":"uid and act_id required"}),400
            with _db() as c: c.execute("DELETE FROM activities WHERE id=? AND user_id=?",(aid,uid))
            return jsonify({"ok":True})

        # ── Профиль ───────────────────────────────────────────────────
        @flask_app.route("/api/profile", methods=["POST"])
        def _update_profile():
            d = request.json or {}
            uid = d.get("uid")
            if not uid: return jsonify({"error":"uid required"}),400
            fields = {}
            if "name" in d: fields["name"] = d["name"]
            if "height" in d: fields["height"] = d["height"]
            if "age" in d: fields["age"] = d["age"]
            if "gender" in d: fields["gender"] = d["gender"]
            if not fields: return jsonify({"ok":True})
            sets = ", ".join(f"{k}=?" for k in fields)
            with _db() as c:
                # ensure user exists
                c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
                c.execute(f"UPDATE users SET {sets} WHERE user_id=?", list(fields.values())+[uid])
            return jsonify({"ok":True})

        # ── Цели ─────────────────────────────────────────────────────
        @flask_app.route("/api/goals", methods=["POST"])
        def _update_goals():
            d = request.json or {}
            uid = d.get("uid")
            if not uid: return jsonify({"error":"uid required"}),400
            fields = {}
            if "goal_weight" in d: fields["goal_weight"] = d["goal_weight"]
            if "water_goal" in d: fields["water_goal"] = d["water_goal"]
            if "cal_goal" in d: fields["cal_goal"] = d["cal_goal"]
            if not fields: return jsonify({"ok":True})
            sets = ", ".join(f"{k}=?" for k in fields)
            with _db() as c:
                c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
                c.execute(f"UPDATE users SET {sets} WHERE user_id=?", list(fields.values())+[uid])
            return jsonify({"ok":True})

        # ── Калории (добавить приём пищи) ────────────────────────────
        @flask_app.route("/api/calories", methods=["POST"])
        def _add_calories():
            d = request.json or {}
            uid, amt, meal_type, desc = d.get("uid"), d.get("amount"), d.get("meal_type","other"), d.get("description","")
            if not uid or amt is None: return jsonify({"error":"uid and amount required"}),400
            with _db() as c: c.execute("INSERT INTO calories_log (user_id,amount,description,meal_type) VALUES (?,?,?,?)",(uid, amt, desc, meal_type))
            return jsonify({"ok":True})

        # ── Создать активность ────────────────────────────────────────
        @flask_app.route("/api/activity/create", methods=["POST"])
        def _create_act():
            d = request.json or {}
            uid = d.get("uid")
            if not uid: return jsonify({"error":"uid required"}),400
            name = d.get("name","Тренировка")
            atype = d.get("type","other")
            time_str = d.get("time","08:00")
            dur = d.get("duration",30)
            today = _today()
            scheduled = f"{today}T{time_str}:00"
            # repeat_days uses JS getDay() convention: 0=Sun,1=Mon,...,6=Sat
            rep = d.get("repeat_days", [])
            dow_str = ",".join(str(r) for r in rep) if rep else ""
            with _db() as c:
                c.execute("INSERT INTO activities (user_id,name,type,scheduled_at,duration,days_of_week) VALUES (?,?,?,?,?,?)",
                          (uid, name, atype, scheduled, dur, dow_str))
            return jsonify({"ok":True})

        # ── Настройки отображения ─────────────────────────────────────
        @flask_app.route("/api/settings", methods=["POST"])
        def _update_settings():
            d = request.json or {}
            uid = d.get("uid")
            if not uid: return jsonify({"error":"uid required"}),400
            fields = {k:v for k,v in d.items() if k in ("show_weight","show_water","show_calories","show_sleep")}
            if not fields: return jsonify({"ok":True})
            sets = ", ".join(f"{k}=?" for k in fields)
            with _db() as c:
                c.execute("INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)", (uid,))
                c.execute(f"UPDATE user_settings SET {sets} WHERE user_id=?", list(fields.values())+[uid])
            return jsonify({"ok":True})

        port = int(_os.environ.get("PORT", 5000))
        t = threading.Thread(target=lambda: flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False), daemon=True)
        t.start()
        log.info(f"🌐 Mini App API запущен на порту {port}")
    except Exception as e:
        log.error(f"Flask не запустился: {e}")



# ── MAIN ────────────────────────────────────────────────────────────
async def main():
    init_db()
    start_flask()   # ← запускаем API сервер
    # Планировщик
    scheduler.add_job(tick_cards,          "interval", seconds=60,  id="tick_cards")
    scheduler.add_job(check_water_reminders,"interval", seconds=60, id="water_remind")
    scheduler.add_job(check_weight_reminders,"interval",seconds=60, id="weight_remind")
    scheduler.add_job(check_weekly_report,  "interval", seconds=60, id="weekly_report")
    scheduler.start()
    log.info("letify ☀️ запущен — путь к лету начинается!")
    await dp.start_polling(bot, drop_pending_updates=True)

if __name__=="__main__":
    asyncio.run(main())