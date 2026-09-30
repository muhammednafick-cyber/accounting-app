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
                 commands_text, not_linked_text, agent_mode=lambda: "offer"):
        self.key = key                      # "whatsapp" / "telegram"
        self.label = label                  # "WhatsApp" / "Telegram"
        self.send_text = send_text          # (ext_id, text) -> bool
        self.send_document = send_document  # (ext_id, bytes, filename, mime) -> bool
        self.ai_enabled = ai_enabled        # () -> bool
        self.commands_text = commands_text
        self.not_linked_text = not_linked_text
        # When the free reports get stuck: "off" never uses the Agent, "offer"
        # asks first (reply AGENT), "auto" runs it straight away.
        self.agent_mode = agent_mode        # () -> "off" | "offer" | "auto"

    def agent_available(self):
        return self.ai_enabled() and self.agent_mode() in ("offer", "auto")


# What the engine answers when no single report fits: several near misses to
# pick from, or an offer to let AI query the database.
STUCK_INTENTS = ("suggestion", "need_permission")
AGENT_WAIT_TEXT = "⏳ Working on it - this needs several reports, so it takes about a minute."


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

    # The answer to a choice offered last time: a number picks that report,
    # AGENT hands the whole question to the Agent. Anything else is a new
    # question, and the offer lapses.
    pending = link.get("pending")
    if pending:
        db.set_pending(ext_id, None)
        options = pending.get("options") or []
        if command.isdigit() and 1 <= int(command) <= len(options):
            text = command = options[int(command) - 1]
        elif command == "agent":
            _run_agent(channel, ext_id, link, pending.get("question") or "")
            return

    # "agent <question>" (Telegram: /agent <question>) asks the Agent directly.
    agent_match = re.fullmatch(r"(?is)agent\b\s*(.*)", text)
    if agent_match:
        question = agent_match.group(1).strip()
        if not question:
            channel.send_text(ext_id, "Put your question after it, for example "
                                      "*agent what is the stock worth and how much has "
                                      "not moved in six months*.")
            return
        _run_agent(channel, ext_id, link, question)
        return

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

    if reply.get("intent") in STUCK_INTENTS and _offer_or_run_agent(
            channel, ext_id, link, question, reply):
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


def _offer_or_run_agent(channel, ext_id, link, question, reply):
    """The free reports got stuck. Offer the choices (and the Agent), or run
    the Agent straight away. True when the reply has been sent here."""
    from .chat_context import take_pending

    options = ((reply.get("data") or {}).get("options") or [])[:9]
    agent_ok = channel.agent_available()
    if reply.get("intent") == "need_permission":
        if not agent_ok:
            return False        # the engine's own "shall I use AI?" question
        # The Agent replaces the engine's offer to query the database, so
        # drop that offer rather than let a later "yes" pick it up.
        take_pending()
    if agent_ok and channel.agent_mode() == "auto":
        _run_agent(channel, ext_id, link, question)
        return True
    if not options and not agent_ok:
        return False

    lines = []
    if options:
        lines.append("I'm not sure which report you meant:")
        lines += [f"*{i}.* {label}" for i, label in enumerate(options, start=1)]
        lines.append("")
        lines.append("Reply with the number"
                     + (", or *AGENT* to have AI work out the whole question "
                        "(about a minute)." if agent_ok else "."))
    else:
        lines.append("I don't have a single report for that.")
        lines.append("Reply *AGENT* to have AI work it out from your reports "
                     "(about a minute), or ask it in different words.")
    db.set_pending(ext_id, {"question": question, "options": options})
    channel.send_text(ext_id, "\n".join(lines))
    db.log_question(ext_id, link["user_id"], link["company_id"], question,
                    reply.get("intent"))
    return True


def _run_agent(channel, ext_id, link, question):
    """The Agent: several reports in turn, then a written summary. Only with
    AI on, and never when an administrator has switched it off."""
    from . import chat_agent
    from .chat_context import use_conversation
    from .chat_permissions import use_user

    if not channel.ai_enabled():
        channel.send_text(ext_id, "AI is switched off for this chat, so the Agent is not "
                                  "available. Ask one thing at a time instead.")
        return
    if channel.agent_mode() == "off":
        channel.send_text(ext_id, "The Agent is switched off by your administrator. "
                                  "Ask one thing at a time instead.")
        return
    if not question:
        channel.send_text(ext_id, "What should the Agent work out?")
        return

    company_id = link["company_id"]
    use_conversation(f"{channel.key}-{ext_id}-{company_id}")
    use_user(link["user_id"])
    channel.send_text(ext_id, AGENT_WAIT_TEXT)
    reply = chat_agent.run(question, company_id, history=[])
    text, token = to_whatsapp(reply.get("response") or "")
    channel.send_text(ext_id, text or "The Agent could not find an answer to that.")
    db.touch(ext_id, last_token=token)
    db.log_question(ext_id, link["user_id"], company_id, question, "agent")


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
