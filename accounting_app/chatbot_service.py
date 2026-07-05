import os
import json
import datetime
import requests
import re
from database.config import get_connection
from database.master_db import get_system_setting
from database.ai_settings_db import get_ai_setting
from database.reports_db import (
    get_trial_balance_data,
    get_balance_sheet_data,
    get_profit_and_loss_data,
    get_ageing_report_data,
    get_voucher_register_data,
    get_stock_movement_data,
    get_slow_moving_items,
    get_negative_stock_items,
    get_ledger_transactions,
    get_item_closing_stock
)
import difflib
from database.analysis_db import (
    get_top_customers,
    get_top_suppliers,
    get_kpi_summary,
    get_stock_category_summary,
    get_monthly_sales_trend,
    get_monthly_purchase_trend
)
from database.accounts_db import get_ledgers
from database.inventory_db import get_items
from database.company_db import get_current_company_id

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "openai/gpt-oss-120b" # Fallback default model

def get_openrouter_api_key():
    api_key = get_system_setting('openrouter_api_key')
    return api_key or os.environ.get('OPENROUTER_API_KEY')

def get_openrouter_model():
    """The default model selected in AI Settings (per company)."""
    return get_ai_setting('openrouter_model', OPENROUTER_MODEL) or OPENROUTER_MODEL

def openrouter_request(payload, headers, max_retries=1):
    """
    POST to OpenRouter with automatic retry on 429 (rate limit).
    Returns (response_json, error_message). Exactly one is None.
    """
    import time
    attempt = 0
    while True:
        response = requests.post(OPENROUTER_URL, json=payload, headers=headers, timeout=90)

        # OpenRouter can return 200 with an embedded error object (upstream provider errors)
        body = None
        try:
            body = response.json()
        except ValueError:
            pass

        err = None
        if response.status_code != 200:
            err = body.get('error') if isinstance(body, dict) else None
        elif isinstance(body, dict) and 'error' in body and 'choices' not in body:
            err = body['error']

        if err is None:
            if not isinstance(body, dict) or 'choices' not in body:
                return None, f"Unexpected response from OpenRouter: {response.text[:300]}"
            return body, None

        code = err.get('code') if isinstance(err, dict) else None
        message = err.get('message', str(err)) if isinstance(err, dict) else str(err)

        # Rate limited: retry once after the suggested delay
        if code == 429 or response.status_code == 429:
            retry_after = 5
            if isinstance(err, dict):
                retry_after = min(int(err.get('metadata', {}).get('retry_after_seconds', 5) or 5), 30)
            if attempt < max_retries:
                attempt += 1
                print(f"OpenRouter rate limited, retrying in {retry_after}s (attempt {attempt})")
                time.sleep(retry_after)
                continue
            model = payload.get('model', 'the selected model')
            return None, (f"'{model}' is temporarily rate-limited (busy). "
                          "Please try again in a moment, or select a different model in AI Settings "
                          "(free models are often busy - paid or less popular models are more reliable).")

        return None, f"AI Provider Error: {message}"

def parse_date_range(range_str):
    today = datetime.date.today()
    
    # Define date formats first
    date_formats = [
        "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y",
        "%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"
    ]
    
    if not range_str:
        return None, None
        
    range_str = range_str.lower().strip()
    
    # Handle month names
    months = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
        'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }
    
    if range_str in months:
        month_num = months[range_str]
        year = today.year
        # If the month is in the future, assume last year? 
        # Or just assume current year as per user intent usually.
        # Let's assume current year.
        start = datetime.date(year, month_num, 1)
        # Get last day of month
        if month_num == 12:
            end = datetime.date(year, 12, 31)
        else:
            end = datetime.date(year, month_num + 1, 1) - datetime.timedelta(days=1)
        return start, end

    if range_str == 'today':
        return today, today
    elif range_str == 'yesterday':
        d = today - datetime.timedelta(days=1)
        return d, d
    elif range_str == 'this_week':
        start = today - datetime.timedelta(days=today.weekday())
        return start, today
    elif range_str == 'last_week':
        end = today - datetime.timedelta(days=today.weekday() + 1)
        start = end - datetime.timedelta(days=6)
        return start, end
    elif range_str == 'this_month':
        start = today.replace(day=1)
        return start, today
    elif range_str == 'last_month':
        end = today.replace(day=1) - datetime.timedelta(days=1)
        start = end.replace(day=1)
        return start, end
    elif range_str == 'this_year':
        start = today.replace(month=1, day=1)
        return start, today
    
    # Handle Month YYYY (e.g., "Aug 2023", "August 2023")
    month_year_match = re.match(r'^([a-zA-Z]+)\s+(\d{4})$', range_str)
    if month_year_match:
        month_str = month_year_match.group(1).lower()
        year_str = month_year_match.group(2)
        
        if month_str in months:
            try:
                year = int(year_str)
                month_num = months[month_str]
                start = datetime.date(year, month_num, 1)
                
                # Get last day of month
                if month_num == 12:
                    end = datetime.date(year, 12, 31)
                else:
                    end = datetime.date(year, month_num + 1, 1) - datetime.timedelta(days=1)
                return start, end
            except ValueError:
                pass

    # Handle YYYY
    if re.match(r'^\d{4}$', range_str):
        try:
            year = int(range_str)
            start = datetime.date(year, 1, 1)
            end = datetime.date(year, 12, 31)
            return start, end
        except ValueError:
            pass

    # Handle "X to Y" Range (e.g. "01-01-2023 to 31-12-2023")
    range_match = re.match(r'^(.+?)\s+to\s+(.+?)$', range_str)
    if range_match:
        start_str = range_match.group(1).strip()
        end_str = range_match.group(2).strip()
        
        start_date = None
        end_date = None
        
        # Parse Start
        for fmt in date_formats:
            try:
                start_date = datetime.datetime.strptime(start_str, fmt).date()
                break
            except ValueError:
                continue
                
        # Parse End
        for fmt in date_formats:
            try:
                end_date = datetime.datetime.strptime(end_str, fmt).date()
                break
            except ValueError:
                continue
                
        if start_date and end_date:
            return start_date, end_date

    # Handle Specific Dates (DD.MM.YYYY, DD-MM-YYYY, DD/MM/YYYY, YYYY-MM-DD)
    # Try parsing common formats
    for fmt in date_formats:
        try:
            d = datetime.datetime.strptime(range_str, fmt).date()
            return d, d
        except ValueError:
            continue

    return None, None


# ============================================================
# Rule-based (no-AI) query parser
# Maps common ERP questions straight to intents so the chatbot
# can answer from the database without calling OpenRouter.
# ============================================================

_DATE_PHRASES = [
    ("this month", "this_month"), ("last month", "last_month"),
    ("this week", "this_week"), ("last week", "last_week"),
    ("this year", "this_year"), ("today", "today"), ("yesterday", "yesterday"),
]

_MONTH_NAMES = r'(?:january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)'

def _extract_date_range(text):
    """Find a date phrase in the text. Returns (date_range_str_or_None, text_without_it)."""
    lower = text.lower()

    # Explicit range "dd-mm-yyyy to dd-mm-yyyy"
    m = re.search(r'(\d{1,2}[-./]\d{1,2}[-./]\d{4}|\d{4}[-./]\d{1,2}[-./]\d{1,2})\s+to\s+(\d{1,2}[-./]\d{1,2}[-./]\d{4}|\d{4}[-./]\d{1,2}[-./]\d{1,2})', lower)
    if m:
        return f"{m.group(1)} to {m.group(2)}", (text[:m.start()] + text[m.end():]).strip()

    # "all time" - explicit request for no date filter
    for phrase in ("all time", "alltime", "full history", "since inception"):
        idx = lower.find(phrase)
        if idx != -1:
            return "all_time", (text[:idx] + text[idx + len(phrase):]).strip()

    for phrase, token in _DATE_PHRASES:
        idx = lower.find(phrase)
        if idx != -1:
            return token, (text[:idx] + text[idx + len(phrase):]).strip()

    # "Month YYYY" e.g. "august 2025"
    m = re.search(rf'\b({_MONTH_NAMES})\s+(\d{{4}})\b', lower)
    if m:
        return f"{m.group(1)} {m.group(2)}", (text[:m.start()] + text[m.end():]).strip()

    # Standalone month name
    m = re.search(rf'\b(in|for|of)\s+({_MONTH_NAMES})\b', lower)
    if m:
        return m.group(2), (text[:m.start()] + text[m.end():]).strip()

    # Single date
    m = re.search(r'\b(\d{1,2}[-./]\d{1,2}[-./]\d{4})\b', lower)
    if m:
        return m.group(1), (text[:m.start()] + text[m.end():]).strip()

    # Standalone year "in 2025"
    m = re.search(r'\b(?:in|for|during)\s+(\d{4})\b', lower)
    if m:
        return m.group(1), (text[:m.start()] + text[m.end():]).strip()

    # Bare year at the end, e.g. "sales 2024"
    m = re.search(r'\b(19\d{2}|20\d{2})\s*$', lower)
    if m:
        return m.group(1), (text[:m.start()] + text[m.end():]).strip()

    return None, text

def rule_based_parse(user_query):
    """
    Try to map the query to an intent using keywords only (no AI).
    Returns a parsed dict compatible with execute_intent, or None.
    """
    if not user_query:
        return None

    text = user_query.strip()
    date_range, remainder = _extract_date_range(text)
    q = remainder.lower().strip(' ?.!')
    # Remove dangling connector words left behind after the date was extracted
    # e.g. "Sales on 31-12-2023" -> "sales on" -> "sales"
    q = re.sub(r'\s+(on|for|in|of|at|during|as\s+of|as\s+at)$', '', q).strip()
    q = re.sub(r'^(on|for|in|during)\s+', '', q).strip()
    # Normalize compact spellings of report names
    q = (q.replace('balancesheet', 'balance sheet')
          .replace('trialbalance', 'trial balance')
          .replace('profitandloss', 'profit and loss')
          .replace('profit & loss', 'profit and loss'))
    params = {"date_range": date_range, "entity_name": None, "item_name": None,
              "expense_type": None, "report_type": None, "limit": None}

    def result(intent, **extra):
        params.update(extra)
        return {"intent": intent, "parameters": params,
                "explanation": f"Matched '{intent}' directly from ERP functions (no AI)."}

    # --- Greetings / help ---
    if re.match(r'^(hi+|hii+|hello+|hey+|hai|salam|salaam|good\s*(morning|afternoon|evening|day)|greetings)\b[\s!.]*$', q):
        return result("greeting")
    if q in ('help', 'what can you do', 'what can you do for me', 'commands', 'menu', 'options', '?'):
        return result("greeting")
    if re.match(r'^(thanks|thank you|thankyou|ok|okay|great|nice|good|super)\b[\s!.]*$', q):
        return result("thanks")

    # --- Voucher lookup by number, e.g. "show voucher SAL-00001" / "SAL-00001" ---
    m = re.search(r'\b([A-Za-z]{2,6}-\d{3,})\b', text)
    if m and ('voucher' in q or 'invoice' in q or q.replace(' ', '') == m.group(1).lower()):
        return result("get_voucher_details", voucher_number=m.group(1).upper())

    # --- Voucher list, e.g. "sales vouchers this month", "show purchase vouchers" ---
    m = re.search(r'\b(sales return|purchase return|sales|purchase|payment|receipt|journal|contra|expense)\s+(vouchers|voucher list|invoices|register)\b', q)
    if m:
        vtype_map = {'sales': 'Sales', 'purchase': 'Purchase', 'sales return': 'Sales Return',
                     'purchase return': 'Purchase Return', 'payment': 'Payment', 'receipt': 'Receipt',
                     'journal': 'Journal', 'contra': 'Contra', 'expense': 'Expense'}
        if 'export' in q or 'download' in q or 'excel' in q:
            return result("export_report", report_type=vtype_map[m.group(1)])
        return result("list_vouchers", voucher_type=vtype_map[m.group(1)])

    # --- Exports ---
    if 'export' in q or 'download' in q:
        if 'trial balance' in q: return result("export_report", report_type="Trial Balance")
        if 'balance sheet' in q: return result("export_report", report_type="Balance Sheet")
        if 'profit' in q or 'p&l' in q or 'pnl' in q: return result("export_report", report_type="P&L")
        if 'purchase' in q: return result("export_report", report_type="Purchase")
        if 'sales' in q: return result("export_report", report_type="Sales")

    # --- Financial statements ---
    if 'trial balance' in q: return result("get_trial_balance")
    if 'balance sheet' in q: return result("get_balance_sheet")
    if 'profit and loss' in q or 'profit & loss' in q or 'p&l' in q or 'pnl' in q or 'income statement' in q:
        return result("get_profit_and_loss")
    if 'net profit' in q or q == 'profit': return result("get_net_profit")

    # --- Balances ---
    if 'cash balance' in q or 'cash in hand' in q: return result("get_cash_balance")
    if 'bank balance' in q: return result("get_bank_balance")
    m = re.match(r'^(?:what is |show |get )?(?:the )?balance (?:of|for) (.+)$', q)
    if m: return result("get_ledger_balance", entity_name=m.group(1).strip())
    m = re.match(r'^(.+?)\s+balance$', q)
    if m and m.group(1).strip() not in ('cash', 'bank', 'trial'):
        return result("get_ledger_balance", entity_name=m.group(1).strip())

    # --- Statements / ledgers ---
    m = re.match(r'^(?:show |get )?(?:customer |supplier )?(?:statement|ledger)(?: of| for)? (.+)$', q)
    if m: return result("get_customer_statement", entity_name=m.group(1).strip())

    # --- Totals ---
    if 'total sales' in q or 'sales total' in q or q == 'sales':
        return result("get_total_sales")
    if 'total purchase' in q or 'purchase total' in q or q == 'purchase' or q == 'purchases':
        return result("get_total_purchase")

    # --- Receivables / payables ---
    if 'receivable' in q or ('outstanding' in q and 'customer' in q):
        return result("get_pending_invoices")
    if 'payable' in q or ('outstanding' in q and 'supplier' in q):
        return {"intent": "get_pending_invoices", "parameters": params,
                "explanation": "supplier payables (no AI)"}
    if ('pending' in q or 'overdue' in q) and 'invoice' in q:
        expl = "supplier invoices (no AI)" if 'supplier' in q else "customer invoices (no AI)"
        return {"intent": "get_pending_invoices", "parameters": params, "explanation": expl}

    # --- Top customers / suppliers ---
    m = re.search(r'top\s*(\d+)?\s*customers', q)
    if m: return result("get_top_customers", limit=int(m.group(1)) if m.group(1) else 5)
    m = re.search(r'top\s*(\d+)?\s*suppliers', q)
    if m: return result("get_top_suppliers", limit=int(m.group(1)) if m.group(1) else 5)

    # --- Stock / inventory ---
    m = re.match(r'^(?:show |what is |current )?stock (?:of|for) (.+)$', q)
    if m: return result("get_stock_status", item_name=m.group(1).strip())
    if 'closing stock' in q or 'stock value' in q or 'inventory valuation' in q or 'inventory value' in q:
        return result("get_closing_stock_value")
    if 'slow moving' in q: return result("get_slow_moving")
    if 'negative stock' in q or 'low stock' in q: return result("get_low_stock")
    if 'no sales' in q and 'item' in q: return result("get_no_sales_items")

    # --- Comparison ---
    if 'compare' in q and 'sales' in q: return result("compare_monthly_sales")

    return None


# ============================================================
# Rule-based (no-AI) voucher message parser
# Handles one-line voucher entry like:
#   "received 5000 from ABC by cash today"
#   "paid 1200 to XYZ by bank yesterday"
#   "transfer 1000 from Cash to Bank"
#   "expense 300 for Fuel by cash"
# Returns the same JSON shape as the AI extraction, or None.
# ============================================================

def _voucher_date_from_text(text):
    """Extract a date from the message; defaults to today. Returns (DD-MM-YYYY, cleaned_text)."""
    today = datetime.date.today()
    lower = text.lower()

    m = re.search(r'\b(\d{1,2}[-./]\d{1,2}[-./]\d{4})\b', lower)
    if m:
        for fmt in ("%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y"):
            try:
                d = datetime.datetime.strptime(m.group(1).replace('.', '-').replace('/', '-'), "%d-%m-%Y").date()
                return d.strftime("%d-%m-%Y"), (text[:m.start()] + text[m.end():]).strip()
            except ValueError:
                continue
    if 'yesterday' in lower:
        d = today - datetime.timedelta(days=1)
        idx = lower.find('yesterday')
        return d.strftime("%d-%m-%Y"), (text[:idx] + text[idx + 9:]).strip()
    if 'today' in lower:
        idx = lower.find('today')
        return today.strftime("%d-%m-%Y"), (text[:idx] + text[idx + 5:]).strip()
    return today.strftime("%d-%m-%Y"), text

def _cash_or_bank(text):
    """Extract 'by cash'/'by bank'/'in cash' hint. Returns (ledger_hint_or_None, cleaned_text)."""
    m = re.search(r'\b(?:by|in|via|through)\s+(cash|bank)\b', text, re.IGNORECASE)
    if m:
        hint = 'Cash' if m.group(1).lower() == 'cash' else 'Bank Account'
        return hint, (text[:m.start()] + text[m.end():]).strip()
    return None, text

def _parse_amount(s):
    try:
        return float(s.replace(',', ''))
    except (ValueError, AttributeError):
        return None

def parse_voucher_message_rule_based(message):
    if not message:
        return None
    text = message.strip()
    date_str, text = _voucher_date_from_text(text)
    cash_bank, text = _cash_or_bank(text)
    q = text.strip(' .!')

    # Receipt: "received 5000 from ABC"
    m = re.match(r'^(?:received|receipt(?:\s+of)?|got)\s+([\d.,]+)\s+from\s+(.+)$', q, re.IGNORECASE)
    if m:
        amount = _parse_amount(m.group(1))
        party = m.group(2).strip()
        if amount and party:
            return {
                "voucher_type": "Receipt", "date": date_str, "amount": amount,
                "narration": f"Received from {party}",
                "ledger_entries": [
                    {"ledger": cash_bank or "Cash", "type": "Debit"},
                    {"ledger": party, "type": "Credit"},
                ],
            }

    # Payment: "paid 1200 to ABC"
    m = re.match(r'^(?:paid|payment(?:\s+of)?|pay)\s+([\d.,]+)\s+to\s+(.+)$', q, re.IGNORECASE)
    if m:
        amount = _parse_amount(m.group(1))
        party = m.group(2).strip()
        if amount and party:
            return {
                "voucher_type": "Payment", "date": date_str, "amount": amount,
                "narration": f"Paid to {party}",
                "ledger_entries": [
                    {"ledger": party, "type": "Debit"},
                    {"ledger": cash_bank or "Cash", "type": "Credit"},
                ],
            }

    # Contra: "transfer 1000 from Cash to Bank"
    m = re.match(r'^(?:transfer(?:red)?|contra)\s+([\d.,]+)\s+from\s+(.+?)\s+to\s+(.+)$', q, re.IGNORECASE)
    if m:
        amount = _parse_amount(m.group(1))
        src, dst = m.group(2).strip(), m.group(3).strip()
        if amount and src and dst:
            return {
                "voucher_type": "Contra", "date": date_str, "amount": amount,
                "narration": f"Transfer from {src} to {dst}",
                "ledger_entries": [
                    {"ledger": dst, "type": "Debit"},
                    {"ledger": src, "type": "Credit"},
                ],
            }

    # Expense: "expense 300 for Fuel"
    m = re.match(r'^(?:expense(?:\s+of)?|spent)\s+([\d.,]+)\s+(?:for|on)\s+(.+)$', q, re.IGNORECASE)
    if m:
        amount = _parse_amount(m.group(1))
        expense = m.group(2).strip()
        if amount and expense:
            return {
                "voucher_type": "Expense", "date": date_str, "amount": amount,
                "narration": expense,
                "ledger_entries": [
                    {"ledger": expense, "type": "Debit"},
                    {"ledger": cash_bank or "Cash", "type": "Credit"},
                ],
            }

    return None


CAPABILITIES_TEXT = (
    "&bull; <b>Balances:</b> 'cash balance', 'bank balance', 'balance of ABC Trading'<br>"
    "&bull; <b>Totals:</b> 'total sales this month', 'total purchase last month', 'net profit this year'<br>"
    "&bull; <b>Reports:</b> 'trial balance', 'balance sheet', 'profit and loss'<br>"
    "&bull; <b>Statements:</b> 'statement of ABC Trading', 'ledger of Fuel'<br>"
    "&bull; <b>Vouchers:</b> 'show voucher SAL-00001', 'sales vouchers this month'<br>"
    "&bull; <b>Stock:</b> 'stock of ItemName', 'closing stock value', 'slow moving items', 'negative stock'<br>"
    "&bull; <b>Analysis:</b> 'top 5 customers', 'top suppliers', 'pending invoices', 'compare monthly sales'<br>"
    "&bull; <b>Exports:</b> 'export sales register', 'download trial balance'"
)

GREETING_TEXT = (
    "Hello! &#128075; I'm your ERP assistant. Here's what I can do for you:<br>" + CAPABILITIES_TEXT +
    "<br>Just type your question - for example, 'total sales this month'."
)

NO_AI_HELP_TEXT = (
    "I couldn't match that to an ERP function. With AI disabled I can directly handle things like:<br>"
    + CAPABILITIES_TEXT +
    "<br>Enable AI for free-form questions."
)

def process_chat_query(user_query, company_id, ai_enabled=True):
    # 1. Always try the fast rule-based ERP parser first - no AI, no data leaves the app.
    parsed = rule_based_parse(user_query)
    if parsed:
        parsed['raw_query'] = user_query
        return execute_intent(parsed, company_id)

    # 2. If AI is disabled, stop here - never send data to OpenRouter.
    if not ai_enabled:
        return {
            "intent": "help",
            "response": NO_AI_HELP_TEXT,
            "data": None,
            "explanation": "AI disabled - rule-based parser could not match the query."
        }

    # 3. AI path (OpenRouter only)
    api_key = get_openrouter_api_key()
    if not api_key:
        return {"error": "OpenRouter API Key not configured."}
    api_url = OPENROUTER_URL
    model_name = get_openrouter_model()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:5000",
    }

    current_date = datetime.date.today().strftime("%Y-%m-%d")
    
    system_prompt = f"""
    You are an intelligent accounting assistant. 
    Your goal is to understand the user's question and map it to a specific intent and parameters.
    The current date is {current_date}.

    Available Intents:
    - get_cash_balance: For questions about cash balance.
    - get_bank_balance: For questions about bank balance.
    - get_total_sales: For questions about total sales (today, this month, etc).
    - get_total_purchase: For questions about total purchase.
    - get_net_profit: For questions about net profit.
    - get_trial_balance: For requests to show trial balance.
    - get_balance_sheet: For requests to show balance sheet.
    - get_profit_and_loss: For requests to show profit and loss statement.
    - get_outstanding_customer: For questions about customer outstanding amount (receivables).
    - get_outstanding_supplier: For questions about supplier payable amount (payables).
    - get_customer_statement: For requests to show customer ledger/statement.
    - get_supplier_statement: For requests to show supplier ledger/statement.
    - get_customer_sales_total: For questions about how much a customer purchased (e.g. "How much did Customer XYZ purchase?").
    - get_supplier_purchase_total: For questions about how much we purchased from a supplier.
    - get_top_customers: For questions about top customers.
    - get_top_suppliers: For questions about top suppliers (purchased from).
    - get_stock_status: For questions about current stock of an item.
    - get_low_stock: For questions about low stock items.
    - get_slow_moving: For questions about slow-moving items.
    - get_no_sales_items: For questions about items with no sales.
    - get_closing_stock_value: For questions about total closing stock value.
    - get_pending_invoices: For questions about overdue or pending invoices.
    - compare_monthly_sales: For requests to compare sales of this month with last month.
    - get_expense_total: For questions about specific expenses, taxes, or ledger accounts (e.g., "Fuel", "Rent", "Input VAT", "Salary").
    - get_ledger_balance: For questions about the balance of a specific account, customer, or supplier at a certain date (e.g., "Balance of Customer X", "What is the balance of Supplier Y").
    - export_report: For requests to download reports in Excel/CSV (e.g., "Give purchase report", "Export sales register", "Download balance sheet").

    Output strict JSON in this format:
    {{
      "intent": "intent_name",
      "parameters": {{
        "date_range": "today" | "yesterday" | "this_week" | "last_week" | "this_month" | "last_month" | "this_year" | "YYYY" | "Month YYYY" | "DD.MM.YYYY" | "Date1 to Date2" | null,
        "entity_name": "extracted customer or supplier name" | null,
        "item_name": "extracted item name" | null,
        "expense_type": "extracted expense name (e.g. Fuel, Rent)" | null,
        "report_type": "Sales" | "Purchase" | "Ledger" | "Trial Balance" | "Balance Sheet" | "P&L" | null,
        "limit": number | null
      }},
      "explanation": "Brief explanation of what you understood"
    }}
    """

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query}
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }

    try:
        result, err = openrouter_request(payload, headers)
        if err:
            return {"error": err}

        content = result['choices'][0]['message']['content']
        print(f"AI Response: {content}") # Log raw response
        
        # Parse JSON with regex fallback
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown or text
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except json.JSONDecodeError:
                    return {"error": "Failed to parse AI response (invalid JSON)"}
            else:
                return {"error": "Failed to parse AI response (no JSON found)"}
            
        print(f"Parsed AI Response: {parsed}")
        parsed['raw_query'] = user_query
        return execute_intent(parsed, company_id)
        
    except requests.exceptions.ConnectionError:
        return {"error": "Connection Error: Could not reach OpenRouter. Please check your internet connection."}
    except Exception as e:
        print(f"Processing Error: {str(e)}")
        return {"error": str(e)}

def serialize_data(data):
    """Recursively convert date/datetime objects to ISO format strings."""
    if isinstance(data, dict):
        return {k: serialize_data(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [serialize_data(v) for v in data]
    elif isinstance(data, (datetime.date, datetime.datetime)):
        return data.isoformat()
    else:
        return data

def execute_intent(parsed_data, company_id):
    intent = parsed_data.get("intent")
    params = parsed_data.get("parameters", {})
    explanation = parsed_data.get("explanation", "")
    
    # Intent Aliases / Normalization
    if intent in ["get_purchase_total", "get_total_purchases", "get_expenses_total", "get_total_expense"]:
        intent = "get_total_purchase"
    elif intent in ["get_sales_total", "get_total_sale", "get_revenue_total"]:
        intent = "get_total_sales"
    elif intent in ["get_profit_total", "get_net_income"]:
        intent = "get_net_profit"
    elif intent in ["get_fuel_expense", "get_expense_total"]:
        intent = "get_expense_total"
    elif intent in ["get_outstanding_customer", "get_outstanding_supplier", "get_customer_balance", "get_supplier_balance", "get_ledger_balance"]:
        intent = "get_ledger_balance"
        
    date_range = params.get("date_range")
    start_date, end_date = parse_date_range(date_range) if date_range else (None, None)
    
    # Format dates as strings if they exist
    start_str = start_date.strftime("%Y-%m-%d") if start_date else None
    end_str = end_date.strftime("%Y-%m-%d") if end_date else None

    result_text = ""
    data = None

    try:
        if intent == "greeting":
            result_text = GREETING_TEXT

        elif intent == "thanks":
            result_text = "You're welcome! &#128522; Ask me anything else about your accounts, vouchers, stock or reports."

        elif intent == "get_cash_balance":
            # Cash is usually Group G005
            ledgers = get_ledgers(group_code='G005', company_id=company_id)
            total_cash = sum(l['closing_balance'] for l in ledgers)
            result_text = f"Total Cash Balance is {total_cash:.2f}"
            data = {"ledgers": ledgers, "total": total_cash}

        elif intent == "get_bank_balance":
            # Bank is usually Group G006
            ledgers = get_ledgers(group_code='G006', company_id=company_id)
            total_bank = sum(l['closing_balance'] for l in ledgers)
            result_text = f"Total Bank Balance is {total_bank:.2f}"
            data = {"ledgers": ledgers, "total": total_bank}

        elif intent == "get_total_sales":
            p_start = start_str
            p_end = end_str
            item_name = params.get("item_name")

            if item_name:
                 # Item-specific sales logic (SUM qty * rate)
                 conn = get_connection()
                 cursor = conn.cursor()
                 
                 query = """
                    SELECT SUM(ie.quantity * ie.unit_price) 
                    FROM item_entries ie
                    JOIN vouchers v ON ie.voucher_number = v.voucher_number
                    WHERE v.company_id=%s AND v.voucher_type='Sales'
                 """
                 params_list = [company_id]
                 
                 if p_start:
                    query += " AND v.date >= %s"
                    params_list.append(p_start)
                 if p_end:
                    query += " AND v.date <= %s"
                    params_list.append(p_end)
                 
                 # Use ILIKE/LOWER for case-insensitive match
                 query += " AND LOWER(ie.item_name) LIKE LOWER(%s)"
                 params_list.append(f"%{item_name}%")
                 
                 try:
                    cursor.execute(query, params_list)
                    total_sales_items = cursor.fetchone()[0] or 0.0
                 except Exception as e:
                    print(f"Error calculating total sales items: {e}")
                    total_sales_items = 0.0
                 finally:
                    cursor.close()
                    conn.close()
                 
                 period = date_range.replace('_', ' ') if date_range else "All Time"
                 result_text = f"Total Sales of '{item_name}' for {period} is {total_sales_items:.2f}"
                 data = {"total_sales": total_sales_items, "item_name": item_name}

            else:
                # Use get_profit_and_loss_data to get actual Revenue (Net Sales) instead of summing Vouchers (which includes VAT)
                # This matches the "Total Income" figure in P&L which users expect as "Actual Sales"
                
                # If no date specified, default to All Time (or handle as YTD if preferred, but P&L defaults to All Time if None)
                # KPI summary used All Time, so we stick to that for consistency if no date.
                
                _, total_income, _, _ = get_profit_and_loss_data(p_start, p_end, company_id=company_id)
                
                if not p_start and not p_end:
                    result_text = f"Total Sales (Revenue) All Time is {total_income:.2f}"
                else:
                    period = date_range.replace('_', ' ') if date_range else "specified period"
                    result_text = f"Total Sales (Revenue) for {period} is {total_income:.2f}"
                
                data = {"total_sales": total_income}

        elif intent == "get_total_purchase":
            # User expectation: Sum of (Item Qty * Unit Rate) from Purchase Vouchers
            # This is essentially the Net Purchase Amount (before tax/discounts applied at voucher level)
            
            p_start = start_str
            p_end = end_str
            item_name = params.get("item_name")
            
            conn = get_connection()
            cursor = conn.cursor()
            
            # User explicitly requested SUM(qty * unit_price)
            query = """
                SELECT SUM(ie.quantity * ie.unit_price) 
                FROM item_entries ie
                JOIN vouchers v ON ie.voucher_number = v.voucher_number AND ie.company_id = v.company_id
                WHERE v.company_id=%s AND v.voucher_type='Purchase'
            """
            params = [company_id]
            
            if p_start:
                query += " AND v.date >= %s"
                params.append(p_start)
            if p_end:
                query += " AND v.date <= %s"
                params.append(p_end)
            
            if item_name:
                # Use ILIKE for Postgres or LIKE for SQLite (case insensitive usually if configured, but better to use LOWER)
                # Since we use standard placeholders, let's use LOWER() for compatibility.
                query += " AND LOWER(ie.item_name) LIKE LOWER(%s)"
                params.append(f"%{item_name}%")
                
            try:
                cursor.execute(query, params)
                total_purchase_items = cursor.fetchone()[0] or 0.0
            except Exception as e:
                print(f"Error calculating total purchase items: {e}")
                total_purchase_items = 0.0
            finally:
                cursor.close()
                conn.close()

            period = date_range.replace('_', ' ') if date_range else "All Time"
            
            if item_name:
                result_text = f"Total Purchase of '{item_name}' for {period} is {total_purchase_items:.2f}"
            elif not p_start and not p_end:
                result_text = f"Total Purchase (Item Value) All Time is {total_purchase_items:.2f}"
            else:
                result_text = f"Total Purchase (Item Value) for {period} is {total_purchase_items:.2f}"
            
            data = {"total_purchase": total_purchase_items, "item_name": item_name}

        elif intent == "get_expense_total":
            # Handle specific expense/ledger queries like "Fuel Expense", "Rent", "Input VAT", etc.
            expense_name = params.get("expense_type") or params.get("item_name")
            
            if not expense_name:
                result_text = "Please specify which account you want to check (e.g., Fuel, Rent, Input VAT)."
            else:
                # Search for ledgers matching the name
                conn = get_connection()
                cursor = conn.cursor()
                
                # Allow searching all ledgers (removed nature filter)
                cursor.execute("""
                    SELECT l.ledger_name
                    FROM ledgers l
                    WHERE l.company_id = %s
                """, (company_id,))
                
                all_ledger_names = [row[0] for row in cursor.fetchall()]
                cursor.close()
                conn.close()
                
                matched_ledgers = []
                # Helper to handle common synonyms (Tax <-> VAT)
                search_terms = {expense_name.lower()}
                if 'tax' in expense_name.lower():
                    search_terms.add(expense_name.lower().replace('tax', 'vat'))
                if 'vat' in expense_name.lower():
                    search_terms.add(expense_name.lower().replace('vat', 'tax'))
                
                for lname in all_ledger_names:
                    l_lower = lname.lower()
                    for term in search_terms:
                        if term in l_lower:
                            if lname not in matched_ledgers:
                                matched_ledgers.append(lname)
                
                if not matched_ledgers:
                    result_text = f"No ledger found matching '{expense_name}'."
                else:
                    total_amount = 0.0
                    ledger_details = []
                    
                    for lname in matched_ledgers:
                        trans, bal = get_ledger_transactions(lname, start_str, end_str, company_id=company_id)
                        
                        # Calculate movement excluding Closing entries
                        # Debit - Credit = Net Movement (Positive for Expense/Asset increase, Negative for Income/Liability increase)
                        amount = 0.0
                        for t in trans:
                            if t['voucher_type'] == 'Closing':
                                continue
                            amount += (t['debit'] or 0) - (t['credit'] or 0)
                            
                        total_amount += amount
                        ledger_details.append({"name": lname, "amount": amount})
                    
                    period = date_range.replace('_', ' ') if date_range else "All Time"
                    
                    if len(matched_ledgers) == 1:
                        result_text = f"Total movement for '{matched_ledgers[0]}' in {period} is {total_amount:.2f}"
                    else:
                        result_text = f"Total movement for '{expense_name}' (across {len(matched_ledgers)} ledgers) in {period} is {total_amount:.2f}"
                    
                    data = {"ledgers": ledger_details, "total": total_amount}

        elif intent == "get_ledger_balance":
            entity_name = params.get("entity_name") or params.get("expense_type") or params.get("item_name")
            
            if not entity_name:
                result_text = "Please specify which account, customer, or supplier you want to check."
            else:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT ledger_name FROM ledgers WHERE company_id = %s", (company_id,))
                all_ledger_names = [row[0] for row in cursor.fetchall()]
                cursor.close()
                conn.close()
                
                matched_ledgers = []
                # Simple matching
                for lname in all_ledger_names:
                    if entity_name.lower() in lname.lower():
                        matched_ledgers.append(lname)
                
                if not matched_ledgers:
                    result_text = f"No account found matching '{entity_name}'."
                else:
                    ledger_details = []
                    total_balance = 0.0
                    
                    # For balance as of date, use end_str as the cutoff.
                    cutoff_date = end_str if end_str else None
                    period_desc = f"as of {cutoff_date}" if cutoff_date else "currently"

                    for lname in matched_ledgers:
                        # get_ledger_transactions returns (transactions, closing_balance)
                        # If we pass to_date=cutoff_date, closing_balance is the balance as of that date.
                        _, bal = get_ledger_transactions(lname, to_date=cutoff_date, company_id=company_id)
                        
                        total_balance += bal
                        ledger_details.append({"name": lname, "balance": bal})
                    
                    if len(matched_ledgers) == 1:
                        bal_val = ledger_details[0]['balance']
                        dr_cr = "Dr" if bal_val >= 0 else "Cr"
                        result_text = f"Balance of '{matched_ledgers[0]}' {period_desc} is {abs(bal_val):.2f} {dr_cr}"
                    else:
                        dr_cr = "Dr" if total_balance >= 0 else "Cr"
                        result_text = f"Total Balance for '{entity_name}' (across {len(matched_ledgers)} accounts) {period_desc} is {abs(total_balance):.2f} {dr_cr}"
                        
                    data = {"ledgers": ledger_details, "total_balance": total_balance}

        elif intent == "get_net_profit":
            # P&L
            _, _, _, net_profit = get_profit_and_loss_data(start_str, end_str, company_id=company_id)
            period = date_range.replace('_', ' ') if date_range else "Inception to Date"
            result_text = f"Net Profit for {period} is {net_profit:.2f}"
            data = {"net_profit": net_profit}

        elif intent == "get_trial_balance":
            tb, dr, cr = get_trial_balance_data(end_str, company_id=company_id)
            result_text = f"Trial Balance generated. Total Debit: {dr:.2f}, Total Credit: {cr:.2f}"
            data = {"trial_balance": tb, "total_debit": dr, "total_credit": cr}

        elif intent == "get_balance_sheet":
            bs, assets, liabs = get_balance_sheet_data(end_str, company_id=company_id)
            result_text = f"Balance Sheet generated. Total Assets: {assets:.2f}, Total Liabilities: {liabs:.2f}"
            data = bs

        elif intent == "get_profit_and_loss":
            pnl_data, inc, exp, net = get_profit_and_loss_data(start_str, end_str, company_id=company_id)
            result_text = f"P&L Statement generated. Income: {inc:.2f}, Expenses: {exp:.2f}, Net Profit: {net:.2f}"
            data = pnl_data

        elif intent == "export_report":
            report_type = params.get("report_type")
            if not report_type:
                # Try to infer from the user's own words first, then the AI explanation.
                # Statement reports are checked before registers to avoid false matches.
                hint = (parsed_data.get("raw_query", "") + " " + explanation).lower().replace('balancesheet', 'balance sheet').replace('trialbalance', 'trial balance')
                if "balance sheet" in hint:
                    report_type = "Balance Sheet"
                elif "trial balance" in hint:
                    report_type = "Trial Balance"
                elif "profit" in hint or "p&l" in hint or "pnl" in hint:
                    report_type = "P&L"
                elif "purchase" in hint:
                    report_type = "Purchase"
                elif "sales" in hint:
                    report_type = "Sales"

            # Ask for a period when no date was given (user can answer
            # e.g. "this month", "August 2025", "01-01-2025 to 30-06-2025" or "all time")
            if report_type and not date_range:
                if report_type in ("Trial Balance", "Balance Sheet"):
                    ask = f"As of which date should I prepare the {report_type}? (e.g., '31-12-2025', 'today', or 'all time' for the latest)"
                else:
                    ask = (f"For which period do you want the {report_type} report? "
                           "(e.g., 'this month', 'last month', 'August 2025', '01-01-2025 to 30-06-2025', or 'all time')")
                return {
                    "intent": "ask_date",
                    "response": ask,
                    "data": {"need_date": True, "pending_query": parsed_data.get("raw_query", "")},
                    "explanation": "Waiting for the user to provide a report period."
                }

            register_types = ["Sales", "Purchase", "Sales Return", "Purchase Return",
                              "Payment", "Receipt", "Journal", "Contra", "Expense"]

            download_url = ""
            file_label = "Report"

            if report_type in register_types:
                # Map to Voucher Register export
                download_url = f"/export_report/voucher_register?voucher_type={report_type}"
                if start_str: download_url += f"&from_date={start_str}"
                if end_str: download_url += f"&to_date={end_str}"
                file_label = f"{report_type} Register"

            elif report_type == "Trial Balance":
                download_url = "/export_report/trial_balance"
                if end_str: download_url += f"?as_of_date={end_str}"
                file_label = "Trial Balance"

            elif report_type == "Balance Sheet":
                download_url = "/export_report/balance_sheet"
                if end_str: download_url += f"?as_of_date={end_str}"
                file_label = "Balance Sheet"

            elif report_type == "P&L":
                download_url = "/export_report/profit_and_loss"
                date_params = []
                if start_str: date_params.append(f"from_date={start_str}")
                if end_str: date_params.append(f"to_date={end_str}")
                if date_params: download_url += "?" + "&".join(date_params)
                file_label = "Profit & Loss"

            if download_url:
                period_desc = " (all time)" if date_range == "all_time" or not date_range else f" for {date_range.replace('_', ' ')}"
                result_text = f"I've generated the {file_label}{period_desc}. <br><a href='{download_url}' target='_blank' class='btn btn-sm btn-primary'>Download Excel</a>"
            else:
                result_text = "I'm not sure which report you want to export. Please specify (e.g., 'Export Purchase Report')."

            data = {"download_url": download_url}

        elif intent in ["get_outstanding_customer", "get_customer_statement"]:
            name = params.get("entity_name")
            if not name:
                result_text = "Please specify a customer name."
            else:
                # Fuzzy match customer name
                ledgers = get_ledgers(company_id=company_id) 
                # Simple fuzzy search
                matched_name = None
                for l in ledgers:
                    lname = l['ledger_name']
                    if name.lower() in lname.lower():
                        matched_name = lname
                        break
                
                if matched_name:
                    if intent == "get_outstanding_customer":
                         # Get current balance
                         bal = next(l['closing_balance'] for l in ledgers if l['ledger_name'] == matched_name)
                         result_text = f"Outstanding amount for {matched_name} is {bal:.2f}"
                    else:
                         trans, bal = get_ledger_transactions(matched_name, start_str, end_str, company_id=company_id)
                         result_text = f"Statement for {matched_name} generated. Current Balance: {bal:.2f}"
                         data = trans
                else:
                    result_text = f"Customer '{name}' not found."

        elif intent in ["get_outstanding_supplier", "get_supplier_statement"]:
             name = params.get("entity_name")
             if not name:
                result_text = "Please specify a supplier name."
             else:
                ledgers = get_ledgers(company_id=company_id)
                matched_name = None
                for l in ledgers:
                    lname = l['ledger_name']
                    if name.lower() in lname.lower():
                        matched_name = lname
                        break
                
                if matched_name:
                    if intent == "get_outstanding_supplier":
                         bal = next(l['closing_balance'] for l in ledgers if l['ledger_name'] == matched_name)
                         result_text = f"Payable amount to {matched_name} is {abs(bal):.2f}"
                    else:
                         trans, bal = get_ledger_transactions(matched_name, start_str, end_str, company_id=company_id)
                         result_text = f"Statement for {matched_name} generated. Current Balance: {bal:.2f}"
                         data = trans
                else:
                    result_text = f"Supplier '{name}' not found."

        elif intent == "get_customer_sales_total":
            name = params.get("entity_name")
            if not name:
                result_text = "Please specify a customer name."
            else:
                ledgers = get_ledgers(company_id=company_id)
                matched_name = None
                for l in ledgers:
                    lname = l['ledger_name']
                    if name.lower() in lname.lower():
                        matched_name = lname
                        break
                
                if matched_name:
                    trans, _ = get_ledger_transactions(matched_name, start_str, end_str, company_id=company_id)
                    # Sum debits or specifically Sales vouchers
                    total = sum(t['debit'] for t in trans if t['voucher_type'] == 'Sales')
                    period = date_range.replace('_', ' ') if date_range else "all time"
                    result_text = f"Total sales to {matched_name} for {period}: {total:.2f}"
                    data = {"transactions": trans, "total": total}
                else:
                    result_text = f"Customer '{name}' not found."

        elif intent == "get_supplier_purchase_total":
            name = params.get("entity_name")
            if not name:
                result_text = "Please specify a supplier name."
            else:
                ledgers = get_ledgers(company_id=company_id)
                matched_name = None
                for l in ledgers:
                    lname = l['ledger_name']
                    if name.lower() in lname.lower():
                        matched_name = lname
                        break
                
                if matched_name:
                    trans, _ = get_ledger_transactions(matched_name, start_str, end_str, company_id=company_id)
                    # Sum credits or specifically Purchase vouchers
                    total = sum(t['credit'] for t in trans if t['voucher_type'] == 'Purchase')
                    period = date_range.replace('_', ' ') if date_range else "all time"
                    result_text = f"Total purchase from {matched_name} for {period}: {total:.2f}"
                    data = {"transactions": trans, "total": total}
                else:
                    result_text = f"Supplier '{name}' not found."

        elif intent == "get_top_customers":
            limit = params.get("limit") or 5
            top = get_top_customers(limit, company_id=company_id)
            names = ", ".join([f"{t['name']} ({t['value']:.2f})" for t in top])
            result_text = f"Top {limit} Customers: {names}"
            data = top

        elif intent == "get_top_suppliers":
            limit = params.get("limit") or 5
            top = get_top_suppliers(limit, company_id=company_id)
            names = ", ".join([f"{t['name']} ({t['value']:.2f})" for t in top])
            result_text = f"Top {limit} Suppliers: {names}"
            data = top

        elif intent == "get_stock_status":
            item_name = params.get("item_name")
            if not item_name:
                result_text = "Please specify an item name."
            else:
                # Fuzzy match item
                items = get_items(company_id=company_id)
                item_names = [i['name'] for i in items]
                
                # Check for exact match first (case-insensitive)
                matched_name = None
                for name in item_names:
                    if name.lower() == item_name.lower():
                        matched_name = name
                        break
                
                # If not exact, try fuzzy match
                if not matched_name:
                    matches = difflib.get_close_matches(item_name, item_names, n=1, cutoff=0.6)
                    if matches:
                        matched_name = matches[0]
                
                if matched_name:
                    # Parse date if provided
                    as_of_date = None
                    date_range = params.get("date_range")
                    if date_range:
                        # parse_date_range returns start, end. For "as of", we want the end date.
                        _, end_date = parse_date_range(date_range)
                        as_of_date = end_date

                    # Use closing inventory logic to get accurate stock status even without movements
                    qty, value = get_item_closing_stock(matched_name, as_of_date=as_of_date, company_id=company_id)
                    wap = (value / qty) if qty != 0 else 0.0
                    
                    date_str = f" as of {as_of_date}" if as_of_date else ""
                    result_text = f"Stock of {matched_name}{date_str}: Qty: {qty}, WAP: {wap:.2f}, Total Value: {value:.2f}"
                else:
                    result_text = f"Item '{item_name}' not found."

        elif intent == "get_low_stock":
             # We can use get_negative_stock_items or define a threshold
             # Or maybe there is a 'low stock' report. 
             # For now, let's return negative stock items as 'Critical'
             neg = get_negative_stock_items(company_id=company_id)
             names = ", ".join([n[1] for n in neg]) # item_code, name, qty
             result_text = f"Items with negative stock: {names}" if names else "No items with negative stock."
             data = neg

        elif intent == "get_slow_moving":
             slow = get_slow_moving_items(company_id=company_id)
             names = ", ".join([s['item_name'] for s in slow[:5]])
             result_text = f"Slow moving items (Top 5): {names}"
             data = slow

        elif intent == "get_closing_stock_value":
             # report_stock_valuation usually calculates this.
             # We can sum up stock value of all items.
             # Or use get_stock_category_summary which sums up values.
             # Let's use that.
             summary = get_stock_category_summary(company_id=company_id)
             total = sum(s['value'] for s in summary)
             result_text = f"Total Closing Stock Value is {total:.2f}"
             data = summary
             
        elif intent == "get_pending_invoices":
             # "Which invoices are overdue?" or "Which supplier invoices are pending?"
             # We need to detect if it's for Customer (Receivables) or Supplier (Payables)
             # The system prompt might not distinguish perfectly, so we can check parameters or return both.
             # Or we can look at the question content in explanation.
             # But for now, let's assume if "supplier" is mentioned in explanation or query, it's G008.
             # A better way is to check the entity_name if provided, or return both.
             
             # Let's check if the intent name can be more specific or we infer.
             # Since we only have one intent "get_pending_invoices", let's try to handle both or specific based on context.
             # Actually, the user asked "Which supplier invoices are pending?" -> get_pending_invoices
             # "Which invoices are overdue?" -> get_pending_invoices (implied customer usually)
             
             is_supplier = "supplier" in explanation.lower() or "payable" in explanation.lower()
             group_code = 'G008' if is_supplier else 'G007'
             entity_type = "Suppliers" if is_supplier else "Customers"
             
             ageing = get_ageing_report_data(group_code, company_id=company_id)
             # Filter those with balance > 0 (Outstanding)
             # For suppliers, balance is Credit (negative or positive depending on implementation). 
             # In get_ageing_report_data, it usually returns positive for outstanding.
             outstanding = [a for a in ageing if a['balance'] > 0]
             
             if not outstanding:
                 result_text = f"No pending invoices found for {entity_type}."
             else:
                 names = ", ".join([f"{a['ledger_name']} ({a['balance']})" for a in outstanding[:5]])
                 result_text = f"{entity_type} with outstanding invoices: {names}"
             data = outstanding

        elif intent == "get_no_sales_items":
             # Reuse slow moving logic but with longer threshold?
             # Or find items with no Sales voucher.
             slow = get_slow_moving_items(days_threshold=365, company_id=company_id)
             names = ", ".join([s['item_name'] for s in slow[:5]])
             result_text = f"Items with no sales in last year: {names}"
             data = slow

        elif intent == "compare_monthly_sales":
             today = datetime.date.today()
             current_year = today.year
             current_month_num = today.month
             
             # Calculate previous month
             if current_month_num == 1:
                 prev_month_num = 12
                 prev_year = current_year - 1
             else:
                 prev_month_num = current_month_num - 1
                 prev_year = current_year

             # Get trends
             current_trend = get_monthly_sales_trend(year=current_year, company_id=company_id)
             
             # Get values
             current_month_str = str(current_month_num).zfill(2)
             current_sales = current_trend.get(current_month_str, 0.0)
             
             prev_month_str = str(prev_month_num).zfill(2)
             if prev_year == current_year:
                 prev_sales = current_trend.get(prev_month_str, 0.0)
             else:
                 prev_trend = get_monthly_sales_trend(year=prev_year, company_id=company_id)
                 prev_sales = prev_trend.get(prev_month_str, 0.0)
                 
             diff = current_sales - prev_sales
             status = "increase" if diff >= 0 else "decrease"
             
             result_text = (
                 f"Sales Comparison:\n"
                 f"This Month ({today.strftime('%B')}): {current_sales:.2f}\n"
                 f"Last Month: {prev_sales:.2f}\n"
                 f"Difference: {abs(diff):.2f} ({status})"
             )
             data = {
                 "current_month": {"month": current_month_str, "sales": current_sales},
                 "prev_month": {"month": prev_month_str, "sales": prev_sales},
                 "difference": diff
             }

        elif intent == "get_voucher_details":
            voucher_number = params.get("voucher_number")
            if not voucher_number:
                result_text = "Please specify a voucher number (e.g., SAL-00001)."
            else:
                conn = get_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute("""
                        SELECT voucher_number, date, voucher_type, amount, narration
                        FROM vouchers WHERE company_id = %s AND UPPER(voucher_number) = %s
                    """, (company_id, voucher_number.upper()))
                    v = cursor.fetchone()
                    if not v:
                        result_text = f"Voucher '{voucher_number}' not found."
                    else:
                        cursor.execute("""
                            SELECT ledger_name, amount, type FROM ledger_entries
                            WHERE company_id = %s AND UPPER(voucher_number) = %s
                        """, (company_id, voucher_number.upper()))
                        entries = cursor.fetchall()
                        cursor.execute("""
                            SELECT item_name, quantity, unit_price, amount FROM item_entries
                            WHERE company_id = %s AND UPPER(voucher_number) = %s
                        """, (company_id, voucher_number.upper()))
                        items = cursor.fetchall()

                        lines = [f"<b>{v[0]}</b> | {v[2]} | Date: {v[1]} | Amount: {v[3]:.2f}"]
                        if v[4]:
                            lines.append(f"Narration: {v[4]}")
                        if entries:
                            lines.append("<b>Ledger Entries:</b>")
                            for e in entries:
                                lines.append(f"&bull; {e[0]}: {e[1]:.2f} {e[2]}")
                        if items:
                            lines.append("<b>Items:</b>")
                            for i in items:
                                lines.append(f"&bull; {i[0]}: Qty {i[1]} x {i[2]:.2f} = {i[3]:.2f}")
                        result_text = "<br>".join(lines)
                        data = {
                            "voucher": {"number": v[0], "date": v[1], "type": v[2], "amount": v[3], "narration": v[4]},
                            "ledger_entries": [{"ledger": e[0], "amount": e[1], "type": e[2]} for e in entries],
                            "items": [{"name": i[0], "qty": i[1], "rate": i[2], "amount": i[3]} for i in items],
                        }
                finally:
                    cursor.close()
                    conn.close()

        elif intent == "list_vouchers":
            vtype = params.get("voucher_type") or "All"
            vouchers = get_voucher_register_data(vtype, start_str, end_str, company_id=company_id)
            period = date_range.replace('_', ' ') if date_range else "all time"
            if not vouchers:
                result_text = f"No {vtype} vouchers found for {period}."
            else:
                total = sum((v['amount'] or 0) for v in vouchers)
                lines = [f"<b>{len(vouchers)} {vtype} voucher(s)</b> for {period}. Total: {total:.2f}"]
                for v in vouchers[:10]:
                    lines.append(f"&bull; {v['voucher_number']} | {v['date']} | {v['party_name']} | {(v['amount'] or 0):.2f}")
                if len(vouchers) > 10:
                    lines.append(f"...and {len(vouchers) - 10} more. Use the register report for the full list.")
                result_text = "<br>".join(lines)
                data = {"count": len(vouchers), "total": total}

        else:
             print(f"Unhandled Intent: {intent}")
             result_text = f"I understood: {explanation}. But I don't know how to fetch that data yet (Unhandled Intent: {intent})."

    except Exception as e:
        result_text = f"Error executing query: {str(e)}"

    return {
        "intent": intent,
        "response": result_text,
        "data": serialize_data(data),
        "explanation": explanation
    }
