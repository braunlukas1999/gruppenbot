"""
Gruppen-Bot für ein Telegram-Forum (Gruppe mit Themen).

Was er macht:
  1. Beitritts-Prüfung: Neue Mitglieder werden stummgeschaltet, müssen bestätigen,
     dass sie die Regeln gelesen haben, und eine leichte Rechenaufgabe lösen.
     Wer das nicht innerhalb der Zeit schafft, wird entfernt (kann aber neu beitreten).
  2. Themen-Regeln: In jedem Thema sind nur bestimmte Inhalte erlaubt
     (z. B. nur Fotos). Alles andere wird gelöscht, mit kurzem Hinweis.

  3. Meldungen: /report und /admin schicken Meldungen privat an alle Admins.

Die Regeln stellt man direkt in Telegram ein (/regeln, /erlaubt ...), für beliebig
viele Gruppen. Sie werden in daten.json gespeichert. Grundeinstellungen: config.yaml.
"""

import logging
import random
import time
from pathlib import Path

import yaml
from telegram import (
    ChatMember,
    ChatMemberUpdated,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.helpers import mention_html
import json

CONFIG_PATH = Path(__file__).with_name("config.yaml")
# Hier speichert der Bot, was Admins per Befehl in Telegram einstellen (/thema, /erlaubt ...).
# Wird automatisch angelegt.
DATA_PATH = Path(__file__).with_name("daten.json")
TOKEN_PATH = Path(__file__).with_name("token.txt")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

# Namen der Inhaltsarten, die man in config.yaml unter "erlaubt" benutzen kann.
ALL_TYPES = [
    "text", "foto", "video", "gif", "sticker", "sprachnachricht",
    "audio", "datei", "videonachricht", "umfrage", "standort", "kontakt",
]

NICE_NAMES = {
    "text": "Text", "foto": "Fotos", "video": "Videos", "gif": "GIFs",
    "sticker": "Sticker", "sprachnachricht": "Sprachnachrichten",
    "audio": "Audio", "datei": "Dateien", "videonachricht": "Videonachrichten",
    "umfrage": "Umfragen", "standort": "Standorte", "kontakt": "Kontakte",
}

MUTED = ChatPermissions.no_permissions()


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------

SYNONYMS = {
    "text": "text", "texte": "text", "nachrichten": "text", "schreiben": "text",
    "foto": "foto", "fotos": "foto", "bild": "foto", "bilder": "foto",
    "video": "video", "videos": "video", "gif": "gif", "gifs": "gif",
    "sticker": "sticker", "audio": "audio", "musik": "audio",
    "sprachnachricht": "sprachnachricht", "sprachnachrichten": "sprachnachricht",
    "datei": "datei", "dateien": "datei",
    "videonachricht": "videonachricht", "videonachrichten": "videonachricht",
    "umfrage": "umfrage", "umfragen": "umfrage", "standort": "standort",
    "standorte": "standort", "kontakt": "kontakt", "kontakte": "kontakt",
}


def norm(name) -> str:
    """'📸 Fotos!' -> 'fotos'  (Emojis, Satzzeichen und Groß/klein egal)"""
    s = "".join(ch if ch.isalnum() else " " for ch in str(name).lower())
    return " ".join(s.split())


def parse_rule(text: str) -> dict | None:
    """'nur Text und Fotos' -> {erlaubt: [text, foto]},  'nur Admins' -> {nur_admins: True}"""
    words = norm(text).split()
    if "admins" in words or "admin" in words:
        return {"erlaubt": [], "nur_admins": True}
    if "alles" in words:
        return {"erlaubt": list(ALL_TYPES), "nur_admins": False}
    types = []
    for w in words:
        t = SYNONYMS.get(w)
        if t and t not in types:
            types.append(t)
    return {"erlaubt": types, "nur_admins": False} if types else None


def parse_rules_message(text: str):
    """Liest Zeilen wie 'Fotos – nur Fotos' oder 'chat: text, foto'."""
    ok, skipped = [], []
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("/"):
        first = lines[0].split(maxsplit=1)  # "/regeln Fotos: nur Fotos" -> Rest der 1. Zeile zählt mit
        lines = ([first[1]] if len(first) > 1 else []) + lines[1:]
    for line in lines:
        line = line.strip()
        if not line:
            continue
        for sep in (":", " – ", " — ", " - ", "="):
            if sep in line:
                left, right = line.split(sep, 1)
                break
        else:
            skipped.append(line)
            continue
        name, rule = norm(left), parse_rule(right)
        if name and rule:
            ok.append((name, rule))
        else:
            skipped.append(line)
    return ok, skipped


def make_rule(r: dict) -> dict:
    return {"allowed": set(r.get("erlaubt") or []), "admins_only": bool(r.get("nur_admins"))}


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # Token aus token.txt (wird bei der Server-Einrichtung angelegt)
    if TOKEN_PATH.exists() and TOKEN_PATH.read_text(encoding="utf-8").strip():
        cfg["token"] = TOKEN_PATH.read_text(encoding="utf-8").strip()
    return cfg


def global_rules(context) -> dict:
    """Regeln nach Themen-Namen, gelten in allen Gruppen.
    Erst config.yaml (vorlagen), darüber was per /regeln eingestellt wurde."""
    rules = {}
    for name, r in (cfg(context).get("vorlagen") or {}).items():
        rules[norm(name)] = r or {}
    rules.update(data(context).get("vorlagen", {}))
    return rules


def group(context, chat_id: int) -> dict:
    g = data(context).setdefault("gruppen", {}).setdefault(str(chat_id), {})
    g.setdefault("namen", {})
    g.setdefault("regeln", {})
    return g


def topic_name(context, chat_id: int, key) -> str | None:
    if key == "general":
        return "General"
    return group(context, chat_id)["namen"].get(str(key))


def rule_for(context, chat_id: int, key):
    """Welche Regel gilt in diesem Thema? (Name, Regel oder None = alles erlaubt)"""
    g = group(context, chat_id)
    name = topic_name(context, chat_id, key)
    if str(key) in g["regeln"]:
        return name, make_rule(g["regeln"][str(key)])
    if name and norm(name) in global_rules(context):
        return name, make_rule(global_rules(context)[norm(name)])
    return name, None


def topic_id_by_name(context, chat_id: int, name: str):
    n = norm(name)
    if n == "general":
        return "general"
    for tid, tname in group(context, chat_id)["namen"].items():
        if norm(tname) == n:
            return int(tid)
    return None


def learn_topic_name(context, msg: Message):
    """Der Bot merkt sich automatisch, wie die Themen heißen."""
    name = None
    if msg.forum_topic_created:
        name = msg.forum_topic_created.name
    elif msg.forum_topic_edited and msg.forum_topic_edited.name:
        name = msg.forum_topic_edited.name
    elif msg.is_topic_message and msg.reply_to_message and msg.reply_to_message.forum_topic_created:
        name = msg.reply_to_message.forum_topic_created.name
    if not name or not msg.message_thread_id:
        return
    namen = group(context, msg.chat.id)["namen"]
    if namen.get(str(msg.message_thread_id)) != name:
        namen[str(msg.message_thread_id)] = name
        save_data(data(context))
        log.info("Thema gelernt: %s = %s", msg.message_thread_id, name)


def load_data() -> dict:
    if DATA_PATH.exists():
        try:
            return json.loads(DATA_PATH.read_text(encoding="utf-8"))
        except ValueError:
            log.warning("daten.json ist beschädigt und wird ignoriert.")
    return {}


def save_data(data: dict):
    tmp = DATA_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA_PATH)


def cfg(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.bot_data["config"]


def data(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.bot_data["data"]


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def topic_key(msg: Message):
    """Gibt die Themen-ID zurück, oder 'general' für das Thema General."""
    if msg.is_topic_message and msg.message_thread_id:
        return msg.message_thread_id
    return "general"


def content_type(msg: Message):
    """Welche Art von Inhalt ist das? None = Systemnachricht (wird ignoriert)."""
    if msg.animation:
        return "gif"
    if msg.photo:
        return "foto"
    if msg.video:
        return "video"
    if msg.sticker:
        return "sticker"
    if msg.voice:
        return "sprachnachricht"
    if msg.video_note:
        return "videonachricht"
    if msg.audio:
        return "audio"
    if msg.document:
        return "datei"
    if msg.poll:
        return "umfrage"
    if msg.location or msg.venue:
        return "standort"
    if msg.contact:
        return "kontakt"
    if msg.text:
        return "text"
    return None


async def admin_ids(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> set:
    """Liste der Admins, wird 5 Minuten zwischengespeichert."""
    cache = context.bot_data.setdefault("admins", {})
    entry = cache.get(chat_id)
    if entry and time.time() - entry[0] < 300:
        return entry[1]
    admins = await context.bot.get_chat_administrators(chat_id)
    ids = {a.user.id for a in admins}
    cache[chat_id] = (time.time(), ids)
    return ids


async def is_admin(context, chat_id: int, msg_or_user_id) -> bool:
    if isinstance(msg_or_user_id, Message):
        msg = msg_or_user_id
        # Anonyme Admins schreiben "als Gruppe"
        if msg.sender_chat and msg.sender_chat.id == chat_id:
            return True
        if not msg.from_user:
            return False
        user_id = msg.from_user.id
    else:
        user_id = msg_or_user_id
    return user_id in await admin_ids(context, chat_id)


def allowed_group(context, chat_id: int) -> bool:
    only = cfg(context).get("nur_diese_gruppen") or []
    return not only or chat_id in [int(x) for x in only]


async def delete_later(context: ContextTypes.DEFAULT_TYPE):
    chat_id, message_id = context.job.data
    try:
        await context.bot.delete_message(chat_id, message_id)
    except TelegramError:
        pass


def schedule_delete(context, chat_id: int, message_id: int, seconds: int):
    context.job_queue.run_once(delete_later, seconds, data=(chat_id, message_id))


def rules_link(context, chat) -> str | None:
    name = str(cfg(context).get("pruefung", {}).get("regeln_thema", "regeln"))
    tid = topic_id_by_name(context, chat.id, name)
    if not tid or tid == "general":
        return None
    if chat.username:
        return f"https://t.me/{chat.username}/{tid}"
    internal = str(chat.id).removeprefix("-100")
    return f"https://t.me/c/{internal}/{tid}"


def welcome_thread(context, chat_id: int):
    name = str(cfg(context).get("pruefung", {}).get("willkommen_thema", "general"))
    tid = topic_id_by_name(context, chat_id, name)
    return tid if isinstance(tid, int) else None


# ---------------------------------------------------------------------------
# Teil 1: Themen-Regeln
# ---------------------------------------------------------------------------

async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or msg.chat.type != ChatType.SUPERGROUP or not allowed_group(context, msg.chat.id):
        return

    learn_topic_name(context, msg)

    if is_report_command(msg):
        return  # /report und /admin sind überall erlaubt

    ctype = content_type(msg)
    if ctype is None:
        return  # Systemnachricht (z. B. "XY ist beigetreten")

    key = topic_key(msg)
    name, rule = rule_for(context, msg.chat.id, key)
    if rule is None:
        return  # für dieses Thema gibt es keine Regel -> alles erlaubt

    if await is_admin(context, msg.chat.id, msg):
        return  # Admins dürfen alles

    if rule["admins_only"]:
        reason = "In diesem Thema dürfen nur Admins schreiben."
    elif ctype in rule["allowed"]:
        return
    else:
        erlaubt = ", ".join(NICE_NAMES.get(a, a) for a in sorted(rule["allowed"])) or "nichts"
        reason = f"In „{name or 'diesem Thema'}“ ist nur erlaubt: {erlaubt}."

    try:
        await msg.delete()
    except TelegramError as e:
        log.warning("Konnte Nachricht nicht löschen (ist der Bot Admin mit Lösch-Recht?): %s", e)
        return

    # Kurzer Hinweis, aber höchstens alle 30 Sekunden pro Person und Thema
    # (sonst gibt es bei einem Album mit 10 Bildern 10 Hinweise).
    seconds = int(cfg(context).get("hinweis_sekunden", 10))
    if seconds <= 0 or not msg.from_user:
        return
    last = context.bot_data.setdefault("last_notice", {})
    nk = (msg.from_user.id, key)
    if time.time() - last.get(nk, 0) < 30:
        return
    last[nk] = time.time()
    try:
        notice = await context.bot.send_message(
            msg.chat.id,
            f"{mention_html(msg.from_user.id, msg.from_user.first_name)}, deine Nachricht wurde entfernt. {reason}",
            message_thread_id=msg.message_thread_id if key != "general" else None,
            parse_mode=ParseMode.HTML,
        )
        schedule_delete(context, msg.chat.id, notice.message_id, seconds)
    except TelegramError as e:
        log.warning("Hinweis konnte nicht gesendet werden: %s", e)


# ---------------------------------------------------------------------------
# Teil 2: Beitritts-Prüfung
# ---------------------------------------------------------------------------

def joined(upd: ChatMemberUpdated) -> bool:
    old, new = upd.old_chat_member, upd.new_chat_member
    was_in = old.status in (ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER) or (
        old.status == ChatMember.RESTRICTED and old.is_member
    )
    is_in = new.status == ChatMember.MEMBER or (
        new.status == ChatMember.RESTRICTED and new.is_member
    )
    return is_in and not was_in


async def on_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upd = update.chat_member
    if not upd or not joined(upd) or not allowed_group(context, upd.chat.id):
        return
    p = cfg(context).get("pruefung", {})
    if not p.get("aktiv", True):
        return
    user = upd.new_chat_member.user
    if user.is_bot:
        return
    chat = upd.chat

    # Falls die Person schon einmal mitten in der Prüfung war (raus und wieder rein)
    old = context.bot_data.get("pending", {}).pop((chat.id, user.id), None)
    if old:
        old["job"].schedule_removal()
        try:
            await context.bot.delete_message(chat.id, old["msg_id"])
        except TelegramError:
            pass

    try:
        await context.bot.restrict_chat_member(chat.id, user.id, MUTED)
    except TelegramError as e:
        log.warning("Konnte %s nicht stummschalten (Bot braucht 'Mitglieder sperren'): %s", user.id, e)
        return

    minutes = int(p.get("zeit_minuten", 5))
    link = rules_link(context, chat)
    regeln = f'<a href="{link}">Regeln</a>' if link else "Regeln"
    text = (
        f"👋 Willkommen {mention_html(user.id, user.first_name)}!\n\n"
        f"Bevor du schreiben kannst:\n"
        f"1️⃣ Lies bitte die {regeln}.\n"
        f"2️⃣ Drück dann auf den Knopf unten.\n\n"
        f"⏳ Du hast {minutes} Minuten Zeit, sonst wirst du aus der Gruppe entfernt."
    )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(
        "✅ Ich habe die Regeln gelesen", callback_data=f"rules:{user.id}")]])

    thread = welcome_thread(context, chat.id)

    try:
        sent = await context.bot.send_message(
            chat.id, text, reply_markup=keyboard, parse_mode=ParseMode.HTML,
            message_thread_id=thread, disable_web_page_preview=True,
        )
    except TelegramError as e:
        log.warning("Willkommensnachricht fehlgeschlagen: %s", e)
        return

    job = context.job_queue.run_once(
        verification_timeout, minutes * 60,
        data={"chat_id": chat.id, "user_id": user.id, "msg_id": sent.message_id},
        name=f"verify:{chat.id}:{user.id}",
    )
    context.bot_data.setdefault("pending", {})[(chat.id, user.id)] = {
        "msg_id": sent.message_id, "answer": None, "wrong": 0, "job": job,
        "name": user.first_name,
    }
    log.info("Neues Mitglied %s (%s) – Prüfung gestartet.", user.first_name, user.id)


def math_keyboard(user_id: int):
    a, b = random.randint(1, 10), random.randint(1, 10)
    answer = a + b
    options = {answer}
    while len(options) < 4:
        options.add(max(1, answer + random.choice([-3, -2, -1, 1, 2, 3, 4])))
    options = list(options)
    random.shuffle(options)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(str(o), callback_data=f"math:{user_id}:{o}") for o in options
    ]])
    return f"{a} + {b}", answer, kb


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = (q.data or "").split(":")
    if len(parts) < 2 or parts[0] not in ("rules", "math"):
        return
    target = int(parts[1])
    if q.from_user.id != target:
        await q.answer("Dieser Knopf ist nicht für dich 🙂", show_alert=True)
        return

    chat_id = q.message.chat.id
    pending = context.bot_data.get("pending", {}).get((chat_id, target))
    if not pending:
        await q.answer("Diese Prüfung ist abgelaufen.", show_alert=True)
        return

    name = mention_html(target, pending["name"])

    if parts[0] == "rules":
        question, answer, kb = math_keyboard(target)
        pending["answer"] = answer
        await q.answer()
        await q.edit_message_text(
            f"Danke {name}! Letzter Schritt:\n\n🧮 Was ist <b>{question}</b>?",
            reply_markup=kb, parse_mode=ParseMode.HTML,
        )
        return

    # Rechenaufgabe
    chosen = int(parts[2])
    if pending["answer"] is None:
        await q.answer()
        return
    if chosen == pending["answer"]:
        await q.answer("Richtig! ✅")
        await finish_verification(context, chat_id, target, success=True)
        return

    pending["wrong"] += 1
    max_wrong = int(cfg(context).get("pruefung", {}).get("max_falsche_antworten", 2))
    if pending["wrong"] >= max_wrong:
        await q.answer("Leider falsch. Du wirst entfernt, kannst aber neu beitreten.", show_alert=True)
        await finish_verification(context, chat_id, target, success=False)
        return
    question, answer, kb = math_keyboard(target)
    pending["answer"] = answer
    await q.answer("Leider falsch, versuch es nochmal.", show_alert=True)
    await q.edit_message_text(
        f"{name}, neue Aufgabe:\n\n🧮 Was ist <b>{question}</b>?",
        reply_markup=kb, parse_mode=ParseMode.HTML,
    )


async def verification_timeout(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    await finish_verification(context, d["chat_id"], d["user_id"], success=False, timed_out=True)


async def finish_verification(context, chat_id: int, user_id: int, success: bool, timed_out=False):
    pending = context.bot_data.get("pending", {}).pop((chat_id, user_id), None)
    if not pending:
        return
    if not timed_out and pending.get("job"):
        pending["job"].schedule_removal()

    try:
        await context.bot.delete_message(chat_id, pending["msg_id"])
    except TelegramError:
        pass

    if success:
        try:
            chat = await context.bot.get_chat(chat_id)
            perms = chat.permissions or ChatPermissions.all_permissions()
            await context.bot.restrict_chat_member(chat_id, user_id, perms)
        except TelegramError as e:
            log.warning("Konnte %s nicht freischalten: %s", user_id, e)
            return
        log.info("%s (%s) hat die Prüfung bestanden.", pending["name"], user_id)
        seconds = int(cfg(context).get("pruefung", {}).get("erfolg_nachricht_sekunden", 30))
        if seconds > 0:
            try:
                m = await context.bot.send_message(
                    chat_id,
                    f"🎉 {mention_html(user_id, pending['name'])} ist jetzt freigeschaltet. Viel Spaß!",
                    parse_mode=ParseMode.HTML,
                    message_thread_id=welcome_thread(context, chat_id),
                )
                schedule_delete(context, chat_id, m.message_id, seconds)
            except TelegramError:
                pass
    else:
        # Entfernen (bannen + sofort entbannen = rauswerfen, neu beitreten bleibt möglich)
        try:
            await context.bot.ban_chat_member(chat_id, user_id)
            await context.bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
            log.info("%s (%s) wurde entfernt (%s).", pending["name"], user_id,
                     "Zeit abgelaufen" if timed_out else "falsche Antworten")
        except TelegramError as e:
            log.warning("Konnte %s nicht entfernen: %s", user_id, e)


# ---------------------------------------------------------------------------
# Teil 3: Meldungen (/report und /admin)
# ---------------------------------------------------------------------------

REPORT_COMMANDS = ("report", "admin")


def is_report_command(msg: Message) -> bool:
    text = (msg.text or "").strip()
    if not text.startswith("/"):
        return False
    cmd = text[1:].split()[0].split("@")[0].lower() if len(text) > 1 else ""
    return cmd in REPORT_COMMANDS


def real_reply(msg: Message):
    """Die Nachricht, auf die geantwortet wurde (nicht der automatische Themen-Anfang)."""
    r = msg.reply_to_message
    if not r or r.forum_topic_created or (msg.is_topic_message and r.message_id == msg.message_thread_id):
        return None
    return r


def describe(msg: Message) -> str:
    ctype = content_type(msg) or "Nachricht"
    text = msg.text or msg.caption or ""
    if len(text) > 300:
        text = text[:300] + "…"
    label = "" if ctype == "text" else f"[{NICE_NAMES.get(ctype, ctype)}] "
    return html_escape(label + text) if (label or text) else "(kein Text)"


def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or msg.chat.type != ChatType.SUPERGROUP or not allowed_group(context, msg.chat.id):
        return
    m_cfg = cfg(context).get("meldungen", {}) or {}
    if not m_cfg.get("aktiv", True) or not msg.from_user:
        return
    learn_topic_name(context, msg)
    chat = msg.chat
    reporter = msg.from_user
    key = topic_key(msg)
    thread = msg.message_thread_id if key != "general" else None

    async def short_reply(text: str):
        try:
            m = await context.bot.send_message(chat.id, text, message_thread_id=thread,
                                               parse_mode=ParseMode.HTML)
            schedule_delete(context, chat.id, m.message_id, 10)
        except TelegramError:
            pass

    # Gegen Spam: pro Person nur alle X Sekunden
    wait = int(m_cfg.get("sperre_sekunden", 60))
    last = context.bot_data.setdefault("last_report", {})
    if time.time() - last.get(reporter.id, 0) < wait and not await is_admin(context, chat.id, reporter.id):
        try:
            await msg.delete()
        except TelegramError:
            pass
        await short_reply("⏳ Bitte warte kurz, bevor du wieder etwas meldest.")
        return
    last[reporter.id] = time.time()

    reported = real_reply(msg)
    reason = (msg.text or "").split(maxsplit=1)
    reason = html_escape(reason[1]) if len(reason) > 1 else ""
    tname = topic_name(context, chat.id, key) or f"Thema {key}"

    lines = [f"🚨 <b>Neue Meldung</b> in <b>{html_escape(chat.title or 'Gruppe')}</b>",
             f"Thema: {html_escape(tname)}",
             f"Von: {mention_html(reporter.id, reporter.first_name)}"]
    buttons = []
    if reported:
        target = reported.from_user
        if target:
            lines.append(f"Gemeldet: {mention_html(target.id, target.first_name)} (ID <code>{target.id}</code>)")
        lines.append(f"Nachricht: {describe(reported)}")
        link = reported.link
    else:
        target = None
        link = msg.link
        lines.append("Art: Hilferuf an die Admins (ohne bestimmte Nachricht)")
    if reason:
        lines.append(f"Grund: {reason}")
    if link:
        lines.append(f'\n👉 <a href="{link}">Zur Nachricht springen</a>')

    report_id = context.bot_data.get("report_counter", 0) + 1
    context.bot_data["report_counter"] = report_id
    if reported:
        buttons.append(InlineKeyboardButton("🗑 Löschen", callback_data=f"rep:{report_id}:del"))
    if target and not target.is_bot and not await is_admin(context, chat.id, target.id):
        buttons.append(InlineKeyboardButton("🔇 24h stumm", callback_data=f"rep:{report_id}:mute"))
        buttons.append(InlineKeyboardButton("🚫 Bannen", callback_data=f"rep:{report_id}:ban"))
    kb = InlineKeyboardMarkup([buttons, [InlineKeyboardButton("✅ Erledigt", callback_data=f"rep:{report_id}:ok")]]
                              if buttons else
                              [[InlineKeyboardButton("✅ Erledigt", callback_data=f"rep:{report_id}:ok")]])
    text = "\n".join(lines)

    # Wohin geht die Meldung?
    receivers = []
    extra_chat = str(m_cfg.get("meldungen_chat_id", "") or "").strip()
    if extra_chat and extra_chat.upper() != "NOCH_EINTRAGEN":
        receivers.append(int(extra_chat))
    if m_cfg.get("an_admins_privat", True):
        admins = await context.bot.get_chat_administrators(chat.id)
        receivers += [a.user.id for a in admins if not a.user.is_bot]

    sent = []
    for r in receivers:
        try:
            m = await context.bot.send_message(r, text, reply_markup=kb, parse_mode=ParseMode.HTML,
                                               disable_web_page_preview=True)
            sent.append((m.chat.id, m.message_id))
        except TelegramError:
            pass  # Admin hat den Bot noch nie privat gestartet

    context.bot_data.setdefault("reports", {})[report_id] = {
        "chat_id": chat.id,
        "msg_id": reported.message_id if reported else None,
        "user_id": target.id if target else None,
        "user_name": target.first_name if target else None,
        "sent": sent,
        "base_text": text,
    }

    if reported:
        try:
            await msg.delete()  # den /report-Befehl selbst entfernen, damit es sauber bleibt
        except TelegramError:
            pass

    if sent:
        await short_reply(f"✅ Danke {mention_html(reporter.id, reporter.first_name)}, die Admins wurden informiert.")
    else:
        # Kein Admin erreichbar: dann eben in der Gruppe die Admins markieren
        admins = await context.bot.get_chat_administrators(chat.id)
        tags = " ".join(mention_html(a.user.id, a.user.first_name) for a in admins if not a.user.is_bot)
        try:
            await context.bot.send_message(chat.id, f"🚨 Meldung von {mention_html(reporter.id, reporter.first_name)}: {tags}",
                                           message_thread_id=thread, parse_mode=ParseMode.HTML,
                                           reply_to_message_id=reported.message_id if reported else None)
        except TelegramError:
            pass
        log.warning("Keine Meldung zugestellt. Admins müssen den Bot einmal privat mit /start anschreiben.")


async def on_report_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, rid, action = q.data.split(":")
    rep = context.bot_data.get("reports", {}).get(int(rid))
    if not rep:
        await q.answer("Diese Meldung ist zu alt (Bot wurde neu gestartet).", show_alert=True)
        return
    chat_id = rep["chat_id"]
    if not await is_admin(context, chat_id, q.from_user.id):
        await q.answer("Nur Admins können das.", show_alert=True)
        return

    who = html_escape(q.from_user.first_name)
    try:
        if action == "del":
            await context.bot.delete_message(chat_id, rep["msg_id"])
            result = f"🗑 Nachricht gelöscht von {who}"
        elif action == "mute":
            await context.bot.restrict_chat_member(chat_id, rep["user_id"], MUTED,
                                                   until_date=int(time.time()) + 24 * 3600)
            result = f"🔇 {html_escape(rep['user_name'])} für 24 Stunden stummgeschaltet von {who}"
        elif action == "ban":
            await context.bot.ban_chat_member(chat_id, rep["user_id"])
            result = f"🚫 {html_escape(rep['user_name'])} gebannt von {who}"
        else:
            result = f"✅ Erledigt von {who}"
    except TelegramError as e:
        await q.answer(f"Hat nicht geklappt: {e}", show_alert=True)
        return

    await q.answer("Gemacht ✅")
    rep["base_text"] += f"\n\n{result}"
    done = action == "ok"
    for cid, mid in rep["sent"]:
        try:
            await context.bot.edit_message_text(
                rep["base_text"], cid, mid, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
                reply_markup=None if done else q.message.reply_markup,
            )
        except TelegramError:
            pass
    if done:
        context.bot_data["reports"].pop(int(rid), None)


# ---------------------------------------------------------------------------
# Befehle für Admins
# ---------------------------------------------------------------------------

async def cmd_themaid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/themaid – zeigt Gruppen-ID und Themen-ID (nur für Admins)."""
    msg = update.message
    if msg.chat.type != ChatType.SUPERGROUP:
        await msg.reply_text("Bitte in der Gruppe benutzen.")
        return
    if not await is_admin(context, msg.chat.id, msg):
        return
    key = topic_key(msg)
    tid = "general" if key == "general" else str(key)
    await msg.reply_text(
        f"Gruppen-ID: <code>{msg.chat.id}</code>\nThemen-ID: <code>{tid}</code>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_neuladen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/neuladen – liest config.yaml neu ein, ohne Neustart."""
    msg = await admin_in_group(update, context)
    if not msg:
        return
    try:
        new = load_config()
    except Exception as e:  # noqa: BLE001 – Fehler in config.yaml anzeigen
        await msg.reply_text(f"❌ Fehler in config.yaml: {e}")
        return
    context.bot_data["config"] = new
    context.bot_data.pop("admins", None)
    await msg.reply_text("✅ config.yaml neu geladen.")


def describe_rule(r) -> str:
    if r is None:
        return "keine Regel (alles erlaubt)"
    if isinstance(r, dict) and "allowed" in r:
        r = {"erlaubt": r["allowed"], "nur_admins": r["admins_only"]}
    if r.get("nur_admins"):
        return "nur Admins"
    allowed = set(r.get("erlaubt") or [])
    if allowed >= set(ALL_TYPES):
        return "alles erlaubt"
    return " + ".join(NICE_NAMES.get(a, a) for a in ALL_TYPES if a in allowed) or "nichts erlaubt"


async def admin_in_group(update: Update, context) -> Message | None:
    msg = update.message
    if not msg or msg.chat.type != ChatType.SUPERGROUP or not allowed_group(context, msg.chat.id):
        return None
    if not await is_admin(context, msg.chat.id, msg):
        return None
    learn_topic_name(context, msg)
    return msg


def current_topic_text(context, msg: Message) -> str:
    key = topic_key(msg)
    name, rule = rule_for(context, msg.chat.id, key)
    return f"Thema <b>{html_escape(name or f'ID {key}')}</b>: {describe_rule(rule)}"


async def cmd_regeln(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/regeln + Liste: legt Regeln nach Themen-Namen fest, für ALLE Gruppen."""
    msg = await admin_in_group(update, context)
    if not msg:
        return
    ok, skipped = parse_rules_message(msg.text or "")
    if not ok:
        rules = global_rules(context)
        lines = ["📋 <b>Regeln für alle Gruppen</b> (nach Themen-Name):"]
        lines += [f"• {html_escape(n)}: {describe_rule(r)}" for n, r in rules.items()] or ["(noch keine)"]
        lines += ["", "Ändern: /regeln und darunter pro Zeile ein Thema, z. B.:",
                  "<code>/regeln\nFotos: nur Fotos\nChat: Text und Fotos\nRegeln: nur Admins</code>"]
        if skipped:
            lines += ["", "⚠️ Nicht verstanden: " + html_escape("; ".join(skipped))]
        await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return
    vorlagen = data(context).setdefault("vorlagen", {})
    for name, rule in ok:
        vorlagen[name] = rule
    save_data(data(context))
    lines = ["✅ <b>Gespeichert</b> (gilt in allen Gruppen für Themen mit diesem Namen):"]
    lines += [f"• {html_escape(n)}: {describe_rule(r)}" for n, r in ok]
    if skipped:
        lines += ["", "⚠️ Diese Zeilen habe ich nicht verstanden:"] + [f"– {html_escape(x)}" for x in skipped]
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_thema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/thema <name> – sagt dem Bot, wie dieses Thema heißt (meist nicht nötig)."""
    msg = await admin_in_group(update, context)
    if not msg:
        return
    parts = (msg.text or "").split(maxsplit=1)
    key = topic_key(msg)
    if len(parts) < 2 or key == "general":
        await msg.reply_text(current_topic_text(context, msg), parse_mode=ParseMode.HTML)
        return
    group(context, msg.chat.id)["namen"][str(key)] = parts[1].strip()
    save_data(data(context))
    await msg.reply_text("✅ " + current_topic_text(context, msg), parse_mode=ParseMode.HTML)


async def set_topic_exception(update, context, rule: dict | None):
    msg = await admin_in_group(update, context)
    if not msg:
        return
    regeln = group(context, msg.chat.id)["regeln"]
    key = str(topic_key(msg))
    if rule is None:
        regeln.pop(key, None)
    else:
        regeln[key] = rule
    save_data(data(context))
    extra = "" if rule is None else "\n<i>(gilt nur für dieses Thema in dieser Gruppe)</i>"
    await msg.reply_text("✅ " + current_topic_text(context, msg) + extra, parse_mode=ParseMode.HTML)


async def cmd_erlaubt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/erlaubt Text und Fotos – nur für das aktuelle Thema in dieser Gruppe."""
    rest = (update.message.text or "").split(maxsplit=1)
    rule = parse_rule(rest[1]) if len(rest) > 1 else None
    if rule is None:
        if await admin_in_group(update, context):
            await update.message.reply_text(
                "Beispiel: /erlaubt Text und Fotos\nMöglich: " + ", ".join(ALL_TYPES) + ", alles")
        return
    await set_topic_exception(update, context, rule)


async def cmd_nuradmins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/nuradmins – im aktuellen Thema dürfen nur noch Admins schreiben."""
    await set_topic_exception(update, context, {"erlaubt": [], "nur_admins": True})


async def cmd_standard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/standard – Ausnahme für dieses Thema löschen, es gilt wieder die Regel nach Name."""
    await set_topic_exception(update, context, None)


async def cmd_einstellungen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/einstellungen – zeigt, welche Regeln in dieser Gruppe gelten."""
    msg = await admin_in_group(update, context)
    if not msg:
        return
    chat_id = msg.chat.id
    g = group(context, chat_id)
    lines = [f"⚙️ <b>Regeln in {html_escape(msg.chat.title or 'dieser Gruppe')}</b>"]
    keys = ["general"] + sorted(int(k) for k in set(g["namen"]) | {k for k in g["regeln"] if k != "general"})
    seen = set()
    for key in keys:
        name, rule = rule_for(context, chat_id, key)
        seen.add(norm(name or ""))
        star = " ✳️" if str(key) in g["regeln"] else ""
        lines.append(f"• {html_escape(name or f'Thema {key}')}: {describe_rule(rule)}{star}")
    missing = [n for n in global_rules(context) if n not in seen]
    if missing:
        lines += ["", "❔ Noch nicht gefunden: " + html_escape(", ".join(missing)),
                  "<i>Schreib in diesen Themen einmal irgendwas (oder /thema Name), dann kennt der Bot sie.</i>"]
    if any(k in g["regeln"] for k in map(str, keys)):
        lines += ["", "✳️ = Ausnahme nur in dieser Gruppe (/standard macht sie rückgängig)"]
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/update – Bot neu starten (holt dabei die neueste Version)."""
    msg = await admin_in_group(update, context)
    if not msg:
        return
    await msg.reply_text("🔄 Ich starte neu und hole die neueste Version. Dauert ca. 30 Sekunden.")
    context.application.stop_running()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type == ChatType.PRIVATE:
        await update.message.reply_text(
            "Hallo! Ich bin der Gruppen-Bot 🙂\n\n"
            "Wenn du Admin bist: Ab jetzt schicke ich dir hier Meldungen, "
            "wenn jemand in der Gruppe /report oder /admin schreibt.")


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

def main():
    config = load_config()
    token = str(config.get("token", "")).strip()
    if not token or "HIER" in token.upper():
        raise SystemExit("❌ Bitte zuerst den Bot-Token in token.txt oder config.yaml eintragen (siehe Anleitung).")

    app = Application.builder().token(token).build()
    app.bot_data["config"] = config
    app.bot_data["data"] = load_data()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("themaid", cmd_themaid))
    app.add_handler(CommandHandler("neuladen", cmd_neuladen))
    app.add_handler(CommandHandler("thema", cmd_thema))
    app.add_handler(CommandHandler("erlaubt", cmd_erlaubt))
    app.add_handler(CommandHandler("nuradmins", cmd_nuradmins))
    app.add_handler(CommandHandler("einstellungen", cmd_einstellungen))
    app.add_handler(CommandHandler("regeln", cmd_regeln))
    app.add_handler(CommandHandler("standard", cmd_standard))
    app.add_handler(CommandHandler("update", cmd_update))
    app.add_handler(ChatMemberHandler(on_member_update, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(CommandHandler(list(REPORT_COMMANDS), cmd_report))
    app.add_handler(CallbackQueryHandler(on_report_button, pattern=r"^rep:"))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.ChatType.SUPERGROUP & ~filters.COMMAND, on_group_message))
    # Befehle von Nicht-Admins (z. B. /irgendwas) auch nach Themen-Regeln behandeln
    app.add_handler(MessageHandler(filters.ChatType.SUPERGROUP & filters.COMMAND, on_group_message), group=1)

    log.info("Bot läuft. Zum Beenden: Strg+C")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
