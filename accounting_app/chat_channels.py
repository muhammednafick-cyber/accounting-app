"""The General Chat over a messaging app - shared by WhatsApp and Telegram.

Each channel supplies how to send (text and files) and a few words of its own;
everything else is here, once: linking by one-time code, the checks that the
linked user still exists and can still open the company, the commands, and the
answer itself from the same engine as the web chat.

Text is written in WhatsApp's markup (*bold*, _italic_); a channel that uses a
different one converts it in its own send_text.
"""
import io
import re

from database import whatsapp_db as db
from database.master_db import get_user_by_id, get_user_companies

from .whatsapp_text import to_whatsapp

QUESTIONS_PER_MINUTE = 20
LINK_TRIES_PER_HOUR = 5


class Channel:
    """What differs between WhatsApp and Telegram."""

    def __init__(self, key, label, send_text, send_document, ai_enabled,
                 commands_text, not_linked_text):
        self.key = key                      # "whatsapp" / "telegram"
        self.label = label                  # "WhatsApp" / "Telegram"
        self.send_text = send_text          # (ext_id, text) -> bool
        self.send_document = send_document  # (ext_id, bytes, filename, mime) -> bool
        self.ai_enabled = ai_enabled        # () -> bool
        self.commands_text = commands_text
        self.not_linked_text = not_linked_text


def handle_text(channel, ext_id, text):
    """Reply to one text message from ext_id."""
    from database.app_state_db import rate_limit_check

    text = (text or "").strip()
    if not ext_id or not text:
        return

    allowed, retry_after = rate_limit_check(channel.key, ext_id, QUESTIONS_PER_MINUTE, 60)
    if not allowed:
        channel.send_text(ext_id, f"That is a lot of questions at once. Please wait {retry_after}s.")
        return

    # Linking: "LINK 123456", with the code the app showed.
    link_match = re.fullmatch(r"(?i)link\s*(\d{6})", text)
    if link_match:
        _link(channel, ext_id, link_match.group(1))
        return

    link = db.link_for_number(ext_id)
    if not link:
        channel.send_text(ext_id, channel.not_linked_text)
        return

    user = get_user_by_id(link["user_id"])
    companies = get_user_companies(link["user_id"]) if user else []
    company = next((c for c in companies if c["id"] == link["company_id"]), None)
    if not user:
        db.unlink_number(ext_id)
        channel.send_text(ext_id, "The account this chat was linked to no longer exists, "
                                  "so it has been unlinked.")
        return
    if not company:
        channel.send_text(ext_id, "Your account no longer has access to the company this chat "
                                  "was linked to. Type *COMPANY* to choose another.")
        if not re.fullmatch(r"(?i)company(\s+\d+)?", text):
            return

    command = text.lower()
    if command == "unlink":
        db.unlink_number(ext_id)
        channel.send_text(ext_id, f"Done - this {channel.label} chat is unlinked and can no "
                                  "longer ask about your accounts.")
        return
    if command in ("commands", "menu", "?"):
        channel.send_text(ext_id, channel.commands_text)
        return
    if command in ("pdf", "excel", "xlsx"):
        _send_last_result(channel, ext_id, link, "pdf" if command == "pdf" else "xlsx")
        return
    company_match = re.fullmatch(r"(?i)company(?:\s+(\d+))?", text)
    if company_match:
        _company(channel, ext_id, link, companies, company_match.group(1))
        return

    _ask(channel, ext_id, link, text, reset=command in ("reset", "new", "start over"))


def _link(channel, ext_id, code):
    from database.app_state_db import rate_limit_check

    allowed, _retry = rate_limit_check(channel.key + "_link", ext_id, LINK_TRIES_PER_HOUR, 3600)
    if not allowed:
        channel.send_text(ext_id, "Too many linking attempts. Try again in an hour.")
        return
    linked = db.complete_link(code, ext_id, channel=channel.key)
    if not linked:
        channel.send_text(ext_id, f"That code is not valid or has expired. In the app, open "
                                  f"*{channel.label}* in the menu and create a new one.")
        return
    user = get_user_by_id(linked[0]) or {}
    company = next((c for c in get_user_companies(linked[0])
                    if c["id"] == linked[1]), {})
    channel.send_text(ext_id, f"✅ Linked to *{user.get('username', '')}* — "
                              f"*{company.get('name', '')}*.\n\n"
                              "Ask me anything you would ask the chat in the app, for example "
                              "*cash balance* or *top customers this month*.\n\n"
                              + channel.commands_text)


def _company(channel, ext_id, link, companies, choice):
    if not companies:
        channel.send_text(ext_id, "Your account has no companies.")
        return
    if choice is None:
        lines = [f"{i}. {c['name']}" + ("  ← current" if c["id"] == link["company_id"] else "")
                 for i, c in enumerate(companies, start=1)]
        channel.send_text(ext_id, "*Your companies*\n" + "\n".join(lines)
                          + "\n\nReply *COMPANY 2* (for example) to switch.")
        return
    index = int(choice) - 1
    if not 0 <= index < len(companies):
        channel.send_text(ext_id, "There is no company with that number. Type *COMPANY* for the list.")
        return
    db.switch_company(ext_id, companies[index]["id"])
    channel.send_text(ext_id, f"Switched to *{companies[index]['name']}*.")


def _ask(channel, ext_id, link, question, reset=False):
    """The question, answered by the same engine as the web chat."""
    from .chat_context import reset as reset_conversation, use_conversation
    from .chat_permissions import use_user
    from .chatbot_service import process_chat_query

    company_id = link["company_id"]
    # A conversation per chat and company, so "and last month?" follows on.
    use_conversation(f"{channel.key}-{ext_id}-{company_id}")
    # The linked user's menu access decides what may be answered.
    use_user(link["user_id"])
    if reset:
        reset_conversation()
        channel.send_text(ext_id, "New conversation started. What would you like to know?")
        return

    reply = process_chat_query(question, company_id, ai_enabled=channel.ai_enabled(), history=[])
    if "error" in reply:
        channel.send_text(ext_id, "Sorry - I could not answer that. Please try again.")
        return

    text, token = to_whatsapp(reply.get("response") or "")
    if reply.get("intent") == "help":
        text += "\n\n" + channel.commands_text
    channel.send_text(ext_id, text or "I have no answer for that.")
    db.touch(ext_id, last_token=token)
    db.log_question(ext_id, link["user_id"], company_id, question, reply.get("intent"))

    from .chat_routes import MISS_INTENTS
    if MISS_INTENTS.get(reply.get("intent")):
        from database.chat_insights_db import record_miss
        record_miss(company_id, link["user_id"], question, MISS_INTENTS[reply["intent"]])


def _send_last_result(channel, ext_id, link, fmt):
    from .chat_export_store import load
    from .export_routes import _chat_result_pdf

    result = load(link.get("last_token"))
    if not result:
        channel.send_text(ext_id, "There is no table to send yet - ask a question first, "
                                  "for example *trial balance*.")
        return
    title = re.sub(r"[^\w\- ]+", "", result.get("title") or "report").strip() or "report"
    if fmt == "pdf":
        pdf = _chat_result_pdf(result)
        if pdf is None:
            channel.send_text(ext_id, "PDF files are not available on this server. Reply *EXCEL* instead.")
            return
        ok = channel.send_document(ext_id, pdf.getvalue(), title + ".pdf", "application/pdf")
    else:
        import pandas as pd
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            pd.DataFrame(result["rows"], columns=result["columns"]).to_excel(
                writer, index=False, sheet_name="Data")
        ok = channel.send_document(
            ext_id, buffer.getvalue(), title + ".xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if not ok:
        channel.send_text(ext_id, "Sorry - the file could not be sent. Please try again.")
