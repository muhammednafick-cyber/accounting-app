"""The General Chat, over WhatsApp - through Meta's WhatsApp Cloud API.

How a message travels:

  phone -> Meta -> POST /whatsapp/webhook (signed by Meta with the app secret)
        -> the number is looked up: it must be linked to a user, and that user
           must still have access to the linked company
        -> the question goes to the same engine as the web chat and the phone
           app (chatbot_service.process_chat_query), with that user's
           permissions and a conversation of its own
        -> the HTML answer is rewritten for WhatsApp (whatsapp_text)
        -> the reply goes back through the Graph API

Read-only, like the web chat: nothing here posts, changes or deletes anything
in the books.

Free to run: Meta charges for conversations a business starts, not for replies
inside the 24 hours after a user writes. Every message here is a reply.

The settings (phone number id, access token, app secret) are pasted in by an
administrator under Admin > WhatsApp Settings and kept in system_settings, like
the OpenRouter key.
"""
import hashlib
import hmac
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

whatsapp_bp = Blueprint("whatsapp_bp", __name__)

DEFAULT_GRAPH_VERSION = "v23.0"
GRAPH_URL = "https://graph.facebook.com"

SETTING_KEYS = {
    "phone_number_id": "whatsapp_phone_number_id",
    "business_number": "whatsapp_business_number",
    "access_token": "whatsapp_access_token",
    "app_secret": "whatsapp_app_secret",
    "verify_token": "whatsapp_verify_token",
    "graph_version": "whatsapp_graph_version",
    "ai_enabled": "whatsapp_ai_enabled",
    "enabled": "whatsapp_enabled",
    "agent_mode": "whatsapp_agent_mode",
}
AGENT_MODES = ("offer", "auto", "off")

COMMANDS_TEXT = (
    "*WhatsApp commands*\n"
    "• *HELP* — what you can ask\n"
    "• *PDF* / *EXCEL* — the last table as a file\n"
    "• *AGENT* <question> — let AI work out a question that needs several reports\n"
    "• *COMPANY* — list your companies; *COMPANY 2* switches\n"
    "• *RESET* — start a new conversation\n"
    "• *UNLINK* — stop this number using your account"
)


# ------------------------------------------------------------------ settings

def setting(name, default=""):
    value = get_system_setting(SETTING_KEYS[name])
    return value if value not in (None, "") else default


def agent_mode():
    mode = setting("agent_mode", "offer")
    return mode if mode in AGENT_MODES else "offer"


def is_configured():
    return bool(setting("enabled") == "1" and setting("phone_number_id")
                and setting("access_token") and setting("app_secret"))


def _graph(path):
    version = setting("graph_version", DEFAULT_GRAPH_VERSION)
    return f"{GRAPH_URL}/{version}/{path.lstrip('/')}"


def _digits(value):
    return re.sub(r"\D", "", value or "")


# ------------------------------------------------------------------ sending

def send_text(wa_id, text):
    """One text message. Returns True when Meta accepted it."""
    try:
        response = requests.post(
            _graph(f"{setting('phone_number_id')}/messages"),
            headers={"Authorization": f"Bearer {setting('access_token')}"},
            json={"messaging_product": "whatsapp", "to": wa_id, "type": "text",
                  "text": {"body": text[:4096], "preview_url": False}},
            timeout=20)
        if response.status_code >= 300:
            print(f"[whatsapp] send failed {response.status_code}: {response.text[:300]}")
            return False
        return True
    except Exception as exc:
        print(f"[whatsapp] send failed: {exc}")
        return False


def send_document(wa_id, data, filename, mime):
    """Upload a file to Meta, then send it. Returns True when both worked."""
    auth = {"Authorization": f"Bearer {setting('access_token')}"}
    try:
        upload = requests.post(
            _graph(f"{setting('phone_number_id')}/media"), headers=auth,
            data={"messaging_product": "whatsapp", "type": mime},
            files={"file": (filename, data, mime)}, timeout=60)
        media_id = (upload.json() or {}).get("id") if upload.status_code < 300 else None
        if not media_id:
            print(f"[whatsapp] upload failed {upload.status_code}: {upload.text[:300]}")
            return False
        response = requests.post(
            _graph(f"{setting('phone_number_id')}/messages"), headers=auth,
            json={"messaging_product": "whatsapp", "to": wa_id, "type": "document",
                  "document": {"id": media_id, "filename": filename}},
            timeout=20)
        return response.status_code < 300
    except Exception as exc:
        print(f"[whatsapp] document failed: {exc}")
        return False


# ------------------------------------------------------------------ webhook

@whatsapp_bp.route("/whatsapp/webhook", methods=["GET"])
def webhook_verify():
    """Meta's one-time check that this address belongs to us."""
    expected = setting("verify_token")
    if (request.args.get("hub.mode") == "subscribe" and expected
            and hmac.compare_digest(request.args.get("hub.verify_token") or "", expected)):
        return request.args.get("hub.challenge") or "", 200
    return "Forbidden", 403


def signature_ok(raw_body, header):
    """Meta signs every delivery with the app secret. Unsigned or wrongly
    signed requests are refused - anyone can post to a public URL."""
    secret = setting("app_secret")
    if not secret or not header or not header.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, header[7:])


@whatsapp_bp.route("/whatsapp/webhook", methods=["POST"])
def webhook_receive():
    raw = request.get_data()
    if not signature_ok(raw, request.headers.get("X-Hub-Signature-256")):
        return "Forbidden", 403
    if not is_configured():
        return "OK", 200            # acknowledged, deliberately ignored

    payload = request.get_json(silent=True) or {}
    our_number_id = setting("phone_number_id")
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            if (value.get("metadata") or {}).get("phone_number_id") != our_number_id:
                continue
            for message in value.get("messages") or []:
                if db.first_sighting(message.get("id")):
                    dispatch(current_app._get_current_object(), message)
    # Meta wants a quick 200; answers go out from the worker thread.
    return "OK", 200


def dispatch(app, message):
    """Answer in the background, so Meta is not kept waiting (it retries a
    slow webhook, which would otherwise answer the same question twice)."""
    threading.Thread(target=_answer_in_context, args=(app, message), daemon=True).start()


def _answer_in_context(app, message):
    with app.test_request_context("/whatsapp/webhook"):
        try:
            handle_message(message)
        except Exception as exc:
            print(f"[whatsapp] could not answer: {exc}")
            send_text(message.get("from"), "Sorry - something went wrong answering that. "
                                           "Please try again.")


# ------------------------------------------------------------------ answering

def _channel():
    """WhatsApp, for the shared answering code in chat_channels. The senders
    are looked up at call time, so tests can replace send_text here."""
    from .chat_channels import Channel
    return Channel(
        key="whatsapp", label="WhatsApp",
        send_text=lambda to, text: send_text(to, text),
        send_document=lambda to, data, name, mime: send_document(to, data, name, mime),
        ai_enabled=lambda: setting("ai_enabled") == "1",
        commands_text=COMMANDS_TEXT,
        agent_mode=lambda: agent_mode(),
        not_linked_text=("This number is not linked to a Prodata account yet.\n\n"
                         "In the app, open *WhatsApp* in the menu, choose *Link this "
                         "phone*, and send the code you are shown here."),
    )


def handle_message(message):
    """Work out the reply to one incoming WhatsApp message and send it."""
    from .chat_channels import handle_text

    wa_id = _digits(message.get("from"))
    if not wa_id:
        return
    if message.get("type") != "text":
        send_text(wa_id, "I can only read text messages. Type your question, "
                         "for example *cash balance*.")
        return
    handle_text(_channel(), wa_id, (message.get("text") or {}).get("body") or "")


# ------------------------------------------------------------------ user page

def _mask(wa_id):
    wa_id = wa_id or ""
    return f"+{wa_id[:3]} •••• {wa_id[-4:]}" if len(wa_id) > 7 else f"+{wa_id}"


@whatsapp_bp.route("/settings/whatsapp", methods=["GET", "POST"])
@login_required
def whatsapp_link_page():
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
                return redirect(url_for("whatsapp_bp.whatsapp_link_page"))
            code = db.start_link(current_user.id, company_id)
        elif action == "unlink":
            db.unlink_user(current_user.id)
            flash("WhatsApp unlinked. That number can no longer ask about your accounts.",
                  "success")
            return redirect(url_for("whatsapp_bp.whatsapp_link_page"))

    link = db.link_for_user(current_user.id)
    linked = link if link and link.get("wa_id") else None
    company_names = {c["id"]: c["name"] for c in companies}
    business = _digits(setting("business_number"))
    return render_template(
        "whatsapp_link.html",
        configured=is_configured(),
        linked=linked,
        linked_number=_mask(linked["wa_id"]) if linked else None,
        linked_company=company_names.get(linked["company_id"]) if linked else None,
        code=code,
        business_number=business,
        wa_link=(f"https://wa.me/{business}?text=LINK%20{code}" if code and business else None),
        companies=companies,
        active_company_id=session.get("company_id"),
        code_minutes=db.CODE_TTL_MINUTES,
    )


# ------------------------------------------------------------------ admin page

@whatsapp_bp.route("/admin/whatsapp", methods=["GET", "POST"])
@login_required
@admin_required
def whatsapp_settings():
    if not setting("verify_token"):
        set_system_setting(SETTING_KEYS["verify_token"], secrets.token_urlsafe(24))

    check = None
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "save":
            for field in ("phone_number_id", "graph_version"):
                set_system_setting(SETTING_KEYS[field], (request.form.get(field) or "").strip())
            set_system_setting(SETTING_KEYS["business_number"],
                               _digits(request.form.get("business_number")))
            # Secrets: blank means "keep what is saved", so they never have to
            # be shown back on the page.
            for field in ("access_token", "app_secret"):
                value = (request.form.get(field) or "").strip()
                if value:
                    set_system_setting(SETTING_KEYS[field], value)
            set_system_setting(SETTING_KEYS["ai_enabled"],
                               "1" if request.form.get("ai_enabled") else "0")
            set_system_setting(SETTING_KEYS["enabled"],
                               "1" if request.form.get("enabled") else "0")
            mode = request.form.get("agent_mode")
            set_system_setting(SETTING_KEYS["agent_mode"],
                               mode if mode in AGENT_MODES else "offer")
            flash("WhatsApp settings saved.", "success")
            return redirect(url_for("whatsapp_bp.whatsapp_settings"))
        if action == "check":
            check = _check_connection()

    def ends(value):
        return f"saved - ends …{value[-4:]}" if value else "not set"

    return render_template(
        "whatsapp_settings.html",
        webhook_url=request.url_root.rstrip("/") + url_for("whatsapp_bp.webhook_verify"),
        verify_token=setting("verify_token"),
        phone_number_id=setting("phone_number_id"),
        business_number=setting("business_number"),
        graph_version=setting("graph_version", DEFAULT_GRAPH_VERSION),
        access_token_state=ends(setting("access_token")),
        app_secret_state=ends(setting("app_secret")),
        ai_enabled=setting("ai_enabled") == "1",
        enabled=setting("enabled") == "1",
        agent_mode=agent_mode(),
        configured=is_configured(),
        check=check,
    )


def _check_connection():
    """Ask Meta about the configured number - proves the id and token work
    without sending anyone a message."""
    if not (setting("phone_number_id") and setting("access_token")):
        return {"ok": False, "message": "Save the Phone Number ID and access token first."}
    try:
        response = requests.get(
            _graph(setting("phone_number_id")),
            params={"fields": "display_phone_number,verified_name,quality_rating"},
            headers={"Authorization": f"Bearer {setting('access_token')}"},
            timeout=15)
        data = response.json() if response.content else {}
    except Exception as exc:
        return {"ok": False, "message": f"Could not reach Meta: {exc}"}
    if response.status_code >= 300:
        message = ((data.get("error") or {}).get("message")) or response.text[:200]
        return {"ok": False, "message": f"Meta refused: {message}"}
    return {"ok": True,
            "message": f"Connected: {data.get('verified_name', '')} "
                       f"({data.get('display_phone_number', '')}), "
                       f"quality {data.get('quality_rating', 'n/a')}."}
