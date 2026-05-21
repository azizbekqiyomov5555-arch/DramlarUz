# -*- coding: utf-8 -*-
"""
Kino Bot - v22 (PostgreSQL + TO'LOVLAR RASM PANEL)
"""

import logging, asyncio, json, time, re, os, threading, copy
from datetime import datetime
from io import BytesIO
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CopyTextButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

# ─── KONFIGURATSIYA ────────────────────────────────────────
BOT_TOKEN  = os.environ.get("BOT_TOKEN")  or "8723400610:AAFOonUirSoYYE4-gVVmUCacYIcUHEWXQzU"
ADMIN_ID   = int(os.environ.get("ADMIN_ID") or "8537782289")

DATABASE_URL      = os.environ.get("DATABASE_URL") or ""
JSONBLOB_URL      = ""   # O'chirildi — PostgreSQL ishlatilmoqda
GSHEET_ID         = os.environ.get("GSHEET_ID")    or "1Lodn9MTb7nysq5l80cQVCu9IKfgQRlnNe654PT0hKQs"

# ─── CHECKCARD API KONFIGURATSIYASI ─────────────────────────
CHECKCARD_SHOP_ID  = os.environ.get("CHECKCARD_SHOP_ID")  or "249444"
CHECKCARD_SHOP_KEY = os.environ.get("CHECKCARD_SHOP_KEY") or "ZB3GJ99FI5"
CHECKCARD_BASE_URL = "https://checkcard.uz/api"

# ─── RAILWAY / WEBHOOK KONFIGURATSIYASI ─────────────────────
RAILWAY_URL = os.environ.get("RAILWAY_URL") or "https://kino-bot-mukammal-production.up.railway.app"
CHECKCARD_WEBHOOK_PATH = "/checkcard_webhook"
GSHEET_API        = os.environ.get("GSHEET_API")   or ""
NPOINT_URL        = os.environ.get("NPOINT_URL")   or ""
LOCAL_BACKUP_FILE = "db_backup.json"
LOCAL_MOVIES_FILE = "movies_backup.json"

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════
# ❗ RAM CACHE — barcha kinolar shu yerda saqlanadi
# ══════════════════════════════════════════════════════════
class RamCache:
    """
    Bot ishlayotganda barcha ma'lumotlar shu obyektda turadi.
    JSONBlob faqat fon rejimida yoziladi — bot sekinlamaydi.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self.movies: dict   = {}   # {code: movie_dict}
        self.users: dict    = {}   # {uid_str: user_dict}
        self.channels: list = []
        self.card_number: str = ""
        self.pending_payments: dict = {}
        self.simple_links: list = []   # Tekshirilmaydigan oddiy havolalar
        self.settings: dict = {
            "install_file_id": None,
            "install_video_id": None,
            "install_caption": "",
            "kino_kanal_url": "",
            "start_msg_text": "",
            "start_msg_photo": None,
            "admin_lichka": "",
        }
        self.stats: dict    = {"total_views": 0}
        self.btn_texts: dict = {}
        self.emoji_ids: dict = {}
        self.sub_admins: dict = {}   # {uid_str: {"perms": {key: bool}}}
        self.blocked_users: dict = {}  # {uid_str: {"blocked_at": timestamp, "by": admin_uid}}
        self.loaded: bool   = False  # bazadan yuklandi?

    # ── Barcha ma'lumotlarni dict ga ──────────────────────
    def to_dict(self) -> dict:
        with self._lock:
            return {
                "movies":           copy.deepcopy(self.movies),
                "users":            copy.deepcopy(self.users),
                "channels":         copy.deepcopy(self.channels),
                "simple_links":     copy.deepcopy(self.simple_links),
                "card_number":      self.card_number,
                "pending_payments": copy.deepcopy(self.pending_payments),
                "settings":         copy.deepcopy(self.settings),
                "stats":            copy.deepcopy(self.stats),
                "btn_texts":        copy.deepcopy(self.btn_texts),
                "emoji_ids":        copy.deepcopy(self.emoji_ids),
                "sub_admins":       copy.deepcopy(self.sub_admins),
                "blocked_users":    copy.deepcopy(self.blocked_users),
            }

    # ── Dict dan yuklash ──────────────────────────────────
    def from_dict(self, data: dict):
        if not isinstance(data, dict):
            return
        with self._lock:
            self.movies           = data.get("movies", {}) or {}
            self.users            = data.get("users", {}) or {}
            self.channels         = data.get("channels", []) or []
            self.simple_links     = data.get("simple_links", []) or []
            self.card_number      = data.get("card_number", "") or ""
            self.pending_payments = data.get("pending_payments", {}) or {}
            self.settings         = data.get("settings", {}) or {}
            # Eski bazalarda yo'q kalitlarni qo'shamiz
            for _k, _v in {
                "install_file_id": None,
                "install_video_id": None,
                "install_caption": "",
                "kino_kanal_url": "",
                "start_msg_text": "",
                "start_msg_photo": None,
                "admin_lichka": "",
            }.items():
                self.settings.setdefault(_k, _v)
            self.stats            = data.get("stats", {"total_views": 0})
            self.btn_texts        = data.get("btn_texts", {}) or {}
            self.emoji_ids        = data.get("emoji_ids", {}) or {}
            self.sub_admins       = data.get("sub_admins", {}) or {}
            self.blocked_users    = data.get("blocked_users", {}) or {}
            self.loaded           = True

    # ── Kino operatsiyalari ───────────────────────────────
    def get_movie(self, code: str) -> dict | None:
        return self.movies.get(code.upper())

    def set_movie(self, code: str, data: dict):
        with self._lock:
            self.movies[code.upper()] = data

    def del_movie(self, code: str):
        with self._lock:
            self.movies.pop(code.upper(), None)

    def get_all_movies(self) -> dict:
        return self.movies  # direct ref — tezkor

    # ── Foydalanuvchi operatsiyalari ──────────────────────
    def get_user(self, uid) -> dict:
        return self.users.get(str(uid), {})

    def set_user(self, uid, data: dict):
        with self._lock:
            self.users[str(uid)] = data

    def ensure_user(self, uid):
        uid_str = str(uid)
        with self._lock:
            if uid_str not in self.users:
                self.users[uid_str] = {
                    "paid_episodes": {},
                    "watched": {},
                }
            u = self.users[uid_str]
            # Eski foydalanuvchilar uchun kalitlar yo'q bo'lsa — qo'shamiz
            if "paid_episodes" not in u or not isinstance(u.get("paid_episodes"), dict):
                u["paid_episodes"] = {}
            if "watched" not in u or not isinstance(u.get("watched"), dict):
                u["watched"] = {}
            if "premium_until" not in u:
                u["premium_until"] = 0
            # ✅ BALANS — eski userlar uchun ham kafolatlanadi
            if "balance" not in u:
                u["balance"] = 0
            if "topup_total" not in u:
                u["topup_total"] = 0   # jami kiritilgan pul
        return self.users[uid_str]


# Global RAM cache
RAM = RamCache()


# ══════════════════════════════════════════════════════════
# CHECKCARD API — AVTOMATIK TO'LOV TIZIMI
# ══════════════════════════════════════════════════════════

def checkcard_create_payment(amount: int, order_id: str = None) -> dict:
    """CheckCard API orqali yangi to'lov yaratadi.
    amount — so'mda (masalan 5000), API so'mda qabul qiladi.
    """
    try:
        if not order_id:
            order_id = f"ord{int(time.time())}"
        # CheckCard API so'mda ishlaydi — x100 KERAK EMAS
        url = (f"{CHECKCARD_BASE_URL}?method=create"
               f"&shop_id={CHECKCARD_SHOP_ID}"
               f"&shop_key={CHECKCARD_SHOP_KEY}"
               f"&amount={amount}"
               f"&order={order_id}")
        r = requests.get(url, timeout=20)
        logger.info(f"CheckCard create javob: {r.text[:300]}")
        return r.json()
    except Exception as e:
        logger.error(f"CheckCard create xato: {e}")
        return {"status": "error", "message": str(e)}


def checkcard_check_payment(order: str) -> dict:
    """CheckCard to'lov statusini tekshiradi."""
    try:
        url = (f"{CHECKCARD_BASE_URL}?method=check"
               f"&order={order}")
        r = requests.get(url, timeout=20)
        logger.info(f"CheckCard check javob: {r.text[:200]}")
        return r.json()
    except Exception as e:
        logger.error(f"CheckCard check xato: {e}")
        return {"status": "error"}


def checkcard_cancel_payment(order: str) -> dict:
    """CheckCard to'lovni bekor qiladi."""
    try:
        url = f"{CHECKCARD_BASE_URL}?method=cancel&order={order}"
        r = requests.get(url, timeout=20)
        return r.json()
    except Exception as e:
        logger.error(f"CheckCard cancel xato: {e}")
        return {"status": "error"}


def checkcard_shop_info() -> dict:
    """Do'kon ma'lumotlarini oladi."""
    try:
        url = (f"{CHECKCARD_BASE_URL}?method=shop"
               f"&shop_id={CHECKCARD_SHOP_ID}"
               f"&shop_key={CHECKCARD_SHOP_KEY}")
        r = requests.get(url, timeout=20)
        return r.json()
    except Exception as e:
        logger.error(f"CheckCard shop info xato: {e}")
        return {"status": "error"}


def price_to_int(value) -> int:
    """Narxni xavfsiz int ga aylantiradi. 0/bo'sh qiymat bepul hisoblanadi."""
    try:
        if value in (None, "", 0, "0"):
            return 0
        return int(str(value).strip())
    except Exception:
        return 0


def episode_paid_key(code, ep) -> str:
    """Har bir kino-qism uchun yagona alohida to'lov kaliti."""
    return f"{str(code).upper()}_{str(ep)}"


def has_approved_payment(user_id, code, ep) -> bool:
    """Faqat shu foydalanuvchi + shu kino + shu qism uchun tasdiqlangan chekni tekshiradi."""
    uid = str(user_id)
    code = str(code).upper()
    ep = str(ep)
    for pay in (RAM.pending_payments or {}).values():
        if (str(pay.get("user_id")) == uid
                and str(pay.get("code", "")).upper() == code
                and str(pay.get("ep")) == ep
                and pay.get("status") == "approved"):
            return True
    return False


def is_premium_user(user_id) -> bool:
    """Foydalanuvchi premium muddati ichidami?"""
    try:
        u = RAM.get_user(str(user_id)) or {}
        until = float(u.get("premium_until") or 0)
        return until > time.time()
    except Exception:
        return False


def is_blocked_user(user_id) -> bool:
    """Foydalanuvchi admin tomonidan bloklangan mi?"""
    uid_str = str(user_id)
    return uid_str in (RAM.blocked_users or {})


def premium_left_days(user_id) -> int:
    try:
        u = RAM.get_user(str(user_id)) or {}
        until = float(u.get("premium_until") or 0)
        left = (until - time.time()) / 86400.0
        return int(left) if left > 0 else 0
    except Exception:
        return 0


EPISODE_ACCESS_DURATION = 7 * 24 * 3600  # 7 kun = 604800 soniya (sotib olgandan keyin 7 kun bepul)


def is_episode_paid(user_id, code, ep) -> bool:
    """
    Bir qism sotib olingani faqat o'sha qism uchun tekshiriladi.
    Premium foydalanuvchilar uchun barcha qismlar bepul ochiq.
    Balansdan sotib olingan qismlar 7 kundan keyin qayta pullik bolib qoladi.
    """
    if is_premium_user(user_id):
        return True
    uid = str(user_id)
    code = str(code).upper()
    ep = str(ep)
    key = episode_paid_key(code, ep)
    user = RAM.ensure_user(uid)
    paid = user.setdefault("paid_episodes", {})
    value = paid.get(key)

    if isinstance(value, dict):
        if value.get("status") != "approved" and not value.get("approved"):
            return False
        # Expire tekshirish — faqat balance orqali sotib olingan qismlar uchun
        expire_at = value.get("expire_at")
        if expire_at:
            if time.time() > float(expire_at):
                return False  # Muddati otgan — qayta pullik
        return True

    if value:
        return has_approved_payment(uid, code, ep)

    return False


def episode_expires_in(user_id, code, ep) -> int:
    """Qism muddati tugashiga necha soniya qolganini qaytaradi. 0 = muddatsiz yoki otgan."""
    uid = str(user_id)
    code = str(code).upper()
    ep = str(ep)
    key = episode_paid_key(code, ep)
    user = RAM.ensure_user(uid)
    paid = user.get("paid_episodes", {})
    value = paid.get(key)
    if isinstance(value, dict) and value.get("expire_at"):
        left = float(value["expire_at"]) - time.time()
        return max(0, int(left))
    return 0


# ── Admin huquqlari ─────────────────────────────────────────
ADMIN_PERM_KEYS = [
    "kino_joy", "qism_qosh", "pullik", "stat", "kanal_post",
    "maj_kanal", "karta", "ilova", "emoji_soz", "kino_kanal_set",
    "qism_tahrir", "kino_uch", "broadcast",
    "premium_ber", "start_xab", "qism_och", "foydalanuvchi_blok",
]

def is_super_admin(uid) -> bool:
    try: return int(uid) == ADMIN_ID
    except: return False

def is_any_admin(uid) -> bool:
    if is_super_admin(uid): return True
    return str(uid) in (RAM.sub_admins or {})

def has_perm(uid, key: str) -> bool:
    if is_super_admin(uid): return True
    sub = (RAM.sub_admins or {}).get(str(uid))
    if not sub: return False
    perms = sub.get("perms", {}) or {}
    # Default: allowed (True). Faqat aniq False bo'lsa — taqiqlangan.
    return perms.get(key, True) is not False


# ❗ Global update_id dedup — bir xil update ikki marta ishlanmasin
_SEEN_UPDATE_IDS: set = set()
_SEEN_MAX = 1000

def _is_duplicate_update(update) -> bool:
    """True bo'lsa — bu update allaqachon ishlangan, o'tkazib yuborish kerak."""
    uid = getattr(update, "update_id", None)
    if uid is None:
        return False
    if uid in _SEEN_UPDATE_IDS:
        logger.warning(f"⚠️ Duplicate update_id={uid} — o'tkazib yuborildi")
        return True
    _SEEN_UPDATE_IDS.add(uid)
    if len(_SEEN_UPDATE_IDS) > _SEEN_MAX:
        oldest = sorted(_SEEN_UPDATE_IDS)[:200]
        for x in oldest:
            _SEEN_UPDATE_IDS.discard(x)
    return False

# ══════════════════════════════════════════════════════════
# STORAGE STATUS
# ══════════════════════════════════════════════════════════
DB_STATUS = {
    "storage_ok": True,
    "fail_count": 0,
    "last_save_ok": None,
    "last_err": None,
    "ram_only": False,
    "pending_save": False,   # saqlanish navbatda?
    "load_failed": False,    # JSONBlob yuklanmadi (faqat log uchun, saqlashni bloklamaydi)
}

EMOJI_IDS: dict = {}  # RAM.emoji_ids bilan sinxron

# ─── UNICODE QALIN ─────────────────────────────────────────
def to_bold(text: str) -> str:
    result = []
    for ch in text:
        if 'A' <= ch <= 'Z':   result.append(chr(0x1D5D4 + ord(ch) - ord('A')))
        elif 'a' <= ch <= 'z': result.append(chr(0x1D5EE + ord(ch) - ord('a')))
        elif '0' <= ch <= '9': result.append(chr(0x1D7EC + ord(ch) - ord('0')))
        else:                  result.append(ch)
    return ''.join(result)

_B = to_bold

# ─── BUTTON TEXTS ──────────────────────────────────────────
DEFAULT_BTN = {
    "yordam":         _B('Yordam'),
    "install":        _B("Qo'llanma video"),
    "barcha_kino":    _B('Barcha kinolar'),
    "kino_kanal":     _B('Kino kodlari kanali'),
    "kino_joy":       _B('Kino joylash'),
    "qism_qosh":      _B('Qism qoshish'),
    "pullik":         _B('Qismni pullik qilish'),
    "stat":           _B('Statistika'),
    "kanal_post":     _B('Kanalga post'),
    "maj_kanal":      _B('Majburiy kanal'),
    "karta":          _B('Karta raqami'),
    "ilova":          _B('Bot qollanma video'),
    "emoji_soz":      _B('Emoji sozlamalari'),
    "asosiy":         _B('Asosiy menyu'),
    "boshqarish":     _B('Boshqarish'),
    "tekshir":        _B('Tekshirish'),
    "tasdiq":         _B('Tasdiqlash'),
    "bekor":          _B('Bekor qilish'),
    "ulash":          _B('Dostlarga ulashish'),
    "tomosha":        _B('Tomosha qilish'),
    "javob":          _B('Javob berish'),
    "yangi":          _B('Yangilash'),
    "qism_add":       _B('Qism qoshish'),
    "narx_bel":       _B('Narx belgilash'),
    "kut":            _B('Tasdiqlanishini kuting'),
    "bosh":           _B('Bosh menyu'),
    "tiklash":        _B('Hammasini tiklash'),
    "yopish":         _B('Yopish'),
    "default_q":      _B('Defaultga qaytarish'),
    "orqaga":         _B('Orqaga'),
    "broadcast":      _B('Barchaga xabar'),
    "kino_uch":       _B('Kino ochirish'),
    "prev_qism":      _B('Oldingi qismlar'),
    "next_qism":      _B('Boshqa qismlar'),
    "kino_kanal_set": _B('Kino kanali linkini ornatish'),
    "chek_yub":       _B('Chek yuborish'),
    "karta_nusxa":    _B('Karta nusxalash'),
    "miqdor_nusxa":   _B('Miqdor nusxalash'),
    "kanal_qosh":     _B('Kanal qoshish'),
    "kanal_uch":      _B('Kanal ochirish'),
    "kanal_royxat":   _B('Kanallar royxati'),
    "oddiy_havola":   _B("Oddiy havola qo'shish"),
    "soruvli_kanal":  _B("So'rovli kanal qo'shish"),
    "admin_lichka_set": _B("👤 Admin lichkasini qo'shish"),
    "qism_tahrir":    _B('Qismlarni tahrirlash'),
    "admin_qosh":     _B('Admin qoshish'),
    "qism_och":       _B("Qism ochish"),
    "premium_ber":    _B('Premium berish'),
    "start_xab":      _B('Start xabarni ozgartirish'),
    "kod_btn":        _B('Kod'),
    "kanal_btn":      _B('Kanal'),
    "balans":         _B('💰 Balans'),
    "hisob_toldirish": _B('💳 Hisobni to\'ldirish'),
    "foydalanuvchi_blok": _B('🚫 Foydalanuvchi bloklash'),
    "tolovlar":           _B("💸 Foydalanuvchi to'lovlari"),
}

BTN_LABELS = {
    "yordam":        "Yordam tugmasi",
    "install":       "O'rnatish tugmasi",
    "barcha_kino":   "Barcha kinolar tugmasi",
    "kino_kanal":    "Kino kodlari kanali tugmasi",
    "kino_kanal_set":"Kino kanali linki",
    "kino_joy":      "Kino joylash",
    "qism_qosh":     "Qism qo'shish",
    "pullik":        "Pullik qilish",
    "stat":          "Statistika",
    "kanal_post":    "Kanalga post",
    "maj_kanal":     "Majburiy kanal",
    "karta":         "Karta raqami",
    "ilova":         "Bot qo'llanma video",
    "emoji_soz":     "Emoji sozlamalari",
    "asosiy":        "Asosiy menyu",
    "boshqarish":    "⚙️ Boshqarish",
    "tekshir":       "Tekshirish",
    "tasdiq":        "Tasdiqlash",
    "bekor":         "Bekor qilish",
    "ulash":         "Ulashish",
    "tomosha":       "Tomosha qilish",
    "javob":         "Javob berish",
    "yangi":         "Yangilash",
    "qism_add":      "Qism qo'shish (inline)",
    "narx_bel":      "Narx belgilash",
    "kut":           "Kuting tugmasi",
    "bosh":          "Bosh menyu (inline)",
    "tiklash":       "Hammasini tiklash",
    "yopish":        "Yopish",
    "default_q":     "Defaultga qaytarish",
    "orqaga":        "Orqaga",
    "broadcast":     "Barchaga xabar",
    "kino_uch":      "Kino o'chirish",
    "prev_qism":     "Oldingi qismlar tugmasi",
    "next_qism":     "Boshqa qismlar tugmasi",
}
BTN_LABELS["chek_yub"]     = "Chek yuborish"
BTN_LABELS["karta_nusxa"]  = "Karta nusxalash"
BTN_LABELS["miqdor_nusxa"] = "Miqdor nusxalash"
BTN_LABELS["kanal_qosh"]   = "Kanal qo'shish"
BTN_LABELS["kanal_uch"]    = "Kanal o'chirish"
BTN_LABELS["kanal_royxat"] = "Kanallar ro'yxati"
BTN_LABELS["oddiy_havola"] = "Oddiy havola qo'shish"
BTN_LABELS["foydalanuvchi_blok"] = "Foydalanuvchi bloklash"
BTN_LABELS["tolovlar"]           = "Foydalanuvchi to'lovlari"
BTN_LABELS["soruvli_kanal"] = "So'rovli kanal qo'shish"
BTN_LABELS["admin_panel"]  = "Admin panel (orqaga)"
BTN_LABELS["qism_tahrir"]  = "Qismlarni tahrirlash"
BTN_LABELS["admin_qosh"]   = "Admin qo'shish"
BTN_LABELS["qism_och"]    = "Qism ochish"
BTN_LABELS["premium_ber"]  = "Premium berish"
BTN_LABELS["start_xab"]    = "Start xabarni o'zgartirish"
BTN_LABELS["kod_btn"]      = "Kod tugmasi"
BTN_LABELS["kanal_btn"]    = "Kanal tugmasi"
LABEL_TO_KEY = {v: k for k, v in BTN_LABELS.items()}

# ══════════════════════════════════════════════════════════
# LOKAL FAYL OPERATSIYALARI
# ══════════════════════════════════════════════════════════

def _save_local(data: dict) -> bool:
    try:
        movies = data.get("movies", {})
        tmp = LOCAL_MOVIES_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(movies, f, ensure_ascii=False)
        os.replace(tmp, LOCAL_MOVIES_FILE)

        db_small = {k: v for k, v in data.items() if k != "movies"}
        db_small["movies"] = {}
        tmp = LOCAL_BACKUP_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(db_small, f, ensure_ascii=False)
        os.replace(tmp, LOCAL_BACKUP_FILE)
        return True
    except Exception as e:
        logger.error(f"Lokal backup xato: {e}")
        return False


def _load_local() -> dict | None:
    try:
        db = {}
        if os.path.exists(LOCAL_BACKUP_FILE):
            with open(LOCAL_BACKUP_FILE, "r", encoding="utf-8") as f:
                db = json.load(f)
        movies = {}
        if os.path.exists(LOCAL_MOVIES_FILE):
            with open(LOCAL_MOVIES_FILE, "r", encoding="utf-8") as f:
                movies = json.load(f)
        db["movies"] = movies
        return db if isinstance(db, dict) else {}
    except Exception as e:
        logger.error(f"Lokal yuklash xato: {e}")
        return None


# ══════════════════════════════════════════════════════════
# POSTGRESQL OPERATSIYALARI
# ══════════════════════════════════════════════════════════

def _get_pg_conn():
    """PostgreSQL ulanish hosil qiladi."""
    if not DATABASE_URL:
        return None
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        return conn
    except Exception as e:
        logger.error(f"PostgreSQL ulanish xato: {e}")
        return None


def _pg_init_table():
    """Jadval yo'q bo'lsa yaratadi."""
    conn = _get_pg_conn()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_data (
                    key TEXT PRIMARY KEY,
                    value JSONB NOT NULL,
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)
        conn.commit()
        conn.close()
        logger.info("✅ PostgreSQL jadval tayyor")
        return True
    except Exception as e:
        logger.error(f"PostgreSQL jadval yaratish xato: {e}")
        return False


def _save_postgres(data: dict, retries: int = 3) -> bool:
    """PostgreSQL ga saqlaydi."""
    if not DATABASE_URL or not PSYCOPG2_AVAILABLE:
        return False
    movies = data.get("movies") if isinstance(data, dict) else None
    if isinstance(movies, dict) and len(movies) == 0 and len(RAM.movies) > 0:
        logger.warning("🛑 Bo'sh movies bilan PostgreSQL ga yozish RAD ETILDI — himoya.")
        return False
    for attempt in range(retries):
        conn = None
        try:
            conn = _get_pg_conn()
            if not conn:
                raise Exception("Ulanish yo'q")
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO bot_data (key, value, updated_at)
                    VALUES ('main', %s, NOW())
                    ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, updated_at = NOW()
                """, (json.dumps(data, ensure_ascii=False),))
            conn.commit()
            conn.close()
            size_kb = len(json.dumps(data, ensure_ascii=False).encode("utf-8")) / 1024
            logger.info(f"✅ PostgreSQL saqlandi ({size_kb:.1f} KB)")
            return True
        except Exception as e:
            logger.error(f"PostgreSQL save #{attempt+1} xato: {e}")
            if conn:
                try: conn.close()
                except: pass
        if attempt < retries - 1:
            time.sleep(3 * (attempt + 1))
    return False


def _load_postgres() -> dict | None:
    """PostgreSQL dan yuklaydi."""
    if not DATABASE_URL or not PSYCOPG2_AVAILABLE:
        return None
    for attempt in range(6):
        conn = None
        try:
            conn = _get_pg_conn()
            if not conn:
                raise Exception("Ulanish yo'q")
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM bot_data WHERE key = 'main'")
                row = cur.fetchone()
            conn.close()
            if row:
                data = row[0]
                if isinstance(data, dict):
                    logger.info(f"✅ PostgreSQL dan yuklandi: "
                                f"{len(data.get('movies', {}))} kino, "
                                f"{len(data.get('users', {}))} user")
                    return data
                elif isinstance(data, str):
                    parsed = json.loads(data)
                    logger.info(f"✅ PostgreSQL dan yuklandi (str): "
                                f"{len(parsed.get('movies', {}))} kino")
                    return parsed
            else:
                logger.warning("PostgreSQL da ma'lumot yo'q (bo'sh jadval)")
                return {}
        except Exception as e:
            logger.error(f"PostgreSQL load #{attempt+1} xato: {e}")
            if conn:
                try: conn.close()
                except: pass
        if attempt < 5:
            time.sleep(2 * (attempt + 1))
    logger.error("❌ PostgreSQL dan yuklab bo'lmadi (6 urinish).")
    return None


# JSONBlob funksiyalar — PostgreSQL ga yo'naltiriladi (orqaga moslik)
def _save_jsonblob(data: dict, retries: int = 3) -> bool:
    return _save_postgres(data, retries)


def _load_jsonblob() -> dict | None:
    return _load_postgres()


# ══════════════════════════════════════════════════════════
# DB YUKLASH — ishga tushganda bir marta chaqiriladi
# ══════════════════════════════════════════════════════════

def _merge_db(blob: dict | None, local: dict | None) -> dict:
    """
    JSONBlob va lokal fayldagi ma'lumotlarni BIRLASHTIRADI.
    Hech qanday kino yo'qolmasligi uchun:
      • Har bir kino bo'yicha — qaysi manbada ko'proq qism bo'lsa, o'sha tanlanadi
      • Faqat bir manbada bor kinolar — o'shanday qo'shiladi
      • Foydalanuvchilar — birlashadi (lokal ustun, chunki yangiroq)
      • Sozlamalar — lokal ustun
    """
    blob  = blob  if isinstance(blob,  dict) else {}
    local = local if isinstance(local, dict) else {}

    blob_movies  = (blob.get("movies")  or {}) if isinstance(blob.get("movies"),  dict) else {}
    local_movies = (local.get("movies") or {}) if isinstance(local.get("movies"), dict) else {}

    merged_movies: dict = {}
    all_codes = set(blob_movies.keys()) | set(local_movies.keys())
    for code in all_codes:
        b = blob_movies.get(code)
        l = local_movies.get(code)
        if b and not l:
            merged_movies[code] = b
        elif l and not b:
            merged_movies[code] = l
        elif b and l:
            # Ikkalasida ham bor — ko'proq qism bor versiyani olamiz
            b_eps = len((b or {}).get("episodes", []) or [])
            l_eps = len((l or {}).get("episodes", []) or [])
            if l_eps >= b_eps:
                base = dict(l)
                # narxlarni ham birlashtir
                prices = dict((b or {}).get("prices", {}) or {})
                prices.update((l or {}).get("prices", {}) or {})
                base["prices"] = prices
                merged_movies[code] = base
            else:
                base = dict(b)
                prices = dict((l or {}).get("prices", {}) or {})
                prices.update((b or {}).get("prices", {}) or {})
                base["prices"] = prices
                merged_movies[code] = base

    # Foydalanuvchilar — birlashadi, balans yo'qolmasin
    merged_users = {}
    blob_users  = (blob.get("users")  or {}) if isinstance(blob.get("users"),  dict) else {}
    local_users = (local.get("users") or {}) if isinstance(local.get("users"), dict) else {}
    all_uids = set(blob_users.keys()) | set(local_users.keys())
    for uid_key in all_uids:
        bu = blob_users.get(uid_key) or {}
        lu = local_users.get(uid_key) or {}
        if not bu:
            merged_users[uid_key] = lu
        elif not lu:
            merged_users[uid_key] = bu
        else:
            # Ikkalasida bor — lokal asosiy, lekin balans/topup_total ni max olamiz
            merged = dict(lu)
            # Balans: ikkalasidan kattasini ol (yo'qolmasin)
            b_bal  = int(bu.get("balance") or 0)
            l_bal  = int(lu.get("balance") or 0)
            merged["balance"] = max(b_bal, l_bal)
            b_top  = int(bu.get("topup_total") or 0)
            l_top  = int(lu.get("topup_total") or 0)
            merged["topup_total"] = max(b_top, l_top)
            # paid_episodes — birlashtir (ikkalasidagi ham bo'lsin)
            paid_b = bu.get("paid_episodes") or {}
            paid_l = lu.get("paid_episodes") or {}
            merged_paid = dict(paid_b)
            merged_paid.update(paid_l)
            merged["paid_episodes"] = merged_paid
            # premium_until — yangiroqni ol
            p_b = float(bu.get("premium_until") or 0)
            p_l = float(lu.get("premium_until") or 0)
            merged["premium_until"] = max(p_b, p_l)
            merged_users[uid_key] = merged

    # Boshqa maydonlar — lokal ustun, bo'lmasa blob
    def pick(key, default):
        if key in local and local.get(key):
            return local.get(key)
        if key in blob and blob.get(key):
            return blob.get(key)
        return default

    return {
        "movies":           merged_movies,
        "users":            merged_users,
        "channels":         pick("channels", []),
        "simple_links":     pick("simple_links", []),
        "card_number":      pick("card_number", ""),
        "pending_payments": pick("pending_payments", {}),
        "settings":         pick("settings", {
            "install_file_id": None, "install_video_id": None, "kino_kanal_url": "",
        }),
        "stats":            pick("stats", {"total_views": 0}),
        "btn_texts":        pick("btn_texts", {}),
        "emoji_ids":        pick("emoji_ids", {}),
        "sub_admins":       pick("sub_admins", {}),
        "blocked_users":    pick("blocked_users", {}),
    }


def db_initial_load():
    """
    Ishga tushganda:
    1. PostgreSQL jadvalni tekshiradi/yaratadi
    2. PostgreSQL VA lokal fayldan ikkalasini ham yuklaymiz
    3. Ularni BIRLASHTIRAMIZ (hech qanday kino yo'qolmaydi)
    4. RAMga yozamiz
    5. Ikkala manbaga ham birlashtirilgan natijani sync qilamiz

    ❗ MUHIM: Agar PostgreSQL yuklab bo'lmasa VA lokal ham bo'sh bo'lsa —
       bot bo'sh RAM bilan ishga tushadi.
    """
    logger.info("🔄 Ma'lumotlar yuklanmoqda (PostgreSQL + lokal birlashtirish)...")

    # PostgreSQL jadvalni yaratamiz (yo'q bo'lsa)
    if DATABASE_URL and PSYCOPG2_AVAILABLE:
        _pg_init_table()

    blob  = _load_postgres()
    local = _load_local()

    has_blob  = bool(blob  and (blob.get("movies")  or blob.get("users")))
    has_local = bool(local and (local.get("movies") or local.get("users")))

    # ❗ XAVFSIZLIK: agar JSONBlob URL berilgan bo'lsa-yu, undan
    # yuklab bo'lmagan bo'lsa — lokal fayldan ishlashda davom etamiz.
    # load_failed ni BOTNI TO'XTATISH uchun emas, faqat log uchun ishlatamiz.
    # Kinolar qo'shilganda save_now() chaqiriladi va JSONBlob ga yozishga urinadi.
    if DATABASE_URL and blob is None:
        logger.error("🛑 PostgreSQL dan yuklab bo'lmadi! "
                     "Lokal fayldan ishlashda davom etamiz.")
        DB_STATUS["storage_ok"] = False
        DB_STATUS["ram_only"]   = True
        DB_STATUS["last_err"]   = "JSONBlob yuklanmadi — lokal fayldan yuklanmoqda"
        # load_failed = False qilamiz — saqlash bloklanmasin!
        DB_STATUS["load_failed"] = False
        if has_local:
            RAM.from_dict(local)
            EMOJI_IDS.clear()
            EMOJI_IDS.update(RAM.emoji_ids)
            logger.warning(f"⚠️ Faqat lokal yuklandi: {len(RAM.movies)} kino. "
                           "JSONBlob ga keyinroq uriniladi...")
        else:
            RAM.loaded = True
            logger.warning("⚠️ Hech narsa yuklanmadi. Bo'sh RAM bilan ishga tushdi.")
        return

    if not has_blob and not has_local:
        logger.warning("⚠️ Ma'lumot topilmadi — bo'sh RAM boshlanadi")
        RAM.loaded = True
        return

    merged = _merge_db(blob, local)
    RAM.from_dict(merged)
    EMOJI_IDS.clear()
    EMOJI_IDS.update(RAM.emoji_ids)

    blob_eps  = sum(len(m.get("episodes", []) or []) for m in (blob.get("movies")  or {}).values()) if has_blob  else 0
    local_eps = sum(len(m.get("episodes", []) or []) for m in (local.get("movies") or {}).values()) if has_local else 0
    merged_eps = sum(len(m.get("episodes", []) or []) for m in RAM.movies.values())
    logger.info(f"✅ Birlashtirildi → RAM: {len(RAM.movies)} kino, {merged_eps} qism, {len(RAM.users)} user "
                f"(blob: {len(blob.get('movies',{}) if has_blob else {})}/{blob_eps}, "
                f"lokal: {len(local.get('movies',{}) if has_local else {})}/{local_eps})")

    # Ikkala manbaga ham birlashtirilgan natijani yozib qo'yamiz —
    # endi keyingi safar ham hech narsa yo'qolmaydi.
    _save_local(merged)
    if DATABASE_URL:
        threading.Thread(target=_save_postgres, args=(copy.deepcopy(merged),), daemon=True).start()
        logger.info("⏳ Birlashtirilgan ma'lumot PostgreSQL ga sync qilinmoqda (fon)...")


# ══════════════════════════════════════════════════════════
# SAQLASH — RAM → lokal + JSONBlob (debounced background)
# ══════════════════════════════════════════════════════════
#
# Strategiya:
#   • RAM      — darhol yoziladi (millisaniyada)
#   • Lokal    — har o'zgarishda darhol yoziladi (tez, ishonchli)
#   • JSONBlob — DEBOUNCE bilan fon rejimida (oxirgi o'zgarishdan
#                JSONBLOB_DEBOUNCE soniya keyin bir marta yoziladi).
#   Misol: admin ketma-ket 10 ta qism yuborsa — JSONBlob ga
#          BIR MARTA, hammasi tugagandan keyin yoziladi.
# ──────────────────────────────────────────────────────────

JSONBLOB_DEBOUNCE = 12.0   # soniya — qism yuborish tugagandan keyin

_jsonblob_timer_task = None     # asyncio.Task — kutilayotgan saqlash
_jsonblob_save_lock = None      # asyncio.Lock — _setup da yaratiladi


def _ensure_lock():
    global _jsonblob_save_lock
    if _jsonblob_save_lock is None:
        _jsonblob_save_lock = asyncio.Lock()
    return _jsonblob_save_lock


async def _do_jsonblob_save() -> bool:
    """Haqiqiy JSONBlob ga yozish (status ham yangilanadi)."""
    async with _ensure_lock():
        data = RAM.to_dict()
        ok = await asyncio.to_thread(_save_jsonblob, data)
        now_str = datetime.now().strftime("%H:%M:%S")
        if ok:
            DB_STATUS.update({
                "storage_ok": True,
                "fail_count": 0,
                "last_save_ok": now_str,
                "ram_only": False,
                "pending_save": False,
            })
        else:
            DB_STATUS["fail_count"] = DB_STATUS.get("fail_count", 0) + 1
            DB_STATUS["last_err"] = now_str
            if DB_STATUS["fail_count"] >= 2:
                DB_STATUS.update({"storage_ok": False, "ram_only": True})
        return ok


async def _delayed_jsonblob_save(delay: float):
    """Belgilangan vaqtdan keyin JSONBlob ga yozish."""
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return  # yangi o'zgarish keldi — bu task bekor qilindi
    try:
        await _do_jsonblob_save()
    except Exception as e:
        logger.error(f"JSONBlob debounced save xato: {e}")


async def schedule_save(delay: float = JSONBLOB_DEBOUNCE):
    """
    RAMni saqlash navbatiga qo'yadi.
      • Lokal faylga DARHOL yoziladi (tez, ishonchli)
      • JSONBlob ga `delay` soniyadan keyin yoziladi
      • Agar `delay` ichida yana o'zgarish bo'lsa — taymer qaytadan
        boshlanadi (oxirgi o'zgarishdan keyin bir marta saqlash)
    """
    global _jsonblob_timer_task
    DB_STATUS["pending_save"] = True
    # Lokal faylga darhol yoz
    try:
        _save_local(RAM.to_dict())
    except Exception as e:
        logger.error(f"Lokal saqlash xato: {e}")

    # Eski kutilayotgan taymer bo'lsa — bekor qil va qaytadan boshla
    if _jsonblob_timer_task and not _jsonblob_timer_task.done():
        _jsonblob_timer_task.cancel()
    _jsonblob_timer_task = asyncio.create_task(_delayed_jsonblob_save(delay))


def save_sync():
    """Sinxron (thread) — lokal + JSONBlob fon rejimida."""
    data = RAM.to_dict()
    _save_local(data)
    threading.Thread(target=_save_jsonblob, args=(copy.deepcopy(data),), daemon=True).start()


async def save_now() -> bool:
    """
    DARHOL saqlash — kutilayotgan debounce taymerni bekor qiladi va
    JSONBlob ga shu lahzada yozadi. Muhim operatsiyalar uchun.
    """
    global _jsonblob_timer_task
    if _jsonblob_timer_task and not _jsonblob_timer_task.done():
        _jsonblob_timer_task.cancel()
    try:
        _save_local(RAM.to_dict())
    except Exception as e:
        logger.error(f"Lokal saqlash xato: {e}")
    return await _do_jsonblob_save()


async def save_ram_only():
    """
    Faqat lokal faylga yozadi — JSONBlob ga TEGMAYDI.
    Qism (video) qo'shganda ishlatiladi: ko'p video ketma-ket
    yuborilsa, har biri uchun JSONBlob ga yozish shart emas.
    """
    try:
        _save_local(RAM.to_dict())
    except Exception as e:
        logger.error(f"save_ram_only lokal xato: {e}")
    DB_STATUS["pending_save"] = True


# ══════════════════════════════════════════════════════════
# YORDAMCHI FUNKSIYALAR
# ══════════════════════════════════════════════════════════

def bt(key: str) -> str:
    raw = RAM.btn_texts.get(key) or DEFAULT_BTN.get(key, "")
    return _B(raw)


def get_eid(key: str):
    return EMOJI_IDS.get(key)


def _norm_search_text(value: str) -> str:
    value = (value or "").upper().strip()
    value = re.sub(r"[^A-Z0-9А-ЯЁЎҚҒҲІЇЄÑÇŞĞÖÜ' ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def find_movie_code(query: str):
    """
    RAMdan kino qidiradi — millisaniyada ishlaydi.
    Qaytaradi: (code, []) yoki (None, [matches])

    Qo'shilgan tuzatishlar:
      • Case-insensitive kod qidirish (RAM kalitlari .upper() bilan saqlanadi)
      • Raqamli kodlar uchun leading-zero (masalan, "1" → "01" / "001" ham topiladi)
      • Yana bo'shliq/belgilarni tozalab solishtirish
    """
    raw = (query or "").strip()
    if not raw:
        return None, []

    movies = RAM.movies  # to'g'ridan-to'g'ri RAM dan
    if not movies:
        return None, []

    # 1. To'liq kod bo'yicha (case-insensitive)
    code_upper = raw.upper().strip()
    if code_upper in movies:
        return code_upper, []

    # 1b. Bo'shliq/belgi tozalangan kod bo'yicha
    code_clean = re.sub(r"\s+", "", code_upper)
    if code_clean and code_clean in movies:
        return code_clean, []

    # 1c. Raqamli kod — leading zero variantlari
    if code_clean.isdigit():
        digit_matches = []
        try:
            num_val = int(code_clean)
        except Exception:
            num_val = None
        for c in movies.keys():
            if isinstance(c, str) and c.isdigit():
                try:
                    if int(c) == num_val:
                        digit_matches.append(c)
                except Exception:
                    pass
        if len(digit_matches) == 1:
            return digit_matches[0], []
        if len(digit_matches) > 1:
            return None, digit_matches[:10]

    # 2. Matn qidirish
    q = _norm_search_text(raw)
    if not q:
        return None, []

    exact, partial = [], []
    for c, movie in movies.items():
        title = movie.get("title", c) if isinstance(movie, dict) else c
        title_norm = _norm_search_text(title)
        code_norm  = _norm_search_text(c)
        if q == title_norm or q == code_norm:
            exact.append(c)
        elif q in title_norm or title_norm in q or q in code_norm or code_norm in q:
            partial.append(c)

    matches = exact or partial
    if len(matches) == 1:
        return matches[0], []
    return None, matches[:10]


def movie_suggestions_text(codes: list) -> str:
    lines = []
    for c in codes:
        movie = RAM.movies.get(c, {})
        lines.append(f"• <b>{movie.get('title', c)}</b> — kod: <code>{c}</code>")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
# FOYDALANUVCHI RO'YXATGA OLISH
# ══════════════════════════════════════════════════════════

def _gsheet_append_row(row_data: list) -> bool:
    if not GSHEET_ID:
        return False
    try:
        url = (f"https://sheets.googleapis.com/v4/spreadsheets/"
               f"{GSHEET_ID}/values/Users!A:Z:append"
               f"?valueInputOption=RAW&insertDataOption=INSERT_ROWS")
        if GSHEET_API:
            url += f"&key={GSHEET_API}"
        body = {"values": [row_data]}
        r = requests.post(url, headers={"Content-Type": "application/json"},
                          data=json.dumps(body), timeout=10)
        return r.status_code in (200, 201)
    except Exception as e:
        logger.warning(f"GSheet xato: {e}")
    return False


def _gsheet_log_user(user_id: int, name: str, username: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [str(user_id), name, f"@{username}" if username else "", now]
    threading.Thread(target=_gsheet_append_row, args=(row,), daemon=True).start()


def register_user(user):
    uid_str = str(user.id)
    if uid_str not in RAM.users:
        RAM.users[uid_str] = {
            "name": user.full_name,
            "username": user.username or "",
            "joined": datetime.now().isoformat(),
            "paid_episodes": {},
            "watched": {},
            "balance": 0,
            "topup_total": 0,
            "premium_until": 0,
        }
        _gsheet_log_user(user.id, user.full_name, user.username or "")
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(schedule_save())
        except RuntimeError:
            save_sync()


# ══════════════════════════════════════════════════════════
# SUB CACHE
# ══════════════════════════════════════════════════════════

_sub_cache: dict[int, tuple[float, list]] = {}
SUB_CACHE_TTL = 10

def _sub_cache_get(user_id):
    e = _sub_cache.get(user_id)
    if e and (time.time() - e[0]) < SUB_CACHE_TTL:
        return e[1]
    return None

def _sub_cache_set(user_id, result):
    _sub_cache[user_id] = (time.time(), result)

def _sub_cache_invalidate(user_id):
    _sub_cache.pop(user_id, None)


# ══════════════════════════════════════════════════════════
# EMOJI YORDAMCHI
# ══════════════════════════════════════════════════════════

EMOJI_RE = re.compile(
    r'[\U0001F000-\U0001FFFF\U00002600-\U000027BF'
    r'\U0000FE00-\U0000FE0F\U00020000-\U0002FA1F'
    r'\u200d\ufe0f]+'
)

def is_only_emoji(text: str) -> bool:
    cleaned = EMOJI_RE.sub('', text).strip()
    return len(cleaned) == 0 and len(text.strip()) > 0

def extract_emoji_prefix(text: str) -> str:
    match = re.match(
        r'^((?:[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE00-\uFE0F\u200d\ufe0f]+\s*)+)',
        text
    )
    return match.group(1).rstrip() if match else ""

def strip_emoji_prefix(text: str) -> str:
    return re.sub(
        r'^(?:[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE00-\uFE0F\u200d\ufe0f]+\s*)+',
        '', text
    ).strip()

def extract_custom_emoji_id(message) -> str | None:
    if not message or not message.entities:
        return None
    for entity in message.entities:
        if entity.type == "custom_emoji":
            return entity.custom_emoji_id
    return None

def text_with_premium_emojis(message) -> str:
    text = message.text or message.caption or ""
    if not text:
        return ""
    entities = list(message.entities or message.caption_entities or [])
    custom = [e for e in entities if e.type == "custom_emoji"]
    def esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if not custom:
        return esc(text)
    units = text.encode("utf-16-le")
    def slice_units(start, length):
        return units[start*2:(start+length)*2].decode("utf-16-le", errors="replace")
    spans = sorted(custom, key=lambda e: e.offset)
    out = []
    cursor = 0
    total = len(units) // 2
    for e in spans:
        if e.offset > cursor:
            out.append(esc(slice_units(cursor, e.offset - cursor)))
        emoji_text = slice_units(e.offset, e.length)
        out.append(f'<tg-emoji emoji-id="{e.custom_emoji_id}">{esc(emoji_text)}</tg-emoji>')
        cursor = e.offset + e.length
    if cursor < total:
        out.append(esc(slice_units(cursor, total - cursor)))
    return "".join(out)

def find_key_by_text(text: str) -> str | None:
    if not text:
        return None
    if text in LABEL_TO_KEY:
        return LABEL_TO_KEY[text]
    for key in BTN_LABELS:
        current = bt(key)
        if current and current == text:
            return key
        cur_stripped = strip_emoji_prefix(current) if current else ""
        txt_stripped = strip_emoji_prefix(text)
        if cur_stripped and txt_stripped and cur_stripped == txt_stripped:
            return key
    return None


# ══════════════════════════════════════════════════════════
# NAVIGATSIYA TEKSHIRISH
# ══════════════════════════════════════════════════════════

def _is_admin_nav_button(text: str) -> bool:
    for k in ["asosiy", "boshqarish", "orqaga", "admin_panel"]:
        v = bt(k)
        if v and (text == v or strip_emoji_prefix(text) == strip_emoji_prefix(v)):
            return True
    return False

def _get_admin_nav_key(text: str) -> str | None:
    for k in ["asosiy", "boshqarish", "orqaga", "admin_panel"]:
        v = bt(k)
        if v and (text == v or strip_emoji_prefix(text) == strip_emoji_prefix(v)):
            # orqaga / admin_panel ham admin panelga qaytaradi
            return "boshqarish" if k in ("orqaga", "admin_panel") else k
    return None


# ══════════════════════════════════════════════════════════
# INLINE KEYBOARD YORDAMCHI
# ══════════════════════════════════════════════════════════

def ibtn(text, data=None, url=None, style=None, emoji_id=None):
    b = {"text": text}
    if data:     b["callback_data"] = data
    if url:      b["url"] = url
    if style:    b["style"] = style
    if emoji_id: b["icon_custom_emoji_id"] = emoji_id
    return b

def rbtn(text, style=None, emoji_id=None):
    b = {"text": text}
    if style:    b["style"] = style
    if emoji_id: b["icon_custom_emoji_id"] = emoji_id
    return b

def ikb(rows):
    return {"inline_keyboard": rows}

def rkb(rows, resize=True):
    return {"keyboard": rows, "resize_keyboard": resize}


# ══════════════════════════════════════════════════════════
# KLAVIATURALAR
# ══════════════════════════════════════════════════════════

def main_menu_kb(is_admin=False):
    rows = [[
        rbtn(bt("yordam"),  style="danger", emoji_id=get_eid("yordam")),
        rbtn(bt("install"), style="success", emoji_id=get_eid("install")),
    ], [
        rbtn(bt("barcha_kino"), style="primary", emoji_id=get_eid("barcha_kino")),
        rbtn(bt("balans"),      style="success", emoji_id=get_eid("balans")),
    ]]
    if is_admin:
        rows.append([rbtn(bt("boshqarish"), style="danger", emoji_id=get_eid("boshqarish"))])
    return rkb(rows)


def admin_menu_kb(uid=None):
    pairs = [
        ("kino_joy",       "success"),
        ("qism_qosh",      "primary"),
        ("pullik",         "danger"),
        ("stat",           "primary"),
        ("kanal_post",     "primary"),
        ("maj_kanal",      "danger"),
        ("karta",          "success"),
        ("ilova",          "primary"),
        ("kino_kanal_set", "success"),
        ("emoji_soz",      "primary"),
        ("qism_tahrir",    "primary"),
        ("kino_uch",       "danger"),
        ("broadcast",      "danger"),
        ("premium_ber",    "success"),
        ("start_xab",      "primary"),
        ("qism_och",       "success"),
        ("foydalanuvchi_blok", "danger"),
    ]
    if uid is not None and not is_super_admin(uid):
        pairs = [(k, st) for (k, st) in pairs if has_perm(uid, k)]
    rows, buf = [], []
    for k, st in pairs:
        buf.append(rbtn(bt(k), style=st, emoji_id=get_eid(k)))
        if len(buf) == 2:
            rows.append(buf); buf = []
    if buf: rows.append(buf)
    if uid is None or is_super_admin(uid):
        rows.append([rbtn(bt("admin_qosh"), style="success", emoji_id=get_eid("admin_qosh"))])
        rows.append([rbtn(bt("admin_lichka_set"), style="primary")])
    rows.append([rbtn(bt("asosiy"), style="success", emoji_id=get_eid("asosiy"))])
    return rkb(rows)


def channel_manage_kb():
    return rkb([
        [rbtn(bt("kanal_qosh"),    style="success", emoji_id=get_eid("kanal_qosh")),
         rbtn(bt("kanal_uch"),     style="danger",  emoji_id=get_eid("kanal_uch"))],
        [rbtn(bt("soruvli_kanal"), style="primary", emoji_id=get_eid("soruvli_kanal"))],
        [rbtn(bt("oddiy_havola"),  style="primary", emoji_id=get_eid("oddiy_havola"))],
        [rbtn(bt("kanal_royxat"),  style="primary", emoji_id=get_eid("kanal_royxat"))],
        [rbtn(bt("admin_panel"),   style="success", emoji_id=get_eid("admin_panel"))],
    ])


def channel_delete_inline_kb(channels: list, simple_links: list = None):
    rows = []
    for i, ch in enumerate(channels):
        title = ch.get('title') or ch.get('username') or '?'
        uname = ch.get('username') or ''
        rows.append([ibtn(
            f"❌ {title} ({uname})",
            data=f"ch_del|{i}", style="danger"
        )])
    for i, sl in enumerate(simple_links or []):
        rows.append([ibtn(
            f"❌ 🔗 {sl.get('title','?')}",
            data=f"sl_del|{i}", style="danger"
        )])
    rows.append([ibtn("🔙 Bekor", data="ch_del_cancel", style="primary")])
    return ikb(rows)


def subscription_kb(channels: list, simple_links: list = None):
    rows = []
    for c in channels:
        if c.get("join_request"):
            # So'rovli kanal — "So'rov yuborish" tugmasi
            rows.append([ibtn(
                f"📨 {c['title']} — So'rov yuborish",
                url=c["url"], style="primary"
            )])
        else:
            rows.append([ibtn(c["title"], url=c["url"], style="primary")])
    for sl in (simple_links or []):
        rows.append([ibtn(sl["title"], url=sl["url"], style="primary")])
    rows.append([ibtn(bt("tekshir"), data="check_sub", style="success", emoji_id=get_eid("tekshir"))])
    return ikb(rows)


PAGE_SIZE = 5

def movie_episodes_kb(movie: dict, code: str, user_id, page: int = 0):
    eps    = movie.get("episodes", [])
    prices = movie.get("prices", {}) or {}
    RAM.ensure_user(user_id)
    code   = str(code).upper()
    total  = len(eps)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    end   = min(start + PAGE_SIZE, total)
    rows  = []
    ep_labels = movie.get("ep_labels", {}) or {}

    # Barcha pullik qismlarning jami narxi (foydalanuvchi uchun ochilmagan)
    locked_eps = []
    total_locked_price = 0
    for i in range(total):
        ek = str(i + 1)
        price_int = price_to_int(prices.get(ek))
        if price_int > 0 and not is_episode_paid(user_id, code, ek):
            locked_eps.append(ek)
            total_locked_price += price_int

    for i in range(start, end):
        ek = str(i + 1)
        price_int = price_to_int(prices.get(ek))
        already_paid = is_episode_paid(user_id, code, ek)
        locked = (price_int > 0) and (not already_paid)
        custom_label = ep_labels.get(ek)
        base_label = _B(custom_label) if custom_label else _B(str(ek)+'-qism')
        if locked:
            rows.append([ibtn(f"🔒 {base_label}  💰 {_B(str(price_int)+' som')}",
                              data=f"ep|{code}|{ek}", style="danger")])
        elif price_int > 0 and already_paid:
            # Muddatini ko'rsatamiz
            secs = episode_expires_in(user_id, code, ek)
            if secs > 0:
                hrs = secs // 3600
                mins = (secs % 3600) // 60
                if hrs >= 24:
                    expire_txt = f"  ⏳{hrs//24}k {hrs%24}s"
                else:
                    expire_txt = f"  ⏳{hrs}s {mins}d"
                rows.append([ibtn(f"✅ {base_label}{expire_txt}",
                                  data=f"ep|{code}|{ek}", style="success")])
            else:
                rows.append([ibtn(f"✅ {base_label}",
                                  data=f"ep|{code}|{ek}", style="success")])
        else:
            rows.append([ibtn(f"🎬 {base_label}",
                              data=f"ep|{code}|{ek}", style="success")])

    nav = []
    if page > 0:
        nav.append(ibtn(bt("prev_qism"), data=f"page|{code}|{page-1}",
                        style="primary", emoji_id=get_eid("prev_qism")))
    if page < total_pages - 1:
        nav.append(ibtn(bt("next_qism"), data=f"page|{code}|{page+1}",
                        style="primary", emoji_id=get_eid("next_qism")))
    if nav:
        rows.append(nav)

    # "Barchasini sotib olish" tugmasi — faqat ochilmagan pullik qismlar bo'lsa
    if locked_eps and total_locked_price > 0:
        rows.append([ibtn(
            _B(f"🛒 Barchasini sotib olish  💰 {total_locked_price} som"),
            data=f"buy_all|{code}",
            style="danger"
        )])

    kanal_url = RAM.settings.get("kino_kanal_url", "")
    if kanal_url:
        rows.append([ibtn(bt("kino_kanal"), url=kanal_url, style="primary",
                          emoji_id=get_eid("kino_kanal"))])
    return ikb(rows)


def payment_admin_kb(pid: str):
    return ikb([[
        ibtn(bt("tasdiq"), data=f"pay_ok|{pid}", style="success", emoji_id=get_eid("tasdiq")),
        ibtn(bt("bekor"),  data=f"pay_no|{pid}", style="danger",  emoji_id=get_eid("bekor")),
    ]])

def share_kb(url: str):
    return ikb([[ibtn(bt("ulash"), url=url, style="primary", emoji_id=get_eid("ulash"))]])

def channel_post_kb(bot_username: str, code: str):
    return ikb([[ibtn(bt("tomosha"),
        url=f"https://t.me/{bot_username}?start=code_{code}",
        style="success", emoji_id=get_eid("tomosha"))]])

# ─── AUTO-POST KANALGA (har qism qo'shilganda) ─────────────
def get_auto_post_channel():
    """kino_kanal_url dan kanal username ni olish (@channel)."""
    url = (RAM.settings.get("kino_kanal_url") or "").strip()
    if not url:
        return None
    if url.startswith("@"):
        return url
    m = re.search(r"t\.me/([A-Za-z0-9_]{4,32})(?:[/?]|$)", url)
    if m:
        return "@" + m.group(1)
    return None

def _pe(key: str, fallback: str) -> str:
    """Premium (custom) emoji yoki oddiy emoji qaytaradi."""
    eid = EMOJI_IDS.get(key)
    if eid:
        return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'
    return fallback

def build_auto_post_caption(movie: dict, code: str, ep_count: int, finished: bool = False, bot_username: str = "") -> str:
    title  = movie.get("title", code)
    janr   = movie.get("janr") or movie.get("genre") or "Drama"
    tili   = movie.get("tili") or movie.get("language") or "O'zbek tili"
    qism_str = f"{ep_count}/{ep_count}" if finished else f"{ep_count} ta"
    bot_line = f"@{bot_username}" if bot_username else ""
    watch_url = f"https://t.me/{bot_username}?start=code_{code}" if bot_username else ""
    watch = f'<a href="{watch_url}">Tomosha qilish</a>' if watch_url else "Tomosha qilish"
    return (
        f'{_pe("post_nomi","📁")} <b>Nomi : {title}</b>\n\n'
        f'{_pe("post_qism","📩")} <b>Qism : {qism_str}</b>\n\n'
        f'{_pe("post_kod","🦹")} <b>Kod : {code}</b>\n\n'
        f'{_pe("post_janr","🎬")} <b>Janr : {janr}</b>\n\n'
        f'{_pe("post_tili","🔮")} <b>Tili : {tili}</b>\n\n'
        f'{_pe("post_bot","🤖")} <b>Bot : {bot_line}</b>\n\n'
        f'{_pe("post_korish","👁")} <b>Ko\'rish : {watch}</b>'
    )

async def auto_post_episode_added(bot, code: str, finished: bool = False):
    """
    Kanalga post yuborish/tahrirlash.
    - Agar avval post yuborilgan bo'lsa (msg_id bor) — DOIM tahrirlaydi (yangi post yuborMASLIK uchun).
    - Agar msg_id yo'q bo'lsa — faqat finished=True (Tugatish) paytida yangi post yuboradi.
      Qism qo'shilganda (finished=False) va msg_id yo'q bo'lsa — hech narsa qilmaydi.
    """
    try:
        chat = get_auto_post_channel()
        if not chat:
            return
        movie = RAM.movies.get(code)
        if not movie:
            return
        ep_count = len(movie.get("episodes", []))
        if ep_count == 0:
            return
        bot_me = await bot.get_me()
        markup = channel_post_kb(bot_me.username, code)
        caption = build_auto_post_caption(movie, code, ep_count, finished=finished, bot_username=bot_me.username)
        msg_id  = movie.get("auto_post_msg_id")
        chat_id = movie.get("auto_post_chat_id") or chat
        poster  = movie.get("poster_file_id")

        if msg_id:
            # Mavjud postni tahrirlash — hech qachon yangi post yubormaymiz
            try:
                if poster:
                    await bot.edit_message_caption(chat_id=chat_id, message_id=msg_id,
                                                   caption=caption, parse_mode="HTML",
                                                   reply_markup=markup)
                else:
                    await bot.edit_message_text(chat_id=chat_id, message_id=msg_id,
                                                text=caption, parse_mode="HTML",
                                                reply_markup=markup)
                logger.info(f"✅ auto_post tahrirlandi ({code}), qism: {ep_count}, finished: {finished}")
                return
            except Exception as e:
                logger.warning(f"auto_post edit xato ({code}): {e}")
                # Tahrirlash muvaffaqiyatsiz — yangi post yubormaymiz, faqat loglaydi
                return

        # msg_id yo'q — faqat "Tugatish" (finished=True) paytida yangi post yuboramiz
        if not finished:
            logger.info(f"ℹ️ auto_post: {code} uchun msg_id yo'q, finished=False — post yuborilmadi")
            return

        # Yangi post — faqat bir marta (finished=True)
        if poster:
            sent = await bot.send_photo(chat_id=chat, photo=poster,
                                        caption=caption, parse_mode="HTML",
                                        reply_markup=markup)
        else:
            sent = await bot.send_message(chat_id=chat, text=caption,
                                          parse_mode="HTML", reply_markup=markup)
        movie["auto_post_msg_id"]  = sent.message_id
        movie["auto_post_chat_id"] = sent.chat.id
        await save_ram_only()
        logger.info(f"✅ auto_post yangi post yuborildi ({code}), qism: {ep_count}")
    except Exception as e:
        logger.error(f"auto_post_episode_added xato ({code}): {e}")

def reply_admin_kb(uid):
    return ikb([[ibtn(bt("javob"), data=f"reply|{uid}", style="primary", emoji_id=get_eid("javob"))]])

def stats_kb():
    return ikb([[ibtn(bt("yangi"), data="refresh_stats", style="primary", emoji_id=get_eid("yangi"))]])

def movie_added_kb(code: str):
    return ikb([
        [
            ibtn(bt("qism_add"), data=f"quick_add_ep|{code}", style="success", emoji_id=get_eid("qism_add")),
            ibtn(bt("narx_bel"), data=f"quick_price|{code}",  style="primary", emoji_id=get_eid("narx_bel")),
        ],
        [
            ibtn(_B("Tugatish va bazaga saqlash"), data=f"finish_movie|{code}", style="success"),
        ],
    ])

def payment_sent_kb(card: str = "", price: int = 0):
    rows = [[ibtn(bt("chek_yub"), data="send_check", style="primary", emoji_id=get_eid("chek_yub"))]]
    copy_row = []
    if card:
        copy_row.append({
            "text": bt("karta_nusxa"),
            "copy_text": {"text": str(card)},
        })
    if price:
        copy_row.append({
            "text": bt("miqdor_nusxa"),
            "copy_text": {"text": str(price)},
        })
    if copy_row:
        rows.append(copy_row)
    return ikb(rows)


def balans_kb():
    """Foydalanuvchi balans sahifasi inline klaviaturasi."""
    return ikb([[
        ibtn(bt("hisob_toldirish"), data="topup_start", style="success"),
    ]])


def topup_sent_kb(card: str = "", price: int = 0):
    """Hisobni to'ldirish — karta va miqdor nusxalash + chek yuborish."""
    rows = [[ibtn(_B("📤 Chek yuborish"), data="topup_send_check", style="primary")]]
    copy_row = []
    if card:
        copy_row.append({"text": bt("karta_nusxa"), "copy_text": {"text": str(card)}})
    if price:
        copy_row.append({"text": bt("miqdor_nusxa"), "copy_text": {"text": str(price)}})
    if copy_row:
        rows.append(copy_row)
    return ikb(rows)


def topup_admin_kb(pid: str, user_id, username: str = ""):
    """Admin uchun hisobni to'ldirish tasdiqlash klaviaturasi."""
    rows = [
        [
            ibtn("✅ Tasdiqlash", data=f"topup_ok|{pid}", style="success"),
            ibtn("❌ Bekor qilish", data=f"topup_no|{pid}", style="danger"),
        ],
    ]
    if username:
        lichka_url = f"https://t.me/{username.lstrip('@')}"
    else:
        lichka_url = f"tg://user?id={user_id}"
    rows.append([ibtn("👤 Foydalanuvchi lichkasi", url=lichka_url, style="primary")])
    return ikb(rows)

def help_kb():
    return ikb([[ibtn(bt("bosh"), data="go_home", style="success", emoji_id=get_eid("bosh"))]])

def emoji_menu_kb():
    rows = []
    keys = list(BTN_LABELS.keys())
    for i in range(0, len(keys), 2):
        row = []
        for key in keys[i:i+2]:
            eid   = get_eid(key)
            label = BTN_LABELS.get(key, key)
            row.append(rbtn(label, style="primary", emoji_id=eid))
        rows.append(row)
    rows.append([rbtn(bt("tiklash"), style="danger")])
    rows.append([rbtn(bt("orqaga"),  style="success")])
    return rkb(rows)

def emoji_single_action_kb(key: str):
    return ikb([
        [ibtn(bt("default_q"), data=f"emoji_reset|{key}", style="danger")],
        [ibtn(bt("orqaga"),    data="emoji_back",         style="success")],
    ])

def broadcast_color_kb():
    return ikb([
        [
            ibtn(_B('Kok'),    data="bc_color|primary", style="primary"),
            ibtn(_B('Qizil'),  data="bc_color|danger",  style="danger"),
            ibtn(_B('Yashil'), data="bc_color|success", style="success"),
        ],
        [ibtn(bt("bekor"), data="bc_cancel", style="danger", emoji_id=get_eid("bekor"))],
    ])

def broadcast_preview_kb(has_btn: bool):
    rows = [[ibtn(_B('Tugma qoshish'), data="bc_add_btn", style="primary")]]
    if has_btn:
        rows.append([ibtn(_B('Tugmani ochirish'), data="bc_remove_btn", style="danger")])
    rows.append([
        ibtn(_B('Yuborish'), data="bc_send",   style="success"),
        ibtn(bt("bekor"),    data="bc_cancel", style="danger", emoji_id=get_eid("bekor")),
    ])
    return ikb(rows)

def bc_yesno_kb():
    """Tugmali xabar yuborasizmi? Ha / Yo'q"""
    return ikb([
        [
            ibtn("✅ Ha",   data="bc_btn_yes", style="success"),
            ibtn("❌ Yo'q", data="bc_btn_no",  style="danger"),
        ],
        [ibtn(bt("bekor"), data="bc_cancel", style="danger", emoji_id=get_eid("bekor"))],
    ])

def bc_more_yesno_kb():
    """Yana bita tugma qo'shasizmi? Ha / Yo'q"""
    return ikb([
        [
            ibtn("➕ Ha, yana qo'shaman", data="bc_more_yes", style="primary"),
            ibtn("📤 Yo'q, yuboraman",   data="bc_more_no",  style="success"),
        ],
        [ibtn(bt("bekor"), data="bc_cancel", style="danger", emoji_id=get_eid("bekor"))],
    ])


# ══════════════════════════════════════════════════════════
# XABAR YUBORISH
# ══════════════════════════════════════════════════════════

async def sm(bot, chat_id, text, markup=None, pm="HTML", reply_to_message_id=None):
    kw = {"chat_id": chat_id, "text": text, "parse_mode": pm}
    if markup: kw["reply_markup"] = markup
    if reply_to_message_id: kw["reply_to_message_id"] = reply_to_message_id
    return await bot.send_message(**kw)

async def sp(bot, chat_id, photo, caption, markup=None, pm="HTML"):
    kw = {"chat_id": chat_id, "photo": photo, "caption": caption, "parse_mode": pm}
    if markup: kw["reply_markup"] = markup
    return await bot.send_photo(**kw)

async def sv(bot, chat_id, video, caption, markup=None, pm="HTML", protect=False):
    kw = {"chat_id": chat_id, "video": video, "caption": caption, "parse_mode": pm}
    if markup: kw["reply_markup"] = markup
    if protect: kw["protect_content"] = True
    return await bot.send_video(**kw)


# ══════════════════════════════════════════════════════════
# KANAL YORDAMCHI
# ══════════════════════════════════════════════════════════

def normalize_channel_username(value: str) -> str:
    value = (value or "").strip()
    if not value: return ""
    if value.startswith("-100") and value[4:].isdigit():
        return value
    value = value.split("?")[0].strip().rstrip("/")
    value = value.replace("https://", "").replace("http://", "")
    for prefix in ("t.me/", "telegram.me/"):
        if prefix in value:
            value = value.split(prefix, 1)[1]
            break
    value = value.strip().lstrip("@").split("/")[0]
    return f"@{value}" if value else ""

def channel_join_url(username: str, fallback: str = "") -> str:
    username = normalize_channel_username(username)
    if username.startswith("@"):
        return f"https://t.me/{username[1:]}"
    return fallback or "https://t.me/"

def _channel_ref(ch: dict):
    chat_id = ch.get("chat_id")
    if chat_id: return chat_id
    return normalize_channel_username(ch.get("username") or ch.get("url") or "")

async def resolve_required_channel(bot, raw_username: str) -> dict:
    username = normalize_channel_username(raw_username)
    if not username: raise ValueError("Kanal username noto'g'ri")
    chat = await bot.get_chat(username)
    bot_user = await bot.get_me()
    bot_member = await bot.get_chat_member(chat.id, bot_user.id)
    if bot_member.status in ("left", "kicked"):
        raise ValueError("Bot kanalga qo'shilmagan yoki admin emas")
    public_username = f"@{chat.username}" if getattr(chat, "username", None) else username
    return {
        "chat_id": chat.id,
        "username": public_username,
        "title": getattr(chat, "title", None) or public_username,
        "url": channel_join_url(public_username),
    }

async def check_subscription(user_id, bot) -> list:
    cached = _sub_cache_get(user_id)
    if cached is not None: return cached
    channels = RAM.channels
    if not channels:
        _sub_cache_set(user_id, [])
        return []

    async def check_one(ch):
        try:
            chat_ref = _channel_ref(ch)
            if not chat_ref:
                return None
            member = await bot.get_chat_member(chat_ref, user_id)
            status = getattr(member, "status", "")
            is_member = getattr(member, "is_member", None)
            # creator, administrator, member — o'tkazamiz
            if status in ("creator", "administrator", "member") or is_member is True:
                return None
            # So'rovli kanal: "restricted" yoki "left" bo'lsa ham
            # so'rov yuborilgan bo'lishi mumkin — bu holatda ham talab qilamiz
            return ch
        except Exception as e:
            logger.warning(f"Sub check {ch.get('username','?')}: {e}")
            return None

    results = await asyncio.gather(*[check_one(ch) for ch in channels], return_exceptions=True)
    not_subbed = [r for r in results if r is not None and not isinstance(r, Exception)]
    _sub_cache_set(user_id, not_subbed)
    return not_subbed


# ══════════════════════════════════════════════════════════
# ADMIN STATE TOZALASH
# ══════════════════════════════════════════════════════════

def sub_admin_perm_kb(target_uid: str):
    """Sub-admin uchun perm toggle inline kb."""
    perms = (RAM.sub_admins.get(str(target_uid), {}) or {}).get("perms", {}) or {}
    rows, buf = [], []
    for k in ADMIN_PERM_KEYS:
        on = perms.get(k, True) is not False
        mark = "✅" if on else "❌"
        label = BTN_LABELS.get(k, k)
        buf.append(ibtn(f"{mark} {label}", data=f"adm_perm|{target_uid}|{k}",
                        style="success" if on else "danger"))
        if len(buf) == 2:
            rows.append(buf); buf = []
    if buf: rows.append(buf)
    rows.append([ibtn("🗑 Adminni o'chirish", data=f"adm_del|{target_uid}", style="danger")])
    rows.append([ibtn("✅ Tayyor", data=f"adm_done|{target_uid}", style="primary")])
    return ikb(rows)


def clear_admin_state(context):
    for key in [
        "admin_state", "new_movie_code", "ep_movie_code",
        "price_movie_code", "price_ep", "post_code",
        "reply_to", "awaiting_help", "awaiting_check",
        "editing_btn_key", "emoji_menu",
        "bc_msg", "bc_buttons", "bc_adding_btn",
        "bc_btn_name", "bc_btn_url", "bc_btn_emoji",
        "del_movie_code", "poster_code",
        "edit_ep_code", "edit_ep_num", "new_admin_id",
        "channel_manage_menu", "ch_info",
        "premium_target_uid", "start_msg_photo_tmp",
        "simple_link_title", "soruvli_ch_info",
        "qism_och_target_uid", "qism_och_code", "qism_och_ep_val",
        "admin_balance_target", "price_ep_range",
    ]:
        context.user_data.pop(key, None)


def _build_ep_price_list(code: str, eps: list, prices: dict) -> str:
    if not eps: return "⚠️ Bu kinoda hali qism yo'q."
    lines = []
    for i in range(len(eps)):
        ek = str(i + 1)
        price = price_to_int((prices or {}).get(ek))
        if price > 0: lines.append(f"  {ek}-qism — 💰 <b>{price} so'm</b>")
        else:         lines.append(f"  {ek}-qism — bepul")
    return f"📺 Qismlar ({len(eps)} ta):\n" + "\n".join(lines)


def _channels_list_text() -> str:
    channels = RAM.channels or []
    simple   = RAM.simple_links or []
    lines = []
    regular = [ch for ch in channels if not ch.get("join_request")]
    soruvli  = [ch for ch in channels if ch.get("join_request")]
    if regular:
        lines.append(f"📋 <b>Majburiy kanallar (tekshiriladi)</b> — {len(regular)} ta:\n")
        for i, ch in enumerate(regular, 1):
            title = ch.get('title') or ch.get('username') or '?'
            uname = ch.get('username') or '?'
            lines.append(f"  {i}. <b>{title}</b> — {uname}")
    if soruvli:
        lines.append(f"\n📨 <b>So'rovli kanallar (join request)</b> — {len(soruvli)} ta:\n")
        for i, ch in enumerate(soruvli, 1):
            title = ch.get('title') or ch.get('username') or '?'
            uname = ch.get('username') or '?'
            lines.append(f"  {i}. <b>{title}</b> — {uname}")
    if simple:
        lines.append(f"\n🔗 <b>Oddiy havolalar (tekshirilmaydi)</b> — {len(simple)} ta:\n")
        for i, sl in enumerate(simple, 1):
            lines.append(f"  {i}. <b>{sl.get('title','?')}</b> — <code>{sl.get('url','?')}</code>")
    if not lines:
        return "📭 Hozircha majburiy kanal yoki havola yo'q."
    return "\n".join(lines)


async def send_movie_menu(src, context, code: str):
    code = str(code).upper().strip()
    movie = RAM.movies.get(code)

    # ✅ TUZATISH: to'g'ridan-to'g'ri topilmasa — raqamli moslik ham sinash
    if not movie and code.isdigit():
        num_val = int(code)
        for c_key in RAM.movies.keys():
            if isinstance(c_key, str) and c_key.isdigit():
                try:
                    if int(c_key) == num_val:
                        code  = c_key
                        movie = RAM.movies[c_key]
                        break
                except Exception:
                    pass

    user_id = src.effective_user.id if hasattr(src, "effective_user") else src.from_user.id
    if not movie:
        await sm(context.bot, user_id, f"❌ <code>{code}</code> kodli kino topilmadi.")
        return
    eps = movie.get("episodes", [])
    if not eps:
        await sm(context.bot, user_id, "⏳ Bu kinoga hali qism yuklanmagan.")
        return
    markup      = movie_episodes_kb(movie, code, user_id, page=0)
    total_pages = max(1, (len(eps) + PAGE_SIZE - 1) // PAGE_SIZE)
    page_info   = f"  (1/{total_pages} sahifa)" if total_pages > 1 else ""
    caption     = (f"🎬 <b>{movie.get('title', 'Kino')}</b>\n"
                   f"📺 Qismlar soni: <b>{len(eps)} ta</b>{page_info}\n\n"
                   f"👇 Qaysi qismni ko'rmoqchisiz?")
    poster = movie.get("poster_file_id")
    try:
        if poster: await sp(context.bot, user_id, poster, caption, markup)
        else:      await sm(context.bot, user_id, caption, markup)
    except Exception as e:
        logger.error(f"send_movie_menu xato: {e}")
        try: await sm(context.bot, user_id, caption, markup)
        except Exception as e2: logger.error(f"fallback xato: {e2}")


# ══════════════════════════════════════════════════════════
# BROADCAST
# ══════════════════════════════════════════════════════════

def build_broadcast_markup(buttons: list):
    if not buttons: return None
    rows = []
    for b in buttons:
        rows.append([ibtn(
            b["text"], url=b["url"],
            style=b.get("style", "primary"),
            emoji_id=b.get("emoji_id"),
        )])
    return ikb(rows)


async def send_broadcast_preview(bot, uid, bc: dict):
    buttons    = bc.get("buttons", [])
    markup     = build_broadcast_markup(buttons)
    preview_kb = broadcast_preview_kb(bool(buttons))
    try:
        kw = {}
        if markup: kw["reply_markup"] = markup
        await bot.copy_message(
            chat_id=uid, from_chat_id=bc["from_chat_id"],
            message_id=bc["message_id"], **kw)
    except Exception as e:
        await sm(bot, uid, f"❌ Preview xato: {e}")
        return
    btn_info = ""
    if buttons:
        btn_info = "\n\n<b>Tugmalar:</b>\n" + "\n".join(
            f"• {b['text']} → {b['url']}" for b in buttons)
    await sm(bot, uid, f"<b>Preview yuqorida ↑</b>{btn_info}\n\nNima qilasiz?",
             parse_mode="HTML", markup=preview_kb)


async def do_broadcast(bot, bc: dict):
    users   = list(RAM.users.keys())
    buttons = bc.get("buttons", [])
    markup  = build_broadcast_markup(buttons)
    ok = fail = 0
    sem = asyncio.Semaphore(10)

    async def send_one(uid):
        nonlocal ok, fail
        async with sem:
            try:
                kw = {}
                if markup: kw["reply_markup"] = markup
                await bot.copy_message(
                    chat_id=int(uid), from_chat_id=bc["from_chat_id"],
                    message_id=bc["message_id"], **kw)
                ok += 1
            except Exception as e:
                fail += 1

    await asyncio.gather(*[send_one(uid) for uid in users])
    return ok, fail


# ══════════════════════════════════════════════════════════
# KINOLAR RO'YXATI — RASM GENERATSIYA
# ══════════════════════════════════════════════════════════

def _strip_html(text: str) -> str:
    """HTML teglarini va rasmda koʻrinmaydigan emoji/unicode belgilarni olib tashlaydi."""
    text = re.sub(r'<[^>]+>', '', text or '')
    # Emoji va maxsus unicode belgilarni olib tashlaymiz (font ko'rsata olmaydi — toʻrtburchak chiqadi)
    text = re.sub(
        r'[\U0001F000-\U0001FFFF\U00002600-\U000027BF\U0000FE00-\U0000FE0F'
        r'\U00020000-\U0002FA1F\u200d\ufe0f\u200b-\u200f]+',
        '', text
    )
    return text.strip()

PHOTO_PAGE_SIZE  = 20
KINO_LIST_PAGE_SIZE = 10


def generate_movies_image(movie_slice: list, page: int = 1, total_pages: int = 1,
                          total_count: int = 0, start_offset: int = 0) -> BytesIO | None:
    if not PIL_AVAILABLE or not movie_slice:
        return None

    BG_COLOR    = (250, 250, 252)
    GRID_COLOR  = (208, 213, 228)
    HEADER_BG   = (20, 60, 160)
    WHITE       = (255, 255, 255)
    TEXT_DARK   = (28, 33, 52)
    CODE_COLOR  = (60, 90, 190)
    VIEWS_COLOR = (40, 140, 70)
    EP_COLOR    = (100, 100, 130)
    ACCENT_COLORS = [
        (25, 95, 215), (40, 160, 70), (200, 50, 60),
        (200, 120, 0), (110, 60, 190), (0, 140, 180),
    ]
    # 4K sifat — kenglik 2160px (4K vertikal). Yozuvlar yirik va tiniq.
    SCALE    = 2
    IMG_W    = 1080 * SCALE   # 2160px
    PAD_X    = 40   * SCALE
    TOP_PAD  = 24   * SCALE
    CARD_H   = 170  * SCALE
    GAP      = 20   * SCALE
    HEADER_H = 160  * SCALE
    FOOTER_H = 100  * SCALE
    BADGE_SZ = 120  * SCALE
    GRID_STP = 50   * SCALE

    img_h = HEADER_H + TOP_PAD + len(movie_slice) * (CARD_H + GAP) + FOOTER_H + 10
    img  = Image.new("RGB", (IMG_W, img_h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    font_paths_bold = [
        "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    ]
    def try_font(size):
        for p in font_paths_bold:
            if os.path.exists(p):
                try: return ImageFont.truetype(p, size)
                except: continue
        # Fontlar topilmasa: yangi PIL'da load_default(size=...) ishlaydi
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    fnt_header = try_font(72 * SCALE)
    fnt_num    = try_font(54 * SCALE)
    fnt_title  = try_font(46 * SCALE)
    fnt_sub    = try_font(34 * SCALE)
    fnt_footer = try_font(38 * SCALE)

    for x in range(0, IMG_W, GRID_STP):
        draw.line([(x, 0), (x, img_h)], fill=GRID_COLOR, width=1)
    for y in range(0, img_h, GRID_STP):
        draw.line([(0, y), (IMG_W, y)], fill=GRID_COLOR, width=1)

    draw.rectangle([(0, 0), (IMG_W, HEADER_H)], fill=HEADER_BG)
    h_text = "BARCHA KINOLAR"
    try:
        hbb = draw.textbbox((0, 0), h_text, font=fnt_header)
        hx  = (IMG_W - (hbb[2] - hbb[0])) // 2
        hy  = (HEADER_H - (hbb[3] - hbb[1])) // 2
    except: hx, hy = 40, 28
    draw.text((hx, hy), h_text, fill=WHITE, font=fnt_header)

    for idx, (code, movie) in enumerate(movie_slice):
        y0 = HEADER_H + TOP_PAD + idx * (CARD_H + GAP)
        y1 = y0 + CARD_H
        x0 = PAD_X
        x1 = IMG_W - PAD_X
        col = ACCENT_COLORS[idx % len(ACCENT_COLORS)]
        draw.rounded_rectangle([x0, y0, x1, y1], radius=20*SCALE, fill=WHITE, outline=col, width=5*SCALE)
        draw.rounded_rectangle([x0, y0, x0 + 14*SCALE, y1], radius=7*SCALE, fill=col)
        bx0 = x0 + 32*SCALE
        bx1 = bx0 + BADGE_SZ
        by0 = y0 + (CARD_H - BADGE_SZ) // 2
        by1 = by0 + BADGE_SZ
        draw.ellipse([bx0, by0, bx1, by1], fill=col)
        num_txt = str(start_offset + idx + 1)
        try:
            nb  = draw.textbbox((0, 0), num_txt, font=fnt_num)
            nxc = bx0 + (BADGE_SZ - (nb[2] - nb[0])) // 2
            nyc = by0 + (BADGE_SZ - (nb[3] - nb[1])) // 2 - 4*SCALE
        except: nxc, nyc = bx0 + 30, by0 + 25
        draw.text((nxc, nyc), num_txt, fill=WHITE, font=fnt_num)
        tx = bx1 + 32*SCALE
        raw_title = _strip_html(movie.get("title", code))
        if len(raw_title) > 40: raw_title = raw_title[:38] + "…"
        title_y = y0 + 28*SCALE
        draw.text((tx, title_y), raw_title, fill=TEXT_DARK, font=fnt_title)
        ep_count    = len(movie.get("episodes", []))
        views_total = sum(movie.get("views", {}).values())
        sub_y = y0 + 100*SCALE
        code_part  = f"Kod: {code}"
        ep_part    = f"  |  {ep_count} ta qism mavjud"
        views_part = f"  |  {views_total} korilgan"
        draw.text((tx, sub_y), code_part, fill=CODE_COLOR, font=fnt_sub)
        try:
            cb = draw.textbbox((0, 0), code_part, font=fnt_sub)
            ex = tx + (cb[2] - cb[0])
        except: ex = tx + 200
        draw.text((ex, sub_y), ep_part, fill=EP_COLOR, font=fnt_sub)
        try:
            eb = draw.textbbox((0, 0), ep_part, font=fnt_sub)
            vx = ex + (eb[2] - eb[0])
        except: vx = ex + 160
        draw.text((vx, sub_y), views_part, fill=VIEWS_COLOR, font=fnt_sub)

    fy = img_h - FOOTER_H
    draw.rectangle([(0, fy), (IMG_W, img_h)], fill=HEADER_BG)
    if total_pages > 1:
        start_n = (page - 1) * KINO_LIST_PAGE_SIZE + 1
        end_n   = start_n + len(movie_slice) - 1
        f_text  = f"{start_n}-{end_n} ko'rsatildi  |  Jami: {total_count} ta  |  Kino kodini yuboring!"
    else:
        f_text = f"Jami: {total_count} ta kino  |  Kino kodini yuboring!"
    try:
        fbb = draw.textbbox((0, 0), f_text, font=fnt_footer)
        fx  = (IMG_W - (fbb[2] - fbb[0])) // 2
        fy2 = fy + (FOOTER_H - (fbb[3] - fbb[1])) // 2
    except: fx, fy2 = 40, fy + 16
    draw.text((fx, fy2), f_text, fill=WHITE, font=fnt_footer)

    buf = BytesIO()
    buf.name = f"kinolar_{int(time.time())}.png"
    img.save(buf, format="PNG", optimize=False, compress_level=1)
    buf.seek(0)
    return buf


# ══════════════════════════════════════════════════════════
# FOYDALANUVCHI TO'LOVLARI — RASM + SAHIFA TIZIMI
# ══════════════════════════════════════════════════════════

TOLOVLAR_PAGE_SIZE = 5


def _parse_ts(value) -> float:
    """ISO string yoki Unix timestamp ni float ga aylantiradi."""
    if not value:
        return 0.0
    try:
        if isinstance(value, str):
            return datetime.fromisoformat(value).timestamp()
        return float(value)
    except Exception:
        return 0.0


def _tashkent_now_str() -> str:
    """Toshkent vaqtini (UTC+5) chiroyli formatda qaytaradi."""
    try:
        import datetime as _dt
        utc_now = _dt.datetime.utcnow()
        tashkent = utc_now + _dt.timedelta(hours=5)
        return tashkent.strftime("%d.%m.%Y  %H:%M:%S")
    except Exception:
        return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def _get_today_payments() -> list:
    """Bugungi barcha to'lovlarni vaqt bo'yicha tartiblaydi."""
    today_start = datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()
    result = []
    for pid, pay in (RAM.pending_payments or {}).items():
        created = _parse_ts(pay.get("created_at") or pay.get("ts"))
        if created >= today_start:
            result.append(dict(pay))
    result.sort(
        key=lambda x: _parse_ts(x.get("created_at") or x.get("ts")),
        reverse=True)
    return result


def generate_tolovlar_image(
    pay_slice: list,
    page: int = 1,
    total_pages: int = 1,
    total_count: int = 0,
    jami_tolangan: int = 0,
    jami_pending: int = 0,
    start_offset: int = 0,
) -> BytesIO | None:
    if not PIL_AVAILABLE or not pay_slice:
        return None

    SCALE    = 2
    IMG_W    = 1080 * SCALE
    PAD_X    = 40   * SCALE
    HEADER_H = 200  * SCALE
    CARD_H   = 220  * SCALE
    GAP      = 18   * SCALE
    FOOTER_H = 110  * SCALE
    TOP_PAD  = 20   * SCALE

    BG_COLOR    = (245, 247, 255)
    HEADER_BG   = (15, 52, 135)
    WHITE       = (255, 255, 255)
    TEXT_DARK   = (22, 28, 50)
    GREEN       = (30, 160, 70)
    RED         = (210, 45, 55)
    ORANGE      = (210, 120, 0)
    BLUE        = (40, 100, 220)
    GRAY        = (110, 115, 135)
    GRID_COLOR  = (210, 215, 230)

    STATUS_COLORS = {
        "paid":      (30, 160, 70),
        "approved":  (30, 160, 70),
        "pending":   (210, 120, 0),
        "cancelled": (210, 45, 55),
        "cancel":    (210, 45, 55),
        "expired":   (140, 140, 140),
    }
    CARD_ACCENT = [
        (25, 95, 215), (40, 160, 70), (200, 50, 60),
        (200, 120, 0), (110, 60, 190), (0, 140, 180),
    ]

    img_h = (HEADER_H + TOP_PAD
             + len(pay_slice) * (CARD_H + GAP)
             + FOOTER_H + 20)
    img  = Image.new("RGB", (IMG_W, img_h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    ]
    font_paths_reg = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    ]

    def try_font(paths, size):
        for p in paths:
            if os.path.exists(p):
                try: return ImageFont.truetype(p, size)
                except: continue
        try: return ImageFont.load_default(size=size)
        except TypeError: return ImageFont.load_default()

    fnt_title  = try_font(font_paths,     68 * SCALE)
    fnt_sub    = try_font(font_paths,     36 * SCALE)
    fnt_name   = try_font(font_paths,     42 * SCALE)
    fnt_info   = try_font(font_paths_reg, 34 * SCALE)
    fnt_badge  = try_font(font_paths,     40 * SCALE)
    fnt_footer = try_font(font_paths,     34 * SCALE)
    fnt_stat   = try_font(font_paths_reg, 32 * SCALE)

    # Grid
    for x in range(0, IMG_W, 50 * SCALE):
        draw.line([(x, 0), (x, img_h)], fill=GRID_COLOR, width=1)
    for y in range(0, img_h, 50 * SCALE):
        draw.line([(0, y), (IMG_W, y)], fill=GRID_COLOR, width=1)

    # Header
    draw.rectangle([(0, 0), (IMG_W, HEADER_H)], fill=HEADER_BG)
    title_txt = "FOYDALANUVCHI TO'LOVLARI"
    try:
        tb = draw.textbbox((0, 0), title_txt, font=fnt_title)
        tx = (IMG_W - (tb[2] - tb[0])) // 2
        ty = 28 * SCALE
    except: tx, ty = 40, 20
    draw.text((tx, ty), title_txt, fill=WHITE, font=fnt_title)

    # Statistika satri
    stat_txt = (f"Bugun: {total_count} ta to'lov   "
                f"✓ To'langan: {jami_tolangan:,} so'm   "
                f"⏳ Kutilmoqda: {jami_pending:,} so'm")
    try:
        sb = draw.textbbox((0, 0), stat_txt, font=fnt_stat)
        sx = (IMG_W - (sb[2] - sb[0])) // 2
        sy = 118 * SCALE
    except: sx, sy = 40, 100
    draw.text((sx, sy), stat_txt, fill=(180, 210, 255), font=fnt_stat)

    # Kartalar
    for idx, pay in enumerate(pay_slice):
        y0  = HEADER_H + TOP_PAD + idx * (CARD_H + GAP)
        y1  = y0 + CARD_H
        x0  = PAD_X
        x1  = IMG_W - PAD_X
        col = CARD_ACCENT[idx % len(CARD_ACCENT)]

        draw.rounded_rectangle(
            [x0, y0, x1, y1], radius=18 * SCALE,
            fill=WHITE, outline=col, width=5 * SCALE)
        # Chiziq chap tomonda
        draw.rounded_rectangle(
            [x0, y0, x0 + 14 * SCALE, y1],
            radius=7 * SCALE, fill=col)

        # Tartib raqami doirasi
        BADGE_R = 55 * SCALE
        bx = x0 + 42 * SCALE
        by = y0 + (CARD_H - BADGE_R) // 2
        draw.ellipse([bx, by, bx + BADGE_R, by + BADGE_R], fill=col)
        num_txt = str(start_offset + idx + 1)
        try:
            nb  = draw.textbbox((0, 0), num_txt, font=fnt_badge)
            nxc = bx + (BADGE_R - (nb[2] - nb[0])) // 2
            nyc = by + (BADGE_R - (nb[3] - nb[1])) // 2 - 2 * SCALE
        except: nxc, nyc = bx + 10, by + 10
        draw.text((nxc, nyc), num_txt, fill=WHITE, font=fnt_badge)

        tx2 = bx + BADGE_R + 28 * SCALE

        # Ism va ID
        uid_p  = pay.get("user_id", "?")
        u_data = RAM.users.get(str(uid_p)) or {}
        name   = (u_data.get("name") or u_data.get("first_name")
                  or f"ID:{uid_p}")
        if len(name) > 22: name = name[:20] + "…"
        name_txt = f"{name}   ID: {uid_p}"
        draw.text((tx2, y0 + 22 * SCALE), name_txt,
                  fill=TEXT_DARK, font=fnt_name)

        # Miqdor
        amount   = int(pay.get("amount") or 0)
        amt_txt  = f"{amount:,} so'm"
        try:
            ntb = draw.textbbox((0, 0), name_txt, font=fnt_name)
            amt_x = tx2 + (ntb[2] - ntb[0]) + 40 * SCALE
        except: amt_x = tx2 + 500
        draw.text((amt_x, y0 + 22 * SCALE), amt_txt,
                  fill=GREEN, font=fnt_name)

        # To'lov turi
        p_type = pay.get("type", "")
        code_p = pay.get("code") or ""
        ep_p   = pay.get("ep") or ""
        if p_type == "topup_checkcard":
            tip_txt = "Avtomatik to'lov (CheckCard)"
        elif p_type == "topup_manual":
            tip_txt = "Qo'lda to'lov"
        elif p_type == "episode":
            tip_txt = f"Kino: {code_p}  Qism: {ep_p}"
        elif p_type == "buy_all":
            tip_txt = f"Barchasi: {code_p}"
        else:
            tip_txt = p_type or "—"
        draw.text((tx2, y0 + 88 * SCALE), tip_txt,
                  fill=BLUE, font=fnt_info)

        # Holat
        cc_status = pay.get("cc_status", "")
        status    = pay.get("status", "")
        if cc_status == "paid" or status == "approved":
            holat_txt = "✓ To'landi"
            h_col = GREEN
        elif cc_status == "pending":
            holat_txt = "⏳ Kutilmoqda"
            h_col = ORANGE
        elif cc_status in ("cancelled", "cancel"):
            holat_txt = "✗ Bekor qilindi"
            h_col = RED
        elif cc_status == "expired":
            holat_txt = "Muddati o'tdi"
            h_col = GRAY
        elif status == "pending":
            holat_txt = "⏳ Tasdiq kutilmoqda"
            h_col = ORANGE
        else:
            holat_txt = status or cc_status or "—"
            h_col = GRAY
        draw.text((tx2, y0 + 138 * SCALE), holat_txt,
                  fill=h_col, font=fnt_info)

        # Vaqt
        ts = float(pay.get("created_at") or pay.get("ts") or 0)
        if ts:
            dt_str = datetime.fromtimestamp(ts).strftime("%d.%m.%Y  %H:%M:%S")
        else:
            dt_str = "—"
        draw.text((tx2 + 380 * SCALE, y0 + 138 * SCALE), dt_str,
                  fill=GRAY, font=fnt_info)

        # Order
        order = pay.get("cc_order") or pay.get("order_id") or "—"
        order_txt = f"Order: {order}"
        draw.text((tx2, y0 + 182 * SCALE), order_txt,
                  fill=GRAY, font=fnt_info)

    # Footer
    fy = img_h - FOOTER_H
    draw.rectangle([(0, fy), (IMG_W, img_h)], fill=HEADER_BG)
    if total_pages > 1:
        s = (page - 1) * TOLOVLAR_PAGE_SIZE + 1
        e = s + len(pay_slice) - 1
        f_txt = (f"{s}-{e} ko'rsatildi  |  "
                 f"Jami bugun: {total_count} ta  |  "
                 f"Sahifa {page}/{total_pages}")
    else:
        f_txt = f"Jami bugun: {total_count} ta to'lov"
    try:
        fbb = draw.textbbox((0, 0), f_txt, font=fnt_footer)
        fx  = (IMG_W - (fbb[2] - fbb[0])) // 2
        fy2 = fy + (FOOTER_H - (fbb[3] - fbb[1])) // 2
    except: fx, fy2 = 40, fy + 16
    draw.text((fx, fy2), f_txt, fill=WHITE, font=fnt_footer)

    buf = BytesIO()
    buf.name = f"tolovlar_{int(time.time())}.png"
    img.save(buf, format="PNG", optimize=False, compress_level=1)
    buf.seek(0)
    return buf


async def _send_tolovlar_page(bot, chat_id: int, page: int = 0, query=None):
    """Balans to'ldirishlarni oddiy matn ro'yxati ko'rinishida ko'rsatadi."""
    # Faqat balans to'ldirishlarni olish (barcha vaqtdan)
    topup_pays = []
    for pid, pay in (RAM.pending_payments or {}).items():
        p_type = pay.get("type", "")
        if p_type in ("topup", "topup_checkcard", "topup_manual"):
            topup_pays.append(dict(pay))

    # Vaqt bo'yicha tartiblash (yangilari tepada)
    topup_pays.sort(
        key=lambda x: _parse_ts(x.get("created_at") or x.get("ts")),
        reverse=True)

    if not topup_pays:
        text = "📭 <b>Hozircha hech qanday balans to'ldirish yo'q.</b>"
        kb = ikb([[ibtn(_B("⬅️ Admin panel"), data="tolovlar_back")]])
        if query:
            try: await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
            except: await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)
        else:
            await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)
        return

    # Statistika
    jami_tolangan = sum(
        int(p.get("amount") or 0) for p in topup_pays
        if p.get("cc_status") == "paid" or p.get("status") == "approved")
    jami_pending = sum(
        int(p.get("amount") or 0) for p in topup_pays
        if p.get("cc_status") == "pending"
        or (p.get("status") == "pending" and not p.get("cc_status")))

    # Sahifalash
    PAG = 10
    total       = len(topup_pays)
    total_pages = max(1, (total + PAG - 1) // PAG)
    page        = max(0, min(page, total_pages - 1))
    start       = page * PAG
    end         = min(start + PAG, total)
    slice_pays  = topup_pays[start:end]

    lines = [
        f"💸 <b>Balans to'ldirish ro'yxati</b>  [{page+1}/{total_pages}]\n"
        f"✅ To'langan: <b>{jami_tolangan:,} so'm</b>   "
        f"⏳ Kutilmoqda: <b>{jami_pending:,} so'm</b>\n"
    ]

    for i, pay in enumerate(slice_pays, start=start + 1):
        uid_p  = pay.get("user_id", "?")
        u_data = RAM.users.get(str(uid_p)) or {}
        name   = (u_data.get("name") or u_data.get("first_name") or f"ID:{uid_p}")
        amount = int(pay.get("amount") or 0)

        ts     = _parse_ts(pay.get("created_at") or pay.get("ts"))
        dt_str = datetime.fromtimestamp(ts).strftime("%d.%m %H:%M") if ts else "—"

        cc_s   = pay.get("cc_status", "")
        status = pay.get("status", "")
        if cc_s == "paid" or status == "approved":
            holat = "✅"
        elif cc_s == "pending" or status == "pending":
            holat = "⏳"
        elif cc_s in ("cancelled", "cancel") or status == "rejected":
            holat = "❌"
        else:
            holat = "❓"

        lines.append(
            f"{i}. {holat} <b>{name}</b> (<code>{uid_p}</code>)\n"
            f"   💵 <b>{amount:,} so'm</b>   🕐 {dt_str}"
        )

    # Navigatsiya tugmalari
    nav = []
    if page > 0:
        nav.append(ibtn(_B("◀ Oldingi"), data=f"tolovlar_page|{page-1}"))
    if page < total_pages - 1:
        nav.append(ibtn(_B("Keyingi ▶"), data=f"tolovlar_page|{page+1}"))

    rows = []
    if nav: rows.append(nav)
    rows.append([ibtn(_B("🔄 Yangilash"), data=f"tolovlar_page|{page}")])
    rows.append([ibtn(_B("⬅️ Admin panel"), data="tolovlar_back")])
    kb = ikb(rows)

    text = "\n".join(lines)
    if query:
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)


async def _send_kino_list_page(bot, chat_id: int, page: int = 0, query=None):
    movies      = RAM.movies
    if not movies:
        if query is not None:
            try:
                await query.answer("🎬 Hozircha hech qanday kino qo'shilmagan.", show_alert=True)
                return
            except Exception:
                pass
        await bot.send_message(chat_id,
            "🎬 <b>Hozircha hech qanday kino qo'shilmagan.</b>", parse_mode="HTML")
        return

    # Yangi qo'shilgan kinolar tepada — added_at bo'yicha tartiblash
    # 0 qismli kinolar ko'rsatilmaydi
    all_items = sorted(
        [(c, m) for c, m in movies.items()
         if isinstance(m, dict) and len(m.get("episodes", []) or []) > 0],
        key=lambda x: float(x[1].get("added_at") or 0),
        reverse=True,
    )
    if not all_items:
        if query is not None:
            try:
                await query.answer("🎬 Hozircha qism qo'shilgan kino yo'q.", show_alert=True)
                return
            except Exception:
                pass
        await bot.send_message(chat_id,
            "🎬 <b>Hozircha qism qo'shilgan kino yo'q.</b>", parse_mode="HTML")
        return
    total_count = len(all_items)
    total_pages = max(1, (total_count + KINO_LIST_PAGE_SIZE - 1) // KINO_LIST_PAGE_SIZE)
    page        = max(0, min(page, total_pages - 1))
    start       = page * KINO_LIST_PAGE_SIZE
    end         = min(start + KINO_LIST_PAGE_SIZE, total_count)
    slice_items = all_items[start:end]

    img_buf = None
    if PIL_AVAILABLE:
        try:
            img_buf = await asyncio.to_thread(
                generate_movies_image, slice_items,
                page + 1, total_pages, total_count, start)
        except Exception as e:
            logger.error(f"kino_list surat xato: {e}")

    nav_row = []
    if page > 0:
        nav_row.append(ibtn(_B('Oldingi kinolar'), data=f"kino_list|{page-1}", style="primary"))
    if page < total_pages - 1:
        nav_row.append(ibtn(_B('Qolgan kinolar'), data=f"kino_list|{page+1}", style="primary"))

    kanal_url = RAM.settings.get("kino_kanal_url", "")
    rows = []
    if nav_row: rows.append(nav_row)
    if kanal_url:
        rows.append([ibtn(bt("kino_kanal"), url=kanal_url, style="primary",
                           emoji_id=get_eid("kino_kanal"))])
    kb = ikb(rows) if rows else None

    caption = "Kino <b>kodini</b> yuboring — video <b>darhol</b> keladi! ⚡"

    # ✅ TUZATISH: Sahifa o'tishda (callback) — eski xabarni edit qilamiz
    # Yangi "Barcha kinolar" tugmasi bosishda (query=None) — yangi xabar yuboramiz
    if query is not None and img_buf is not None:
        try:
            from telegram import InputMediaPhoto
            img_buf.seek(0)
            media = InputMediaPhoto(media=img_buf, caption=caption, parse_mode="HTML")
            await query.edit_message_media(media=media, reply_markup=kb)
            return
        except Exception as e:
            logger.warning(f"kino_list edit_media xato (fallback yangi xabar): {e}")
            try: img_buf.seek(0)
            except Exception: pass

    # Yangi xabar — doim yangi rasm bilan
    if img_buf:
        try: img_buf.seek(0)
        except Exception: pass
        await bot.send_photo(chat_id=chat_id, photo=img_buf,
                             caption=caption, parse_mode="HTML", reply_markup=kb)
    else:
        # PIL yo'q — matn ro'yxat
        lines = []
        for i, (c, m) in enumerate(slice_items, start=start+1):
            ep_c = len(m.get("episodes", []))
            lines.append(f"{i}. <b>{m.get('title', c)}</b> — Kod: <code>{c}</code> | {ep_c} qism")
        text_list = "\n".join(lines)
        await bot.send_message(chat_id=chat_id,
            text=f"🎬 <b>Barcha kinolar</b>\n\n{text_list}\n\nKino <b>kodini</b> yuboring ⚡",
            parse_mode="HTML", reply_markup=kb)


# ══════════════════════════════════════════════════════════
# START
# ══════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_duplicate_update(update): return
    user = update.effective_user
    register_user(user)
    clear_admin_state(context)
    args = context.args

    # ── Bloklangan foydalanuvchi tekshiruvi ───────────────
    if is_blocked_user(user.id) and not is_any_admin(user.id):
        await context.bot.send_message(
            chat_id=user.id,
            text="🚫 <b>Siz admin tomonidan bloklangansiz.</b>",
            parse_mode="HTML"
        )
        return

    if args and args[0].startswith("code_"):
        code = args[0].replace("code_", "").upper().strip()
        ns   = await check_subscription(user.id, context.bot)
        if ns:
            context.user_data["pending_code"] = code
            await sm(context.bot, user.id,
                "Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:",
                subscription_kb(ns, simple_links=RAM.simple_links))
            return
        await send_movie_menu(update, context, code)
        return

    ns = await check_subscription(user.id, context.bot)
    if ns:
        await sm(context.bot, user.id,
            "⚠️ Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling 👇\n"
            "Obuna bo'lgach <b>Tekshirish</b> tugmasini bosing.",
            subscription_kb(ns, simple_links=RAM.simple_links))
        return

    # ── Admin tomonidan o'rnatilgan custom start xabari (rasm + matn + premium emoji) ──
    custom_text  = (RAM.settings.get("start_msg_text") or "").strip()
    custom_photo = RAM.settings.get("start_msg_photo")

    # Inline tugmalar: Kod | Kanal (har doim ikkisi ham ko'rinadi)
    # "Kod" tugmasi — kino_kanal_url ga yo'naltiradi
    kino_kanal_url = RAM.settings.get("kino_kanal_url", "") or ""

    # Birinchi majburiy kanal URL
    majburiy_url = ""
    if RAM.channels:
        first_ch = RAM.channels[0]
        majburiy_url = first_ch.get("url") or channel_join_url(first_ch.get("username", ""), "")

    # "Kod" tugmasi — kino_kanal_url (yo'q bo'lsa majburiy kanal, yo'q bo'lsa callback)
    kod_url = kino_kanal_url or majburiy_url
    if kod_url:
        kod_btn = ibtn(bt("kod_btn"), url=kod_url, style="primary",
                       emoji_id=get_eid("kod_btn"))
    else:
        kod_btn = ibtn(bt("kod_btn"), data="start_kod", style="primary",
                       emoji_id=get_eid("kod_btn"))

    # "Kanal" tugmasi — majburiy kanal URL (yo'q bo'lsa kino_kanal_url)
    kanal_url = majburiy_url or kino_kanal_url
    if kanal_url:
        kanal_btn_item = ibtn(bt("kanal_btn"), url=kanal_url, style="primary",
                              emoji_id=get_eid("kanal_btn"))
        start_inline_rows = [[kod_btn, kanal_btn_item]]
    else:
        start_inline_rows = [[kod_btn]]

    # "Qo'llanma" tugmasi — install_video_id bo'lsa qo'shiladi
    if RAM.settings.get("install_video_id"):
        start_inline_rows.append([
            ibtn(bt("install"), data="start_qollanma", style="success",
                 emoji_id=get_eid("install"))
        ])

    inline_kb = ikb(start_inline_rows)

    if custom_photo and custom_text:
        # Admin sozlagan rasm + matn (premium emojilar saqlanadi)
        await sp(context.bot, user.id, custom_photo, custom_text, inline_kb)
    elif custom_text:
        await sm(context.bot, user.id, custom_text, inline_kb)
    else:
        hello = (f"Assalomu alaykum, <b>{user.full_name}</b>! 👋\n\n"
                 f"🎬 <b>Kino botga xush kelibsiz!</b>\n\n"
                 f"Kino <b>kodini</b> yuboring — video <b>darhol</b> keladi! ⚡")
        await sm(context.bot, user.id, hello, inline_kb)

    # Quyiga reply menyu ham yuboramiz (yordam, barcha kino, va h.k.)
    await sm(context.bot, user.id, "👇 Menyu",
             main_menu_kb(is_admin=(is_any_admin(user.id))))


# ══════════════════════════════════════════════════════════
# CALLBACK HANDLER
# ══════════════════════════════════════════════════════════

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_duplicate_update(update): return
    q    = update.callback_query
    data = q.data or ""
    uid  = q.from_user.id
    await q.answer()

    # ── Bloklangan foydalanuvchi — hech narsa qilmaymiz ──
    if is_blocked_user(uid) and not is_any_admin(uid):
        await context.bot.send_message(
            chat_id=uid,
            text="🚫 <b>Siz admin tomonidan bloklangansiz.</b>",
            parse_mode="HTML"
        )
        return

    if data == "start_kod":
        await sm(context.bot, uid,
                 "🎬 Kino <b>kodini</b> yuboring — video darhol keladi!")
        return

    if data == "start_qollanma":
        vid_id = RAM.settings.get("install_video_id")
        if vid_id:
            cap = RAM.settings.get("install_caption") or "📖 <b>Bot qo'llanmasi</b>"
            # Bot haqida qo'shimcha ma'lumot — blockquote formatida
            cap += (
                "\n\n"
                "<blockquote>"
                "ℹ️ <b>Bot haqida:</b>\n\n"
                "🎬 Ushbu bot orqali kinolarni qulay tarzda tomosha qilishingiz mumkin.\n"
                "🔍 Kino kodini yuboring va video darhol keladi!\n"
                "💰 Balans tizimi orqali pullik qismlarni sotib olishingiz mumkin.\n"
                "📡 Yangi kinolardan xabardor bo'lish uchun kanalimizga obuna bo'ling.\n\n"
                "💳 <b>Balansni qanday to'ldirish:</b>\n\n"
                "1️⃣ Pastdagi <b>«Balans»</b> tugmasini bosing\n"
                "2️⃣ <b>«Hisobni to'ldirish»</b> tugmasini bosing\n"
                "3️⃣ To'ldirmoqchi bo'lgan <b>miqdorni</b> kiriting\n"
                "4️⃣ Bot sizga <b>karta raqamini</b> yuboradi — to'lang\n"
                "5️⃣ To'lov o'tishi bilan balans <b>avtomatik</b> hisobingizga qo'shiladi!\n\n"
                "⚠️ <b>Diqqat:</b> 1 so'm yoki undan ko'proq tashlasangiz — "
                "pul hisobingizga <b>tushmaydi!</b> Faqat <b>aniq miqdorni</b> to'lang."
                "</blockquote>"
            )
            # Admin lichkasi tugmasi
            admin_lichka = (RAM.settings.get("admin_lichka") or "").strip().lstrip("@")
            kb = None
            if admin_lichka:
                kb = ikb([[ibtn("👤 Admin lichkasi", url=f"https://t.me/{admin_lichka}",
                                style="danger", emoji_id=get_eid("admin_lichka_set"))]])
            try:
                await sv(context.bot, uid, vid_id, cap, kb)
            except Exception as e:
                await sm(context.bot, uid, f"❌ Video yuborishda xato: {e}")
        else:
            await q.answer("Qo'llanma videosi hali o'rnatilmagan!", show_alert=True)
        return

    if data.startswith("kino_list|"):
        try: pg = int(data.split("|")[1])
        except: pg = 0
        await _send_kino_list_page(context.bot, uid, page=pg, query=q)
        return

    if data.startswith("tolovlar_page|"):
        if not is_any_admin(uid): return
        try: pg = int(data.split("|")[1])
        except: pg = 0
        await _send_tolovlar_page(context.bot, uid, page=pg, query=q)
        return

    if data == "tolovlar_back":
        if not is_any_admin(uid): return
        try: await q.edit_message_reply_markup(reply_markup=None)
        except: pass
        await sm(context.bot, uid, "<b>Admin panel</b>", admin_menu_kb(uid))
        return

    if data.startswith("adm_perm|"):
        if not is_super_admin(uid): return
        try:
            _, target, key = data.split("|", 2)
            if key not in ADMIN_PERM_KEYS: return
            if target not in RAM.sub_admins: return
            perms = RAM.sub_admins[target].setdefault("perms", {})
            cur = perms.get(key, True) is not False
            new_val = not cur
            perms[key] = new_val
            await schedule_save()
            # ✅ Darhol toast xabar — tez bosilganda ham ko'rinadi
            label = BTN_LABELS.get(key, key)
            try:
                status = '✅ Yoqildi' if new_val else '❌ O\'chirildi'
                await q.answer(
                    f"{status}: {label}",
                    show_alert=False
                )
            except Exception:
                pass
            # Klaviaturani yangilash — xatoni jimgina o'tkazib yuboramiz
            try:
                new_kb = sub_admin_perm_kb(target)
                await q.edit_message_reply_markup(reply_markup=new_kb)
            except Exception as e:
                err_str = str(e).lower()
                # "not modified" — aslida o'zgargan, lekin Telegram ko'rmadi
                if "not modified" not in err_str and "message_not_modified" not in err_str:
                    logger.warning(f"adm_perm edit kb: {e}")
        except Exception as e:
            logger.error(f"adm_perm xato: {e}")
        return

    if data.startswith("adm_del|"):
        if not is_super_admin(uid): return
        try:
            _, target = data.split("|", 1)
            if target in RAM.sub_admins:
                RAM.sub_admins.pop(target, None)
                await schedule_save()
                try: await q.edit_message_text(f"✅ Admin <code>{target}</code> o'chirildi.", parse_mode="HTML")
                except: pass
                await sm(context.bot, uid, "<b>Admin panel</b>", admin_menu_kb(uid))
                # O'chirilgan adminga xabar + oddiy keyboard
                try:
                    from telegram import ReplyKeyboardRemove
                    await context.bot.send_message(
                        int(target),
                        "ℹ️ Sizning admin huquqingiz bekor qilindi.\n"
                        "Botdan oddiy foydalanuvchi sifatida foydalanishingiz mumkin 🎬",
                        parse_mode="HTML",
                        reply_markup=main_menu_kb(is_admin=False)
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"adm_del xato: {e}")
        return

    # ── 🚫 BLOKLASH / BLOKDAN CHIQARISH ─────────────────────
    if data == "block_cancel":
        if not is_any_admin(uid): return
        try:
            await q.edit_message_text("❌ Bekor qilindi.", parse_mode="HTML")
        except Exception:
            pass
        await sm(context.bot, uid, "<b>Admin panel</b>", admin_menu_kb(uid))
        return

    if data.startswith("block_confirm|"):
        if not is_any_admin(uid) or not has_perm(uid, "foydalanuvchi_blok"):
            return
        try:
            target_uid = data.split("|", 1)[1]
            if int(target_uid) == ADMIN_ID:
                await q.answer("Asosiy adminni bloklash mumkin emas!", show_alert=True)
                return
            RAM.blocked_users[target_uid] = {
                "blocked_at": time.time(),
                "by": str(uid),
            }
            await save_now()
            u = RAM.get_user(target_uid) or {}
            target_name = u.get("name") or f"ID: {target_uid}"
            target_uname = u.get("username") or ""
            uname_str = f" (@{target_uname})" if target_uname else ""
            try:
                await q.edit_message_text(
                    f"🚫 <b>{target_name}</b>{uname_str} (<code>{target_uid}</code>) <b>bloklandi!</b>\n\n"
                    f"Foydalanuvchi botdan foydalana olmaydi.",
                    parse_mode="HTML")
            except Exception:
                pass
            # Foydalanuvchiga xabar yuborish
            try:
                await context.bot.send_message(
                    int(target_uid),
                    "⛔ Siz botdan foydalanish huquqingizdan mahrum bo'ldingiz.\n"
                    "Murojaat uchun adminga yozing.")
            except Exception:
                pass
            await sm(context.bot, uid, "<b>Admin panel</b>", admin_menu_kb(uid))
        except Exception as e:
            logger.error(f"block_confirm xato: {e}")
        return

    if data.startswith("unblock_confirm|"):
        if not is_any_admin(uid) or not has_perm(uid, "foydalanuvchi_blok"):
            return
        try:
            target_uid = data.split("|", 1)[1]
            RAM.blocked_users.pop(target_uid, None)
            await save_now()
            u = RAM.get_user(target_uid) or {}
            target_name = u.get("name") or f"ID: {target_uid}"
            target_uname = u.get("username") or ""
            uname_str = f" (@{target_uname})" if target_uname else ""
            try:
                await q.edit_message_text(
                    f"✅ <b>{target_name}</b>{uname_str} (<code>{target_uid}</code>) <b>blokdan chiqarildi!</b>",
                    parse_mode="HTML")
            except Exception:
                pass
            # Foydalanuvchiga xabar
            try:
                await context.bot.send_message(
                    int(target_uid),
                    "✅ Sizning bloklashingiz olib tashlandi. Botdan foydalanishingiz mumkin!")
            except Exception:
                pass
            await sm(context.bot, uid, "<b>Admin panel</b>", admin_menu_kb(uid))
        except Exception as e:
            logger.error(f"unblock_confirm xato: {e}")
        return

    # ── Admin: foydalanuvchi balansiga pul qo'shish ──
    if data.startswith("admin_add_balance|"):
        if not is_any_admin(uid): return
        try:
            target_uid = data.split("|", 1)[1]
            context.user_data["admin_state"] = "admin_add_balance_amount"
            context.user_data["admin_balance_target"] = target_uid
            u = RAM.get_user(target_uid) or {}
            target_name = u.get("name") or f"ID: {target_uid}"
            balance = int((RAM.ensure_user(target_uid)).get("balance") or 0)
            try: await q.edit_message_reply_markup(reply_markup=None)
            except: pass
            await sm(context.bot, uid,
                f"➕ <b>Pul qo'shish</b>\n\n"
                f"👤 {target_name} (<code>{target_uid}</code>)\n"
                f"💰 Hozirgi balans: <b>{balance:,} so'm</b>\n\n"
                f"Qancha so'm qo'shmoqchisiz?\n"
                f"<i>Faqat raqam yuboring (masalan: 10000)</i>")
        except Exception as e:
            logger.error(f"admin_add_balance xato: {e}")
        return

    # ── Admin: foydalanuvchi balansidan pul ayirish ──
    if data.startswith("admin_sub_balance|"):
        if not is_any_admin(uid): return
        try:
            target_uid = data.split("|", 1)[1]
            context.user_data["admin_state"] = "admin_sub_balance_amount"
            context.user_data["admin_balance_target"] = target_uid
            u = RAM.get_user(target_uid) or {}
            target_name = u.get("name") or f"ID: {target_uid}"
            balance = int((RAM.ensure_user(target_uid)).get("balance") or 0)
            try: await q.edit_message_reply_markup(reply_markup=None)
            except: pass
            await sm(context.bot, uid,
                f"💸 <b>Pul ayirish</b>\n\n"
                f"👤 {target_name} (<code>{target_uid}</code>)\n"
                f"💰 Hozirgi balans: <b>{balance:,} so'm</b>\n\n"
                f"Qancha so'm ayirmoqchisiz?\n"
                f"<i>Faqat raqam yuboring (masalan: 5000)</i>")
        except Exception as e:
            logger.error(f"admin_sub_balance xato: {e}")
        return

    if data.startswith("adm_done|"):
        if not is_super_admin(uid): return
        try:
            _, target = data.split("|", 1)
            try: await q.edit_message_text(f"✅ Admin <code>{target}</code> sozlamalari saqlandi.", parse_mode="HTML")
            except: pass
            await sm(context.bot, uid, "<b>Admin panel</b>", admin_menu_kb(uid))
        except Exception as e:
            logger.error(f"adm_done xato: {e}")
        return

    if data.startswith("ch_del|"):
        if not is_any_admin(uid): return
        try:
            idx      = int(data.split("|")[1])
            channels = RAM.channels
            if 0 <= idx < len(channels):
                removed = channels.pop(idx)
                await save_now()
                title = removed.get('title') or removed.get('username') or '?'
                try:
                    await q.edit_message_text(
                        f"✅ <b>{title}</b> o'chirildi!\n\n{_channels_list_text()}",
                        parse_mode="HTML")
                except: pass
                await sm(context.bot, uid, "Majburiy kanal boshqaruvi:", channel_manage_kb())
            else:
                await sm(context.bot, uid, "❌ Kanal topilmadi.", channel_manage_kb())
        except Exception as e:
            logger.error(f"ch_del xato: {e}")
        return

    if data.startswith("sl_del|"):
        if not is_any_admin(uid): return
        try:
            idx    = int(data.split("|")[1])
            simple = RAM.simple_links
            if 0 <= idx < len(simple):
                removed = simple.pop(idx)
                await save_now()
                try:
                    await q.edit_message_text(
                        f"✅ <b>{removed.get('title','?')}</b> havola o'chirildi!\n\n{_channels_list_text()}",
                        parse_mode="HTML")
                except: pass
                await sm(context.bot, uid, "Majburiy kanal boshqaruvi:", channel_manage_kb())
            else:
                await sm(context.bot, uid, "❌ Havola topilmadi.", channel_manage_kb())
        except Exception as e:
            logger.error(f"sl_del xato: {e}")
        return

    if data == "ch_del_cancel":
        try: await q.edit_message_reply_markup(reply_markup=None)
        except: pass
        await sm(context.bot, uid, "Bekor qilindi.", channel_manage_kb())
        return

    if data.startswith("bc_"):
        await cb_broadcast(update, context)
        return

    if data == "check_sub":
        await cb_check_sub(update, context)
        return

    if data.startswith("page|"):
        await cb_page(update, context)
        return

    if data.startswith("ep|"):
        await cb_episode(update, context)
        return

    if data.startswith("buy_all|"):
        await cb_buy_all(update, context)
        return

    if data.startswith("pay_ok|") or data.startswith("pay_no|"):
        await cb_payment(update, context)
        return

    if data.startswith("reply|"):
        await cb_reply(update, context)
        return

    if data == "refresh_stats":
        if not is_any_admin(uid): return
        u = len(RAM.users)
        m = len(RAM.movies)
        v = RAM.stats.get("total_views", 0)
        if DB_STATUS["ram_only"]:
            storage_line = (f"\n\n🔴 <b>Storage: RAM ONLY</b>\n"
                           f"JSONBlob ishlamayapti! Xato: <b>{DB_STATUS['fail_count']}</b>x")
        elif DB_STATUS["last_save_ok"]:
            storage_line = f"\n\n🟢 Storage OK | {DB_STATUS['last_save_ok']}"
        else:
            storage_line = "\n\n🟡 Storage tekshirilmagan"
        try:
            await q.edit_message_text(
                f"<b>Statistika</b>\n\nFoydalanuvchilar: <b>{u}</b>\n"
                f"Kinolar: <b>{m}</b>\nJami ko'rishlar: <b>{v}</b>{storage_line}",
                parse_mode="HTML", reply_markup=stats_kb())
        except: pass
        return

    if data == "go_home":
        try: await q.edit_message_reply_markup(reply_markup=None)
        except: pass
        await sm(context.bot, uid, "Bosh menyu",
                 main_menu_kb(is_admin=(is_any_admin(uid))))
        return

    if data == "waiting_confirm":
        await q.answer("Admin ko'rib chiqmoqda, sabrli bo'ling!", show_alert=True)
        return

    # copy_card / copy_amount endi CopyTextButton orqali ishlaydi — handler kerak emas

    if data == "send_check":
        pending = context.user_data.get("pending_check")
        if not pending:
            await q.answer("To'lov ma'lumoti topilmadi", show_alert=True)
            return
        context.user_data["awaiting_check"] = pending
        context.user_data.pop("pending_check", None)
        await sm(context.bot, uid, "📤 <b>Chek rasmini yuboring</b> 👇")
        return

    # ══════════════════════════════════════════════
    # BALANS TO'LDIRISH — foydalanuvchi callbacklari
    # ══════════════════════════════════════════════
    if data == "topup_start":
        # Foydalanuvchi "Hisobni to'ldirish" tugmasini bosdi
        context.user_data["admin_state"] = "topup_amount"
        await sm(context.bot, uid,
            "💳 <b>Hisobni to'ldirish</b>\n\n"
            "Qancha so'm kiritmoqchisiz?\n"
            "💡 <b>Minimal miqdor: 1 000 so'm</b>\n\n"
            "<i>Faqat raqam yuboring (masalan: 10000)</i>")
        return

    if data == "topup_send_check":
        pending_topup = context.user_data.get("pending_topup")
        if not pending_topup:
            await q.answer("Ma'lumot topilmadi, qayta bosing", show_alert=True)
            return
        context.user_data["awaiting_topup_check"] = pending_topup
        context.user_data.pop("pending_topup", None)
        await sm(context.bot, uid, "📤 <b>Chek rasmini yuboring</b> 👇")
        return

    if data.startswith("topup_ok|") or data.startswith("topup_no|"):
        await cb_topup_payment(update, context)
        return

    # ── CheckCard: manual status tekshirish ──
    if data.startswith("cc_check|"):
        pid = data.split("|", 1)[1]
        pay = RAM.pending_payments.get(pid)
        if not pay or pay.get("type") != "topup_checkcard":
            await q.answer("To'lov topilmadi!", show_alert=True)
            return
        if pay.get("cc_status") == "paid":
            await sm(context.bot, uid, "✅ To'lov allaqachon tasdiqlangan!")
            return
        if pay.get("cc_status") in ("cancelled", "cancel"):
            await sm(context.bot, uid, "❌ Bu to'lov bekor qilingan.")
            return
        cc_order = pay.get("cc_order")
        # Tekshirilmoqda xabari
        await sm(context.bot, uid, f"⏳ CheckCard tekshirilmoqda... Order: <code>{cc_order}</code>")
        result = await asyncio.to_thread(checkcard_check_payment, cc_order)
        logger.info(f"cc_check result for {cc_order}: {result}")
        cc_data = result.get("data", {}) or {}
        # CheckCard turli formatlarda qaytarishi mumkin
        cc_status = (cc_data.get("status")
                     or cc_data.get("state")
                     or result.get("status")
                     or result.get("state")
                     or "")
        cc_status = str(cc_status).lower().strip()
        if cc_status == "paid":
            pay["cc_status"] = "paid"
            amount = int(pay.get("amount", 0))
            u_data = RAM.ensure_user(str(uid))
            u_data["balance"] = int(u_data.get("balance") or 0) + amount
            u_data["topup_total"] = int(u_data.get("topup_total") or 0) + amount
            await save_now()
            try:
                await q.edit_message_text(
                    f"✅ <b>To'lov tasdiqlandi!</b>\n\n"
                    f"💵 <b>{amount:,} so'm</b> balansingizga qo'shildi!\n"
                    f"💰 Joriy balans: <b>{u_data['balance']:,} so'm</b>",
                    parse_mode="HTML")
            except Exception:
                pass
            await sm(context.bot, uid,
                f"<blockquote>✅ <b>HISOBINGIZGA PUL QO'SHILDI!</b>\n\n"
                f"💵 Miqdor: <b>{amount:,} so'm</b>\n"
                f"💰 Joriy balans: <b>{u_data['balance']:,} so'm</b>\n\n"
                f"Endi balansdan pullik qismlarni tomosha qilishingiz mumkin! 🎬</blockquote>")
            try:
                u_d = RAM.users.get(str(uid)) or {}
                u_name = u_d.get("name") or u_d.get("first_name") or f"ID: {uid}"
                u_uname = u_d.get("username") or q.from_user.username or ""
                uname_adm = f"@{u_uname}" if u_uname else f"ID: {uid}"
                if u_uname:
                    lichka_url = f"https://t.me/{u_uname}"
                else:
                    lichka_url = f"tg://user?id={uid}"
                tashkent_time = _tashkent_now_str()
                card_info = f"\n💳 Karta: <code>{RAM.card_number}</code>" if RAM.card_number else ""
                adm_cap = (
                    f"<blockquote>"
                    f"✅ <b>AUTO TO'LOV ORQALI TO'LANDI</b>\n\n"
                    f"👤 <b>Ism:</b> {u_name}\n"
                    f"🆔 <b>ID:</b> <code>{uid}</code>\n"
                    f"📱 <b>Username:</b> {uname_adm}\n\n"
                    f"💵 <b>To'langan summa:</b> <b>{amount:,} so'm</b>\n"
                    f"💰 <b>Joriy balans:</b> <b>{u_data['balance']:,} so'm</b>\n"
                    f"{card_info}\n\n"
                    f"🕐 <b>Vaqt (Toshkent):</b> {tashkent_time}"
                    f"</blockquote>"
                )
                lichka_kb_cc = {"inline_keyboard": [[{"text": "👤 Foydalanuvchi lichkasi", "url": lichka_url}]]}
                await context.bot.send_message(
                    ADMIN_ID, adm_cap, parse_mode="HTML",
                    reply_markup=lichka_kb_cc)
            except Exception as _e:
                logger.warning(f"cc_check admin notify xato: {_e}")
        elif cc_status in ("cancel", "cancelled"):
            pay["cc_status"] = "cancelled"
            await schedule_save()
            await sm(context.bot, uid, "❌ To'lov bekor qilingan.")
        else:
            await sm(context.bot, uid,
                f"⏳ To'lov hali tasdiqlanmagan.\n"
                f"📋 Order: <code>{cc_order}</code>\n"
                f"📊 Status: <b>{cc_status if cc_status else 'noaniq'}</b>\n\n"
                f"To'lovni amalga oshirgan bo'lsangiz, bir necha daqiqa kuting va qayta tekshiring.")
        return

    # ── CheckCard: to'lovni bekor qilish ──
    if data.startswith("cc_cancel|"):
        pid = data.split("|", 1)[1]
        pay = RAM.pending_payments.get(pid)
        if not pay or pay.get("type") != "topup_checkcard":
            await q.answer("To'lov topilmadi!", show_alert=True)
            return
        if pay.get("cc_status") == "paid":
            await q.answer("✅ To'lov allaqachon amalga oshirilgan!", show_alert=True)
            return
        cc_order = pay.get("cc_order")
        if cc_order:
            await asyncio.to_thread(checkcard_cancel_payment, cc_order)
        pay["cc_status"] = "cancelled"
        await schedule_save()
        try:
            await q.edit_message_text("❌ <b>To'lov bekor qilindi.</b>", parse_mode="HTML")
        except Exception:
            pass
        await sm(context.bot, uid, "❌ To'lov bekor qilindi. Qayta urinish uchun balans bo'limiga boring.")
        return

    if data == "emoji_back":
        if not is_any_admin(uid): return
        context.user_data.pop("editing_btn_key", None)
        context.user_data["emoji_menu"] = True
        try: await q.edit_message_text("Tugmani pastdan tanlang 👇")
        except: pass
        await sm(context.bot, uid,
            "<b>Tugma sozlamalari</b>\nO'zgartirmoqchi bo'lgan tugmani pastdan tanlang 👇",
            emoji_menu_kb())
        return

    if data == "emoji_reset_all":
        if not is_any_admin(uid): return
        RAM.btn_texts = {}
        RAM.emoji_ids = {}
        EMOJI_IDS.clear()
        await save_now()
        try: await q.edit_message_text("✅ Barcha tugmalar tiklandi!")
        except: pass
        context.user_data["emoji_menu"] = True
        context.user_data.pop("editing_btn_key", None)
        await sm(context.bot, uid, "✅ Tiklandi! Tugmani tanlang:", emoji_menu_kb())
        return

    if data.startswith("emoji_reset|"):
        if not is_any_admin(uid): return
        key = data.split("|", 1)[1]
        RAM.btn_texts.pop(key, None)
        RAM.emoji_ids.pop(key, None)
        EMOJI_IDS.pop(key, None)
        await save_now()
        default = DEFAULT_BTN.get(key, "")
        context.user_data.pop("editing_btn_key", None)
        context.user_data["emoji_menu"] = True
        try:
            await q.edit_message_text(
                f"✅ <b>{BTN_LABELS.get(key, key)}</b> tiklandi!\nDefault: <code>{default}</code>",
                parse_mode="HTML")
        except: pass
        await sm(context.bot, uid, "Tugmani tanlang:", emoji_menu_kb())
        return

    if data.startswith("quick_add_ep|"):
        if not is_any_admin(uid): return
        code = data.split("|", 1)[1]
        context.user_data["admin_state"]   = "add_ep_video"
        context.user_data["ep_movie_code"] = code
        movie  = RAM.movies.get(code, {})
        ep_num = len(movie.get("episodes", [])) + 1
        await sm(context.bot, uid,
            f"🎬 <b>{movie.get('title', code)}</b>\n"
            f"📹 <b>{ep_num}-qism</b> uchun video yuboring:")
        return

    if data.startswith("finish_movie|"):
        if not is_any_admin(uid): return
        code = data.split("|", 1)[1]
        movie = RAM.movies.get(code, {})
        ep_count = len(movie.get("episodes", []))

        # ❗ Agar kino bo'sh (0 qism) bo'lsa — saqlamaymiz, admin'ga ogohlantirish
        if ep_count == 0:
            # state'ni saqlab qolamiz — admin video yuborsa, qism sifatida qabul qilinadi
            context.user_data["admin_state"]   = "add_ep_video"
            context.user_data["ep_movie_code"] = code
            await sm(context.bot, uid,
                f"⚠️ <b>{movie.get('title', code)}</b> kinoda hali <b>birorta ham qism yo'q</b>!\n\n"
                f"Avval kamida 1 ta video yuboring, keyin <b>Tugatish</b> tugmasini bosing.\n\n"
                f"📹 <b>1-qism</b> uchun video yuboring:",
                movie_added_kb(code))
            return

        context.user_data.pop("admin_state", None)
        context.user_data.pop("ep_movie_code", None)

        await sm(context.bot, uid, "💾 Bazaga (JSONBlob) saqlanmoqda, kuting...")
        ok = await save_now()
        if not ok:
            await asyncio.sleep(2)
            ok = await save_now()

        total_movies = len(RAM.movies)
        total_eps = sum(len(m.get("episodes", [])) for m in RAM.movies.values())

        if ok:
            await sm(context.bot, uid,
                f"✅ <b>{movie.get('title', code)}</b> bazaga saqlandi!\n"
                f"Kod: <code>{code}</code>\n"
                f"Bu kinoda qismlar: <b>{ep_count} ta</b>\n\n"
                f"📊 Bazada jami: <b>{total_movies} kino</b>, <b>{total_eps} qism</b>",
                admin_menu_kb(uid))
        else:
            await sm(context.bot, uid,
                f"⚠️ Lokal saqlandi, lekin JSONBlob xato berdi.\n"
                f"Bot ishlayveradi, keyinroq avtomatik qayta urinadi.",
                admin_menu_kb(uid))

        # 🟢 Kanaldagi auto-postni "To'liq yuklandi" holatiga o'tkazamiz
        asyncio.create_task(auto_post_episode_added(context.bot, code, finished=True))
        return

    if data.startswith("quick_price|"):
        if not is_any_admin(uid): return
        code  = data.split("|", 1)[1]
        movie = RAM.movies.get(code)
        if not movie:
            await sm(context.bot, uid, "❌ Kino topilmadi!")
            return
        eps = movie.get("episodes", [])
        if not eps:
            await sm(context.bot, uid,
                f"⚠️ <b>{movie.get('title', code)}</b> kinoda hali qism yo'q.")
            return
        prices  = movie.get("prices", {})
        ep_list = _build_ep_price_list(code, eps, prices)
        context.user_data["price_movie_code"] = code
        context.user_data["admin_state"]      = "set_price_ep"
        await sm(context.bot, uid,
            f"💰 <b>{movie.get('title', code)}</b> — narx belgilash\n\n{ep_list}\n\n"
            f"Qism <b>raqamini</b> kiriting (1 dan {len(eps)} gacha):\n"
            f"<i>Bir nechta qism uchun: <code>1+20</code> (1 dan 20 gacha)</i>")
        return


# ══════════════════════════════════════════════════════════
# CALLBACK: BROADCAST
# ══════════════════════════════════════════════════════════

async def cb_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    uid = q.from_user.id
    data = q.data or ""
    if not is_any_admin(uid): return

    bc = context.user_data.get("bc_msg", {})

    if data == "bc_cancel":
        for k in ["bc_msg", "bc_buttons", "bc_adding_btn", "bc_btn_name", "bc_btn_emoji"]:
            context.user_data.pop(k, None)
        try: await q.edit_message_text("❌ Broadcast bekor qilindi.")
        except: pass
        await sm(context.bot, uid, "Admin panel", admin_menu_kb(uid))
        return

    # Tugmali xabar yuborasizmi? Ha/Yo'q
    if data == "bc_btn_yes":
        try: await q.edit_message_reply_markup(reply_markup=None)
        except: pass
        await sm(context.bot, uid, "Tugma rangini tanlang:", broadcast_color_kb())
        return

    if data == "bc_btn_no":
        try: await q.edit_message_reply_markup(reply_markup=None)
        except: pass
        # Tugmasiz darhol barchaga yuborish
        total = len(RAM.users)
        prog_msg = await sm(context.bot, uid, f"⏳ Yuborilmoqda... 0/{total}")
        ok, fail = await do_broadcast(context.bot, bc)
        for k in ["bc_msg", "bc_buttons"]:
            context.user_data.pop(k, None)
        try:
            await context.bot.edit_message_text(
                f"✅ Broadcast tugadi!\n\nYuborildi: <b>{ok}</b>\nXato: <b>{fail}</b>",
                chat_id=uid, message_id=prog_msg.message_id, parse_mode="HTML")
        except:
            await sm(context.bot, uid, f"✅ Broadcast tugadi! Ok:{ok}, Xato:{fail}")
        await sm(context.bot, uid, "Admin panel", admin_menu_kb(uid))
        return

    # Yana bita tugma qo'shasizmi?
    if data == "bc_more_yes":
        try: await q.edit_message_reply_markup(reply_markup=None)
        except: pass
        await sm(context.bot, uid, "Yangi tugma rangini tanlang:", broadcast_color_kb())
        return

    if data == "bc_more_no":
        try: await q.edit_message_reply_markup(reply_markup=None)
        except: pass
        # ✅ TUZATILDI: "Yo'q, yuboraman" — darhol BARCHA foydalanuvchilarga yuboradi
        total = len(RAM.users)
        prog_msg = await sm(context.bot, uid, f"⏳ Barchaga yuborilmoqda... 0/{total}")
        ok, fail = await do_broadcast(context.bot, bc)
        for k in ["bc_msg", "bc_buttons", "bc_adding_btn", "bc_btn_name", "bc_btn_url", "bc_btn_emoji"]:
            context.user_data.pop(k, None)
        try:
            await context.bot.edit_message_text(
                f"✅ Broadcast tugadi!\n\nYuborildi: <b>{ok}</b>\nXato: <b>{fail}</b>\nJami: <b>{total}</b>",
                chat_id=uid, message_id=prog_msg.message_id, parse_mode="HTML")
        except:
            await sm(context.bot, uid, f"✅ Broadcast tugadi! Ok:{ok}, Xato:{fail}")
        await sm(context.bot, uid, "Admin panel", admin_menu_kb(uid))
        return

    if data.startswith("bc_color|"):
        color = data.split("|", 1)[1]
        bc["btn_color"] = color
        context.user_data["bc_msg"]        = bc
        context.user_data["bc_adding_btn"] = "text"
        try: await q.edit_message_reply_markup(reply_markup=None)
        except: pass
        color_names = {"primary": "🔵 Ko'k", "danger": "🔴 Qizil", "success": "🟢 Yashil"}
        await sm(context.bot, uid,
            f"Rang: <b>{color_names.get(color, color)}</b>\n\nTugma nomini kiriting:")
        return

    if data == "bc_add_btn":
        try: await q.edit_message_reply_markup(reply_markup=None)
        except: pass
        await sm(context.bot, uid, "Tugma rangini tanlang:", broadcast_color_kb())
        return

    if data == "bc_remove_btn":
        bc["buttons"] = []
        context.user_data["bc_msg"] = bc
        try: await q.edit_message_reply_markup(reply_markup=None)
        except: pass
        await sm(context.bot, uid, "✅ Tugmalar o'chirildi. Preview:")
        await send_broadcast_preview(context.bot, uid, bc)
        return

    if data == "bc_send":
        total = len(RAM.users)
        try: await q.edit_message_reply_markup(reply_markup=None)
        except: pass
        prog_msg = await sm(context.bot, uid, f"⏳ Yuborilmoqda... 0/{total}")
        ok, fail = await do_broadcast(context.bot, bc)
        for k in ["bc_msg", "bc_buttons"]:
            context.user_data.pop(k, None)
        try:
            await context.bot.edit_message_text(
                f"✅ Broadcast tugadi!\n\nYuborildi: <b>{ok}</b>\nXato: <b>{fail}</b>",
                chat_id=uid, message_id=prog_msg.message_id, parse_mode="HTML")
        except:
            await sm(context.bot, uid, f"✅ Broadcast tugadi! Ok:{ok}, Xato:{fail}")
        await sm(context.bot, uid, "Admin panel", admin_menu_kb(uid))
        return


# ══════════════════════════════════════════════════════════
# CALLBACK: SAHIFALASH
# ══════════════════════════════════════════════════════════

async def cb_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split("|")
    if len(parts) != 3: return
    _, code, page_str = parts
    try: page = int(page_str)
    except: return
    movie = RAM.movies.get(code)
    if not movie: return
    user_id     = q.from_user.id
    eps         = movie.get("episodes", [])
    markup      = movie_episodes_kb(movie, code, user_id, page=page)
    total_pages = max(1, (len(eps) + PAGE_SIZE - 1) // PAGE_SIZE)
    caption     = (f"🎬 <b>{movie.get('title', 'Kino')}</b>\n"
                   f"📺 Qismlar soni: <b>{len(eps)} ta</b>  "
                   f"({page + 1}/{total_pages} sahifa)\n\n"
                   f"👇 Qaysi qismni ko'rmoqchisiz?")
    try: await q.edit_message_caption(caption=caption, parse_mode="HTML", reply_markup=markup)
    except:
        try: await q.edit_message_text(caption, parse_mode="HTML", reply_markup=markup)
        except Exception as e: logger.error(f"cb_page xato: {e}")


# ══════════════════════════════════════════════════════════
# CALLBACK: SUBSCRIPTION
# ══════════════════════════════════════════════════════════

async def cb_check_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    uid = q.from_user.id
    _sub_cache_invalidate(uid)
    ns = await check_subscription(uid, context.bot)
    if ns:
        await q.answer("Hali obuna bo'lmagansiz! ❌", show_alert=True)
        return
    try: await q.edit_message_text("✅ Zo'r! Barcha kanallarga obuna bo'ldingiz!")
    except: pass
    pending = context.user_data.pop("pending_code", None)
    if pending:
        await send_movie_menu(q, context, pending)
    else:
        await sm(context.bot, uid,
            f"🎉 Xush kelibsiz, <b>{q.from_user.full_name}</b>!\n\nKino kodini yuboring 👇",
            main_menu_kb(is_admin=(is_any_admin(uid))))


# ══════════════════════════════════════════════════════════
# CALLBACK: QISM KO'RISH
# ══════════════════════════════════════════════════════════

async def cb_episode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split("|")
    if len(parts) != 3: return
    _, code, ep = parts
    movie = RAM.movies.get(code)
    if not movie:
        await q.answer("Kino topilmadi", show_alert=True)
        return

    code = str(code).upper()
    user_id = str(q.from_user.id)
    price = price_to_int(movie.get("prices", {}).get(ep))
    RAM.ensure_user(user_id)

    # ❗ Har bir qism alohida tekshiriladi — boshqa qismni sotib olish
    # bu qismni ochmaydi. Faqat shu kino+qism uchun approved to'lov bo'lsa ochiq.
    if price > 0 and not is_episode_paid(user_id, code, ep):
        u_data = RAM.ensure_user(user_id)
        balance = int(u_data.get("balance") or 0)

        if balance >= price:
            # ✅ Balansdan yechib, qismni ochib beramiz (7 kun muddatli)
            u_data["balance"] = balance - price
            paid_key = episode_paid_key(code, ep)
            expire_at = time.time() + EPISODE_ACCESS_DURATION
            u_data["paid_episodes"][paid_key] = {
                "status": "approved",
                "price": price,
                "payment_id": f"balance_{int(time.time())}",
                "approved_at": datetime.now().isoformat(),
                "expire_at": expire_at,  # 7 kundan keyin qayta pullik
            }
            await schedule_save()
            # 📢 Adminga balansdan to'lov haqida xabar
            try:
                u_obj = q.from_user
                uname_adm = f"@{u_obj.username}" if u_obj.username else f"ID: {q.from_user.id}"
                if u_obj.username:
                    lichka_url = f"https://t.me/{u_obj.username}"
                else:
                    lichka_url = f"tg://user?id={q.from_user.id}"
                tashkent_time = _tashkent_now_str()
                card_info = f"\n💳 Karta: <code>{RAM.card_number}</code>" if RAM.card_number else ""
                adm_cap = (
                    f"<blockquote>"
                    f"💸 <b>BALANSDAN TO'LOV AMALGA OSHDI</b>\n\n"
                    f"👤 <b>Ism:</b> {u_obj.full_name}\n"
                    f"🆔 <b>ID:</b> <code>{q.from_user.id}</code>\n"
                    f"📱 <b>Username:</b> {uname_adm}\n\n"
                    f"🎬 <b>Kino:</b> {movie.get('title', code)} (<code>{code}</code>)\n"
                    f"📺 <b>Qism:</b> {ep}-qism\n"
                    f"💵 <b>To'langan:</b> <b>{price:,} so'm</b>\n"
                    f"💰 <b>Qolgan balans:</b> <b>{u_data['balance']:,} so'm</b>\n"
                    f"{card_info}\n\n"
                    f"🕐 <b>Vaqt (Toshkent):</b> {tashkent_time}"
                    f"</blockquote>"
                )
                lichka_kb = ikb([[ibtn("👤 Foydalanuvchi lichkasi", url=lichka_url, style="primary")]])
                await sm(context.bot, ADMIN_ID, adm_cap, lichka_kb)
            except Exception as _e:
                logger.warning(f"Admin balance notify xato: {_e}")
            # Qismni yuboramiz (quyidagi kod ishlaydi)
        else:
            # ❌ Balans yetarli emas — faqat balansni to'ldirish kerakligini aytamiz
            txt  = (f"🔒 <b>Bu qism pullik</b>\n\n"
                    f"🎬 Kino: <b>{movie.get('title')}</b>\n"
                    f"📺 Qism: <b>{ep}</b>\n💰 Narxi: <b>{price} so'm</b>\n\n"
                    f"💰 Balansingiz: <b>{balance} so'm</b>\n"
                    f"<i>(yetarli emas — {price - balance} so'm kam)</i>\n\n"
                    f"💳 <b>Iltimos, balansingizni to'ldiring.</b>\n"
                    f"Balansingizdan avtomatik yechib olinadi.")
            await sm(context.bot, q.from_user.id, txt, balans_kb())
            return

    idx = int(ep) - 1
    eps = movie.get("episodes", [])
    if idx < 0 or idx >= len(eps):
        await q.answer("Qism topilmadi", show_alert=True)
        return

    bot_me    = await context.bot.get_me()
    share_url = f"https://t.me/share/url?url=https://t.me/{bot_me.username}?start=code_{code}"
    caption   = f"🎬 <b>{movie.get('title')}</b>\n📺 Qism: <b>{ep}</b>"
    try:
        await sv(context.bot, q.from_user.id, eps[idx], caption, share_kb(share_url), protect=True)
    except Exception as e:
        logger.error(f"Video yuborishda xato: {e}")
        await sm(context.bot, q.from_user.id, "❌ Video yuborishda xato. Qayta urinib ko'ring.")
        return

    async def update_stats():
        try:
            movie.setdefault("views", {})
            movie["views"][ep] = movie["views"].get(ep, 0) + 1
            RAM.ensure_user(user_id)["watched"][f"{code}_{ep}"] = True
            RAM.stats["total_views"] = RAM.stats.get("total_views", 0) + 1
            await schedule_save()
        except Exception as e:
            logger.error(f"update_stats xato: {e}")
    asyncio.create_task(update_stats())


# ══════════════════════════════════════════════════════════
# CALLBACK: BARCHASINI SOTIB OLISH
# ══════════════════════════════════════════════════════════

async def cb_buy_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Foydalanuvchi 'Barchasini sotib olish' tugmasini bosadi.
    Balansdan jami narxni yechib, barcha pullik qismlarni 7 kunlik kirish bilan ochadi.
    """
    q = update.callback_query
    code = q.data.split("|", 1)[1].upper()
    user_id = str(q.from_user.id)
    movie = RAM.movies.get(code)
    if not movie:
        await q.answer("Kino topilmadi!", show_alert=True)
        return

    prices = movie.get("prices", {}) or {}
    eps = movie.get("episodes", [])
    u_data = RAM.ensure_user(user_id)
    balance = int(u_data.get("balance") or 0)

    # Ochilmagan pullik qismlarni aniqlaymiz
    locked_eps = []
    total_price = 0
    for i in range(len(eps)):
        ek = str(i + 1)
        price_int = price_to_int(prices.get(ek))
        if price_int > 0 and not is_episode_paid(user_id, code, ek):
            locked_eps.append((ek, price_int))
            total_price += price_int

    if not locked_eps:
        await q.answer("Barcha qismlar allaqachon ochiq!", show_alert=True)
        return

    if balance < total_price:
        txt = (
            f"💰 <b>Barchasini sotib olish</b>\n\n"
            f"🎬 Kino: <b>{movie.get('title', code)}</b>\n"
            f"📺 Ochilmagan qismlar: <b>{len(locked_eps)} ta</b>\n"
            f"💵 Jami narx: <b>{total_price:,} som</b>\n\n"
            f"💰 Balansingiz: <b>{balance:,} som</b>\n"
            f"❌ <i>Yetarli emas — {total_price - balance:,} som kam</i>\n\n"
            f"💳 <b>Balansingizni to'ldiring</b>"
        )
        await sm(context.bot, q.from_user.id, txt, balans_kb())
        return

    # ✅ Balansdan yechib barcha qismlarni ochamiz (har biri 7 kunlik)
    u_data["balance"] = balance - total_price
    expire_at = time.time() + EPISODE_ACCESS_DURATION
    now_iso = datetime.now().isoformat()
    paid_eps_updated = []
    for ek, price_int in locked_eps:
        paid_key = episode_paid_key(code, ek)
        u_data["paid_episodes"][paid_key] = {
            "status": "approved",
            "price": price_int,
            "payment_id": f"buy_all_{int(time.time())}",
            "approved_at": now_iso,
            "expire_at": expire_at,  # 7 kundan keyin qayta pullik
        }
        paid_eps_updated.append(ek)

    await save_now()

    expire_dt = datetime.fromtimestamp(expire_at).strftime("%d.%m.%Y %H:%M")
    await q.answer(f"✅ {len(paid_eps_updated)} ta qism ochildi! 7 kun ochiq.", show_alert=True)

    # 📢 Adminga xabar
    try:
        u_obj = q.from_user
        uname_adm = f"@{u_obj.username}" if u_obj.username else f"ID: {q.from_user.id}"
        if u_obj.username:
            lichka_url = f"https://t.me/{u_obj.username}"
        else:
            lichka_url = f"tg://user?id={q.from_user.id}"
        tashkent_time = _tashkent_now_str()
        card_info = f"\n💳 Karta: <code>{RAM.card_number}</code>" if RAM.card_number else ""
        adm_cap = (
            f"<blockquote>"
            f"💸 <b>BALANSDAN TO'LOV (BARCHASI)</b>\n\n"
            f"👤 <b>Ism:</b> {u_obj.full_name}\n"
            f"🆔 <b>ID:</b> <code>{q.from_user.id}</code>\n"
            f"📱 <b>Username:</b> {uname_adm}\n\n"
            f"🎬 <b>Kino:</b> {movie.get('title', code)} (<code>{code}</code>)\n"
            f"📺 <b>Ochilgan qismlar:</b> {len(paid_eps_updated)} ta\n"
            f"💵 <b>To'langan:</b> <b>{total_price:,} so'm</b>\n"
            f"💰 <b>Qolgan balans:</b> <b>{u_data['balance']:,} so'm</b>\n"
            f"{card_info}\n\n"
            f"🕐 <b>Vaqt (Toshkent):</b> {tashkent_time}"
            f"</blockquote>"
        )
        lichka_kb = ikb([[ibtn("👤 Foydalanuvchi lichkasi", url=lichka_url, style="primary")]])
        await sm(context.bot, ADMIN_ID, adm_cap, lichka_kb)
    except Exception as _e:
        logger.warning(f"Admin buy_all notify xato: {_e}")

    # Klaviaturani yangilaymiz
    try:
        markup = movie_episodes_kb(movie, code, int(user_id), page=0)
        await q.edit_message_reply_markup(reply_markup=markup)
    except Exception:
        pass

    await sm(context.bot, q.from_user.id,
        f"✅ <b>Barcha qismlar ochildi!</b>\n\n"
        f"🎬 Kino: <b>{movie.get('title', code)}</b>\n"
        f"📺 Ochilgan qismlar: <b>{len(paid_eps_updated)} ta</b>\n"
        f"💵 Yechildi: <b>{total_price:,} som</b>\n"
        f"💰 Qolgan balans: <b>{u_data['balance']:,} som</b>\n\n"
        f"⏰ Kirish muddati: <b>{expire_dt} gacha</b>\n"
        f"<i>(7 kundan keyin qayta pullik bo'ladi)</i>")


# ══════════════════════════════════════════════════════════
# CALLBACK: TO'LOV
# ══════════════════════════════════════════════════════════

async def cb_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split("|", 1)
    if len(parts) != 2: return
    action, pid = parts
    pay = RAM.pending_payments.get(pid)
    if not pay:
        try: await q.edit_message_caption("To'lov topilmadi.")
        except: pass
        return

    # ❗ Takror tasdiqlash/rad etishni bloklaymiz
    if pay.get("status") in ("approved", "rejected"):
        try:
            await q.answer(f"Bu to'lov allaqachon {pay.get('status')}!", show_alert=True)
        except: pass
        return

    if action == "pay_no":
        pay["status"] = "rejected"
        await save_now()
        try:
            await q.edit_message_caption(
                (q.message.caption or "") + "\n\n<b>❌ Bekor qilindi</b>", parse_mode="HTML")
        except: pass
        await sm(context.bot, pay["user_id"],
                 f"❌ <b>To'lovingiz rad etildi.</b>\n"
                 f"Kino: <code>{pay['code']}</code>, Qism: <b>{pay['ep']}</b>\n"
                 f"Boshqa qismlar uchun ham alohida to'lov qilishingiz kerak.")
        return

    # ✅ TASDIQLASH — faqat shu bitta qism ochiladi
    pay["status"] = "approved"
    pay["code"] = str(pay.get("code", "")).upper()
    pay["ep"] = str(pay.get("ep"))
    pay["approved_at"] = datetime.now().isoformat()
    uid = str(pay["user_id"])
    user_dict = RAM.ensure_user(uid)
    paid_key = episode_paid_key(pay["code"], pay["ep"])
    user_dict["paid_episodes"][paid_key] = {
        "status": "approved",
        "price": pay.get("price"),
        "payment_id": pid,
        "approved_at": pay["approved_at"],
    }
    await save_now()  # darhol saqlash — yo'qolib qolmasin
    try:
        await q.edit_message_caption(
            (q.message.caption or "") + f"\n\n<b>✅ Tasdiqlandi</b> — {pay['ep']}-qism ochildi",
            parse_mode="HTML")
    except: pass

    movie = RAM.movies.get(pay["code"])
    if movie:
        idx = int(pay["ep"]) - 1
        eps = movie.get("episodes", [])
        if 0 <= idx < len(eps):
            await asyncio.gather(
                sm(context.bot, pay["user_id"],
                   "✅ <b>Admin chekingizni tasdiqladi!</b>\n\n"
                   f"🎬 Mana <b>{pay['ep']}-qism</b> videosini tomosha qiling 👇"),
                sv(context.bot, pay["user_id"], eps[idx],
                   f"<b>{movie.get('title')}</b>\nQism: {pay['ep']}", protect=True),
                return_exceptions=True,
            )
            async def update_pay_stats():
                movie.setdefault("views", {})
                movie["views"][pay["ep"]] = movie["views"].get(pay["ep"], 0) + 1
                RAM.stats["total_views"] = RAM.stats.get("total_views", 0) + 1
                await schedule_save()
            asyncio.create_task(update_pay_stats())
    else:
        await sm(context.bot, pay["user_id"], "✅ <b>Admin chekingizni tasdiqladi!</b>")


# ══════════════════════════════════════════════════════════
# CALLBACK: BALANS TO'LDIRISH TASDIQLASH (Admin)
# ══════════════════════════════════════════════════════════

async def cb_topup_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split("|", 1)
    if len(parts) != 2: return
    action, pid = parts
    pay = RAM.pending_payments.get(pid)
    if not pay or pay.get("type") != "topup":
        try: await q.answer("To'lov topilmadi!", show_alert=True)
        except: pass
        return

    if pay.get("status") in ("approved", "rejected"):
        try: await q.answer(f"Bu to'lov allaqachon {pay.get('status')}!", show_alert=True)
        except: pass
        return

    if action == "topup_no":
        pay["status"] = "rejected"
        await save_now()
        try:
            if q.message and q.message.caption is not None:
                await q.edit_message_caption(
                    (q.message.caption or "") + "\n\n<b>❌ Bekor qilindi</b>", parse_mode="HTML")
            elif q.message:
                await q.edit_message_text(
                    (q.message.text or "") + "\n\n<b>❌ Bekor qilindi</b>", parse_mode="HTML")
        except Exception as e:
            logger.warning(f"topup_no edit xato: {e}")
        await sm(context.bot, int(pay["user_id"]),
            f"❌ <b>Hisobni to'ldirish so'rovingiz rad etildi.</b>\n"
            f"Miqdor: <b>{pay['amount']:,} so'm</b>\n\n"
            f"Savollar uchun adminga murojaat qiling.")
        return

    # ✅ TASDIQLASH — balansga pul qo'shamiz
    pay["status"] = "approved"
    pay["approved_at"] = datetime.now().isoformat()
    uid_str = str(pay["user_id"])
    amount  = int(pay.get("amount", 0))
    u_data  = RAM.ensure_user(uid_str)
    u_data["balance"]     = int(u_data.get("balance") or 0) + amount
    u_data["topup_total"] = int(u_data.get("topup_total") or 0) + amount
    await save_now()

    try:
        # Rasm bo'lsa caption, matn bo'lsa text edit qilamiz
        if q.message and q.message.caption is not None:
            await q.edit_message_caption(
                (q.message.caption or "") + f"\n\n<b>✅ Tasdiqlandi</b> — {amount:,} so'm qo'shildi",
                parse_mode="HTML")
        elif q.message:
            await q.edit_message_text(
                (q.message.text or "") + f"\n\n<b>✅ Tasdiqlandi</b> — {amount:,} so'm qo'shildi",
                parse_mode="HTML")
    except Exception as e:
        logger.warning(f"topup edit xato: {e}")

    await sm(context.bot, int(pay["user_id"]),
        f"<blockquote>✅ <b>HISOBINGIZGA PUL QO'SHILDI!</b>\n\n"
        f"💵 Miqdor: <b>{amount:,} so'm</b>\n"
        f"💰 Joriy balans: <b>{u_data['balance']:,} so'm</b>\n\n"
        f"Endi balansdan pullik qismlarni tomosha qilishingiz mumkin! 🎬</blockquote>")


# ══════════════════════════════════════════════════════════
# CALLBACK: ADMIN JAVOB
# ══════════════════════════════════════════════════════════

async def cb_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split("|", 1)
    if len(parts) != 2: return
    _, uid_str = parts
    try:
        context.user_data["reply_to"] = int(uid_str)
        await q.message.reply_text(f"<code>{uid_str}</code> ga xabar yozing.", parse_mode="HTML")
    except Exception as e:
        logger.error(f"cb_reply xato: {e}")


# ══════════════════════════════════════════════════════════
# ADMIN RESERVED TEXTS
# ══════════════════════════════════════════════════════════

def _get_admin_reserved_texts() -> set:
    keys = [
        "kino_joy", "qism_qosh", "pullik", "stat", "kanal_post",
        "maj_kanal", "karta", "ilova", "emoji_soz", "asosiy",
        "boshqarish", "broadcast", "kino_uch", "yordam", "install",
        "barcha_kino", "kino_kanal_set",
        "premium_ber", "start_xab", "balans",
    ]
    result = set()
    for k in keys:
        v = bt(k)
        if v:
            result.add(v)
            result.add(strip_emoji_prefix(v))
    return result


# ══════════════════════════════════════════════════════════
# TEXT HANDLER
# ══════════════════════════════════════════════════════════

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_duplicate_update(update): return
    user = update.effective_user
    uid  = user.id
    msg  = update.message
    text = (msg.text or "").strip()

    # ── Bloklangan foydalanuvchi — hech narsa qilmaymiz ──
    if is_blocked_user(uid) and not is_any_admin(uid):
        await context.bot.send_message(
            chat_id=uid,
            text="🚫 <b>Siz admin tomonidan bloklangansiz.</b>",
            parse_mode="HTML"
        )
        return

    # ── 0. Hisobni to'ldirish — miqdor kiritish (ADMIN STATE DAN OLDIN) ──
    if context.user_data.get("admin_state") == "topup_amount":
        if not text.strip().isdigit() or int(text.strip()) <= 0:
            await sm(context.bot, uid,
                "❌ Faqat musbat <b>raqam</b> kiriting (masalan: 10000):")
            return
        amount = int(text.strip())
        if amount < 1000:
            await sm(context.bot, uid,
                "❌ <b>Minimal to'ldirish miqdori — 1 000 so'm!</b>\n\n"
                "Kamida <b>1 000 so'm</b> kiriting:")
            return
        context.user_data.pop("admin_state", None)

        # ══════════════════════════════════════════════════════
        # ⚡ TO'QNASHUV TEKSHIRUVI — bir vaqtda xuddi shu miqdor
        # ══════════════════════════════════════════════════════
        # Hozirgi barcha pending to'lovlar miqdorlarini yig'amiz
        # (faqat BOSHQA foydalanuvchilarning pending to'lovlari)
        busy_amounts: set = set()
        for _pid, _pay in RAM.pending_payments.items():
            if (
                _pay.get("type") == "topup_checkcard"
                and _pay.get("cc_status") == "pending"
                and str(_pay.get("user_id")) != str(uid)
            ):
                _amt = _pay.get("amount")
                if _amt:
                    busy_amounts.add(int(_amt))

        # Agar konflikt bo'lsa — noyob miqdor topamiz
        conflict_amount = None
        final_amount = amount
        if amount in busy_amounts:
            conflict_amount = amount
            candidate = amount + 1
            while candidate in busy_amounts:
                candidate += 1
                if candidate > amount + 200:
                    # 200 dan oshib ketsa — katta offset qo'shamiz
                    import random as _random
                    candidate = amount + _random.randint(201, 500)
                    break
            final_amount = candidate

        # Foydalanuvchini xabardor qilish — agar miqdor o'zgartirildisa
        if conflict_amount is not None:
            await sm(context.bot, uid,
                f"⚠️ <b>Diqqat! Miqdor o'zgartirildi.</b>\n\n"
                f"Ayni paytda boshqa foydalanuvchi tomonidan "
                f"<b>{conflict_amount:,} so'm</b> to'lov qilinmoqda.\n\n"
                f"To'lovlar aralashib ketmasligi uchun siz aynan:\n\n"
                f"💵 <b>{final_amount:,} so'm</b> to'lang!\n\n"
                f"<i>Miqdor avtomatik tanlandi — o'zgartirmang.</i>")
        # ══════════════════════════════════════════════════════

        # Foydalanuvchining o'zining eski pending to'lovini bekor qilamiz
        for pid_old, pay_old in list(RAM.pending_payments.items()):
            if (pay_old.get("type") == "topup_checkcard"
                    and str(pay_old.get("user_id")) == str(uid)
                    and pay_old.get("cc_status") == "pending"):
                old_order = pay_old.get("cc_order")
                if old_order:
                    await asyncio.to_thread(checkcard_cancel_payment, old_order)
                RAM.pending_payments.pop(pid_old, None)

        # ── CheckCard API orqali to'lov yaratish ──
        wait_msg = await sm(context.bot, uid, "⏳ To'lov yaratilmoqda...")
        order_id = f"t{uid}{int(time.time())}"
        result = await asyncio.to_thread(checkcard_create_payment, final_amount, order_id)

        # API xato qaytarsa — qayta urinish
        if result.get("status") == "error" and "pending" in (result.get("message", "")).lower():
            order_id2 = f"t{uid}{int(time.time())}r"
            result = await asyncio.to_thread(checkcard_create_payment, final_amount, order_id2)

        try:
            await context.bot.delete_message(chat_id=uid, message_id=wait_msg.message_id)
        except Exception:
            pass

        if result.get("status") != "success":
            card = RAM.card_number or "Admin karta raqamini o'rnatmagan"
            context.user_data["pending_topup"] = {"user_id": uid, "amount": final_amount}
            await sm(context.bot, uid,
                f"⚠️ <b>Avtomatik to'lov tizimi vaqtincha ishlamayapti.</b>\n\n"
                f"💵 Miqdor: <b>{final_amount:,} so'm</b>\n\n"
                f"Quyidagi kartaga pul o'tkaring:\n"
                f"💳 <code>{card}</code>\n\n"
                f"O'tkazmadan so'ng <b>chek rasmini</b> yuboring 👇",
                topup_sent_kb(card, final_amount))
            return

        cc_order = result.get("order")
        pid = f"topup_{uid}_{int(time.time())}"
        RAM.pending_payments[pid] = {
            "type":           "topup_checkcard",
            "user_id":        uid,
            "amount":         final_amount,       # CheckCard ga yuborilgan haqiqiy miqdor
            "original_amount": amount,            # Foydalanuvchi kiritgan asl miqdor
            "cc_order":       cc_order,
            "cc_status":      "pending",
            "created_at":     datetime.now().isoformat(),
        }
        await schedule_save()

        card = RAM.card_number or "Karta raqami o'rnatilmagan"
        copy_row = []
        if card and card != "Karta raqami o'rnatilmagan":
            copy_row.append({"text": bt("karta_nusxa"), "copy_text": {"text": str(card)}})
        copy_row.append({"text": bt("miqdor_nusxa"), "copy_text": {"text": str(final_amount)}})
        kb_rows = []
        if copy_row:
            kb_rows.append(copy_row)
        kb_rows.append([ibtn("❌ Bekor qilish", data=f"cc_cancel|{pid}", style="danger")])
        topup_auto_kb = InlineKeyboardMarkup(kb_rows)

        card_line = f"<blockquote>💳 <b>To'lov uchun karta:</b> <code>{card}</code></blockquote>\n" if card and card != "Karta raqami o'rnatilmagan" else ""

        # Agar miqdor o'zgartirildisa — alohida ogohlantirish
        if conflict_amount is not None:
            amount_line = (
                f"💵 To'lanadigan miqdor: <b>{final_amount:,} so'm</b>\n"
                f"<i>(Siz {amount:,} so'm kiritgandingiz, lekin boshqa foydalanuvchi "
                f"bilan to'qnashuv bo'lgani uchun {final_amount:,} so'm belgilandi)</i>\n"
            )
        else:
            amount_line = f"💵 Miqdor: <b>{final_amount:,} so'm</b>\n"

        sent_msg = await sm(context.bot, uid,
            f"✅ <b>To'lov yaratildi!</b>\n\n"
            f"{amount_line}"
            f"📋 Order ID: <code>{cc_order}</code>\n\n"
            f"{card_line}"
            f"<blockquote>🚨 <b>Diqqat! Yuqoridagi karta raqamiga aynan shu miqdorni o'tkazing!</b></blockquote>\n"
            f"<blockquote>⚠️ <b>DIQQAT:</b> {final_amount:,} so'mdan kam yoki ko'p tashlasangiz to'lov qabul qilinmaydi!</blockquote>\n"
            f"<i>To'lovni amalga oshirgach bot avtomatik ravishda tekshirib, balansingizni to'ldiradi.</i>\n\n"
            f"⏱ To'lov <b>5 daqiqa</b> ichida amalga oshirilishi kerak.",
            topup_auto_kb)
        # To'lov xabarini keyinchalik o'chirish uchun message_id saqlaymiz
        topup_msg_id = sent_msg.message_id if sent_msg else None

        if context.application.job_queue:
            context.application.job_queue.run_repeating(
                _checkcard_poll_job,
                interval=15,
                first=15,
                data={"pid": pid, "uid": uid, "order": cc_order, "amount": final_amount, "tries": 0, "topup_msg_id": topup_msg_id},
                name=f"cc_poll_{pid}",
            )
        return

    # ── 1. editing_btn_key ─────────────────────────────
    if is_any_admin(uid) and context.user_data.get("editing_btn_key"):
        key = context.user_data.pop("editing_btn_key")
        if not text:
            context.user_data["editing_btn_key"] = key
            await sm(context.bot, uid, "Bo'sh bo'lmasin. Qayta yuboring:")
            return
        custom_emoji_id  = extract_custom_emoji_id(msg)
        existing         = RAM.btn_texts.get(key) or DEFAULT_BTN.get(key, "")
        existing_label   = strip_emoji_prefix(existing) or DEFAULT_BTN.get(key, "")
        existing_emoji_p = extract_emoji_prefix(existing)
        if custom_emoji_id:
            new_text = existing_label
            EMOJI_IDS[key] = custom_emoji_id
            RAM.emoji_ids[key] = custom_emoji_id
            eid_info = f"\nCustom emoji ID: <code>{custom_emoji_id}</code>"
        elif is_only_emoji(text):
            new_emoji_p = (existing_emoji_p + text) if existing_emoji_p else text
            new_text    = f"{new_emoji_p} {existing_label}"
            EMOJI_IDS.pop(key, None)
            RAM.emoji_ids.pop(key, None)
            eid_info = ""
        else:
            new_text = text
            EMOJI_IDS.pop(key, None)
            RAM.emoji_ids.pop(key, None)
            eid_info = ""
        RAM.btn_texts[key] = new_text
        await save_now()
        eid = get_eid(key)
        if eid: eid_info = f"\nCustom emoji ID: <code>{eid}</code>"
        await sm(context.bot, uid,
            f"✅ <b>{BTN_LABELS.get(key, key)}</b> yangilandi!\n"
            f"Ko'rinish: <code>{new_text}</code>{eid_info}")
        context.user_data["emoji_menu"] = True
        await sm(context.bot, uid, "Tugmani tanlang:", emoji_menu_kb())
        return

    # ── 2. Broadcast tugma qo'shish ────────────────────
    if is_any_admin(uid) and context.user_data.get("bc_adding_btn"):
        stage = context.user_data["bc_adding_btn"]
        bc    = context.user_data.get("bc_msg", {})
        if stage == "text":
            context.user_data["bc_btn_name"]   = text
            context.user_data["bc_adding_btn"] = "url"
            await sm(context.bot, uid,
                f"Tugma nomi: <b>{text}</b>\n\nEndi tugma linkini kiriting (https:// bilan):")
        elif stage == "url":
            context.user_data["bc_btn_url"]    = text
            context.user_data["bc_adding_btn"] = "emoji"
            await sm(context.bot, uid,
                "Tugmaga premium emoji qo'shasizmi?\n\n"
                "• Yo'q bo'lsa — <b>0</b> deb yuboring\n"
                "• Bor bo'lsa — premium emoji yuboring (telegram premium emoji)")
        elif stage == "emoji":
            btn_text_val = context.user_data.pop("bc_btn_name", "Tugma")
            btn_url_val  = context.user_data.pop("bc_btn_url", text)
            color        = bc.pop("btn_color", "primary")
            emoji_id     = None
            if text.strip() != "0":
                emoji_id = extract_custom_emoji_id(update.message)
            context.user_data.pop("bc_adding_btn", None)
            new_btn = {"text": btn_text_val, "url": btn_url_val, "style": color}
            if emoji_id:
                new_btn["emoji_id"] = emoji_id
            bc.setdefault("buttons", []).append(new_btn)
            context.user_data["bc_msg"] = bc
            emoji_info = " (premium emoji bilan)" if emoji_id else ""
            await sm(context.bot, uid,
                f"✅ Tugma qo'shildi{emoji_info}!\n\n<b>Yana bita tugma qo'shasizmi?</b>",
                markup=bc_more_yesno_kb())
        return

    # ── 3. Emoji menyu ──────────────────────────────────
    if is_any_admin(uid) and context.user_data.get("emoji_menu"):
        if text == bt("orqaga") or strip_emoji_prefix(text) == strip_emoji_prefix(bt("orqaga")):
            context.user_data.pop("emoji_menu", None)
            context.user_data.pop("editing_btn_key", None)
            await sm(context.bot, uid, "Admin panel", admin_menu_kb(uid))
            return
        if text == bt("tiklash") or strip_emoji_prefix(text) == strip_emoji_prefix(bt("tiklash")):
            RAM.btn_texts = {}
            RAM.emoji_ids = {}
            EMOJI_IDS.clear()
            await save_now()
            await sm(context.bot, uid, "✅ Barcha tugmalar tiklandi!", emoji_menu_kb())
            return
        key = find_key_by_text(text)
        if key:
            cur        = RAM.btn_texts.get(key) or DEFAULT_BTN.get(key, "")
            eid        = get_eid(key)
            cur_emoji  = extract_emoji_prefix(cur)
            eid_info   = f"\nCustom emoji ID: <code>{eid}</code>" if eid else ""
            emoji_info = f"\nHozirgi emoji: <code>{cur_emoji}</code>" if cur_emoji else ""
            context.user_data["editing_btn_key"] = key
            await sm(context.bot, uid,
                f"<b>{BTN_LABELS.get(key, key)}</b>\n\n"
                f"Hozirgi matn: <code>{cur}</code>{eid_info}{emoji_info}\n\n"
                f"Yuboring:\n• Faqat emoji → qo'shiladi\n• Emoji+matn → yangilanadi\n"
                f"• Custom emoji → icon\n• Faqat matn → emoji o'chadi",
                emoji_single_action_kb(key))
        return

    # ── 4. Kanal boshqarish ─────────────────────────────
    if is_any_admin(uid) and context.user_data.get("channel_manage_menu"):
        ch_states = ("add_channel_username", "add_channel_title", "add_channel_url", "add_channel",
                     "add_simple_link_title", "add_simple_link_url")
        if context.user_data.get("admin_state") in ch_states:
            handled = await admin_state_handler(update, context, text)
            if handled: return
        if text in (bt("admin_panel"), bt("orqaga")) or strip_emoji_prefix(text) in (
            strip_emoji_prefix(bt("admin_panel")), strip_emoji_prefix(bt("orqaga"))
        ):
            context.user_data.pop("channel_manage_menu", None)
            context.user_data.pop("admin_state", None)
            await sm(context.bot, uid, "Admin panel", admin_menu_kb(uid))
            return
        if text == bt("kanal_qosh") or strip_emoji_prefix(text) == strip_emoji_prefix(bt("kanal_qosh")):
            context.user_data["admin_state"] = "add_channel_username"
            await sm(context.bot, uid,
                "➕ <b>Kanal qo'shish</b>\n\nKanal <b>username</b>ini kiriting:\n"
                "<i>Misol: @mykinochannel yoki https://t.me/mykinochannel</i>")
            return
        if text == bt("kanal_uch") or strip_emoji_prefix(text) == strip_emoji_prefix(bt("kanal_uch")):
            channels = RAM.channels
            simple   = RAM.simple_links or []
            if not channels and not simple:
                await sm(context.bot, uid, "❌ Hozircha kanal yoki havola yo'q.", channel_manage_kb())
                return
            await sm(context.bot, uid,
                f"{_channels_list_text()}\n\nO'chirmoqchi bo'lgan elementni tanlang 👇",
                channel_delete_inline_kb(channels, simple))
            return
        if text == bt("kanal_royxat") or strip_emoji_prefix(text) == strip_emoji_prefix(bt("kanal_royxat")):
            await sm(context.bot, uid, _channels_list_text(), channel_manage_kb())
            return
        if text == bt("oddiy_havola") or strip_emoji_prefix(text) == strip_emoji_prefix(bt("oddiy_havola")):
            context.user_data["admin_state"] = "add_simple_link_title"
            await sm(context.bot, uid,
                "🔗 <b>Oddiy havola qo'shish</b>\n\n"
                "Bu havola foydalanuvchilarga ko'rsatiladi, lekin bot obunani <b>tekshirmaydi</b>.\n\n"
                "Havola nomini kiriting (masalan: <code>Kino kanali</code>):")
            return
        if text == bt("soruvli_kanal") or strip_emoji_prefix(text) == strip_emoji_prefix(bt("soruvli_kanal")):
            context.user_data["admin_state"] = "add_soruvli_kanal"
            await sm(context.bot, uid,
                "📨 <b>So'rovli kanal qo'shish</b>\n\n"
                "Bu turdagi kanalda foydalanuvchi qo'shilish <b>so'rovi yuboradi</b>.\n"
                "Bot a'zolikni <b>avtomatik tasdiqlaydi</b>.\n\n"
                "⚠️ <b>Shart:</b> Bot kanalga <b>admin</b> bo'lishi va "
                "<b>\"A'zolikni boshqarish\"</b> huquqi bo'lishi kerak!\n\n"
                "Kanal username yoki invite linkini kiriting:\n"
                "<i>Misol: @mykanal yoki https://t.me/+xxxxx</i>")
            return
        return

    # ── 5. Admin reply_to ───────────────────────────────
    if is_any_admin(uid) and "reply_to" in context.user_data:
        target = context.user_data.pop("reply_to")
        try:
            await sm(context.bot, target, f"<b>Admin javobi:</b>\n{text}")
            await sm(context.bot, uid, "✅ Yuborildi!")
        except Exception as e:
            await sm(context.bot, uid, f"❌ Xato: {e}")
        return

    # ── 6. Admin holati + navigatsiya tekshirish ────────
    if is_any_admin(uid) and context.user_data.get("admin_state"):
        nav_key = _get_admin_nav_key(text)
        if nav_key:
            state = context.user_data.get("admin_state")
            clear_admin_state(context)
            if nav_key == "asosiy":
                await sm(context.bot, uid, "Asosiy menyu", main_menu_kb(is_admin=True))
            else:
                await sm(context.bot, uid, "<b>Admin panel</b>", admin_menu_kb(uid))
            logger.info(f"Admin holat '{state}' bekor → {nav_key}")
            return

    # ── 7. Admin state handler ──────────────────────────
    if is_any_admin(uid):
        state = context.user_data.get("admin_state")
        if state:
            handled = await admin_state_handler(update, context, text)
            if handled: return

    # ── 8. Admin tugmalarini aniqlash ───────────────────
    if is_any_admin(uid):
        all_admin_btn_keys = [
            "kino_joy", "qism_qosh", "pullik", "stat",
            "kanal_post", "maj_kanal", "karta", "ilova",
            "emoji_soz", "asosiy", "boshqarish", "broadcast", "kino_uch",
            "kino_kanal_set", "qism_tahrir", "admin_qosh",
            "premium_ber", "start_xab", "qism_och", "foydalanuvchi_blok",
            "tolovlar", "admin_lichka_set",
        ]
        # Ham to'liq matn, ham emoji-siz matn bilan tekshiramiz
        all_admin_btns = {}
        for k in all_admin_btn_keys:
            v = bt(k)
            if v:
                all_admin_btns[v] = k
                stripped = strip_emoji_prefix(v)
                if stripped and stripped != v:
                    all_admin_btns[stripped] = k
        text_stripped = strip_emoji_prefix(text)
        matched_key = all_admin_btns.get(text) or all_admin_btns.get(text_stripped)
        if matched_key:
            key = matched_key
            # Sub-admin perm check (super-admin har doim ruxsatli)
            if key in ADMIN_PERM_KEYS and not has_perm(uid, key):
                await sm(context.bot, uid, "⛔ Sizda bu amalga ruxsat yo'q.", admin_menu_kb(uid))
                return
            if key == "admin_qosh" and not is_super_admin(uid):
                await sm(context.bot, uid, "⛔ Faqat asosiy admin yangi admin qo'sha oladi.", admin_menu_kb(uid))
                return
            if key == "emoji_soz":
                clear_admin_state(context)
                context.user_data["emoji_menu"] = True
                await sm(context.bot, uid,
                    "<b>Tugma sozlamalari</b>\nO'zgartirmoqchi bo'lgan tugmani pastdan tanlang 👇",
                    emoji_menu_kb())
                return
            if key == "broadcast":
                context.user_data.pop("admin_state", None)
                context.user_data.pop("emoji_menu", None)
                context.user_data.pop("editing_btn_key", None)
                await sm(context.bot, uid,
                    "📢 <b>Barchaga xabar yuborish</b>\n\n"
                    "Xabar yuboring — matn, rasm yoki video.")
                context.user_data["admin_state"] = "broadcast_msg"
                return
            if key == "kino_uch":
                context.user_data.pop("emoji_menu", None)
                context.user_data["admin_state"] = "delete_movie_code"
                await sm(context.bot, uid, "🗑 <b>Kino o'chirish</b>\n\nKino kodini kiriting:")
                return
            if key == "admin_lichka_set":
                if not is_super_admin(uid):
                    await sm(context.bot, uid, "⛔ Faqat asosiy admin lichka o'rnatishi mumkin.", admin_menu_kb(uid))
                    return
                clear_admin_state(context)
                context.user_data.pop("emoji_menu", None)
                context.user_data["admin_state"] = "set_admin_lichka"
                cur_lichka = (RAM.settings.get("admin_lichka") or "").strip()
                cur_info = f"\n\nJoriy admin username: <code>@{cur_lichka}</code>" if cur_lichka else "\n\n<i>Hali o'rnatilmagan</i>"
                await sm(context.bot, uid,
                    f"👤 <b>Admin lichkasini qo'shish / o'chirish</b>{cur_info}\n\n"
                    f"Admin <b>@username</b>ini kiriting\n"
                    f"<i>O'chirish uchun <code>0</code> kiriting</i>")
                return
            context.user_data.pop("emoji_menu", None)
            context.user_data.pop("editing_btn_key", None)
            await admin_buttons(update, context, bt(key))
            return

    # ── 9. Asosiy tugmalar ──────────────────────────────
    def _main_btn(key):
        v = bt(key)
        if not v: return False
        return text == v or strip_emoji_prefix(text) == strip_emoji_prefix(v)

    if _main_btn("yordam"):
        await sm(context.bot, uid,
            "💬 <b>Yordam kerakmi?</b>\n\n"
            "Savol yoki muammoingizni <b>matn, rasm yoki video</b> ko'rinishida yuboring.\n"
            "Admin tez orada javob beradi! 🙂",
            help_kb(), reply_to_message_id=msg.message_id)
        context.user_data["awaiting_help"] = True
        return

    if _main_btn("install"):
        v_id = RAM.settings.get("install_video_id")
        if not v_id:
            await sm(context.bot, uid, "📹 Admin hali bot qo'llanma videosini joylamagan.")
            return
        cap = (RAM.settings.get("install_caption") or "").strip()
        if not cap:
            cap = "<b>Bot qo'llanma videosi</b>"
        # Bot haqida qo'shimcha ma'lumot — blockquote formatida
        cap += (
            "\n\n"
            "<blockquote>"
            "ℹ️ <b>Bot haqida:</b>\n\n"
            "🎬 Ushbu bot orqali kinolarni qulay tarzda tomosha qilishingiz mumkin.\n"
            "🔍 Kino kodini yuboring va video darhol keladi!\n"
            "💰 Balans tizimi orqali pullik qismlarni sotib olishingiz mumkin.\n"
            "📡 Yangi kinolardan xabardor bo'lish uchun kanalimizga obuna bo'ling.\n\n"
            "💳 <b>Balansni qanday to'ldirish:</b>\n\n"
            "1️⃣ Pastdagi <b>«Balans»</b> tugmasini bosing\n"
            "2️⃣ <b>«Hisobni to'ldirish»</b> tugmasini bosing\n"
            "3️⃣ To'ldirmoqchi bo'lgan <b>miqdorni</b> kiriting\n"
            "4️⃣ Bot sizga <b>karta raqamini</b> yuboradi — to'lang\n"
            "5️⃣ To'lov o'tishi bilan balans <b>avtomatik</b> hisobingizga qo'shiladi!\n\n"
            "⚠️ <b>Diqqat:</b> 1 so'm yoki undan ko'proq tashlasangiz — "
            "pul hisobingizga <b>tushmaydi!</b> Faqat <b>aniq miqdorni</b> to'lang."
            "</blockquote>"
        )
        # Admin lichkasi tugmasi
        admin_lichka = (RAM.settings.get("admin_lichka") or "").strip().lstrip("@")
        kb = None
        if admin_lichka:
            kb = ikb([[ibtn("👤 Admin lichkasi", url=f"https://t.me/{admin_lichka}",
                            style="danger", emoji_id=get_eid("admin_lichka_set"))]])
        await sv(context.bot, uid, v_id, cap, kb)
        return

    if _main_btn("barcha_kino"):
        movies = RAM.movies
        if not movies:
            await sm(context.bot, uid,
                "🎬 <b>Hozircha hech qanday kino qo'shilmagan.</b>\n\nKino qo'shilganda bu yerda ko'rinadi! 📽")
            return
        await _send_kino_list_page(context.bot, uid, page=0)
        return

    if _main_btn("balans"):
        u_data = RAM.ensure_user(uid)
        user_obj = update.effective_user
        balance   = int(u_data.get("balance") or 0)
        topup_tot = int(u_data.get("topup_total") or 0)
        name      = user_obj.full_name or "Noma'lum"
        txt = (
            f"💰 <b>Balansingiz</b>\n\n"
            f"👤 Ism: <b>{name}</b>\n"
            f"🆔 ID: <code>{uid}</code>\n\n"
            f"💵 Joriy balans: <b>{balance:,} so'm</b>\n"
            f"📥 Jami kiritilgan: <b>{topup_tot:,} so'm</b>"
        )
        await sm(context.bot, uid, txt, balans_kb())
        return

    # ── 10. Yordam so'rovi ──────────────────────────────
    if context.user_data.get("awaiting_help"):
        context.user_data.pop("awaiting_help", None)
        cap = (f"<b>Yordam so'rovi</b>\n{user.full_name} (@{user.username or '-'})\n"
               f"<code>{uid}</code>\n\n{text}")
        await sm(context.bot, ADMIN_ID, cap, reply_admin_kb(uid))
        await sm(context.bot, uid, "✅ Xabaringiz adminga yuborildi!")
        return

    if context.user_data.get("awaiting_check"):
        await sm(context.bot, uid, "Iltimos, chek <b>rasmini</b> yuboring.")
        return

    # ── 11. Kino kodi qidirish (RAMdan — tez!) ──────────
    code, matches = find_movie_code(text)
    if code:
        ns = await check_subscription(uid, context.bot)
        if ns:
            context.user_data["pending_code"] = code
            await sm(context.bot, uid,
                "Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:",
                subscription_kb(ns, simple_links=RAM.simple_links))
            return
        await send_movie_menu(update, context, code)
    elif matches:
        await sm(context.bot, uid,
            "🔎 Bir nechta kino topildi. Kerakli kino <b>kodini</b> yuboring:\n\n" +
            movie_suggestions_text(matches))
    else:
        if not is_any_admin(uid):
            # Avval "qidirilmoqda" xabari chiqaramiz
            wait_msg = await sm(context.bot, uid,
                f"🔍 <b>Qidirilmoqda:</b> <code>{text}</code> ⏳")

            # Kengaytirilgan qidiruv — har bir so'z alohida, qisman moslik
            def _deep_search(query_text):
                words = query_text.strip().split()
                found = []
                seen = set()
                # 1) Har bir so'z bo'yicha find_movie_code
                for word in words:
                    if len(word) < 2:
                        continue
                    _, wm = find_movie_code(word)
                    for c in wm:
                        if c not in seen:
                            seen.add(c)
                            found.append(c)
                # 2) Title ichidagi qisman moslik
                q_norm = _norm_search_text(query_text)
                q_words = [w for w in q_norm.split() if len(w) >= 2]
                for c, movie in RAM.movies.items():
                    if c in seen:
                        continue
                    title = movie.get("title", c) if isinstance(movie, dict) else c
                    title_norm = _norm_search_text(title)
                    if q_words and any(w in title_norm for w in q_words):
                        found.append(c)
                        seen.add(c)
                return found[:8]

            deep_matches = _deep_search(text)

            # Agar hali ham topilmasa — 2 soniya kutib, RAMdan qayta urinish
            # (baza hali to'liq yuklanmagan bo'lishi mumkin)
            if not deep_matches and len(RAM.movies) == 0:
                await asyncio.sleep(2)
                deep_matches = _deep_search(text)

            try:
                await context.bot.delete_message(chat_id=uid, message_id=wait_msg.message_id)
            except Exception:
                pass

            if deep_matches:
                await sm(context.bot, uid,
                    f"🔎 <b>\"{text}\"</b> bo'yicha topilganlar:\n\n" +
                    movie_suggestions_text(deep_matches) +
                    "\n\n👆 Kerakli kino <b>kodini</b> yuboring:")
            elif not RAM.loaded:
                # Baza hali yuklanmagan — foydalanuvchiga qayta urinishni so'raymiz
                await sm(context.bot, uid,
                    "⏳ <b>Baza yuklanmoqda...</b>\n\n"
                    f"Bir oz kutib, <code>{text}</code> kodini qayta yuboring.")
            else:
                # Baza to'liq yuklangan, lekin kino yo'q
                await sm(context.bot, uid,
                    f"❌ <b>\"{text}\"</b> — bazada bunday kino topilmadi.\n\n"
                    "📋 Barcha kinolarni ko'rish uchun 👇",
                    main_menu_kb(is_admin=False))


# ══════════════════════════════════════════════════════════
# ADMIN BUTTONS
# ══════════════════════════════════════════════════════════

async def admin_buttons(update, context, text: str):
    uid = update.effective_user.id

    def _btn_match(key):
        v = bt(key)
        if not v: return False
        return text == v or strip_emoji_prefix(text) == strip_emoji_prefix(v)

    if _btn_match("boshqarish"):
        context.user_data.pop("admin_state", None)
        context.user_data.pop("channel_manage_menu", None)
        await sm(context.bot, uid, "<b>Admin panel</b>", admin_menu_kb(uid))
        return

    if _btn_match("asosiy"):
        context.user_data.pop("admin_state", None)
        context.user_data.pop("channel_manage_menu", None)
        await sm(context.bot, uid, "Asosiy menyu", main_menu_kb(is_admin=True))
        return

    if _btn_match("stat"):
        u = len(RAM.users)
        m = len(RAM.movies)
        v = RAM.stats.get("total_views", 0)
        if DB_STATUS["ram_only"]:
            storage_line = (f"\n\n🔴 <b>Storage holati: RAM ONLY</b>\n"
                           f"JSONBlob ishlamayapti! Xatolar: <b>{DB_STATUS['fail_count']}</b>")
        elif DB_STATUS["last_save_ok"]:
            storage_line = f"\n\n🟢 <b>Storage holati: OK</b>\nOxirgi saqlash: <code>{DB_STATUS['last_save_ok']}</code>"
        else:
            storage_line = "\n\n🟡 <b>Storage holati: Tekshirilmagan</b>"
        await sm(context.bot, uid,
            f"<b>Statistika</b>\n\nFoydalanuvchilar: <b>{u}</b>\n"
            f"Kinolar: <b>{m}</b>\nJami ko'rishlar: <b>{v}</b>{storage_line}", stats_kb())
        return

    if _btn_match("tolovlar"):
        if not is_any_admin(uid): return
        try:
            await _send_tolovlar_page(context.bot, uid, page=0)
        except Exception as e:
            logger.error(f"tolovlar ko'rsatishda xato: {e}")
            await sm(context.bot, uid, "❌ To'lovlarni ko'rsatishda xato yuz berdi.", admin_menu_kb(uid))
        return

    if _btn_match("karta"):
        context.user_data["admin_state"] = "set_card"
        cur = RAM.card_number or "Kiritilmagan"
        await sm(context.bot, uid, f"Joriy karta: <code>{cur}</code>\n\nYangi karta raqamini yuboring:")
        return

    if _btn_match("kino_joy"):
        context.user_data["admin_state"] = "add_movie_code"
        context.user_data.pop("ep_movie_code", None)   # ✅ Eski qism state ni tozala
        context.user_data.pop("new_movie_code", None)
        context.user_data.pop("poster_code", None)
        await sm(context.bot, uid,
            "🎬 <b>Yangi kino qo'shish</b>\n\n"
            "Kino kodini kiriting (masalan: AVATAR yoki 001):")
        return

    if _btn_match("qism_qosh"):
        context.user_data["admin_state"] = "add_ep_code"
        context.user_data.pop("ep_movie_code", None)  # ✅ Eski kino kodini tozalaymiz
        movies = RAM.movies
        if movies:
            codes_list = "\n".join([f"• <code>{c}</code> — {m.get('title', c)}"
                                    for c, m in list(movies.items())[-10:]])
            await sm(context.bot, uid,
                f"📺 <b>Qism qo'shish</b>\n\nSo'nggi kinolar:\n{codes_list}\n\n"
                f"Qism qo'shmoqchi bo'lgan kino <b>kodini</b> kiriting:")
        else:
            await sm(context.bot, uid, "📺 <b>Qism qo'shish</b>\n\nKino kodini kiriting:")
        return

    if _btn_match("pullik"):
        context.user_data["admin_state"] = "set_price_code"
        context.user_data.pop("price_movie_code", None)
        context.user_data.pop("price_ep", None)
        await sm(context.bot, uid, "💰 <b>Qismni pullik qilish</b>\n\nKino <b>kodini</b> kiriting:")
        return

    if _btn_match("ilova"):
        context.user_data["admin_state"] = "set_install"
        await sm(context.bot, uid,
            "📹 <b>Bot qo'llanma videosi</b>\n\n"
            "Video yuboring. Video <b>captioniga</b> izoh yozsangiz — start menyuda "
            "shu izoh ko'rsatiladi.\n\n"
            "<i>Faqat video qabul qilinadi.</i>")
        return

    if _btn_match("premium_ber"):
        if not has_perm(uid, "premium_ber"):
            await sm(context.bot, uid, "⛔ Sizda bu huquq yo'q.", admin_menu_kb(uid))
            return
        context.user_data["admin_state"] = "premium_user"
        await sm(context.bot, uid,
            "💎 <b>Premium berish</b>\n\n"
            "Foydalanuvchi <b>username</b> (masalan @user) yoki <b>ID</b> raqamini yuboring:\n"
            "<i>Premium muddati tugaganda foydalanuvchi avtomatik oddiy holatga qaytadi.</i>")
        return

    if _btn_match("start_xab"):
        if not has_perm(uid, "start_xab"):
            await sm(context.bot, uid, "⛔ Sizda bu huquq yo'q.", admin_menu_kb(uid))
            return
        context.user_data["admin_state"] = "set_start_msg"
        context.user_data.pop("start_msg_photo_tmp", None)
        cur_t = RAM.settings.get("start_msg_text") or ""
        cur_p = "✅ bor" if RAM.settings.get("start_msg_photo") else "❌ yo'q"
        cur_info = (f"\n\nHozirgi rasm: {cur_p}\n"
                    f"Hozirgi matn: <code>{cur_t[:200]}</code>" if cur_t else
                    f"\n\nHozirgi rasm: {cur_p}\nHozirgi matn: <i>yo'q</i>")
        await sm(context.bot, uid,
            "🖼 <b>Start xabarni o'zgartirish</b>\n\n"
            "Quyidagilardan birini yuboring:\n\n"
            "1️⃣ <b>Rasm + caption</b> (matn) — rasm ham, matn ham o'zgaradi\n"
            "2️⃣ <b>Faqat rasm</b> — rasm o'zgaradi, eski matn saqlanib qoladi\n"
            "3️⃣ <b>Faqat matn</b> — matn o'zgaradi, rasm saqlanib qoladi\n"
            "4️⃣ <code>0</code> yuboring — hammasini tozalash\n\n"
            + cur_info)
        return

    if _btn_match("kino_kanal_set"):
        context.user_data["admin_state"] = "set_kino_kanal"
        cur_url  = RAM.settings.get("kino_kanal_url", "")
        cur_info = f"\n\nJoriy link: <code>{cur_url}</code>" if cur_url else "\n\n<i>Hali o'rnatilmagan</i>"
        await sm(context.bot, uid,
            f"📺 <b>Kino kodlari kanali linki</b>{cur_info}\n\n"
            f"Kanal linkini kiriting (masalan: https://t.me/mykinochannel)\n"
            f"<i>O'chirish uchun <code>0</code> kiriting</i>")
        return

    if _btn_match("maj_kanal"):
        context.user_data.pop("admin_state", None)
        context.user_data["channel_manage_menu"] = True
        await sm(context.bot, uid,
            f"📡 <b>Majburiy kanal boshqaruvi</b>\n\n{_channels_list_text()}\n\nNima qilmoqchisiz?",
            channel_manage_kb())
        return

    if _btn_match("kanal_post"):
        context.user_data["admin_state"] = "post_channel_code"
        movies = RAM.movies
        if movies:
            codes_list = "\n".join([f"• <code>{c}</code> — {m.get('title', c)}"
                                    for c, m in list(movies.items())[-10:]])
            await sm(context.bot, uid,
                f"📤 <b>Kanalga post</b>\n\nSo'nggi kinolar:\n{codes_list}\n\n"
                f"Post qilmoqchi bo'lgan kino <b>kodini</b> kiriting:")
        else:
            await sm(context.bot, uid, "Post qilmoqchi bo'lgan kino kodini kiriting:")
        return

    if _btn_match("admin_qosh"):
        if not is_super_admin(uid):
            await sm(context.bot, uid, "⛔ Faqat asosiy admin yangi admin qo'sha oladi.", admin_menu_kb(uid))
            return
        context.user_data["admin_state"] = "add_admin_id"
        cur = RAM.sub_admins or {}
        cur_list = "\n".join([f"• <code>{u}</code>" for u in cur.keys()]) or "<i>Hozircha yo'q</i>"
        await sm(context.bot, uid,
            "👮 <b>Admin qo'shish</b>\n\n"
            f"Hozirgi adminlar:\n{cur_list}\n\n"
            "Yangi admin uchun foydalanuvchi <b>ID</b> raqamini yuboring:\n"
            "<i>(ID o'chirish uchun: <code>-12345</code> — minus bilan ID)</i>")
        return

    if _btn_match("qism_tahrir"):
        context.user_data["admin_state"] = "edit_ep_code"
        context.user_data.pop("edit_ep_num", None)
        movies = RAM.movies
        if movies:
            codes_list = "\n".join([f"• <code>{c}</code> — {m.get('title', c)}"
                                    for c, m in list(movies.items())[-10:]])
            await sm(context.bot, uid,
                f"✏️ <b>Qismlarni tahrirlash</b>\n\nSo'nggi kinolar:\n{codes_list}\n\n"
                f"Tahrirlamoqchi bo'lgan kino <b>kodini</b> kiriting:")
        else:
            await sm(context.bot, uid, "✏️ <b>Qismlarni tahrirlash</b>\n\nKino kodini kiriting:")
        return

    if _btn_match("kino_uch"):
        context.user_data["admin_state"] = "delete_movie_code"
        await sm(context.bot, uid, "🗑 <b>Kino o'chirish</b>\n\nKino kodini kiriting:")
        return

    if _btn_match("qism_och"):
        if not has_perm(uid, "qism_och"):
            await sm(context.bot, uid, "⛔ Sizda bu huquq yo'q.", admin_menu_kb(uid))
            return
        context.user_data["admin_state"] = "qism_och_uid"
        await sm(context.bot, uid,
            "🔓 <b>Foydalanuvchiga qism ochish</b>\n\n"
            "Avval foydalanuvchi <b>ID</b> raqamini yuboring:\n"
            "<i>(Foydalanuvchi botga /start bosgan bo'lishi kerak)</i>")
        return

    if _btn_match("foydalanuvchi_blok"):
        if not has_perm(uid, "foydalanuvchi_blok"):
            await sm(context.bot, uid, "⛔ Sizda bu huquq yo'q.", admin_menu_kb(uid))
            return
        context.user_data["admin_state"] = "block_user_input"
        total_blocked = len(RAM.blocked_users or {})
        blocked_info = f"\n\n🔒 Hozir bloklangan: <b>{total_blocked} ta</b>" if total_blocked else ""
        await sm(context.bot, uid,
            f"🚫 <b>Foydalanuvchi bloklash / blokdan chiqarish</b>{blocked_info}\n\n"
            "Foydalanuvchi <b>ID raqami</b> yoki <b>@username</b>ini yuboring:\n"
            "<i>Misol: <code>123456789</code> yoki <code>@username</code></i>")
        return


# ══════════════════════════════════════════════════════════
# ADMIN STATE HANDLER
# ══════════════════════════════════════════════════════════

async def admin_state_handler(update, context, text: str) -> bool:
    state = context.user_data.get("admin_state")
    uid   = update.effective_user.id
    if not state: return False

    if state == "set_admin_lichka":
        val = text.strip().lstrip("@")
        if val == "0":
            RAM.settings["admin_lichka"] = ""
            import asyncio as _asyncio
            _asyncio.create_task(save_now())
            context.user_data.pop("admin_state", None)
            await sm(context.bot, uid, "✅ Admin lichkasi o'chirildi.", admin_menu_kb(uid))
        elif val:
            RAM.settings["admin_lichka"] = val
            import asyncio as _asyncio
            _asyncio.create_task(save_now())
            context.user_data.pop("admin_state", None)
            await sm(context.bot, uid,
                f"✅ Admin lichkasi saqlandi: <code>@{val}</code>\n\n"
                f"Endi «Qo'llanma video» tugmasida 👤 <b>Admin lichkasi</b> tugmasi ko'rinadi.",
                admin_menu_kb(uid))
        else:
            await sm(context.bot, uid, "⚠️ Username kiriting (masalan: @username) yoki o'chirish uchun <code>0</code>:")
        return True

    if state == "broadcast_msg":
        bc = {
            "type": "copy",
            "from_chat_id": update.message.chat_id,
            "message_id": update.message.message_id,
            "buttons": [],
        }
        context.user_data["bc_msg"] = bc
        context.user_data.pop("admin_state", None)
        await sm(context.bot, uid, "✅ Xabar qabul qilindi.\n\n<b>Tugmali xabar yuborasizmi?</b>",
                 markup=bc_yesno_kb())
        return True

    if state == "delete_movie_code":
        code = text.upper().strip()
        if code not in RAM.movies:
            _, matches = find_movie_code(text)
            if matches:
                hint = "\n".join([f"• <code>{c}</code> — {RAM.movies.get(c,{}).get('title',c)}"
                                   for c in matches[:5]])
                await sm(context.bot, uid,
                    f"❌ <code>{code}</code> topilmadi.\n\nShunga o'xshash:\n{hint}\n\nTo'g'ri kodini kiriting:")
            else:
                await sm(context.bot, uid, f"❌ <code>{code}</code> kodli kino topilmadi. Qayta kiriting:")
            return True
        movie = RAM.movies[code]
        title = movie.get("title", code)
        eps   = movie.get("episodes", [])
        ep_lines = "\n".join([f"  {i+1}-qism" for i in range(len(eps))]) if eps else "  (qismlar yo'q)"
        context.user_data["del_movie_code"] = code
        context.user_data["admin_state"]    = "delete_movie_ep"
        await sm(context.bot, uid,
            f"🎬 <b>{title}</b>  |  <code>{code}</code>\n"
            f"📺 Qismlar soni: <b>{len(eps)} ta</b>\n\n{ep_lines}\n\n"
            f"Qaysi qismni o'chirmoqchisiz?\n"
            f"• Raqam kiriting (masalan: <code>3</code>)\n"
            f"• Barcha qismlar: <code>hammasi</code>\n"
            f"• Butun kino: <code>kino</code>")
        return True

    if state == "delete_movie_ep":
        code  = context.user_data.get("del_movie_code")
        movie = RAM.movies.get(code) if code else None
        if not movie:
            await sm(context.bot, uid, "❌ Kino topilmadi. /start bosing.")
            context.user_data.pop("admin_state", None)
            context.user_data.pop("del_movie_code", None)
            return True
        title = movie.get("title", code)
        eps   = movie.get("episodes", [])
        val   = text.strip().lower()

        if val == "kino":
            RAM.del_movie(code)
            save_ok = await save_now()
            context.user_data.pop("admin_state", None)
            context.user_data.pop("del_movie_code", None)
            storage_warn = "\n⚠️ <i>Faqat RAMda saqlandi!</i>" if not save_ok else ""
            await sm(context.bot, uid,
                f"✅ <b>{title}</b> (<code>{code}</code>) butunlay o'chirildi!\n"
                f"Qolgan kinolar: <b>{len(RAM.movies)} ta</b>{storage_warn}",
                admin_menu_kb(uid))
            return True

        if val == "hammasi":
            RAM.movies[code]["episodes"] = []
            RAM.movies[code]["prices"]   = {}
            save_ok = await save_now()
            context.user_data.pop("admin_state", None)
            context.user_data.pop("del_movie_code", None)
            storage_warn = "\n⚠️ <i>Faqat RAMda saqlandi!</i>" if not save_ok else ""
            await sm(context.bot, uid,
                f"✅ <b>{title}</b> kinoning barcha qismlari o'chirildi!{storage_warn}",
                admin_menu_kb(uid))
            return True

        if val.isdigit():
            ep_num = int(val)
            if ep_num < 1 or ep_num > len(eps):
                await sm(context.bot, uid,
                    f"❌ <b>{ep_num}</b>-qism mavjud emas. 1–{len(eps)} oralig'ida kiriting:")
                return True
            idx = ep_num - 1
            RAM.movies[code]["episodes"].pop(idx)
            old_prices = movie.get("prices", {})
            new_prices = {}
            for k, v in old_prices.items():
                try:
                    k_int = int(k)
                    if k_int < ep_num:   new_prices[k] = v
                    elif k_int > ep_num: new_prices[str(k_int - 1)] = v
                except: pass
            RAM.movies[code]["prices"] = new_prices
            save_ok = await save_now()
            context.user_data.pop("admin_state", None)
            context.user_data.pop("del_movie_code", None)
            storage_warn = "\n⚠️ Faqat RAMda saqlandi!" if not save_ok else ""
            await sm(context.bot, uid,
                f"✅ <b>{title}</b> — <b>{ep_num}-qism</b> o'chirildi!\n"
                f"Qolgan qismlar: <b>{len(RAM.movies[code]['episodes'])} ta</b>{storage_warn}",
                admin_menu_kb(uid))
            return True

        await sm(context.bot, uid,
            "❌ Noto'g'ri. Qism raqami, <code>hammasi</code> yoki <code>kino</code> kiriting:")
        return True

    if state == "set_card":
        RAM.card_number = text
        await schedule_save()
        context.user_data.pop("admin_state", None)
        await sm(context.bot, uid, f"✅ Karta saqlandi: <code>{text}</code>", admin_menu_kb(uid))
        return True

    if state == "set_kino_kanal":
        context.user_data.pop("admin_state", None)
        if text.strip() == "0":
            RAM.settings["kino_kanal_url"] = ""
            await schedule_save()
            await sm(context.bot, uid, "✅ Kino kodlari kanali linki <b>o'chirildi</b>!", admin_menu_kb(uid))
        elif text.startswith("http"):
            RAM.settings["kino_kanal_url"] = text.strip()
            await schedule_save()
            await sm(context.bot, uid,
                f"✅ <b>Kino kodlari kanali</b> linki saqlandi!\nLink: <code>{text.strip()}</code>",
                admin_menu_kb(uid))
        else:
            await sm(context.bot, uid,
                "❌ Link noto'g'ri. <code>https://</code> bilan boshlanishi kerak.\n"
                "Qayta kiriting yoki o'chirish uchun <code>0</code> yuboring:")
            context.user_data["admin_state"] = "set_kino_kanal"
        return True

    if state == "add_movie_code":
        code = text.upper().strip()
        if not code:
            await sm(context.bot, uid, "❌ Kod bo'sh bo'lmasin. Qayta kiriting:")
            return True
        if len(code) > 30:
            await sm(context.bot, uid, "❌ Kod 30 ta belgidan oshmasin.")
            return True
        reserved = _get_admin_reserved_texts()
        if text in reserved or text.startswith("/"):
            await sm(context.bot, uid, "❌ Bu kino kodi emas. To'g'ri kod kiriting:")
            return True
        if code in RAM.movies:
            movie = RAM.movies[code]
            await sm(context.bot, uid,
                f"⚠️ <code>{code}</code> kodi allaqachon mavjud!\n\n"
                f"🎬 Nomi: <b>{movie.get('title', code)}</b>\n"
                f"📺 Qismlar: <b>{len(movie.get('episodes', []))} ta</b>\n\n"
                f"Boshqa kod kiriting.")
            return True
        context.user_data["new_movie_code"] = code
        context.user_data["admin_state"]    = "add_movie_title"
        await sm(context.bot, uid, f"✅ Kod: <code>{code}</code>\n\nEndi kino <b>nomini</b> kiriting:")
        return True

    if state == "add_movie_title":
        reserved = _get_admin_reserved_texts()
        if text in reserved or text.startswith("/"):
            await sm(context.bot, uid,
                "❌ Bu kino nomi emas — admin tugmasi bosildi.\n\n"
                f"Kino nomini kiriting (masalan: <b>Avatar 2</b>):")
            return True
        code = context.user_data.get("new_movie_code")
        if not code:
            await sm(context.bot, uid, "❌ Xatolik. Qaytadan boshlang.")
            context.user_data.pop("admin_state", None)
            return True
        if not text.strip():
            await sm(context.bot, uid, "❌ Nom bo'sh bo'lmasin.")
            return True
        now        = datetime.now().strftime("%d.%m.%Y %H:%M")
        title_html = text_with_premium_emojis(update.message) or text
        # ❗ Darhol RAMga yoz
        RAM.movies[code] = {
            "title": title_html,
            "episodes": [],
            "prices": {},
            "added_date": now,
            "added_at": time.time(),
            "poster_file_id": None,
        }
        # ❗ DARHOL bazaga ham saqla — kino yo'qolib qolmasin
        await save_now()

        context.user_data["admin_state"] = "add_movie_poster"
        context.user_data["poster_code"] = code
        await sm(context.bot, uid,
            f"✅ <b>{title_html}</b> kinosi RAMga qo'shildi!\n"
            f"Kod: <code>{code}</code>\n"
            f"Jami kinolar: <b>{len(RAM.movies)} ta</b>\n\n"
            f"📷 Kino posterini yuboring\n"
            f"<i>(poster yo'q bo'lsa <b>0</b> kiriting)</i>")
        return True

    if state == "add_movie_poster":
        code = context.user_data.pop("poster_code", None)
        context.user_data.pop("admin_state", None)
        context.user_data.pop("new_movie_code", None)
        if code and code in RAM.movies:
            await sm(context.bot, uid,
                f"✅ Poster o'tkazib yuborildi.\nKod: <code>{code}</code>\n\nQism qo'shishingiz mumkin 👇",
                movie_added_kb(code))
        else:
            await sm(context.bot, uid, "✅ Kino qo'shildi!", admin_menu_kb(uid))
        return True

    if state == "add_ep_code":
        code = text.upper().strip()
        if not code:
            await sm(context.bot, uid, "❌ Kod kiriting:")
            return True
        reserved = _get_admin_reserved_texts()
        if text in reserved or text.startswith("/"):
            await sm(context.bot, uid, "❌ Bu kino kodi emas. Kino kodini kiriting:")
            return True
        if code not in RAM.movies:
            _, matches = find_movie_code(text)
            if matches:
                hint = "\n".join([f"• <code>{c}</code> — {RAM.movies.get(c,{}).get('title',c)}"
                                   for c in matches[:5]])
                await sm(context.bot, uid,
                    f"❌ <code>{code}</code> topilmadi.\n\nShunga o'xshash:\n{hint}\n\nTo'g'ri kodini kiriting:")
            else:
                movies_list = RAM.movies
                if movies_list:
                    last5 = "\n".join([f"• <code>{c}</code> — {m.get('title',c)}"
                                       for c, m in list(movies_list.items())[-5:]])
                    await sm(context.bot, uid,
                        f"❌ <code>{code}</code> kodli kino topilmadi.\n\n"
                        f"So'nggi kinolar:\n{last5}\n\nQayta kino kodini kiriting:")
                else:
                    await sm(context.bot, uid,
                        f"❌ <code>{code}</code> topilmadi.\n⚠️ Hali kino qo'shilmagan!")
                    context.user_data.pop("admin_state", None)
            return True
        movie  = RAM.movies[code]
        ep_num = len(movie.get("episodes", [])) + 1
        context.user_data["ep_movie_code"] = code
        context.user_data["admin_state"]   = "add_ep_video"
        await sm(context.bot, uid,
            f"🎬 <b>{movie.get('title', code)}</b>\n"
            f"Kod: <code>{code}</code>\n"
            f"Hozirgi qismlar: <b>{len(movie.get('episodes', []))} ta</b>\n\n"
            f"📹 <b>{ep_num}-qism</b> uchun video yuboring:")
        return True

    if state == "add_ep_video":
        # Video kutilayapti — oddiy matn kelsa xabar beramiz
        if context.user_data.get("awaiting_check") or context.user_data.get("awaiting_help"):
            return False
        code = context.user_data.get("ep_movie_code")
        # ✅ Admin menyu tugmasi bosilsa — state ni tozalab, admin menyuga qaytaramiz
        if _is_admin_nav_button(text):
            clear_admin_state(context)
            return False  # admin_buttons ga o'tsin
        if code and code in RAM.movies:
            ep_num = len(RAM.movies[code].get("episodes", [])) + 1
            movie  = RAM.movies[code]
            await sm(context.bot, uid,
                f"⚠️ Matn emas — <b>video</b> yuboring!\n"
                f"Kino: <b>{movie.get('title', code)}</b>\n"
                f"📹 <b>{ep_num}-qism</b> kutilmoqda...\n\n"
                f"<i>Tugatish uchun «Tugatish va bazaga saqlash» tugmasini bosing.</i>",
                movie_added_kb(code))
        else:
            clear_admin_state(context)
            await sm(context.bot, uid,
                "❌ Kino kodi yo'qoldi. Qaytadan «Qism qo'shish» tugmasini bosing.",
                admin_menu_kb(uid))
        return True

    if state == "set_price_code":
        reserved = _get_admin_reserved_texts()
        if text in reserved or text.startswith("/"):
            await sm(context.bot, uid, "❌ Bu kino kodi emas. Kino kodini kiriting:")
            return True
        code = text.upper().strip()
        if code not in RAM.movies:
            _, matches = find_movie_code(text)
            if matches:
                hint = "\n".join([f"• <code>{c}</code> — {RAM.movies.get(c,{}).get('title',c)}"
                                   for c in matches[:5]])
                await sm(context.bot, uid,
                    f"❌ Topilmadi.\n\nShunga o'xshash:\n{hint}\n\nTo'g'ri kodini kiriting:")
            else:
                await sm(context.bot, uid, f"❌ <code>{code}</code> topilmadi. Qayta kiriting:")
            return True
        movie  = RAM.movies[code]
        eps    = movie.get("episodes", [])
        prices = movie.get("prices", {})
        if not eps:
            await sm(context.bot, uid,
                f"⚠️ <b>{movie.get('title', code)}</b> kinoda hali qism yo'q.")
            context.user_data.pop("admin_state", None)
            return True
        ep_list = _build_ep_price_list(code, eps, prices)
        context.user_data["price_movie_code"] = code
        context.user_data["admin_state"]      = "set_price_ep"
        await sm(context.bot, uid,
            f"💰 <b>{movie.get('title', code)}</b>\n\n{ep_list}\n\n"
            f"Qism <b>raqamini</b> kiriting (1 dan {len(eps)} gacha):\n"
            f"<i>Bir nechta qism uchun: <code>1+20</code> (1 dan 20 gacha)</i>")
        return True

    if state == "set_price_ep":
        code = context.user_data.get("price_movie_code")
        if not code or code not in RAM.movies:
            await sm(context.bot, uid, "❌ Xatolik. Kino kodini qayta kiriting:")
            context.user_data["admin_state"] = "set_price_code"
            context.user_data.pop("price_movie_code", None)
            return True
        movie = RAM.movies[code]
        eps   = movie.get("episodes", [])
        raw_text = text.strip()

        # ── Diapazon formati: "1+20" yoki "1-20" ──────────────
        range_match = re.match(r'^(\d+)[+\-](\d+)$', raw_text)
        if range_match:
            start_ep = int(range_match.group(1))
            end_ep   = int(range_match.group(2))
            if start_ep < 1 or end_ep > len(eps) or start_ep > end_ep:
                await sm(context.bot, uid,
                    f"❌ Noto'g'ri diapazon. 1–{len(eps)} orasida kiriting.\n"
                    f"Masalan: <code>1+20</code>")
                return True
            # Narx so'raymiz — diapazonni saqlaymiz
            context.user_data["price_ep"]       = None   # diapazon uchun None
            context.user_data["price_ep_range"]  = (start_ep, end_ep)
            context.user_data["admin_state"]     = "set_price_amount"
            prices = movie.get("prices", {})
            # Diapazondagi hozirgi narxlarni ko'rsatamiz
            paid_eps   = [str(i) for i in range(start_ep, end_ep+1) if prices.get(str(i))]
            free_eps   = [str(i) for i in range(start_ep, end_ep+1) if not prices.get(str(i))]
            info_parts = []
            if paid_eps:   info_parts.append(f"💰 Pullik: {', '.join(paid_eps)}-qism")
            if free_eps:   info_parts.append(f"🆓 Bepul:  {', '.join(free_eps[:10])}{'...' if len(free_eps)>10 else ''}-qism")
            cur_info = "\n" + "\n".join(info_parts) if info_parts else ""
            await sm(context.bot, uid,
                f"💰 <b>{movie.get('title', code)}</b>\n"
                f"📺 <b>{start_ep}–{end_ep}-qismlar</b> ({end_ep-start_ep+1} ta){cur_info}\n\n"
                f"Yangi narxni kiriting (so'mda):\n<i>Bepul qilish uchun <code>0</code></i>")
            return True

        # ── Oddiy raqam: bitta qism ────────────────────────────
        if not raw_text.isdigit():
            await sm(context.bot, uid,
                f"❌ Faqat <b>raqam</b> kiriting (1 dan {len(eps)} gacha)\n"
                f"Yoki diapazon: <code>1+20</code>")
            return True
        ep_num = int(raw_text)
        if ep_num < 1 or ep_num > len(eps):
            await sm(context.bot, uid,
                f"❌ <b>{ep_num}</b>-qism mavjud emas. 1–{len(eps)} kiriting:")
            return True
        context.user_data["price_ep"]       = str(ep_num)
        context.user_data.pop("price_ep_range", None)
        context.user_data["admin_state"]    = "set_price_amount"
        cur_price = movie.get("prices", {}).get(str(ep_num))
        cur_info  = f"\nHozirgi narx: <b>{cur_price} so'm</b>" if cur_price else "\nHozir: <b>bepul</b>"
        await sm(context.bot, uid,
            f"💰 <b>{movie.get('title', code)}</b>\n<b>{ep_num}-qism</b>{cur_info}\n\n"
            f"Yangi narxni kiriting (so'mda):\n<i>Bepul qilish uchun <code>0</code></i>")
        return True

    # ── 🚫 Foydalanuvchi bloklash: ID yoki username qabul qilish ──
    if state == "block_user_input":
        if not has_perm(uid, "foydalanuvchi_blok"):
            clear_admin_state(context)
            await sm(context.bot, uid, "⛔ Ruxsat yo'q.", admin_menu_kb(uid))
            return True
        raw = text.strip().lstrip("@")
        target_uid = None
        target_name = None
        target_uname = None
        # ID raqami bo'yicha qidirish
        if raw.isdigit():
            target_uid = raw
            u = RAM.get_user(target_uid)
            target_name = (u or {}).get("name") or f"ID: {raw}"
            target_uname = (u or {}).get("username") or ""
        else:
            # Username bo'yicha RAM.users dan qidirish
            uname_low = raw.lower()
            for k, v in RAM.users.items():
                if (v.get("username") or "").lower() == uname_low:
                    target_uid = k
                    target_name = v.get("name") or raw
                    target_uname = v.get("username") or ""
                    break
        if not target_uid:
            await sm(context.bot, uid,
                "❌ Bunday foydalanuvchi topilmadi.\n"
                "<i>Foydalanuvchi avval botga /start bosgan bo'lishi kerak.</i>\n\n"
                "ID yoki @username ni qayta yuboring:")
            return True
        # Super-admini bloklashga ruxsat yo'q
        if int(target_uid) == ADMIN_ID:
            await sm(context.bot, uid, "⚠️ Asosiy adminni bloklash mumkin emas.")
            return True
        clear_admin_state(context)
        already_blocked = target_uid in (RAM.blocked_users or {})
        uname_str = f" (@{target_uname})" if target_uname else ""
        # Foydalanuvchi balansi
        u_data = RAM.ensure_user(target_uid)
        balance = int(u_data.get("balance") or 0)
        premium_until = float(u_data.get("premium_until") or 0)
        import time as _time
        premium_str = ""
        if premium_until > _time.time():
            from datetime import datetime as _dt
            prem_date = _dt.fromtimestamp(premium_until).strftime("%d.%m.%Y")
            premium_str = f"\n👑 Premium: <b>{prem_date} gacha</b>"
        if already_blocked:
            # Blokdan chiqarish + pul qo'shish tugmalari
            kb = ikb([
                [
                    ibtn("✅ Blokdan chiqarish", data=f"unblock_confirm|{target_uid}", style="success"),
                ],
                [
                    ibtn("➕ Pul qo'shish", data=f"admin_add_balance|{target_uid}", style="primary"),
                    ibtn("💸 Pul ayirish", data=f"admin_sub_balance|{target_uid}", style="danger"),
                ],
                [ibtn("❌ Bekor", data="block_cancel", style="danger")],
            ])
            await sm(context.bot, uid,
                f"🔒 <b>{target_name}</b>{uname_str}\n"
                f"🆔 <code>{target_uid}</code>\n"
                f"💰 Balans: <b>{balance:,} so'm</b>{premium_str}\n\n"
                f"Bu foydalanuvchi hozir <b>bloklangan</b>.\n"
                f"Blokdan chiqarasizmi yoki pul amallari?",
                kb)
        else:
            # Bloklash + pul qo'shish tugmalari
            kb = ikb([
                [
                    ibtn("🚫 Bloklash", data=f"block_confirm|{target_uid}", style="danger"),
                ],
                [
                    ibtn("➕ Pul qo'shish", data=f"admin_add_balance|{target_uid}", style="primary"),
                    ibtn("💸 Pul ayirish", data=f"admin_sub_balance|{target_uid}", style="danger"),
                ],
                [ibtn("❌ Bekor", data="block_cancel", style="primary")],
            ])
            await sm(context.bot, uid,
                f"👤 <b>{target_name}</b>{uname_str}\n"
                f"🆔 <code>{target_uid}</code>\n"
                f"💰 Balans: <b>{balance:,} so'm</b>{premium_str}\n\n"
                f"Bu foydalanuvchi bilan qanday amal bajarmoqchisiz?",
                kb)
        return True

    # ── Admin: pul qo'shish miqdori ──
    if state == "admin_add_balance_amount":
        if not is_any_admin(uid):
            clear_admin_state(context)
            return True
        target_uid = context.user_data.get("admin_balance_target")
        if not target_uid:
            clear_admin_state(context)
            return True
        if not text.strip().isdigit() or int(text.strip()) <= 0:
            await sm(context.bot, uid, "❌ Faqat musbat <b>raqam</b> kiriting:")
            return True
        amount = int(text.strip())
        u_data = RAM.ensure_user(target_uid)
        old_balance = int(u_data.get("balance") or 0)
        u_data["balance"] = old_balance + amount
        await save_now()
        clear_admin_state(context)
        context.user_data.pop("admin_balance_target", None)
        u = RAM.get_user(target_uid) or {}
        target_name = u.get("name") or f"ID: {target_uid}"
        await sm(context.bot, uid,
            f"✅ <b>Balans yangilandi!</b>\n\n"
            f"👤 {target_name} (<code>{target_uid}</code>)\n"
            f"💰 Oldingi: <b>{old_balance:,} so'm</b>\n"
            f"➕ Qo'shildi: <b>{amount:,} so'm</b>\n"
            f"💳 Yangi balans: <b>{u_data['balance']:,} so'm</b>",
            admin_menu_kb(uid))
        # Foydalanuvchiga xabar
        try:
            await context.bot.send_message(
                int(target_uid),
                f"✅ Balansingizga <b>{amount:,} so'm</b> qo'shildi!\n"
                f"💰 Joriy balansingiz: <b>{u_data['balance']:,} so'm</b>",
                parse_mode="HTML")
        except Exception:
            pass
        return True

    # ── Admin: pul ayirish miqdori ──
    if state == "admin_sub_balance_amount":
        if not is_any_admin(uid):
            clear_admin_state(context)
            return True
        target_uid = context.user_data.get("admin_balance_target")
        if not target_uid:
            clear_admin_state(context)
            return True
        if not text.strip().isdigit() or int(text.strip()) <= 0:
            await sm(context.bot, uid, "❌ Faqat musbat <b>raqam</b> kiriting:")
            return True
        amount = int(text.strip())
        u_data = RAM.ensure_user(target_uid)
        old_balance = int(u_data.get("balance") or 0)
        new_balance = max(0, old_balance - amount)
        u_data["balance"] = new_balance
        await save_now()
        clear_admin_state(context)
        context.user_data.pop("admin_balance_target", None)
        u = RAM.get_user(target_uid) or {}
        target_name = u.get("name") or f"ID: {target_uid}"
        actually_removed = old_balance - new_balance
        await sm(context.bot, uid,
            f"✅ <b>Balans yangilandi!</b>\n\n"
            f"👤 {target_name} (<code>{target_uid}</code>)\n"
            f"💰 Oldingi: <b>{old_balance:,} so'm</b>\n"
            f"💸 Ayirildi: <b>{actually_removed:,} so'm</b>\n"
            f"💳 Yangi balans: <b>{new_balance:,} so'm</b>",
            admin_menu_kb(uid))
        return True

    if state == "add_admin_id":
        if not is_super_admin(uid):
            clear_admin_state(context)
            await sm(context.bot, uid, "⛔ Ruxsat yo'q.", admin_menu_kb(uid))
            return True
        raw = text.strip()
        # Manfiy ID — admin o'chirish
        if raw.startswith("-") and raw[1:].isdigit():
            target = raw[1:]
            if target in (RAM.sub_admins or {}):
                RAM.sub_admins.pop(target, None)
                await schedule_save()
                clear_admin_state(context)
                await sm(context.bot, uid, f"✅ Admin <code>{target}</code> o'chirildi.", admin_menu_kb(uid))
                # O'chirilgan adminga xabar
                try:
                    from telegram import ReplyKeyboardRemove
                    await context.bot.send_message(
                        int(target),
                        "ℹ️ Sizning admin huquqingiz bekor qilindi.",
                        parse_mode="HTML",
                        reply_markup=ReplyKeyboardRemove()
                    )
                    # Oddiy foydalanuvchi klaviaturasini berish
                    await context.bot.send_message(
                        int(target),
                        "Botdan foydalanishda davom etishingiz mumkin 🎬",
                        parse_mode="HTML",
                        reply_markup=main_menu_kb(is_admin=False)
                    )
                except Exception:
                    pass
            else:
                await sm(context.bot, uid, f"❌ <code>{target}</code> admin emas.")
            return True
        if not raw.isdigit():
            await sm(context.bot, uid, "❌ Faqat raqamli <b>ID</b> kiriting (yoki <code>-ID</code> o'chirish uchun):")
            return True
        target = raw
        if int(target) == ADMIN_ID:
            await sm(context.bot, uid, "⚠️ Bu allaqachon asosiy admin.")
            clear_admin_state(context)
            return True
        # Yangi yoki mavjud admin
        already_admin = target in RAM.sub_admins
        if not already_admin:
            RAM.sub_admins[target] = {"perms": {k: True for k in ADMIN_PERM_KEYS}}
        await schedule_save()
        clear_admin_state(context)
        # ✅ Asosiy adminga ruxsatlarni sozlash klaviaturasi
        await sm(context.bot, uid,
            f"👮 <b>Admin: <code>{target}</code></b>\n\n"
            f"Quyidagi tugmalardan istalganini bosing — <b>yoqib/o'chirib</b> turing.\n"
            f"✅ — admin ko'radi, ❌ — admin ko'rmaydi.",
            sub_admin_perm_kb(target))
        # ✅ Yangi adminga darhol xabar + admin keyboard yuborish
        if not already_admin:
            try:
                u_info = RAM.get_user(target) or {}
                target_name = u_info.get("name") or f"ID: {target}"
                await context.bot.send_message(
                    int(target),
                    f"🎉 <b>Tabriklaymiz, {target_name}!</b>\n\n"
                    f"Siz botga <b>admin</b> sifatida qo'shildingiz.\n"
                    f"Quyida admin panelga kirish tugmalari 👇",
                    parse_mode="HTML",
                    reply_markup=admin_menu_kb(int(target))
                )
                logger.info(f"✅ Yangi admin {target} ga xabar yuborildi")
            except Exception as e:
                logger.warning(f"Yangi admin notify xato ({target}): {e}")
        return True

    if state == "edit_ep_code":
        code = text.strip().upper()
        if code not in RAM.movies:
            await sm(context.bot, uid, f"❌ <code>{code}</code> kodli kino topilmadi. Qayta kiriting:")
            return True
        movie = RAM.movies[code]
        eps = movie.get("episodes", [])
        if not eps:
            await sm(context.bot, uid, f"⚠️ <b>{movie.get('title', code)}</b> kinoda hali qism yo'q.",
                     admin_menu_kb(uid))
            clear_admin_state(context)
            return True
        ep_labels = movie.get("ep_labels", {}) or {}
        lines = []
        for i in range(len(eps)):
            ek = str(i + 1)
            cur = ep_labels.get(ek)
            if cur: lines.append(f"  {ek} → <b>{cur}</b>")
            else:   lines.append(f"  {ek} → {ek}-qism")
        context.user_data["edit_ep_code"] = code
        context.user_data["admin_state"]  = "edit_ep_num"
        await sm(context.bot, uid,
            f"✏️ <b>{movie.get('title', code)}</b>\n\n"
            f"📺 Qismlar ({len(eps)} ta):\n" + "\n".join(lines) +
            f"\n\nNechanchi qismni tahrirlamoqchisiz? <b>Raqam</b> kiriting (1 dan {len(eps)} gacha):")
        return True

    if state == "edit_ep_num":
        code = context.user_data.get("edit_ep_code")
        if not code or code not in RAM.movies:
            await sm(context.bot, uid, "❌ Xatolik. Kino kodini qayta kiriting:")
            context.user_data["admin_state"] = "edit_ep_code"
            context.user_data.pop("edit_ep_code", None)
            return True
        movie = RAM.movies[code]
        eps = movie.get("episodes", [])
        if not text.strip().isdigit():
            await sm(context.bot, uid, "❌ Faqat <b>raqam</b> kiriting:")
            return True
        ep_num = int(text.strip())
        if ep_num < 1 or ep_num > len(eps):
            await sm(context.bot, uid, f"❌ <b>{ep_num}</b>-qism mavjud emas. 1–{len(eps)} kiriting:")
            return True
        context.user_data["edit_ep_num"]  = str(ep_num)
        context.user_data["admin_state"]  = "edit_ep_label"
        cur_label = (movie.get("ep_labels", {}) or {}).get(str(ep_num)) or f"{ep_num}-qism"
        await sm(context.bot, uid,
            f"✏️ <b>{movie.get('title', code)}</b> — <b>{ep_num}-qism</b>\n\n"
            f"Hozirgi nom: <code>{cur_label}</code>\n\n"
            f"Yangi nomni kiriting (masalan: <code>1-qismdan 10-qismgacha</code>)\n"
            f"<i>Asl nomga qaytarish uchun <code>0</code> yuboring</i>")
        return True

    if state == "edit_ep_label":
        code = context.user_data.get("edit_ep_code")
        ep   = context.user_data.get("edit_ep_num")
        if not code or not ep or code not in RAM.movies:
            await sm(context.bot, uid, "❌ Xatolik. /start bosing.")
            clear_admin_state(context)
            return True
        movie = RAM.movies[code]
        new_label = text.strip()
        movie_title = movie.get("title", code)
        clear_admin_state(context)
        if new_label == "0":
            movie.setdefault("ep_labels", {}).pop(ep, None)
            await schedule_save()
            await sm(context.bot, uid,
                f"✅ <b>{movie_title}</b> — <b>{ep}-qism</b> nomi asl holatga qaytarildi.",
                admin_menu_kb(uid))
        else:
            movie.setdefault("ep_labels", {})[ep] = new_label
            await schedule_save()
            await sm(context.bot, uid,
                f"✅ <b>{movie_title}</b> — <b>{ep}-qism</b> nomi yangilandi:\n<b>{new_label}</b>",
                admin_menu_kb(uid))
        return True

    if state == "set_price_amount":
        code = context.user_data.get("price_movie_code")
        ep   = context.user_data.get("price_ep")
        ep_range = context.user_data.get("price_ep_range")  # (start, end) yoki None
        if not code or code not in RAM.movies:
            await sm(context.bot, uid, "❌ Xatolik. /start bosing.")
            clear_admin_state(context)
            return True
        if not text.strip().isdigit():
            await sm(context.bot, uid, "❌ Faqat <b>raqam</b> kiriting.")
            return True
        amount      = text.strip()
        movie_title = RAM.movies[code].get("title", code)
        prices_dict = RAM.movies[code].setdefault("prices", {})
        clear_admin_state(context)
        context.user_data.pop("price_ep_range", None)

        if ep_range:
            # ── Diapazon uchun narx belgilash ─────────────────
            start_ep, end_ep = ep_range
            changed = []
            for i in range(start_ep, end_ep + 1):
                k = str(i)
                if amount == "0":
                    prices_dict.pop(k, None)
                else:
                    prices_dict[k] = amount
                changed.append(k)
            await save_now()
            if amount == "0":
                await sm(context.bot, uid,
                    f"✅ <b>{movie_title}</b>\n"
                    f"<b>{start_ep}–{end_ep}-qismlar</b> ({len(changed)} ta) endi <b>bepul</b>!",
                    admin_menu_kb(uid))
            else:
                await sm(context.bot, uid,
                    f"✅ <b>{movie_title}</b>\n"
                    f"<b>{start_ep}–{end_ep}-qismlar</b> ({len(changed)} ta) narxi: <b>{amount} so'm</b>",
                    admin_menu_kb(uid))
        else:
            # ── Bitta qism uchun narx belgilash ───────────────
            if amount == "0":
                prices_dict.pop(ep, None)
                await save_now()
                await sm(context.bot, uid,
                    f"✅ <b>{movie_title}</b> — <b>{ep}-qism</b> endi <b>bepul</b>!", admin_menu_kb(uid))
            else:
                prices_dict[ep] = amount
                await save_now()
                await sm(context.bot, uid,
                    f"✅ <b>{movie_title}</b> — <b>{ep}-qism</b> narxi: <b>{amount} so'm</b>",
                    admin_menu_kb(uid))
        return True

    if state == "add_channel_username":
        raw_uname = text.strip()
        uname     = normalize_channel_username(raw_uname)
        if not uname or (not uname.startswith("@") and not uname.startswith("-100")):
            await sm(context.bot, uid,
                "❌ Kanal username noto'g'ri.\n"
                "Misol: <code>@mykinochannel</code> yoki <code>https://t.me/mykinochannel</code>")
            return True
        # Duplikat tekshirish
        for ch in RAM.channels:
            ch_uname = normalize_channel_username(ch.get("username", ""))
            if ch_uname.lower() == uname.lower():
                context.user_data.pop("admin_state", None)
                context.user_data["channel_manage_menu"] = True
                await sm(context.bot, uid,
                    f"⚠️ <b>{uname}</b> allaqachon qo'shilgan!\n\n{_channels_list_text()}",
                    channel_manage_kb())
                return True
        # Kanal ma'lumotlarini olishga urinamiz
        channel_info = None
        try:
            channel_info = await resolve_required_channel(context.bot, uname)
        except Exception as e:
            err_str = str(e)
            # Bot admin emas yoki kanal topilmadi — manual qo'shishga ruxsat beramiz
            if "admin" in err_str.lower() or "left" in err_str.lower() or "kicked" in err_str.lower():
                # Bot kanalga admin qo'shilmagan — faqat oddiy havola sifatida qo'shamiz
                await sm(context.bot, uid,
                    f"⚠️ Bot <b>{uname}</b> kanalga admin sifatida qo'shilmagan.\n\n"
                    f"Kanal havolasini tekshira olmayman.\n\n"
                    f"Shunga qaramay qo'shishni xohlaysizmi?\n"
                    f"• Ha bo'lsa — kanal <b>nomini</b> kiriting\n"
                    f"• Yo'q bo'lsa — <b>Asosiy menyu</b> bosing")
                context.user_data["ch_info"] = {
                    "chat_id": None,
                    "username": uname,
                    "title": uname,
                    "url": channel_join_url(uname),
                }
                context.user_data["admin_state"] = "add_channel_title"
                return True
            await sm(context.bot, uid,
                f"❌ Kanal topilmadi. Kanal public ekanligini tekshiring.\n\n"
                f"Xato: <code>{e}</code>\n\n"
                f"Qayta username kiriting:")
            return True
        context.user_data["ch_info"]     = channel_info
        context.user_data["admin_state"] = "add_channel_title"
        await sm(context.bot, uid,
            f"✅ Kanal topildi!\n\n"
            f"📛 Nom: <b>{channel_info['title']}</b>\n"
            f"👤 Username: <b>{channel_info['username']}</b>\n\n"
            f"Kanal nomini shu holatda qoldirish uchun <b>✅</b> yuboring\n"
            f"yoki yangi nom kiriting:")
        return True

    if state == "add_channel_title":
        channel_info = context.user_data.pop("ch_info", None)
        if not channel_info:
            context.user_data.pop("admin_state", None)
            await sm(context.bot, uid, "❌ Xatolik. Kanalni qaytadan qo'shing.", channel_manage_kb())
            return True
        title = text.strip()
        if title in ("✅", "+", ".", "-", ""):
            title = channel_info.get("title") or channel_info.get("username", "")
        if not title:
            context.user_data["ch_info"] = channel_info
            await sm(context.bot, uid, "❌ Nom bo'sh bo'lmasin.")
            return True
        channel_info["title"] = title
        channel_info["url"]   = channel_join_url(channel_info.get("username", ""), channel_info.get("url", ""))
        RAM.channels.append(channel_info)
        await schedule_save()
        context.user_data.pop("admin_state", None)
        context.user_data["channel_manage_menu"] = True
        await sm(context.bot, uid,
            f"✅ Kanal muvaffaqiyatli qo'shildi!\n\n"
            f"📛 Nom: <b>{channel_info['title']}</b>\n"
            f"👤 Username: <b>{channel_info['username']}</b>\n\n"
            f"{_channels_list_text()}",
            channel_manage_kb())
        return True

    if state in ("add_channel_url", "add_channel"):
        context.user_data.pop("admin_state", None)
        context.user_data["channel_manage_menu"] = True
        await sm(context.bot, uid, "ℹ️ Qaytadan <b>➕ Kanal qo'shish</b> tugmasini bosing.", channel_manage_kb())
        return True

    if state == "add_soruvli_kanal":
        raw = text.strip()
        # Invite link yoki username
        invite_link = None
        username    = None
        if raw.startswith("https://t.me/+") or raw.startswith("https://t.me/joinchat"):
            invite_link = raw
        else:
            username = normalize_channel_username(raw)
            if not username:
                await sm(context.bot, uid,
                    "❌ Noto'g'ri format.\n"
                    "Misol: <code>@mykanal</code> yoki <code>https://t.me/+xxxxx</code>")
                return True

        # Duplikat tekshirish
        check_val = invite_link or username
        for ch in RAM.channels:
            if (ch.get("invite_link") == check_val or
                    normalize_channel_username(ch.get("username","")).lower() == (username or "").lower()):
                context.user_data.pop("admin_state", None)
                context.user_data["channel_manage_menu"] = True
                await sm(context.bot, uid,
                    f"⚠️ Bu kanal allaqachon qo'shilgan!\n\n{_channels_list_text()}",
                    channel_manage_kb())
                return True

        # Kanal ma'lumotlarini olishga urinamiz
        chat_id   = None
        title     = None
        uname_out = username or ""
        if username:
            try:
                chat = await context.bot.get_chat(username)
                chat_id   = chat.id
                title     = getattr(chat, "title", None) or username
                uname_out = f"@{chat.username}" if getattr(chat, "username", None) else username
            except Exception as e:
                logger.warning(f"So'rovli kanal get_chat xato: {e}")
                # Topilmasa ham qo'shishga ruxsat beramiz
                title = username

        if invite_link:
            try:
                chat = await context.bot.get_chat(invite_link)
                chat_id   = chat.id
                title     = getattr(chat, "title", None) or "So'rovli kanal"
                uname_out = f"@{chat.username}" if getattr(chat, "username", None) else ""
            except Exception as e:
                logger.warning(f"So'rovli kanal invite get_chat xato: {e}")
                title = "So'rovli kanal"

        # join_url — foydalanuvchiga ko'rsatiladigan havola
        join_url = invite_link or (channel_join_url(uname_out) if uname_out else "")

        context.user_data["soruvli_ch_info"] = {
            "chat_id":      chat_id,
            "username":     uname_out,
            "title":        title or uname_out or "So'rovli kanal",
            "url":          join_url,
            "invite_link":  invite_link or "",
            "join_request": True,
        }
        context.user_data["admin_state"] = "add_soruvli_kanal_title"
        await sm(context.bot, uid,
            f"✅ Topildi!\n\n"
            f"📛 Nom: <b>{title or uname_out}</b>\n"
            f"🔗 Havola: <code>{join_url}</code>\n\n"
            f"Kanal nomini o'zgartirmoqchi bo'lsangiz yozing,\n"
            f"o'zgartirishni istasangiz <b>✅</b> yuboring:")
        return True

    if state == "add_soruvli_kanal_title":
        ch_info = context.user_data.pop("soruvli_ch_info", None)
        if not ch_info:
            context.user_data.pop("admin_state", None)
            await sm(context.bot, uid, "❌ Xatolik. Qaytadan bosing.", channel_manage_kb())
            return True
        new_title = text.strip()
        if new_title not in ("✅", "+", ".", "-", ""):
            ch_info["title"] = new_title
        RAM.channels.append(ch_info)
        await save_now()
        context.user_data.pop("admin_state", None)
        context.user_data["channel_manage_menu"] = True
        await sm(context.bot, uid,
            f"✅ <b>So'rovli kanal</b> qo'shildi!\n\n"
            f"📛 Nom: <b>{ch_info['title']}</b>\n"
            f"🔗 Havola: <code>{ch_info['url']}</code>\n\n"
            f"⚠️ Bot shu kanalga <b>admin</b> bo'lishi va "
            f"<b>\"A'zolikni boshqarish\"</b> huquqi bo'lishi kerak!\n\n"
            f"{_channels_list_text()}",
            channel_manage_kb())
        return True

    if state == "post_channel_code":
        reserved = _get_admin_reserved_texts()
        if text in reserved or text.startswith("/"):
            await sm(context.bot, uid, "❌ Bu kino kodi emas. Kino kodini kiriting:")
            return True
        code = text.upper().strip()
        if code not in RAM.movies:
            _, matches = find_movie_code(text)
            if matches:
                hint = "\n".join([f"• <code>{c}</code> — {RAM.movies.get(c,{}).get('title',c)}"
                                   for c in matches[:5]])
                await sm(context.bot, uid,
                    f"❌ <code>{code}</code> topilmadi.\n\nShunga o'xshash:\n{hint}\n\nTo'g'ri kodini kiriting:")
            else:
                await sm(context.bot, uid, "❌ Bunday kod yo'q. Qayta kiriting:")
            return True
        context.user_data["post_code"]   = code
        context.user_data["admin_state"] = "post_channel_target"
        await sm(context.bot, uid, "Kanal username'ini kiriting (masalan @mychannel):")
        return True

    if state == "post_channel_target":
        channel = text.strip()
        code    = context.user_data.pop("post_code", None)
        context.user_data.pop("admin_state", None)
        if not code:
            await sm(context.bot, uid, "❌ Kino kodi topilmadi. Qayta boshlang.")
            return True
        movie    = RAM.movies.get(code, {})
        bot_me   = await context.bot.get_me()
        markup   = channel_post_kb(bot_me.username, code)
        ep_count = len(movie.get("episodes", []))
        finished = ep_count > 0 and ep_count == int(movie.get("total_episodes", ep_count) or ep_count)
        caption  = build_auto_post_caption(movie, code, ep_count, finished=finished, bot_username=bot_me.username)
        poster = movie.get("poster_file_id")
        try:
            if poster: await sp(context.bot, channel, poster, caption, markup)
            else:      await sm(context.bot, channel, caption, markup)
            await sm(context.bot, uid, "✅ Post yuborildi!", admin_menu_kb(uid))
        except Exception as e:
            await sm(context.bot, uid, f"❌ Xato: {e}")
        return True

    if state == "set_install":
        await sm(context.bot, uid, "⚠️ Iltimos, matn emas — <b>video</b> yuboring (caption qo'shsangiz bo'ladi):")
        return True

    # ── 💎 Premium berish: foydalanuvchini aniqlash ──
    if state == "premium_user":
        if not has_perm(uid, "premium_ber"):
            clear_admin_state(context)
            await sm(context.bot, uid, "⛔ Ruxsat yo'q.", admin_menu_kb(uid))
            return True
        raw = text.strip().lstrip("@")
        target_uid = None
        target_name = None
        if raw.isdigit():
            target_uid = raw
            u = RAM.get_user(target_uid)
            target_name = (u or {}).get("name") or raw
        else:
            # username bo'yicha qidiramiz
            uname_low = raw.lower()
            for k, v in RAM.users.items():
                if (v.get("username") or "").lower() == uname_low:
                    target_uid = k
                    target_name = v.get("name") or raw
                    break
        if not target_uid:
            await sm(context.bot, uid,
                "❌ Bunday foydalanuvchi topilmadi.\n"
                "<i>Foydalanuvchi avval botga /start bosgan bo'lishi kerak.</i>\n\n"
                "Username yoki ID ni qayta yuboring (yoki bekor qilish uchun "
                "<b>Asosiy menyu</b>ni bosing):")
            return True
        context.user_data["premium_target_uid"] = target_uid
        context.user_data["admin_state"] = "premium_days"
        await sm(context.bot, uid,
            f"👤 Foydalanuvchi: <b>{target_name}</b>\n"
            f"ID: <code>{target_uid}</code>\n\n"
            f"💎 Necha <b>kun</b>ga premium berasiz? (raqam yuboring, masalan: <code>30</code>)\n"
            f"<i>O'chirish uchun: <code>0</code></i>")
        return True

    if state == "premium_days":
        target = context.user_data.get("premium_target_uid")
        if not target:
            clear_admin_state(context)
            await sm(context.bot, uid, "❌ Foydalanuvchi yo'qolib qoldi. Qayta boshlang.", admin_menu_kb(uid))
            return True
        if not text.strip().isdigit():
            await sm(context.bot, uid, "❌ Faqat <b>raqam</b> yuboring (kun soni).")
            return True
        days = int(text.strip())
        u = RAM.ensure_user(target)
        if days <= 0:
            u["premium_until"] = 0
            msg_text = f"❌ Foydalanuvchi <code>{target}</code> uchun premium <b>o'chirildi</b>."
            user_notify = "ℹ️ Sizning premium statusingiz o'chirildi."
        else:
            u["premium_until"] = time.time() + days * 86400
            msg_text = (f"✅ Foydalanuvchi <code>{target}</code> uchun premium "
                        f"<b>{days} kun</b>ga ulandi!")
            user_notify = (f"💎 <b>Tabriklaymiz!</b>\n\n"
                           f"Sizga <b>{days} kun</b>lik premium ulandi.\n"
                           f"Endi barcha pullik kinolar siz uchun <b>bepul</b> ochiq! 🎬")
        await save_now()
        try:
            await sm(context.bot, int(target), user_notify)
        except Exception as e:
            logger.warning(f"premium notify {target}: {e}")
        clear_admin_state(context)
        await sm(context.bot, uid, msg_text, admin_menu_kb(uid))
        return True

    # ── 🖼 Start xabarni o'zgartirish: matn (rasm media_handler da) ──
    if state == "set_start_msg":
        if not has_perm(uid, "start_xab"):
            clear_admin_state(context)
            await sm(context.bot, uid, "⛔ Ruxsat yo'q.", admin_menu_kb(uid))
            return True
        if text.strip() == "0":
            RAM.settings["start_msg_text"]  = ""
            RAM.settings["start_msg_photo"] = None
            await save_now()
            clear_admin_state(context)
            await sm(context.bot, uid,
                "✅ Start xabari <b>tozalandi</b>. Endi default xabar ko'rsatiladi.",
                admin_menu_kb(uid))
            return True
        # Faqat matn — premium emojilar bilan saqlaymiz (rasm o'zgarmaydi)
        html_text = text_with_premium_emojis(update.message) or text
        RAM.settings["start_msg_text"] = html_text
        # ✅ TUZATISH: faqat matn bo'lsa rasm o'chirilmaydi — eski rasm saqlanib qoladi
        await save_now()
        clear_admin_state(context)
        cur_photo = "✅ bor (o'zgarmadi)" if RAM.settings.get("start_msg_photo") else "❌ yo'q"
        await sm(context.bot, uid,
            f"✅ Start <b>matni</b> saqlandi!\n"
            f"📷 Rasm: {cur_photo}\n\n"
            "Tekshirish uchun /start bosing.",
            admin_menu_kb(uid))
        return True
        return True

    if state == "add_simple_link_title":
        title = text.strip()
        if not title:
            await sm(context.bot, uid, "❌ Nom bo'sh bo'lmasin. Qayta kiriting:")
            return True
        context.user_data["simple_link_title"] = title
        context.user_data["admin_state"] = "add_simple_link_url"
        await sm(context.bot, uid,
            f"✅ Nom: <b>{title}</b>\n\n"
            f"Endi havola linkini kiriting (masalan: <code>https://t.me/mychannel</code>):")
        return True

    if state == "add_simple_link_url":
        url = text.strip()
        if not url.startswith("http"):
            await sm(context.bot, uid,
                "❌ Link noto'g'ri. <code>https://</code> bilan boshlanishi kerak.\nQayta kiriting:")
            return True
        title = context.user_data.pop("simple_link_title", "Havola")
        context.user_data.pop("admin_state", None)
        context.user_data["channel_manage_menu"] = True
        RAM.simple_links.append({"title": title, "url": url})
        await save_now()
        await sm(context.bot, uid,
            f"✅ Oddiy havola qo'shildi!\n\n"
            f"📛 Nom: <b>{title}</b>\n"
            f"🔗 Link: <code>{url}</code>\n\n"
            f"<i>Bot bu havolaga obunani tekshirmaydi — faqat ko'rsatadi.</i>\n\n"
            f"{_channels_list_text()}",
            channel_manage_kb())
        return True

    # ── 🔓 Qism ochish: foydalanuvchi UID ──
    if state == "qism_och_uid":
        raw = text.strip()
        if not raw.isdigit():
            await sm(context.bot, uid,
                "❌ Faqat raqamli <b>ID</b> kiriting:\n"
                "<i>Misol: <code>123456789</code></i>")
            return True
        target_uid = raw
        u = RAM.get_user(target_uid)
        if not u:
            await sm(context.bot, uid,
                f"❌ <code>{target_uid}</code> ID li foydalanuvchi topilmadi.\n"
                "<i>Foydalanuvchi avval botga /start bosgan bo'lishi kerak.</i>\n\n"
                "Qayta ID yuboring:")
            return True
        target_name = u.get("name") or target_uid
        context.user_data["qism_och_target_uid"] = target_uid
        context.user_data["admin_state"] = "qism_och_code"
        await sm(context.bot, uid,
            f"👤 Foydalanuvchi: <b>{target_name}</b>\n"
            f"ID: <code>{target_uid}</code>\n\n"
            f"Kino <b>kodini</b> kiriting:")
        return True

    if state == "qism_och_code":
        target_uid = context.user_data.get("qism_och_target_uid")
        if not target_uid:
            clear_admin_state(context)
            await sm(context.bot, uid, "❌ Xatolik. Qaytadan boshlang.", admin_menu_kb(uid))
            return True
        code = text.strip().upper()
        if code not in RAM.movies:
            _, matches = find_movie_code(text)
            if matches:
                hint = "\n".join([f"• <code>{c}</code> — {RAM.movies.get(c,{}).get('title',c)}"
                                   for c in matches[:5]])
                await sm(context.bot, uid,
                    f"❌ <code>{code}</code> topilmadi.\n\nShunga o'xshash:\n{hint}\n\nTo'g'ri kodini kiriting:")
            else:
                await sm(context.bot, uid, f"❌ <code>{code}</code> kodli kino topilmadi. Qayta kiriting:")
            return True
        movie = RAM.movies[code]
        eps = movie.get("episodes", [])
        prices = movie.get("prices", {}) or {}
        if not eps:
            await sm(context.bot, uid,
                f"⚠️ <b>{movie.get('title', code)}</b> kinoda hali qism yo'q.")
            context.user_data.pop("admin_state", None)
            context.user_data.pop("qism_och_target_uid", None)
            return True
        # Barcha qismlarni ko'rsatamiz
        ep_list_lines = []
        for i in range(len(eps)):
            ek = str(i + 1)
            price = price_to_int(prices.get(ek))
            already = is_episode_paid(target_uid, code, ek)
            if price > 0:
                status = "✅ ochiq" if already else "🔒 yopiq"
                ep_list_lines.append(f"  {ek}-qism — 💰 {price} so'm — {status}")
            else:
                ep_list_lines.append(f"  {ek}-qism — bepul")
        ep_list_text = "\n".join(ep_list_lines)
        context.user_data["qism_och_code"] = code
        context.user_data["admin_state"] = "qism_och_ep"
        target_name = (RAM.get_user(target_uid) or {}).get("name") or target_uid
        await sm(context.bot, uid,
            f"🎬 <b>{movie.get('title', code)}</b>  |  <code>{code}</code>\n"
            f"👤 Foydalanuvchi: <b>{target_name}</b> (<code>{target_uid}</code>)\n\n"
            f"📺 Qismlar:\n{ep_list_text}\n\n"
            f"Ochmoqchi bo'lgan qism <b>raqamini</b> kiriting (1 dan {len(eps)} gacha):\n"
            f"<i>• Bitta qism: <code>5</code>\n"
            f"• Diapazon: <code>1+10</code> (1 dan 10 gacha)\n"
            f"• Barcha pullik: <code>hammasi</code></i>")
        return True

    if state == "qism_och_ep":
        target_uid = context.user_data.get("qism_och_target_uid")
        code = context.user_data.get("qism_och_code")
        if not target_uid or not code or code not in RAM.movies:
            clear_admin_state(context)
            context.user_data.pop("qism_och_target_uid", None)
            context.user_data.pop("qism_och_code", None)
            await sm(context.bot, uid, "❌ Xatolik. Qaytadan boshlang.", admin_menu_kb(uid))
            return True
        movie = RAM.movies[code]
        eps = movie.get("episodes", [])
        val = text.strip().lower()
        target_name = (RAM.get_user(target_uid) or {}).get("name") or target_uid

        # Qism tanlandi — narxni so'raymiz
        # hammasi
        if val == "hammasi":
            context.user_data["qism_och_ep_val"] = "hammasi"
            context.user_data["admin_state"] = "qism_och_price"
            await sm(context.bot, uid,
                f"💰 <b>Narx belgilash</b>\n\n"
                f"👤 {target_name} | 🎬 {movie.get('title', code)}\n"
                f"📺 Qismlar: <b>Hammasi</b>\n\n"
                f"• <code>0</code> — bepul ochish 🔓\n"
                f"• <code>1000</code> — 1000 so'm qilib qulflash 🔒\n"
                f"• Istalgan miqdor kiriting:")
            return True

        # diapazon: 1+10
        range_match = re.match(r'^(\d+)[+\-](\d+)$', val)
        if range_match:
            start_ep = int(range_match.group(1))
            end_ep   = int(range_match.group(2))
            if start_ep < 1 or end_ep > len(eps) or start_ep > end_ep:
                await sm(context.bot, uid,
                    f"❌ Noto'g'ri diapazon. 1–{len(eps)} orasida kiriting.\n"
                    f"Masalan: <code>1+10</code>")
                return True
            context.user_data["qism_och_ep_val"] = f"{start_ep}+{end_ep}"
            context.user_data["admin_state"] = "qism_och_price"
            await sm(context.bot, uid,
                f"💰 <b>Narx belgilash</b>\n\n"
                f"👤 {target_name} | 🎬 {movie.get('title', code)}\n"
                f"📺 Qismlar: <b>{start_ep}–{end_ep}-qismlar</b>\n\n"
                f"• <code>0</code> — bepul ochish 🔓\n"
                f"• <code>1000</code> — 1000 so'm qilib qulflash 🔒\n"
                f"• Istalgan miqdor kiriting:")
            return True

        # bitta raqam
        if not val.isdigit():
            await sm(context.bot, uid,
                "❌ Qism raqami, diapazon (<code>1+10</code>) yoki <code>hammasi</code> kiriting:")
            return True
        ep_num = int(val)
        if ep_num < 1 or ep_num > len(eps):
            await sm(context.bot, uid,
                f"❌ <b>{ep_num}</b>-qism mavjud emas. 1–{len(eps)} kiriting:")
            return True
        context.user_data["qism_och_ep_val"] = str(ep_num)
        context.user_data["admin_state"] = "qism_och_price"
        await sm(context.bot, uid,
            f"💰 <b>Narx belgilash</b>\n\n"
            f"👤 {target_name} | 🎬 {movie.get('title', code)}\n"
            f"📺 Qism: <b>{ep_num}-qism</b>\n\n"
            f"• <code>0</code> — bepul ochish 🔓\n"
            f"• <code>1000</code> — 1000 so'm qilib qulflash 🔒\n"
            f"• Istalgan miqdor kiriting:")
        return True

    # ── qism_och_price: narx kiritildi ──
    # 0 => ochiladi (bepul), >0 => qulflanadi (narxi o'rnatiladi, paid_episodes dan o'chiriladi)
    if state == "qism_och_price":
        target_uid = context.user_data.get("qism_och_target_uid")
        code = context.user_data.get("qism_och_code")
        ep_val = context.user_data.get("qism_och_ep_val")
        if not target_uid or not code or not ep_val or code not in RAM.movies:
            clear_admin_state(context)
            context.user_data.pop("qism_och_target_uid", None)
            context.user_data.pop("qism_och_code", None)
            context.user_data.pop("qism_och_ep_val", None)
            await sm(context.bot, uid, "❌ Xatolik. Qaytadan boshlang.", admin_menu_kb(uid))
            return True
        if not text.strip().isdigit():
            await sm(context.bot, uid, "❌ Faqat raqam kiriting:\n• <code>0</code> — bepul ochish\n• <code>1000</code> — 1000 so'm qilish (qulflash):")
            return True
        set_price = int(text.strip())
        if set_price < 0:
            await sm(context.bot, uid, "❌ Narx 0 yoki undan katta bo'lishi kerak:")
            return True

        movie = RAM.movies[code]
        eps = movie.get("episodes", [])
        target_name = (RAM.get_user(target_uid) or {}).get("name") or target_uid
        u_data = RAM.ensure_user(target_uid)
        is_lock = set_price > 0  # True=qulflash, False=ochish

        def _open_ep(ek_str):
            """Qismni ochib berish (bepul)."""
            paid_key = episode_paid_key(code, ek_str)
            u_data["paid_episodes"][paid_key] = {
                "status": "approved",
                "price": 0,
                "payment_id": f"admin_och_{uid}_{int(time.time())}",
                "approved_at": datetime.now().isoformat(),
            }

        def _lock_ep(ek_str):
            """Faqat shu foydalanuvchi uchun qulflash — paid_episodes dan o'chirish."""
            paid_key = episode_paid_key(code, ek_str)
            u_data["paid_episodes"].pop(paid_key, None)

        processed_count = 0

        if ep_val == "hammasi":
            ep_list = [str(i + 1) for i in range(len(eps))]
            ep_info = "barcha qismlar"
        elif "+" in ep_val:
            s, e = map(int, ep_val.split("+"))
            ep_list = [str(i) for i in range(s, e + 1)]
            ep_info = f"{s}–{e}-qismlar"
        else:
            ep_list = [ep_val]
            ep_info = f"{ep_val}-qism"

        for ek in ep_list:
            if is_lock:
                _lock_ep(ek)
            else:
                _open_ep(ek)
            processed_count += 1

        await save_now()
        clear_admin_state(context)
        context.user_data.pop("qism_och_target_uid", None)
        context.user_data.pop("qism_och_code", None)
        context.user_data.pop("qism_och_ep_val", None)

        if is_lock:
            action_info = "🔒 Qulflandi (faqat shu foydalanuvchi uchun)"
            notify_msg = (f"🔒 <b>{ep_info} sizdan qayta qulflandi.</b>\n\n"
                          f"🎬 <b>{movie.get('title', code)}</b>")
            result_emoji = "🔒"
        else:
            action_info = "🔓 Bepul ochildi"
            notify_msg = (f"🎉 <b>Admin sizga {ep_info}ni ochib berdi!</b>\n\n"
                          f"🎬 <b>{movie.get('title', code)}</b>\n"
                          f"Kino kodini yuboring va tomosha qiling 🍿")
            result_emoji = "✅"

        try:
            await sm(context.bot, int(target_uid), notify_msg)
        except Exception as e:
            logger.warning(f"qism_och notify xato: {e}")

        await sm(context.bot, uid,
            f"{result_emoji} <b>{target_name}</b> uchun <b>{code}</b> — <b>{ep_info}</b>\n"
            f"{action_info}\n"
            f"📺 Ishlangan: <b>{processed_count} ta</b>\n\n"
            f"Foydalanuvchiga xabar yuborildi.",
            admin_menu_kb(uid))
        return True

    return False


# ══════════════════════════════════════════════════════════
# STICKER HANDLER
# ══════════════════════════════════════════════════════════

async def sticker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_duplicate_update(update): return
    uid = update.effective_user.id
    if not is_any_admin(uid): return
    key = context.user_data.get("editing_btn_key")
    if not key: return
    sticker = update.message.sticker
    if not sticker: return
    emoji = sticker.emoji or ""
    if not emoji:
        await sm(context.bot, uid, "Bu stickerda emoji yo'q.")
        return
    context.user_data.pop("editing_btn_key", None)
    existing         = RAM.btn_texts.get(key) or DEFAULT_BTN.get(key, "")
    existing_label   = strip_emoji_prefix(existing) or DEFAULT_BTN.get(key, "")
    existing_emoji_p = extract_emoji_prefix(existing)
    new_emoji_p      = (existing_emoji_p + emoji) if existing_emoji_p else emoji
    new_text         = f"{new_emoji_p} {existing_label}"
    RAM.btn_texts[key] = new_text
    EMOJI_IDS.pop(key, None)
    RAM.emoji_ids.pop(key, None)
    await save_now()
    await sm(context.bot, uid,
        f"✅ <b>{BTN_LABELS.get(key, key)}</b> yangilandi!\nKo'rinish: <code>{new_text}</code>")
    context.user_data["emoji_menu"] = True
    await sm(context.bot, uid, "Tugmani tanlang:", emoji_menu_kb())


# ══════════════════════════════════════════════════════════
# MEDIA HANDLER
# ══════════════════════════════════════════════════════════

async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_duplicate_update(update): return
    user  = update.effective_user
    uid   = user.id
    msg   = update.message
    state = context.user_data.get("admin_state")

    if is_any_admin(uid) and state == "broadcast_msg":
        bc = {
            "type": "copy",
            "from_chat_id": msg.chat_id,
            "message_id": msg.message_id,
            "buttons": [],
        }
        context.user_data["bc_msg"] = bc
        context.user_data.pop("admin_state", None)
        await sm(context.bot, uid, "✅ Xabar qabul qilindi.\n\n<b>Tugmali xabar yuborasizmi?</b>",
                 markup=bc_yesno_kb())
        return

    if is_any_admin(uid) and state == "add_movie_poster":
        code = context.user_data.pop("poster_code", None)
        context.user_data.pop("admin_state", None)
        context.user_data.pop("new_movie_code", None)
        if msg.photo and code and code in RAM.movies:
            RAM.movies[code]["poster_file_id"] = msg.photo[-1].file_id
            await schedule_save()
            await sm(context.bot, uid,
                f"✅ Poster saqlandi!\nKod: <code>{code}</code>",
                movie_added_kb(code))
        else:
            await sm(context.bot, uid, "⚠️ Rasm yuboring!", movie_added_kb(code) if code else None)
        return

    if is_any_admin(uid) and state == "add_ep_video":
        code = context.user_data.get("ep_movie_code")
        if not code:
            await sm(context.bot, uid, "❌ Kino kodi topilmadi. Qaytadan bosing.")
            context.user_data.pop("admin_state", None)
            return

        # Forward qilingan video document sifatida ham kelishi mumkin
        video_file_id = None
        if msg.video:
            video_file_id = msg.video.file_id
        elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("video"):
            video_file_id = msg.document.file_id

        if not video_file_id:
            movie  = RAM.movies.get(code, {})
            ep_num = len(movie.get("episodes", [])) + 1
            await sm(context.bot, uid,
                f"⚠️ Faqat <b>video</b> yuboring!\n"
                f"Kino: <b>{movie.get('title', code)}</b>\n"
                f"<b>{ep_num}-qism</b> kutilmoqda...")
            return

        # ❗ Duplicate himoya — bir xil file_id ikki marta qo'shilmasin
        movie = RAM.movies.get(code, {})
        if video_file_id in movie.get("episodes", []):
            ep_num = len(movie.get("episodes", []))
            await sm(context.bot, uid,
                f"⚠️ Bu video allaqachon saqlangan!\n"
                f"Jami qismlar: <b>{ep_num} ta</b>\n\n"
                f"📹 Yana video yuboring yoki <b>Tugatish</b> tugmasini bosing.",
                movie_added_kb(code))
            return

        # ❗ Darhol RAMga yoz
        RAM.movies[code]["episodes"].append(video_file_id)
        ep_num = len(RAM.movies[code]["episodes"])

        # ❗ Lokal faylga darhol yoz (har bir qism uchun) — bot to'xtab qolsa ham yo'qolmaydi
        await save_ram_only()
        # ❗ JSONBlob ga ham — har 3-qismda bir marta + debounce
        # (har video uchun yozish sekin, lekin to'liq tashlab qo'yish xavfli)
        if ep_num == 1 or ep_num % 3 == 0:
            # birinchi qism va har 3-qismda — DARHOL JSONBlob ga
            asyncio.create_task(_do_jsonblob_save())
        else:
            # qolganlari uchun debounce taymeri (12 sek dan keyin)
            await schedule_save()

        # Admin yana video yuborishi mumkin — state saqlab qolamiz
        context.user_data["admin_state"]   = "add_ep_video"
        context.user_data["ep_movie_code"] = code

        movie = RAM.movies[code]
        await sm(context.bot, uid,
            f"✅ <b>{ep_num}-qism</b> saqlandi!\n"
            f"Kino: <b>{movie.get('title', code)}</b>\n"
            f"Kod: <code>{code}</code>\n"
            f"Jami qismlar: <b>{ep_num} ta</b>\n\n"
            f"📹 Yana video yuboring yoki <b>Tugatish</b> tugmasini bosing.\n"
            f"<i>Avtomatik bazaga ham saqlandi — yo'qolmaydi.</i>",
            movie_added_kb(code))

        # 🔔 Kanalga avto-post (har qism qo'shilganda yuboradi/tahrirlaydi)
        asyncio.create_task(auto_post_episode_added(context.bot, code, finished=False))
        return

    if is_any_admin(uid) and state == "set_install":
        if msg.video:
            RAM.settings["install_video_id"] = msg.video.file_id
            # caption (premium emojilar bilan) — agar yuborilgan bo'lsa
            cap_html = text_with_premium_emojis(msg) if msg.caption else ""
            if cap_html:
                RAM.settings["install_caption"] = cap_html
            await save_now()
            context.user_data.pop("admin_state", None)
            cap_info = "\n📝 Caption ham saqlandi." if cap_html else ""
            await sm(context.bot, uid,
                f"✅ Bot qo'llanma videosi saqlandi!{cap_info}",
                admin_menu_kb(uid))
        else:
            await sm(context.bot, uid, "⚠️ Faqat <b>video</b> yuboring (document/fayl emas):")
        return

    # ── 🖼 Start xabari uchun rasm + matn (premium emoji bilan) ──
    if is_any_admin(uid) and state == "set_start_msg":
        if msg.photo:
            RAM.settings["start_msg_photo"] = msg.photo[-1].file_id
            cap_html = text_with_premium_emojis(msg) if msg.caption else ""
            if cap_html:
                RAM.settings["start_msg_text"] = cap_html
            # ✅ TUZATISH: captionsiz rasm yuborilsa, eski matnni saqlab qolamiz
            # (faqat rasm o'zgaradi, matn o'chirilmaydi)
            await save_now()
            clear_admin_state(context)
            cap_info = ("\n📝 Matn (premium emoji bilan) saqlandi."
                        if cap_html else "\n<i>Matn o'zgarmadi (eski matn saqlanib qoldi).</i>")
            await sm(context.bot, uid,
                f"✅ Start <b>rasm</b>i saqlandi!{cap_info}\n\nTekshirish uchun /start bosing.",
                admin_menu_kb(uid))
        elif msg.video or msg.document:
            await sm(context.bot, uid,
                "⚠️ Iltimos <b>rasm</b> yuboring (video emas).\n"
                "Yoki faqat <b>matn</b> yuboring.")
        else:
            await sm(context.bot, uid,
                "⚠️ Iltimos <b>rasm</b> yuboring (caption sifatida matn yozsangiz bo'ladi).")
        return

    if context.user_data.get("awaiting_check") and msg.photo:
        pay_info = context.user_data.pop("awaiting_check")
        code = str(pay_info.get("code", "")).upper()
        ep   = str(pay_info.get("ep", ""))
        movie = RAM.movies.get(code)
        idx = int(ep) - 1 if ep.isdigit() else -1
        if not movie or idx < 0 or idx >= len(movie.get("episodes", []) or []):
            await sm(context.bot, uid, "❌ Bu qism topilmadi. Kino kodini qayta yuboring.")
            return
        price = price_to_int(movie.get("prices", {}).get(ep))
        if price <= 0:
            await sm(context.bot, uid, "ℹ️ Bu qism hozir pullik emas. Kino kodini qayta yuboring.")
            return
        if is_episode_paid(uid, code, ep):
            await sm(context.bot, uid, f"✅ Siz <b>{ep}-qism</b>ni allaqachon sotib olgansiz.")
            return

        pid = f"{uid}_{code}_{ep}_{int(time.time())}"
        RAM.pending_payments[pid] = {
            "user_id": uid,
            "code": code,
            "ep": ep,
            "price": price,
            "status": "pending",
        }
        await save_now()
        cap = (f"<b>To'lov cheki</b>\n{user.full_name} (@{user.username or '-'})\n"
               f"<code>{uid}</code>\nKino: <b>{code}</b>\n"
               f"Qism: <b>{ep}</b>\nNarx: <b>{price} so'm</b>")
        await sp(context.bot, ADMIN_ID, msg.photo[-1].file_id, cap, payment_admin_kb(pid))
        await sm(context.bot, uid,
            "✅ <b>Chekingiz muvaffaqiyatli qabul qilindi!</b>\n\n"
            "👨‍💼 Admin tekshirib tasdiqlaydi va faqat shu qism videosini sizga yuboradi.\n"
            "⏱ Tekshirish vaqti: <b>5 daqiqadan 2 soatgacha</b>.\n\n"
            "🙏 Kutganingiz uchun rahmat!")
        return

    # ── 💰 Hisobni to'ldirish cheki ──────────────────────
    if context.user_data.get("awaiting_topup_check") and msg.photo:
        topup_info = context.user_data.pop("awaiting_topup_check")
        amount     = int(topup_info.get("amount", 0))
        if amount <= 0:
            await sm(context.bot, uid, "❌ Xatolik. Qaytadan urinib ko'ring.")
            return
        pid = f"topup_{uid}_{int(time.time())}"
        RAM.pending_payments[pid] = {
            "type":    "topup",
            "user_id": uid,
            "amount":  amount,
            "status":  "pending",
        }
        await save_now()
        uname_str = f"@{user.username}" if user.username else f"ID: {uid}"
        card_info = f"\n💳 Karta: <code>{RAM.card_number}</code>" if RAM.card_number else ""
        tashkent_time = _tashkent_now_str()
        cap = (
            f"<blockquote>"
            f"💰 <b>BALANS TO'LDIRISH SO'ROVI</b>\n\n"
            f"👤 <b>Ism:</b> {user.full_name}\n"
            f"🆔 <b>ID:</b> <code>{uid}</code>\n"
            f"📱 <b>Username:</b> {uname_str}\n\n"
            f"💵 <b>To'langan summa:</b> <b>{amount:,} so'm</b>\n"
            f"{card_info}\n\n"
            f"🕐 <b>Vaqt (Toshkent):</b> {tashkent_time}"
            f"</blockquote>"
        )
        username_for_btn = user.username or ""
        await sp(context.bot, ADMIN_ID, msg.photo[-1].file_id, cap,
                 topup_admin_kb(pid, uid, username_for_btn))
        await sm(context.bot, uid,
            "✅ <b>Chekingiz muvaffaqiyatli qabul qilindi!</b>\n\n"
            f"💵 Miqdor: <b>{amount:,} so'm</b>\n\n"
            "👨‍💼 Admin tekshirib, <b>HISOBINGIZGA</b> pul tushurib beradi.\n"
            "⏱ Tekshirish vaqti: <b>5 daqiqadan 2 soatgacha</b>.\n\n"
            "🙏 Sabr qilganingiz uchun rahmat!")
        return

    if context.user_data.get("awaiting_help"):
        context.user_data.pop("awaiting_help", None)
        user_text = msg.caption or msg.text or ""
        cap       = (f"<b>Yordam so'rovi</b>\n{user.full_name} (@{user.username or '-'})\n"
                     f"<code>{uid}</code>\n\n{user_text}")
        if msg.photo:
            await sp(context.bot, ADMIN_ID, msg.photo[-1].file_id, cap, reply_admin_kb(uid))
        elif msg.video:
            await sv(context.bot, ADMIN_ID, msg.video.file_id, cap, reply_admin_kb(uid))
        await sm(context.bot, uid, "✅ Xabaringiz adminga yuborildi!")
        return

    if is_any_admin(uid) and "reply_to" in context.user_data:
        target = context.user_data.pop("reply_to")
        try:
            cap = "<b>Admin javobi</b>"
            if msg.photo:
                if msg.caption: cap += f"\n{msg.caption}"
                await sp(context.bot, target, msg.photo[-1].file_id, cap)
            elif msg.video:
                if msg.caption: cap += f"\n{msg.caption}"
                await sv(context.bot, target, msg.video.file_id, cap)
            await sm(context.bot, uid, "✅ Yuborildi!")
        except Exception as e:
            await sm(context.bot, uid, f"❌ Xato: {e}")


# ══════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════
# JOIN REQUEST HANDLER — So'rovli kanallar uchun
# ══════════════════════════════════════════════════════════

async def join_request_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Foydalanuvchi so'rovli kanalga qo'shilish so'rovi yuborganda
    bot avtomatik tasdiqlaydi va foydalanuvchiga xabar beradi.
    """
    try:
        req     = update.chat_join_request
        if not req:
            return
        user    = req.from_user
        chat    = req.chat
        chat_id = chat.id

        # Faqat bizning so'rovli kanallarimiz uchun ishlasin
        soruvli_ids = {
            ch.get("chat_id")
            for ch in RAM.channels
            if ch.get("join_request") and ch.get("chat_id")
        }
        if chat_id not in soruvli_ids:
            return

        # So'rovni avtomatik tasdiqlash
        try:
            await context.bot.approve_chat_join_request(chat_id=chat_id, user_id=user.id)
            logger.info(f"✅ Join request tasdiqlandi: {user.id} → {chat.title}")
        except Exception as e:
            logger.warning(f"Join request tasdiqda xato {user.id} → {chat_id}: {e}")
            return

        # Foydalanuvchiga xabar
        try:
            await context.bot.send_message(
                chat_id=user.id,
                text=(f"✅ <b>{chat.title}</b> kanaliga qo'shildingiz!\n\n"
                      f"Endi botdan foydalanishingiz mumkin 🎬"),
                parse_mode="HTML"
            )
        except Exception:
            pass  # Foydalanuvchi bota yozmagan bo'lishi mumkin

        # Sub cache ni yangilash — endi obuna bor
        _sub_cache_invalidate(user.id)

    except Exception as e:
        logger.error(f"join_request_handler xato: {e}")


# ══════════════════════════════════════════════════════════
# RAILWAY HEALTH CHECK + CHECKCARD WEBHOOK SERVER
# ══════════════════════════════════════════════════════════

# Global bot reference — webhook handler uchun
_BOT_APP = None

class _WebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def do_POST(self):
        """CheckCard webhook — to'lov bo'lganda keladi."""
        if self.path != CHECKCARD_WEBHOOK_PATH:
            self.send_response(404)
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length)
            data   = json.loads(body.decode("utf-8"))
            logger.info(f"📩 CheckCard webhook keldi: {data}")
            # Async handler ni event loop orqali chaqiramiz
            import asyncio
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_handle_checkcard_webhook(data))
            loop.close()
        except Exception as e:
            logger.error(f"Webhook handler xato: {e}")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args):
        pass


async def _handle_checkcard_webhook(data: dict):
    """
    CheckCard to'lov webhookini qayta ishlaydi.
    JSON: {"shopkey": "...", "amount": 50000, "order": "owld1002", "date": "..."}
    """
    try:
        shop_key = data.get("shopkey") or data.get("shop_key") or ""
        order    = str(data.get("order") or "")
        amount   = int(data.get("amount") or 0)

        # Shop key tekshiruvi — xavfsizlik uchun
        if shop_key and shop_key != CHECKCARD_SHOP_KEY:
            logger.warning(f"Webhook: noto'g'ri shop_key: {shop_key}")
            return

        if not order:
            logger.warning("Webhook: order yo'q")
            return

        # RAM.pending_payments dan shu order'ni topamiz
        found_pid = None
        found_pay = None
        for pid, pay in list(RAM.pending_payments.items()):
            if (pay.get("type") == "topup_checkcard"
                    and str(pay.get("cc_order")) == order
                    and pay.get("cc_status") == "pending"):
                found_pid = pid
                found_pay = pay
                break

        if not found_pay:
            logger.warning(f"Webhook: order {order} uchun pending to'lov topilmadi")
            return

        # Balansga qo'shamiz
        if found_pay.get("cc_status") == "paid":
            logger.warning(f"Webhook: order {order} allaqachon to'langan — ikki marta qo'shilmaydi!")
            return
        found_pay["cc_status"] = "paid"
        # found_pay["amount"] — so'mda saqlangan (biz shu qiymatni ishlatamiz)
        # webhook "amount" — so'mda keladi, found_pay dagi so'm qiymatini ishlatamiz
        pay_amount = int(found_pay.get("amount") or amount)
        uid_str    = str(found_pay.get("user_id"))
        u_data     = RAM.ensure_user(uid_str)
        u_data["balance"]     = int(u_data.get("balance") or 0) + pay_amount
        u_data["topup_total"] = int(u_data.get("topup_total") or 0) + pay_amount

        # Fon saqlash
        import asyncio
        asyncio.create_task(save_now()) if _is_event_loop_running() else None
        _save_local(RAM.to_dict())

        logger.info(f"✅ Webhook: {uid_str} ga {pay_amount:,} so'm qo'shildi (order: {order})")

        # Foydalanuvchiga xabar — bot mavjud bo'lsa
        if _BOT_APP and _BOT_APP.bot:
            try:
                await _BOT_APP.bot.send_message(
                    int(uid_str),
                    f"<blockquote>✅ <b>TO'LOV TASDIQLANDI!</b>\n\n"
                    f"💵 Miqdor: <b>{pay_amount:,} so'm</b> balansingizga qo'shildi!\n"
                    f"💰 Joriy balans: <b>{u_data['balance']:,} so'm</b>\n\n"
                    f"Endi balansdan pullik qismlarni tomosha qilishingiz mumkin! 🎬</blockquote>",
                    parse_mode="HTML")
            except Exception as e:
                logger.warning(f"Webhook notify xato: {e}")
            # Adminga xabar
            try:
                u_d = RAM.users.get(uid_str) or {}
                u_name = u_d.get("name") or u_d.get("first_name") or f"ID: {uid_str}"
                u_uname = u_d.get("username") or ""
                uname_adm = f"@{u_uname}" if u_uname else f"ID: {uid_str}"
                if u_uname:
                    lichka_url = f"https://t.me/{u_uname}"
                else:
                    lichka_url = f"tg://user?id={uid_str}"
                tashkent_time = _tashkent_now_str()
                card_info = f"\n💳 Karta: <code>{RAM.card_number}</code>" if RAM.card_number else ""
                adm_cap = (
                    f"<blockquote>"
                    f"✅ <b>AUTO TO'LOV ORQALI TO'LANDI</b>\n\n"
                    f"👤 <b>Ism:</b> {u_name}\n"
                    f"🆔 <b>ID:</b> <code>{uid_str}</code>\n"
                    f"📱 <b>Username:</b> {uname_adm}\n\n"
                    f"💵 <b>To'langan summa:</b> <b>{pay_amount:,} so'm</b>\n"
                    f"💰 <b>Joriy balans:</b> <b>{u_data['balance']:,} so'm</b>\n"
                    f"{card_info}\n\n"
                    f"🕐 <b>Vaqt (Toshkent):</b> {tashkent_time}"
                    f"</blockquote>"
                )
                lichka_kb_wh = {"inline_keyboard": [[{"text": "👤 Foydalanuvchi lichkasi", "url": lichka_url}]]}
                await _BOT_APP.bot.send_message(
                    ADMIN_ID, adm_cap, parse_mode="HTML",
                    reply_markup=lichka_kb_wh)
            except Exception as _e:
                logger.warning(f"Webhook admin notify xato: {_e}")
    except Exception as e:
        logger.error(f"_handle_checkcard_webhook xato: {e}")


def _is_event_loop_running() -> bool:
    try:
        import asyncio
        loop = asyncio.get_running_loop()
        return loop is not None
    except RuntimeError:
        return False


def _start_health_server():
    port = int(os.environ.get("PORT", 8080))
    try:
        server = HTTPServer(("0.0.0.0", port), _WebhookHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        logger.info(f"✅ Railway webhook server port {port} da ishga tushdi")
        logger.info(f"📡 CheckCard webhook URL: {RAILWAY_URL}{CHECKCARD_WEBHOOK_PATH}")
    except Exception as e:
        logger.warning(f"Health/Webhook server xato: {e}")


# ══════════════════════════════════════════════════════════
# CHECKCARD POLLING JOB — Avtomatik to'lov tekshiruvi
# ══════════════════════════════════════════════════════════

async def _checkcard_poll_job(context):
    """
    Har 15 soniyada CheckCard to'lov statusini tekshiradi.
    paid => balans to'ldiriladi va job to'xtatiladi.
    cancel/expired => job to'xtatiladi.
    20 ta urinishdan keyin (5 min) ham to'xtatiladi.
    """
    try:
        job_data = context.job.data
        pid      = job_data["pid"]
        uid      = job_data["uid"]
        order    = job_data["order"]
        amount   = job_data["amount"]
        tries    = job_data.get("tries", 0)
        topup_msg_id = job_data.get("topup_msg_id")

        # Max urinish
        if tries >= 20:  # 20 * 15s = 5 daqiqa
            context.job.schedule_removal()
            pay = RAM.pending_payments.get(pid)
            if pay and pay.get("cc_status") == "pending":
                pay["cc_status"] = "expired"
                await schedule_save()
                try:
                    await context.bot.send_message(
                        uid,
                        f"⏰ <b>To'lov muddati tugadi.</b>\n\n"
                        f"Order: <code>{order}</code>\n"
                        f"Miqdor: <b>{amount:,} so'm</b>\n\n"
                        f"Qayta to'ldirish uchun balans bo'limiga boring.",
                        parse_mode="HTML")
                except Exception:
                    pass
            return

        # Job data ni yangilaymiz
        job_data["tries"] = tries + 1

        # Hozirgi to'lov holati
        pay = RAM.pending_payments.get(pid)
        if not pay or pay.get("cc_status") in ("paid", "cancelled", "expired"):
            context.job.schedule_removal()
            return

        # API dan so'raymiz
        result = await asyncio.to_thread(checkcard_check_payment, order)
        logger.info(f"poll check result for {order}: {result}")
        cc_data = result.get("data", {}) or {}
        cc_status = (cc_data.get("status")
                     or cc_data.get("state")
                     or result.get("status")
                     or result.get("state")
                     or "")
        cc_status = str(cc_status).lower().strip()

        if cc_status == "paid":
            context.job.schedule_removal()
            if pay.get("cc_status") == "paid":
                logger.warning(f"Poll: {order} allaqachon to'langan — ikki marta qo'shilmaydi!")
                return
            pay["cc_status"] = "paid"
            u_data = RAM.ensure_user(str(uid))
            u_data["balance"]     = int(u_data.get("balance") or 0) + amount
            u_data["topup_total"] = int(u_data.get("topup_total") or 0) + amount
            await save_now()
            # To'lov xabarini o'chiramiz
            if topup_msg_id:
                try:
                    await context.bot.delete_message(chat_id=uid, message_id=topup_msg_id)
                except Exception:
                    pass
            try:
                await context.bot.send_message(
                    uid,
                    f"<blockquote>✅ <b>PUL O'TKAZILDI!</b>\n\n"
                    f"💵 Miqdor: <b>{amount:,} so'm</b> balansingizga qo'shildi!\n"
                    f"💰 Joriy balans: <b>{u_data['balance']:,} so'm</b>\n\n"
                    f"Endi balansdan pullik qismlarni tomosha qilishingiz mumkin! 🎬</blockquote>",
                    parse_mode="HTML")
            except Exception as e:
                logger.warning(f"CheckCard poll notify xato: {e}")
            # Adminga xabar
            try:
                u_d = RAM.users.get(str(uid)) or {}
                u_name = u_d.get("name") or u_d.get("first_name") or f"ID: {uid}"
                u_uname = u_d.get("username") or ""
                uname_adm = f"@{u_uname}" if u_uname else f"ID: {uid}"
                if u_uname:
                    lichka_url = f"https://t.me/{u_uname}"
                else:
                    lichka_url = f"tg://user?id={uid}"
                tashkent_time = _tashkent_now_str()
                card_info = f"\n💳 Karta: <code>{RAM.card_number}</code>" if RAM.card_number else ""
                adm_cap = (
                    f"<blockquote>"
                    f"✅ <b>AUTO TO'LOV ORQALI TO'LANDI</b>\n\n"
                    f"👤 <b>Ism:</b> {u_name}\n"
                    f"🆔 <b>ID:</b> <code>{uid}</code>\n"
                    f"📱 <b>Username:</b> {uname_adm}\n\n"
                    f"💵 <b>To'langan summa:</b> <b>{amount:,} so'm</b>\n"
                    f"💰 <b>Joriy balans:</b> <b>{u_data['balance']:,} so'm</b>\n"
                    f"{card_info}\n\n"
                    f"🕐 <b>Vaqt (Toshkent):</b> {tashkent_time}"
                    f"</blockquote>"
                )
                lichka_kb_poll = {"inline_keyboard": [[{"text": "👤 Foydalanuvchi lichkasi", "url": lichka_url}]]}
                await context.bot.send_message(
                    ADMIN_ID, adm_cap, parse_mode="HTML",
                    reply_markup=lichka_kb_poll)
            except Exception as _e:
                logger.warning(f"Poll admin notify xato: {_e}")

        elif cc_status == "cancel":
            context.job.schedule_removal()
            pay["cc_status"] = "cancelled"
            await schedule_save()
            try:
                await context.bot.send_message(
                    uid,
                    f"❌ <b>To'lov bekor qilindi.</b>\n\n"
                    f"💵 Miqdor: <b>{amount:,} so'm</b>\n"
                    f"Qayta urinish uchun balans bo'limiga boring.",
                    parse_mode="HTML")
            except Exception:
                pass

    except Exception as e:
        logger.error(f"_checkcard_poll_job xato: {e}")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment o'zgaruvchisi kiritilmagan")
    if not ADMIN_ID:
        raise RuntimeError("ADMIN_ID environment o'zgaruvchisi kiritilmagan")

    # ── Railway health check serverni ishga tushur ────────
    _start_health_server()

    # ── Ishga tushganda bazadan RAM ga yukla ──────────────
    db_initial_load()
    logger.info(f"🚀 RAM cache: {len(RAM.movies)} kino, {len(RAM.users)} user yuklandi")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(15)
        .pool_timeout(30)
        .build()
    )

    # Global bot reference — CheckCard webhook uchun
    global _BOT_APP
    _BOT_APP = app
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.Sticker.ALL, sticker_handler))
    app.add_handler(MessageHandler(
        filters.PHOTO | filters.VIDEO | filters.Document.ALL, media_handler))
    # So'rovli kanal uchun — join request handler
    from telegram.ext import ChatJoinRequestHandler
    app.add_handler(ChatJoinRequestHandler(join_request_handler))

    # ── Har 5 daqiqada JSONBlob ga sinxron saqlash ────────
    async def _periodic_sync(context_job):
        try:
            data    = RAM.to_dict()
            was_down = DB_STATUS.get("ram_only", False)
            ok      = await asyncio.to_thread(_save_jsonblob, data)
            _save_local(data)
            now_str = datetime.now().strftime("%H:%M:%S")
            if ok:
                DB_STATUS.update({
                    "storage_ok": True, "fail_count": 0,
                    "last_save_ok": now_str, "ram_only": False,
                })
                if was_down:
                    try:
                        await context_job.bot.send_message(
                            ADMIN_ID,
                            f"✅ <b>PostgreSQL tiklandi!</b>\n{now_str}\n"
                            f"RAMdagi {len(RAM.movies)} kino saqlandi.",
                            parse_mode="HTML")
                    except: pass
            else:
                DB_STATUS["fail_count"] = DB_STATUS.get("fail_count", 0) + 1
                DB_STATUS["last_err"]   = now_str
                if DB_STATUS["fail_count"] >= 2:
                    DB_STATUS.update({"storage_ok": False, "ram_only": True})
                if DB_STATUS["fail_count"] == 2:
                    try:
                        await context_job.bot.send_message(
                            ADMIN_ID,
                            f"⚠️ <b>PostgreSQL ishlamayapti!</b>\n{now_str}\n"
                            f"Bot RAMdan ishlayapti (ma'lumotlar yo'qolmaydi).",
                            parse_mode="HTML")
                    except: pass
            status = "✅" if ok else "⚠️"
            logger.info(f"{status} Periodik sync: {len(RAM.movies)} kino, {len(RAM.users)} user")
        except Exception as e:
            logger.error(f"Periodik sync xato: {e}")

    async def _startup_notify(context_job):
        try:
            ok = await asyncio.to_thread(_save_postgres, RAM.to_dict())
            now_str = datetime.now().strftime("%H:%M:%S")
            if ok:
                DB_STATUS.update({"storage_ok": True, "last_save_ok": now_str, "ram_only": False})
                storage_msg = f"🟢 PostgreSQL ishlayapti — {now_str}"
            else:
                DB_STATUS.update({"storage_ok": False, "ram_only": True, "last_err": now_str})
                storage_msg = f"🔴 PostgreSQL ishlamayapti! Bot RAMdan ishlaydi."
            webhook_url = f"{RAILWAY_URL}{CHECKCARD_WEBHOOK_PATH}"
            await context_job.bot.send_message(
                ADMIN_ID,
                f"🚀 <b>Bot v20 Railway da ishga tushdi!</b>\n\n"
                f"💾 RAM: <b>{len(RAM.movies)}</b> kino, <b>{len(RAM.users)}</b> user\n"
                f"📦 Storage: {storage_msg}\n\n"
                f"💳 CheckCard Webhook URL:\n<code>{webhook_url}</code>\n"
                f"<i>(Bu URLni @CheckCardUz_bot ga webhook sifatida kiriting)</i>\n\n"
                f"✅ Barcha so'rovlar RAMdan javob beradi — tez!",
                parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Startup notify xato: {e}")

    if app.job_queue:
        app.job_queue.run_once(_startup_notify, when=5)
        app.job_queue.run_repeating(_periodic_sync, interval=120, first=60)
        logger.info("🔄 Periodik sync yoqildi (har 2 daqiqada)")

    logger.info(f"🚀 Bot v20 Railway ishga tushdi! RAM: {len(RAM.movies)} kino, {len(RAM.users)} user")
    app.run_polling(drop_pending_updates=True, allowed_updates=[
        "message", "callback_query", "chat_join_request"
    ])


if __name__ == "__main__":
    main()
