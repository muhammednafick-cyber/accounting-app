"""The General Chat over Telegram - through the Telegram Bot API.

The same answering code as WhatsApp (chat_channels); only the transport is
Telegram's:

  phone -> Telegram -> POST /telegram/webhook, carrying the secret token this
           app gave Telegram when it registered the webhook
        -> chat_channels.handle_text: linked user, their permissions, one
           company, the web chat's engine
        -> the reply goes back through sendMessage / sendDocument

Free: the Bot API has no charges. An administrator makes a bot with
@BotFather and pastes its token under Admin > Telegram Settings; the app then
registers the webhook itself. Read-only, like the web chat.
"""
import hmac
import html as html_lib
import re
import secrets
import threading

import requests
from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from database import whatsapp_db as db
from database.master_db import (get_system_setting, get_user_companies,
                                set_system_setting)

from .models import admin_required

telegram_bp = Blueprint("telegram_bp", __name__)

API_URL = "https://api.telegram.org"
CHANNEL = "telegram"
ID_PREFIX = "tg:"

SETTING_KEYS = {
    "bot_token": "telegram_bot_token",
    "webhook_secret": "telegram_webhook_secret",
    "bot_username": "telegram_bot_username",
    "ai_enabled": "telegram_ai_enabled",
    "enabled": "telegram_enabled",
}

COMMANDS_TEXT = (
    "*Commands*\n"
    "• /help — what you can ask\n"
    "• /pdf or /excel — the last table as a file\n"
    "• /company — list your companies; /company 2 switches\n"
    "• /reset — start a new conversation\n"
    "• /unlink — stop this chat using your account"
)

BOT_COMMANDS = [
    {"command": "help", "description": "What you can ask"},
    {"command": "pdf", "description": "The last table as a PDF"},
    {"command": "excel", "description": "The last table as an Excel file"},
    {"command": "company", "description": "List or switch companies"},
    {"command": "reset", "description": "Start a new conversation"},
    {"command": "unlink", "description": "Stop this chat using your account"},
]


# ------------------------------------------------------------------ settings

def setting(name, default=""):
    value = get_system_setting(SETTING_KEYS[name])
    return value if value not in (None, "") else default


def is_configured():
    return bool(setting("enabled") == "1" and setting("bot_token")
                and setting("webhook_secret"))


def _api(method):
    return f"{API_URL}/bot{setting('bot_token')}/{method}"


def _chat_id(ext_id):
    return ext_id[len(ID_PREFIX):] if ext_id.startswith(ID_PREFIX) else ext_id


# ------------------------------------------------------------------ sending

def to_telegram_html(text):
    """WhatsApp-style *bold* / _italic_ as Telegram HTML, everything else
    escaped - Telegram rejects a message whose HTML does not parse."""
    out = html_lib.escape(text or "", quote=False)
    out = re.sub(r"\*([^*\n]+)\*", r"<b>\1</b>", out)
    out = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"<i>\1</i>", out)
    return out


def _plain(text):
    return re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", re.sub(r"\*([^*\n]+)\*", r"\1", text or ""))


def send_text(ext_id, text):
    """One message. Falls back to plain text if Telegram cannot parse it."""
    chat_id = _chat_id(ext_id)
    try:
        response = requests.post(_api("sendMessage"), json={
            "chat_id": chat_id, "text": to_telegram_html(text)[:4096],
            "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
        if response.status_code == 400:
            response = requests.post(_api("sendMessage"), json={
                "chat_id": chat_id, "text": _plain(text)[:4096],
                "disable_web_page_preview": True}, timeout=20)
        if response.status_code >= 300:
            print(f"[telegram] send failed {response.status_code}: {response.text[:300]}")
            return False
        return True
    except Exception as exc:
        print(f"[telegram] send failed: {exc}")
        return False


def send_document(ext_id, data, filename, mime):
    try:
        response = requests.post(_api("sendDocument"), data={"chat_id": _chat_id(ext_id)},
                                 files={"document": (filename, data, mime)}, timeout=60)
        if response.status_code >= 300:
            print(f"[telegram] document failed {response.status_code}: {response.text[:300]}")
            return False
        return True
    except Exception as exc:
        print(f"[telegram] document failed: {exc}")
        return False


def _channel():
    from .chat_channels import Channel
    return Channel(
        key=CHANNEL, label="Telegram",
        send_text=lambda to, text: send_text(to, text),
        send_document=lambda to, data, name, mime: send_document(to, data, name, mime),
        ai_enabled=lambda: setting("ai_enabled") == "1",
        commands_text=COMMANDS_TEXT,
        not_linked_text=("This chat is not linked to a Prodata account yet.\n\n"
                         "In the app, open *Telegram* in the menu, choose *Get a link "
                         "code*, and tap the button it shows - or send the code here "
                         "as *LINK 123456*."),
    )


# ------------------------------------------------------------------ webhook

def secret_ok(header_value):
    """Telegram sends back the secret this app registered with the webhook.
    Without it, anyone could post fake messages to a public URL."""
    expected = setting("webhook_secret")
    return bool(expected and header_value and hmac.compare_digest(header_value, expected))


@telegram_bp.route("/telegram/webhook", methods=["POST"])
def webhook():
    if not secret_ok(request.headers.get("X-Telegram-Bot-Api-Secret-Token")):
        return "Forbidden", 403
    if not is_configured():
        return "OK", 200

    update = request.get_json(silent=True) or {}
    if not db.first_sighting(f"tg-update:{update.get('update_id')}"):
        return "OK", 200
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    # Private chats only: in a group, everyone in it would read the answers.
    if not message or chat.get("type") != "private":
        return "OK", 200
    dispatch(current_app._get_current_object(), message)
    return "OK", 200


def dispatch(app, message):
    threading.Thread(target=_answer_in_context, args=(app, message), daemon=True).start()


def _answer_in_context(app, message):
    with app.test_request_context("/telegram/webhook"):
        ext_id = ID_PREFIX + str((message.get("chat") or {}).get("id") or "")
        try:
            handle_message(message)
        except Exception as exc:
            print(f"[telegram] could not answer: {exc}")
            send_text(ext_id, "Sorry - something went wrong answering that. Please try again.")


def normalise(text):
    """Telegram's /commands as the words the shared code understands.

    "/start LINK123456" (the link button) -> "LINK 123456"
    "/pdf@ProdataBot"                     -> "pdf"
    "/company 2"                          -> "company 2"
    A bare "/start" returns None: the caller greets instead.
    """
    text = (text or "").strip()
    if not text.startswith("/"):
        return text
    command, _, rest = text[1:].partition(" ")
    command = command.split("@", 1)[0].lower()
    rest = rest.strip()
    if command == "start":
        match = re.fullmatch(r"(?i)link_?(\d{6})", rest)
        return f"LINK {match.group(1)}" if match else None
    return (command + " " + rest).strip()


def handle_message(message):
    from .chat_channels import handle_text

    chat_id = (message.get("chat") or {}).get("id")
    if chat_id is None:
        return
    ext_id = ID_PREFIX + str(chat_id)
    if "text" not in message:
        send_text(ext_id, "I can only read text messages. Type your question, "
                          "for example *cash balance*.")
        return
    text = normalise(message.get("text"))
    channel = _channel()
    if text is None:    # a bare /start
        if db.link_for_number(ext_id):
            send_text(ext_id, "Welcome back. Ask me anything, for example *cash balance*.\n\n"
                              + COMMANDS_TEXT)
        else:
            send_text(ext_id, channel.not_linked_text)
        return
    handle_text(channel, ext_id, text)


# ------------------------------------------------------------------ user page

@telegram_bp.route("/settings/telegram", methods=["GET", "POST"])
@login_required
def telegram_link_page():
    from flask import session

    companies = get_user_companies(current_user.id) or []
    code = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "start":
            try:
                company_id = int(request.form.get("company_id") or 0)
            except ValueError:
                company_id = 0
            if company_id not in [c["id"] for c in companies]:
                flash("Choose one of your companies.", "error")
                return redirect(url_for("telegram_bp.telegram_link_page"))
            code = db.start_link(current_user.id, company_id, channel=CHANNEL)
        elif action == "unlink":
            db.unlink_user(current_user.id, channel=CHANNEL)
            flash("Telegram unlinked. That chat can no longer ask about your accounts.",
                  "success")
            return redirect(url_for("telegram_bp.telegram_link_page"))

    link = db.link_for_user(current_user.id, channel=CHANNEL)
    linked = link if link and link.get("wa_id") else None
    company_names = {c["id"]: c["name"] for c in companies}
    username = setting("bot_username")
    return render_template(
        "telegram_link.html",
        configured=is_configured(),
        linked=linked,
        linked_company=company_names.get(linked["company_id"]) if linked else None,
        code=code,
        bot_username=username,
        start_link=(f"https://t.me/{username}?start=LINK{code}" if code and username else None),
        companies=companies,
        active_company_id=session.get("company_id"),
        code_minutes=db.CODE_TTL_MINUTES,
    )


# ------------------------------------------------------------------ admin page

def _webhook_url():
    return request.url_root.rstrip("/") + url_for("telegram_bp.webhook")


def _connect():
    """Check the token, then point Telegram at this app. Returns (ok, message)."""
    try:
        me = requests.get(_api("getMe"), timeout=15).json()
    except Exception as exc:
        return False, f"Could not reach Telegram: {exc}"
    if not me.get("ok"):
        return False, f"Telegram refused the token: {me.get('description', 'unknown error')}"
    username = (me.get("result") or {}).get("username") or ""
    set_system_setting(SETTING_KEYS["bot_username"], username)

    url = _webhook_url()
    if not url.startswith("https://"):
        return False, (f"Token works (@{username}), but Telegram only delivers to an https "
                       f"address and this page was opened on {url}. Save it again from the "
                       "live site.")
    result = requests.post(_api("setWebhook"), json={
        "url": url, "secret_token": setting("webhook_secret"),
        "allowed_updates": ["message"], "drop_pending_updates": True}, timeout=15).json()
    if not result.get("ok"):
        return False, f"Telegram would not set the webhook: {result.get('description')}"
    try:
        requests.post(_api("setMyCommands"), json={"commands": BOT_COMMANDS}, timeout=15)
    except Exception:
        pass    # only the command menu; the bot works without it
    return True, f"Connected: @{username} now delivers messages to {url}."


def _status():
    if not setting("bot_token"):
        return None
    try:
        info = requests.get(_api("getWebhookInfo"), timeout=10).json().get("result") or {}
    except Exception as exc:
        return {"ok": False, "message": f"Could not reach Telegram: {exc}"}
    if info.get("url") != _webhook_url():
        return {"ok": False, "message": "Telegram is not delivering to this app yet. "
                                        "Press Save to connect."}
    error = info.get("last_error_message")
    pending = info.get("pending_update_count", 0)
    if error:
        return {"ok": False, "message": f"Connected, but Telegram's last delivery failed: "
                                        f"{error} ({pending} waiting)."}
    return {"ok": True, "message": f"Connected. {pending} message(s) waiting."}


@telegram_bp.route("/admin/telegram", methods=["GET", "POST"])
@login_required
@admin_required
def telegram_settings():
    if not setting("webhook_secret"):
        set_system_setting(SETTING_KEYS["webhook_secret"], secrets.token_urlsafe(32))

    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "save":
            token = (request.form.get("bot_token") or "").strip()
            if token:
                set_system_setting(SETTING_KEYS["bot_token"], token)
            set_system_setting(SETTING_KEYS["ai_enabled"],
                               "1" if request.form.get("ai_enabled") else "0")
            set_system_setting(SETTING_KEYS["enabled"],
                               "1" if request.form.get("enabled") else "0")
            if setting("bot_token"):
                ok, message = _connect()
                flash(message, "success" if ok else "error")
            else:
                flash("Settings saved. Paste the bot token from @BotFather to connect.", "success")
        elif action == "disconnect" and setting("bot_token"):
            try:
                requests.post(_api("deleteWebhook"), timeout=15)
            except Exception:
                pass
            set_system_setting(SETTING_KEYS["enabled"], "0")
            flash("Telegram disconnected. The bot no longer answers.", "success")
        return redirect(url_for("telegram_bp.telegram_settings"))

    token = setting("bot_token")
    return render_template(
        "telegram_settings.html",
        bot_token_state=(f"saved - ends …{token[-4:]}" if token else "not set"),
        bot_username=setting("bot_username"),
        ai_enabled=setting("ai_enabled") == "1",
        enabled=setting("enabled") == "1",
        configured=is_configured(),
        status=_status(),
        webhook_url=_webhook_url(),
    )
