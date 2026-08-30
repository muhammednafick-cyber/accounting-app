"""Deciding which coded tool answers a question.

Three stages, in order:

1. Rules. Keyword and pattern matching straight to a tool. No network call,
   works with AI switched off, and costs nothing.
2. The model as a chooser. It sees the tool catalogue and the conversation so
   far and returns a tool name plus arguments - never a figure, never SQL.
   Anything it returns is validated against the registry before it runs.
3. Permission. If no tool fits, the assistant stops and asks whether it may
   let the AI query the database directly, and only proceeds on a yes.

Continuity lives in chat_context: a follow-up inherits the period, the party
and the item from the previous answer rather than starting from nothing.
"""
import json
import re

from . import chat_context as ctx
from . import chat_resolver as R
from . import chat_toolkit as TK
from . import chat_permissions as P

MAX_TABLE_ROWS = 15


# ============================================================
# Rendering
# ============================================================

def _cell(value):
    """(text, css_class) for one table cell.

    Figures are right-aligned so they line up column-wise, and negatives are
    marked - in an accounting table the sign is the point.
    """
    if value is None:
        return "", ""
    if isinstance(value, bool):
        return ("Yes" if value else "No"), ""

    number = None
    if isinstance(value, int):
        text, number = f"{value:,}", value          # counts
    elif isinstance(value, float):
        text, number = TK.fmt(value), value         # money and quantities
    else:
        text = str(value)
        # Some amount columns arrive from the driver as strings. Only decimals
        # are treated as money - a bare "2024" is a year or a code, and putting
        # a thousands separator in it turns it into nonsense.
        if re.fullmatch(r'-?\d+\.\d+', text):
            number = float(text)
            text = TK.fmt(number)

    if number is None:
        return text, ""
    return text, ("rv-num rv-neg" if number < 0 else "rv-num")


def render_table(result, limit=MAX_TABLE_ROWS):
    """The result's rows as a compact HTML table, or '' when there are none."""
    columns, rows = result.get("columns"), result.get("rows")
    if not columns or not rows:
        return ""
    shown = rows[:limit]
    rendered = [[_cell(v) for v in r] for r in shown]

    # A column is numeric when every filled cell in it is - then the heading is
    # right-aligned to sit over its own figures.
    numeric_cols = set()
    for idx in range(len(columns)):
        cells = [row[idx] for row in rendered if idx < len(row) and row[idx][0] != ""]
        if cells and all("rv-num" in css for _, css in cells):
            numeric_cols.add(idx)

    head = "".join(
        f"<th class='rv-num'>{c}</th>" if i in numeric_cols else f"<th>{c}</th>"
        for i, c in enumerate(columns))
    body = ""
    for row in rendered:
        body += "<tr>" + "".join(
            (f"<td class='{css}'>{text}</td>" if css else f"<td>{text}</td>")
            for text, css in row) + "</tr>"
    html = ("<div class='rv-table-wrap'><table class='rv-table'>"
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>")
    if len(rows) > limit:
        html += (f"<div class='rv-table-more'>Showing {limit} of {len(rows)} rows - "
                 "download the file for all of them.</div>")
    return html


def render_totals(result):
    totals = result.get("totals") or {}
    if not totals:
        return ""
    return ("<div class='rv-totals'>"
            + " &nbsp;|&nbsp; ".join(f"<b>{k}:</b> {v}" for k, v in totals.items())
            + "</div>")


FORMAT_WORDS = ((r'\bcsv\b', "csv"), (r'\bpdf\b', "pdf"))


def requested_format(question):
    """The file format the user named, defaulting to Excel."""
    low = (question or "").lower()
    for pattern, fmt in FORMAT_WORDS:
        if re.search(pattern, low):
            return fmt
    return "xlsx"


def export_link(token, label=None, chart=None, fmt="xlsx"):
    if not token:
        return ""
    href = f"/export_chat_result?token={token}"
    if fmt and fmt != "xlsx":
        href += f"&format={fmt}"
    if chart and fmt == "xlsx":
        href += f"&chart={chart}"
    if label is None:
        label = {"csv": "Download CSV", "pdf": "Download PDF"}.get(
            fmt, "Download Excel")
        if chart and fmt == "xlsx":
            label = f"Download Excel ({chart} chart)"
    return (f"<a href='{href}' target='_blank' class='btn btn-sm btn-primary "
            f"rv-dl'>{label}</a>")


def export_links(token, chart=None, primary="xlsx"):
    """The primary download, plus the other two formats as quiet alternatives."""
    if not token:
        return ""
    others = [f for f in ("xlsx", "csv", "pdf") if f != primary]
    alternatives = " ".join(
        f"<a href='/export_chat_result?token={token}&format={f}' target='_blank' "
        f"class='rv-alt-dl'>{f.upper()}</a>" for f in others)
    return (export_link(token, chart=chart, fmt=primary)
            + f" <small class='rv-alt'>or {alternatives}</small>")


def remember_result(result, question):
    """Park the rows so they can be exported. Returns the token, or None."""
    if not result.get("rows"):
        return None
    try:
        from .chat_export_store import remember
        title = (result.get("title") or question or "chat_result")
        title = re.sub(r'[\\/:*?"<>|]', '', str(title))[:60] or "chat_result"
        return remember(result["columns"], result["rows"], title=title)
    except Exception as exc:
        print(f"[chat] could not cache result: {exc}")
        return None


def answer(result, question, tool_name, source="coded", chart=None):
    """Assemble the chat bubble for a finished tool result."""
    token = remember_result(result, question)
    parts = [result.get("summary") or result.get("title") or ""]
    tbl = render_table(result)
    if tbl:
        parts.append(tbl)
    totals = render_totals(result)
    if totals and tbl:
        parts.append(totals)
    if result.get("note"):
        parts.append(f"<small class='rv-note'>{result['note']}</small>")
    if token:
        parts.append(export_links(token, chart=chart,
                                  primary=requested_format(question)))
    badge = ("<small class='rv-src rv-src-ai'>AI-generated answer</small>"
             if source == "ai" else
             f"<small class='rv-src'>Computed from your data &middot; {tool_name}</small>")
    parts.append(badge)

    return {
        "intent": tool_name,
        "response": "<br>".join(p for p in parts if p),
        "data": {
            "tool": tool_name,
            "source": source,
            "title": result.get("title"),
            "columns": result.get("columns"),
            "rows": result.get("rows"),
            "totals": result.get("totals"),
            "export_token": token,
        },
        "explanation": f"{tool_name} computed from the database.",
    }, token


def plain(text, intent="message", data=None):
    return {"intent": intent, "response": text, "data": data,
            "explanation": intent}


def choice_buttons(options, prefix=""):
    return "".join(
        f"<button type='button' class='rv-pick' data-value=\"{o}\">{prefix}{o}</button>"
        for o in options)


# ============================================================
# Follow-up detection
# ============================================================

FOLLOWUP_PRONOUNS = re.compile(
    r'\b(it|that|this|those|these|them|him|her|his|hers|their|theirs|its|same|'
    r'the same|there)\b', re.I)

BREAKDOWN_TARGETS = [
    (r'\b(customer|client|debtor|buyer)s?\b', "customer"),
    (r'\b(supplier|vendor|creditor)s?\b', "supplier"),
    (r'\b(item|product|sku|stock)s?\b', "item"),
    (r'\b(month|monthly)\b', "month"),
    (r'\b(location|branch|store|warehouse)s?\b', "location"),
    (r'\b(cost cent(?:re|er))s?\b', "cost_center"),
]

BREAKDOWN_TOOLS = {
    ("sales", "customer"): "sales_by_customer",
    ("sales", "item"): "sales_by_item",
    ("sales", "month"): "sales_by_month",
    ("sales", "location"): "sales_by_location",
    ("sales", "cost_center"): "sales_by_cost_center",
    ("purchase", "supplier"): "purchases_by_supplier",
    ("purchase", "item"): "purchases_by_item",
    ("purchase", "month"): "purchases_by_month",
}


# Which subject a category word belongs to. "items" is about stock even when
# the sentence looked like a ledger question ("details of items").
ITEM_WORDS = {"item", "items", "product", "products", "stock", "goods"}
PARTY_WORDS = {"customer", "customers", "client", "clients", "buyer", "buyers",
               "debtor", "debtors", "supplier", "suppliers", "vendor", "vendors",
               "creditor", "creditors", "party", "parties", "account",
               "accounts", "ledger", "ledgers"}

# When a single-record tool is asked about a whole category ("stock of items"),
# the honest answer is that tool's list-everything counterpart.
GENERIC_FALLBACKS = {
    "item_stock": "closing_stock_value",
    "item_master_details": "list_items",
    "item_opening_stock": "item_opening_stock",
    "price_list": "price_list",
    "stock_movement": "closing_stock_value",
    "ledger_balance": "all_ledger_balances",
    "ledger_master_details": "list_ledgers",
    "party_details": "party_details",
    "ledger_opening_balance": "ledger_opening_balance",
    "settlements_by_party": "settlements_by_party",
}

# Used when the tool itself has no counterpart but the word makes the subject
# obvious - "details of items" is an item listing, not a ledger listing.
GENERIC_BY_SUBJECT = {"item": "list_items", "party": "list_ledgers"}


def _generic_subject(term):
    word = str(term or "").lower().strip()
    if word in ITEM_WORDS:
        return "item"
    if word in PARTY_WORDS:
        return "party"
    return None


def _generic_subject_of_tool(tool_name):
    """Whether a tool is about items or about accounts, by its parameters."""
    tool = TK.TOOLS.get(tool_name)
    if not tool:
        return None
    names = tool.param_names
    if "item" in names or tool.group == "Inventory":
        return "item"
    if "ledger" in names or tool_name in ("list_ledgers", "all_ledger_balances"):
        return "party"
    return None


def _family(tool_name):
    name = tool_name or ""
    if "purchase" in name:
        return "purchase"
    if "sales" in name or name in ("top_customers",):
        return "sales"
    return None


def match_followup(q, text, state):
    """A short question that only makes sense against the previous answer."""
    last = state.get("last_tool")
    if not last:
        return None

    stripped = q.strip(' ?.!')

    # "break it by customer", "split by month", "now by item"
    m = re.match(r'^(?:and\s+|now\s+|ok\s+)?(?:break|split|group|show|give|list)?\s*'
                 r'(?:it|that|them|this)?\s*(?:down\s+)?(?:by|per)\s+(.+)$', stripped)
    if m:
        family = _family(last)
        for pattern, target in BREAKDOWN_TARGETS:
            if re.search(pattern, m.group(1), re.I):
                tool_name = BREAKDOWN_TOOLS.get((family or "sales", target))
                if tool_name:
                    return tool_name, {"inherit_period": True}
        return None

    # "only the top 5", "top 3 of those", "just 10"
    m = re.match(r'^(?:only\s+|just\s+)?(?:the\s+)?(?:top|first)\s+(\d{1,4})'
                 r'(?:\s+of\s+(?:those|them|that))?$', stripped)
    if m and last in TK.TOOLS:
        return last, {"limit": int(m.group(1)), "inherit_period": True,
                      "inherit_entity": True}

    # A bare period: "and for last month?", "what about 2023", "2024"
    period, remainder = R.extract_period(text)
    if period is not None:
        leftover = re.sub(
            r'^(?:and|ok|also|now|what|how)?\s*(?:about|for|in|during|of)?\s*'
            r'(?:the\s+)?(?:same|it|that|this)?\s*[?.!]*$',
            '', remainder.strip(), flags=re.I).strip()
        if not leftover and last in TK.TOOLS:
            return last, {"period": remainder or text, "_period": period,
                          "inherit_entity": True}

    # A bare name after a party-shaped answer: "what about ABC Trading?"
    m = re.match(r'^(?:and|what about|how about|same for|now)\s+(?:for\s+)?(.+)$',
                 stripped, re.I)
    if m and last in TK.TOOLS and "ledger" in (TK.TOOLS[last].params or ""):
        return last, {"ledger": m.group(1).strip(), "inherit_period": True}

    # "his outstanding", "their statement", "its stock" - a different tool for
    # the party or item already under discussion.
    m = re.match(r'^(?:and\s+|what about\s+|show\s+|give me\s+)?'
                 r'(?:his|her|hers|their|theirs|its|the same|that)\s+(.+)$', stripped)
    if m and state.get("last_ledger"):
        noun = m.group(1).strip()
        for pattern, tool_name in (
            (r'^(?:outstanding|balance|dues?|amount due)$', "ledger_balance"),
            (r'^(?:statement|ledger|account|transactions?|history)$', "ledger_statement"),
            (r'^(?:sales|purchases?)$',
             "purchase_total" if 'purchase' in noun else "sales_total"),
            (r'^(?:invoices?|vouchers?|bills?)$', "list_vouchers"),
            (r'^(?:details?|contact|contact details?|info)$', "party_details"),
            (r'^(?:ageing|aging|matching)$', "party_matching"),
        ):
            if re.match(pattern, noun):
                return tool_name, {"ledger": state["last_ledger"],
                                   "inherit_period": True}

    if m and state.get("last_item"):
        noun = m.group(1).strip()
        for pattern, tool_name in (
            (r'^(?:stock|quantity|qty|balance)$', "item_stock"),
            (r'^(?:movements?|history)$', "stock_movement"),
            (r'^(?:price|rate)$', "price_list"),
            (r'^(?:sales)$', "sales_total"),
            (r'^(?:purchases?)$', "purchase_total"),
        ):
            if re.match(pattern, noun):
                return tool_name, {"item": state["last_item"],
                                   "inherit_period": True}

    if FOLLOWUP_PRONOUNS.search(stripped) and len(stripped.split()) <= 6:
        guess = match_rules(text, state, allow_followup=False)
        if guess:
            tool_name, args = guess
            args.setdefault("inherit_entity", True)
            args.setdefault("inherit_period", True)
            return tool_name, args

    return None


# ============================================================
# Rules
# ============================================================

GREETING_RE = re.compile(
    r'^(hi+|hii+|hello+|hey+|hai|salam|salaam|good\s*(morning|afternoon|evening|day)'
    r'|greetings)\b[\s!.]*$', re.I)

THANKS_RE = re.compile(r'^(thanks|thank you|thankyou|ok|okay|great|nice|good|super|'
                       r'perfect|cool)\b[\s!.]*$', re.I)

HELP_RE = re.compile(r'^(help|what can you do|what can you do for me|commands|menu|'
                     r'options|\?|what can i ask)\b[\s!.]*$', re.I)


def _norm(text):
    q = (text or "").lower().strip(' ?.!')
    q = re.sub(r'\s+', ' ', q)
    return (q.replace('balancesheet', 'balance sheet')
             .replace('trialbalance', 'trial balance')
             .replace('profitandloss', 'profit and loss')
             .replace('profit & loss', 'profit and loss')
             .replace('p & l', 'p&l')
             .replace('cashflow', 'cash flow')
             .replace('daybook', 'day book')
             .replace('centre', 'center'))


def match_rules(text, state, allow_followup=True):
    """(tool_name, raw_args) matched from the words alone, or None."""
    if not text:
        return None

    period, remainder = R.extract_period(text)
    q = _norm(remainder)
    full = _norm(text)
    args = {}
    if period is not None:
        # Already parsed - execute() applies it directly rather than re-reading
        # the phrase, so a date that only the extractor understood survives.
        args["_period"] = period

    limit = R.extract_limit(text)
    if limit:
        args["limit"] = limit
    lo, hi = R.extract_amount_bounds(text)
    if lo is not None:
        args["min_amount"] = lo
    if hi is not None:
        args["max_amount"] = hi

    def hit(tool_name, **extra):
        out = dict(args)
        out.update(extra)
        return tool_name, out

    # --- voucher by number -------------------------------------------------
    vnum = R.extract_voucher_number(text)
    if vnum and (re.search(r'\b(voucher|invoice|bill|entry|show|detail)\b', full)
                 or full.replace(' ', '') == vnum.lower().replace('-', '')
                 or len(full.split()) <= 2):
        return hit("voucher_details", voucher_number=vnum)

    # --- exports of the previous answer ------------------------------------
    if re.match(r'^(?:(?:and|also|now|then|ok)\b\s*)?'
                r'(?:(?:give|send|show|get|download|export|put|make|save)\b)?'
                r'(?:\s*\b(?:me|it|that|this|them|the|previous|last|result|results|data|'
                r'above|one|in|as|to|into|a|an|with)\b)*'
                r'\s*(?:excel|xlsx|xls|spreadsheet|csv|pdf|file|workbook|sheet|'
                r'download|export|chart|graph|plot)\b', q) and state.get("last_token"):
        return hit("__export_last")

    # --- masters: ledgers --------------------------------------------------
    m = re.match(r'^(?:show |get |what (?:is|are) )?(?:the )?'
                 r'(?:master )?details? (?:of|for) (?:item|product) (.+)$', q)
    if m:
        return hit("item_master_details", item=m.group(1))
    m = re.match(r'^(?:show |get |what (?:is|are) )?(?:the )?'
                 r'(?:ledger |account )?(?:master )?details? (?:of|for) (.+)$', q)
    if m:
        return hit("ledger_master_details", ledger=m.group(1))

    m = re.match(r'^(?:show |list |get )?(?:contact|party) details?(?: (?:of|for) (.+))?$', q)
    if m:
        return hit("party_details", ledger=m.group(1))

    if re.match(r'^(?:list |show |get |all )*(?:the )?ledgers?(?: list| master)?$', q):
        return hit("list_ledgers")
    m = re.match(r'^(?:list |show |get )?(?:all )?(?:ledgers?|accounts?) (?:under|in|of) (.+)$', q)
    if m:
        return hit("ledgers_in_group", group=m.group(1))
    m = re.match(r'^(?:search|find|lookup) (?:ledger|account|party)s? '
                 r'(?:with|containing|named|for|by)? ?(.+)$', q)
    if m:
        return hit("search_ledger", text=m.group(1))

    if re.search(r'\b(group list|list groups|account groups|chart of accounts group)\b', q):
        return hit("list_groups")
    if re.search(r'\bchart of accounts\b', q):
        return hit("coa_balances")
    if re.search(r'\bcost cent(?:er|re)s?\b', q) and not re.search(r'\bsales\b', q):
        return hit("list_cost_centers")

    m = re.match(r'^(?:show |what (?:is|are) )?(?:the )?opening (?:balance|balances)'
                 r'(?: (?:of|for) (.+))?$', q)
    if m:
        return hit("ledger_opening_balance", ledger=m.group(1))
    m = re.match(r'^(?:show |what (?:is|are) )?(?:the )?opening stock(?: (?:of|for) (.+))?$', q)
    if m:
        return hit("item_opening_stock", item=m.group(1))

    # --- masters: inventory ------------------------------------------------
    if re.match(r'^(?:list |show |get |all )*(?:the )?items?(?: list| master)?$', q):
        return hit("list_items")
    m = re.match(r'^(?:list |show |get )?(?:all )?items? (?:under|in|of) (.+)$', q)
    if m:
        return hit("list_items", group=m.group(1))
    if re.search(r'\b(stock groups?|item groups?|inventory groups?)\b', q):
        return hit("list_stock_groups")
    if re.search(r'\b(units?(?: of measure)?|uom)\b', q) and len(q.split()) <= 4:
        return hit("list_units")
    m = re.match(r'^(?:show |what (?:is|are) )?(?:the )?(?:selling )?price(?: list)?'
                 r'(?: (?:of|for) (.+))?$', q)
    if m:
        return hit("price_list", item=m.group(1))
    if re.search(r'\bprice list\b', q):
        return hit("price_list")

    if re.search(r'\b(locations?|branch(?:es)?|warehouses?)\b', q) and \
            re.match(r'^(?:list|show|all|get)\b', q):
        return hit("list_locations")
    if re.search(r'\bfinancial years?\b|\bfy list\b', q):
        return hit("list_financial_years")
    if re.search(r'\bfixed assets?\b|\basset register\b|\bdepreciation\b', q):
        return hit("fixed_asset_register")
    if re.search(r'\b(company (?:details|settings|info)|currency)\b', q):
        return hit("company_settings")
    if re.match(r'^(?:list|show)\s+users?$', q):
        return hit("list_users")

    # --- inventory reports -------------------------------------------------
    m = re.match(r'^(?:show |what (?:is|are) )?(?:the )?(?:current )?stock '
                 r'(?:of|for) (.+)$', q)
    if m:
        return hit("item_stock", item=m.group(1))
    if re.search(r'\bstock by location\b|\bstock per location\b|\blocation wise stock\b', q):
        return hit("stock_by_location")
    if re.search(r'\b(batch(?:es)?|expiry|expiring)\b', q):
        return hit("stock_by_batch")
    m = re.match(r'^(?:show )?(?:the )?stock movements?(?: (?:of|for) (.+))?$', q)
    if m:
        return hit("stock_movement", item=m.group(1))
    if re.search(r'\b(closing stock|stock value|inventory valuation|inventory value|'
                 r'stock valuation)\b', q):
        return hit("closing_stock_value")
    if re.search(r'\b(inventory ageing|inventory aging|stock ageing|stock aging)\b', q):
        return hit("inventory_ageing")
    if re.search(r'\bslow[- ]moving\b|\bnot moving\b|\bdead stock\b', q):
        days = None
        m = re.search(r'(\d{2,4})\s*days', q)
        if m:
            days = int(m.group(1))
        return hit("slow_moving_items", days=days)
    if re.search(r'\bnegative stock\b|\boversold\b|\bstock below zero\b', q):
        return hit("negative_stock")
    if re.search(r'\bno sales\b|\bnever sold\b|\bnot sold\b', q):
        return hit("no_sales_items")
    if re.search(r'\bstock (?:by |per )categor|categor(?:y|ies) summary\b', q):
        return hit("stock_category_summary")
    if re.search(r'\b(item profitab|profit (?:by|per) item|margin (?:by|per) item|'
                 r'gross profit (?:by|per) item)', q):
        return hit("item_profitability")

    # --- statements --------------------------------------------------------
    # Report names first: "general ledger" is a report, not an account called
    # "general", and "cash book" is not a party statement.
    if re.search(r'\b(general ledger|gl dump|all entries|all transactions)\b', q):
        return hit("gl_dump")
    if re.search(r'\b(cash book|bank book|cash and bank|cashbook|bankbook)\b', q):
        return hit("cash_bank_book")

    m = re.match(r'^(?:show |get |give me )?(?:the )?(?:customer |supplier |party |account )?'
                 r'(?:statement|ledger)(?: of| for)? (.+)$', q)
    if m and not re.search(r'^(?:all|every)\b', m.group(1)):
        return hit("ledger_statement", ledger=m.group(1))
    m = re.match(r'^(.+?)(?:\'s)? (?:statement|ledger)$', q)
    if m:
        return hit("ledger_statement", ledger=m.group(1))
    m = re.match(r'^(?:show )?(?:matching|matched|settlement matching) (?:of|for) (.+)$', q)
    if m:
        return hit("party_matching", ledger=m.group(1))
    if re.search(r'\bsettlements?\b', q):
        m = re.search(r'\bsettlements? (?:of|for) (.+)$', q)
        return hit("settlements_by_party", ledger=m.group(1) if m else None)

    # --- receivables / payables -------------------------------------------
    if re.search(r'\breceivable|\bdebtors?\b|\bowed to (?:me|us)\b', q) or \
            (re.search(r'\boutstanding\b', q) and re.search(r'\bcustomer', q)):
        return hit("outstanding_receivables")
    if re.search(r'\bpayable|\bcreditors?\b|\bwe owe\b|\bi owe\b', q) or \
            (re.search(r'\boutstanding\b', q) and re.search(r'\bsupplier|vendor', q)):
        return hit("outstanding_payables")
    if re.search(r'\b(pending|overdue|unpaid|due)\b', q) and \
            re.search(r'\b(invoice|bill|payment)s?\b', q):
        if re.search(r'\bsupplier|vendor|purchase\b', q):
            return hit("outstanding_payables")
        return hit("outstanding_receivables")
    if re.search(r'\bage(?:ing|ing report)\b|\bageing\b|\baging\b', q):
        if re.search(r'\bsupplier|vendor|payable', q):
            return hit("outstanding_payables")
        return hit("outstanding_receivables")

    # --- financial reports -------------------------------------------------
    if 'trial balance' in q:
        return hit("trial_balance")
    if 'balance sheet' in q:
        return hit("balance_sheet")
    if re.search(r'\b(profit and loss|p&l|pnl|income statement|trading account)\b', q):
        return hit("profit_and_loss")
    if re.search(r'\b(net profit|net loss|profit|loss|earnings|bottom line)\b', q) and \
            not re.search(r'\bitem|product|margin\b', q):
        return hit("net_profit")
    if 'cash flow' in q:
        return hit("cash_flow")
    if re.search(r'\bvat\b|\btax\b', q):
        if re.search(r'\bdetail', q):
            return hit("vat_detailed")
        return hit("vat_summary")
    if re.search(r'\b(compare|comparison)\b', q) and re.search(r'\b(year|fy|financial)\b', q):
        return hit("fy_comparison")
    if re.search(r'\b(compare|comparison|versus|vs)\b', q):
        return hit("compare_periods")

    # --- balances ----------------------------------------------------------
    if re.search(r'\b(cash balance|cash in hand|how much cash)\b', q):
        return hit("cash_balance")
    if re.search(r'\b(bank balance|in the bank)\b', q):
        return hit("bank_balance")
    if re.match(r'^(?:show |list )?all (?:ledger |account )?balances$', q):
        return hit("all_ledger_balances")
    m = re.match(r'^(?:what is |show |get )?(?:the )?balance (?:of|for) (.+)$', q)
    if m:
        return hit("ledger_balance", ledger=m.group(1))
    m = re.match(r'^(.+?)(?:\'s)? balance$', q)
    if m and m.group(1).strip() not in ('cash', 'bank', 'trial', 'closing', 'opening'):
        return hit("ledger_balance", ledger=m.group(1))

    # --- sales / purchase analysis ----------------------------------------
    by_target = None
    m = re.search(r'\b(?:by|per|wise)\s+(customer|client|supplier|vendor|item|product|'
                  r'month|monthly|location|branch|cost cent(?:er|re))s?\b', q)
    if m:
        raw = m.group(1)
        by_target = {"client": "customer", "vendor": "supplier", "product": "item",
                     "monthly": "month", "branch": "location",
                     "cost center": "cost_center"}.get(raw, raw)
    m = re.search(r'\b(customer|supplier|item|month|location)s?[- ]wise\b', q)
    if m:
        by_target = m.group(1)
    # "monthly sales" and "sales trend" name the split without saying "by".
    if by_target is None and re.search(r'\bmonthly\b|\bmonth on month\b|\btrend\b', q):
        by_target = "month"

    is_sales = bool(re.search(r'\bsales?\b|\brevenue\b|\bturnover\b|\bsold\b', q))
    is_purchase = bool(re.search(r'\bpurchases?\b|\bbought\b|\bprocurement\b', q))

    if by_target and (is_sales or is_purchase):
        family = "purchase" if is_purchase else "sales"
        tool_name = BREAKDOWN_TOOLS.get((family, by_target))
        if tool_name:
            return hit(tool_name)

    m = re.search(r'\btop\s*\d*\s*(customers?|clients?|buyers?)\b', q)
    if m:
        return hit("top_customers")
    m = re.search(r'\btop\s*\d*\s*(suppliers?|vendors?)\b', q)
    if m:
        return hit("top_suppliers")
    m = re.search(r'\btop\s*\d*\s*(items?|products?)\b', q)
    if m:
        return hit("sales_by_item")

    # "sales of Cement", "sales to ABC Trading", "purchases from XYZ"
    m = re.match(r'^(?:total |show |what (?:is|are|were) )?(?:the )?sales? '
                 r'(?:of|for) (.+)$', q)
    if m:
        term = m.group(1).strip()
        return hit("sales_total", item=term, _maybe_ledger=term)
    m = re.match(r'^(?:total |show |what (?:is|are|were) )?(?:the )?sales? '
                 r'(?:to|from|by) (.+)$', q)
    if m:
        return hit("sales_total", ledger=m.group(1).strip())
    m = re.match(r'^(?:total |show |what (?:is|are|were) )?(?:the )?purchases? '
                 r'(?:of|for) (.+)$', q)
    if m:
        term = m.group(1).strip()
        return hit("purchase_total", item=term, _maybe_ledger=term)
    m = re.match(r'^(?:total |show |what (?:is|are|were) )?(?:the )?purchases? '
                 r'(?:from|by) (.+)$', q)
    if m:
        return hit("purchase_total", ledger=m.group(1).strip())

    # --- vouchers ----------------------------------------------------------
    if re.search(r'\bday ?book\b|\bposted (?:on|today|yesterday)\b', q):
        return hit("day_book", date=(period[1] if period else None) or "today")
    if re.search(r'\baudit (?:trail|log)\b|\bwho (?:deleted|edited|created)\b', q):
        return hit("audit_trail")
    if re.search(r'\brecurring\b', q):
        return hit("recurring_vouchers")

    m = re.search(r'\b(sales return|purchase return|credit note|debit note|sales|purchase|'
                  r'payment|receipt|journal|contra|expense|service income)s?\s+'
                  r'(vouchers?|invoices?|bills?|register|list|entries)\b', q)
    if m:
        vtype = R.resolve_voucher_type(m.group(1))
        if 'register' in m.group(2):
            return hit("voucher_register", voucher_type=vtype)
        return hit("list_vouchers", voucher_type=vtype)

    if re.search(r'\b(voucher summary|summary by type|how many vouchers)\b', q):
        return hit("voucher_type_summary")
    m = re.match(r'^(?:show |list )?(?:the )?(?:last|latest|recent)\s*\d*\s*'
                 r'(vouchers?|entries|transactions?)$', q)
    if m:
        return hit("last_vouchers")
    m = re.search(r'\bvouchers?\b.*\b(?:with|containing|for) item (.+)$', q)
    if m:
        return hit("vouchers_with_item", item=m.group(1))
    if re.match(r'^(?:show |list )?(?:all )?vouchers?\b', q):
        return hit("list_vouchers")

    # --- expenses ----------------------------------------------------------
    if re.search(r'\bexpense (?:breakdown|summary|analysis)\b|\bwhat did (?:i|we) spend\b'
                 r'|\ball expenses\b|\bexpenses? by (?:head|account)\b', q):
        return hit("expense_breakdown")
    m = re.match(r'^(?:total |how much )?(.+?) expenses?$', q)
    if m and m.group(1).strip() not in ('', 'all', 'total', 'the'):
        return hit("expense_total", ledger=m.group(1).strip())
    m = re.match(r'^(?:how much )?(?:did (?:i|we) )?(?:spend|paid|pay) (?:on|for) (.+)$', q)
    if m:
        return hit("expense_total", ledger=m.group(1).strip())

    # --- headline totals ---------------------------------------------------
    if re.search(r'\b(summary|overview|kpi|dashboard|how (?:is|are) (?:the )?business)\b', q):
        return hit("kpi_summary")
    if is_sales and re.match(r'^(?:total |show |get |what (?:is|are|were) )?(?:the )?'
                             r'(?:total )?(?:sales?|revenue|turnover)$', q):
        return hit("sales_total")
    if is_purchase and re.match(r'^(?:total |show |get |what (?:is|are|were) )?(?:the )?'
                                r'(?:total )?purchases?$', q):
        return hit("purchase_total")

    return None


# ============================================================
# The AI tool-picker
# ============================================================

PICKER_PROMPT = """You choose which of an accounting system's built-in functions
answers the user's question. You never answer the question yourself and you
never produce any figure - the function computes every number from the
database.

Available functions (name(parameters) [area] - what it returns):
{catalogue}

Parameter meanings:
  period        - a phrase such as "2024", "last month", "August 2025",
                  "01-01-2025 to 30-06-2025", "this quarter", "all time"
  ledger        - the name of an account, customer or supplier, as the user
                  wrote it (do not correct the spelling)
  item          - an item / product name as the user wrote it
  voucher_type  - Sales, Purchase, Receipt, Payment, Journal, Contra, Expense,
                  Sales Return, Purchase Return, Credit Note, Debit Note
  voucher_number, limit, location, cost_center, group, text, days, date

Today is {today}.

Conversation so far (most recent last):
{history}

Rules:
1. Reply with JSON only: {{"tool": "<name>", "arguments": {{...}}}}
2. The tool name must be exactly one from the list. Never invent one.
3. Omit any parameter the user did not mention. Do not guess a period.
4. If the question is a follow-up that reuses the previous period or party,
   add "inherit_period": true and/or "inherit_entity": true instead of
   repeating the value.
5. If no function in the list can answer the question, reply
   {{"tool": null, "reason": "<one short sentence saying what is missing>"}}.
"""


def pick_with_ai(question, state):
    """(tool_name, raw_args) chosen by the model, or (None, reason)."""
    from . import chatbot_service as CS

    api_key = CS.get_openrouter_api_key()
    if not api_key:
        return None, "no_api_key"

    import datetime
    prompt = PICKER_PROMPT.format(
        catalogue=TK.catalogue(),
        today=datetime.date.today().isoformat(),
        history=ctx.history_for_prompt() or "(nothing yet)",
    )
    payload = {
        "model": CS.get_openrouter_model(),
        "messages": [{"role": "system", "content": prompt},
                     {"role": "user", "content": question}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json",
               "HTTP-Referer": "http://localhost:5000"}

    body, err = CS.openrouter_request(payload, headers)
    if err:
        return None, err

    content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    parsed = None
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r'\{.*\}', content, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(0))
            except json.JSONDecodeError:
                parsed = None
    if not isinstance(parsed, dict):
        return None, "unusable_response"

    name = parsed.get("tool")
    if not name:
        return None, parsed.get("reason") or "no_tool"
    name = str(name).strip()
    if name not in TK.TOOLS:
        return None, f"unknown_tool:{name}"

    raw = parsed.get("arguments")
    if not isinstance(raw, dict):
        raw = {}
    return name, raw


# ============================================================
# Execution
# ============================================================

def execute(tool_name, raw_args, question, company_id, source="coded",
            asked_for=None):
    """Run a tool and turn its result into a chat reply, or ask for what's missing."""
    state = ctx.get()

    # A period the rules already parsed beats re-parsing the phrase.
    pre_period = raw_args.pop("_period", None)
    # "sales of X" - X is usually an item, but it may be a customer. Try the
    # item first and fall back rather than telling the user there is no such
    # item when there is a ledger by that name.
    maybe_ledger = raw_args.pop("_maybe_ledger", None)

    tool_obj = TK.TOOLS[tool_name]

    # The assistant answers with the same data the screens show, so it obeys
    # the same permissions. Checked before the arguments are resolved: a
    # refusal must not first tell the user which ledgers exist.
    try:
        P.check(tool_name)
    except P.PermissionDenied as denied:
        return plain(
            f"You don't have access to <b>{denied.label}</b>, so I can't answer "
            f"that one. Ask your administrator if you need it.",
            "permission_denied", {"permission": denied.permission})

    try:
        prepared_raw = dict(raw_args)
        if pre_period is not None:
            prepared_raw["period"] = None
        a = TK.prepare_args(tool_obj, prepared_raw, company_id, state)
        if pre_period is not None:
            a["start"], a["end"], a["period_label"] = pre_period
        elif (raw_args.get("inherit_period") and not a.get("start") and not a.get("end")
              and state.get("last_period")):
            a["start"], a["end"], a["period_label"] = state["last_period"]

        result = tool_obj.fn(a)

    except TK.NeedsArgument as need:
        if asked_for == need.param:
            # We asked for this once and what came back still isn't usable.
            # Asking again would loop, so stop and hand back control.
            return plain(
                f"I still couldn't read that as a {need.param.replace('_', ' ')}. "
                f"{need.question}<br>Or ask something else and I'll drop this one.",
                "ask_argument_failed", {"need": need.param})
        ctx.set_pending("missing", tool=tool_name, args=raw_args, param=need.param,
                        question=question)
        return plain(need.question, "ask_argument",
                     {"need": need.param, "pending_query": question})

    except R.Ambiguous as amb:
        ctx.set_pending("clarify", tool=tool_name, args=raw_args, param=amb.kind,
                        term=amb.term, options=amb.options, question=question)
        return plain(
            f"There is more than one {amb.kind} matching '<b>{amb.term}</b>'. "
            f"Which one did you mean?<br>{choice_buttons(amb.options)}",
            "clarify", {"options": amb.options})

    except R.GenericTerm as generic:
        # "purchases from vendors" means every vendor, not one called "vendors".
        # Swap to the matching breakdown rather than hunting for that name.
        family = _family(tool_name)
        singular = {"customer": "customer", "client": "customer", "buyer": "customer",
                    "debtor": "customer", "supplier": "supplier", "vendor": "supplier",
                    "creditor": "supplier", "item": "item", "product": "item",
                    "stock": "item", "goods": "item", "month": "month",
                    "location": "location", "branch": "location"}
        word = generic.term.lower().strip()
        # Look the word up as written first: rstrip('s') turns "goods" into
        # "good", which is in no map at all.
        target = singular.get(word) or singular.get(word.rstrip('s'))

        # Only a sales/purchase question turns into a per-party breakdown.
        # Anything else gets the "all of them" report for its own subject -
        # "stock of items" is a stock listing, not a sales analysis.
        breakdown = BREAKDOWN_TOOLS.get((family, target)) if (family and target) else None
        subject = _generic_subject(generic.term)
        if breakdown is None:
            candidate = GENERIC_FALLBACKS.get(tool_name)
            # Only keep the tool's own counterpart when it is about the same
            # subject as the word the user used.
            if candidate and subject and _generic_subject_of_tool(candidate) not in (
                    None, subject):
                candidate = None
            breakdown = candidate or GENERIC_BY_SUBJECT.get(subject)
        if breakdown:
            # Dropping the category word is what makes this terminate: the
            # retry has no name to resolve, so it cannot raise GenericTerm
            # again even when the fallback is the same tool run unfiltered.
            retry = dict(raw_args)
            for key in ("ledger", "party", "customer", "supplier", "account",
                        "item", "product"):
                retry.pop(key, None)
            if pre_period is not None:
                retry["_period"] = pre_period
            return execute(breakdown, retry, question, company_id, source)
        return plain(
            f"'{generic.term}' names a category rather than one "
            f"{generic.kind}. Tell me which one, or ask for a breakdown - "
            f"for example <i>\"purchases by supplier\"</i>.", "generic_term")

    except R.NotFound as nf:
        if maybe_ledger and nf.kind == "item":
            retry = dict(raw_args)
            retry.pop("item", None)
            retry["ledger"] = maybe_ledger
            if pre_period is not None:
                retry["_period"] = pre_period
            return execute(tool_name, retry, question, company_id, source)
        suggestion = ""
        if nf.suggestions:
            suggestion = ("<br>Did you mean:<br>" + choice_buttons(nf.suggestions))
        article = "an" if nf.kind[:1] in "aeiou" else "a"
        return plain(f"I could not find {article} {nf.kind} named '<b>{nf.term}</b>'."
                     + suggestion, "not_found", {"suggestions": nf.suggestions})

    except Exception as exc:
        print(f"[chat] {tool_name} failed: {exc}")
        return plain(f"I could not complete that: {exc}", "error")

    # Which chart the user asked for, if any - the export can draw it.
    from .chatbot_service import _requested_chart, _chart_note
    chart = _requested_chart(question)

    reply, token = answer(result, question, tool_name, source=source, chart=chart)
    if chart:
        note = _chart_note(chart)
        if note:
            reply["response"] += note

    ctx.record_turn(question, tool_name, a, result, token)
    return reply


# ============================================================
# Pending answers
# ============================================================

YES_RE = re.compile(r'^(yes|y|yeah|yep|sure|ok|okay|go ahead|please do|do it|'
                    r'proceed|allow|permit|continue)\b', re.I)
NO_RE = re.compile(r'^(no|n|nope|don\'t|dont|do not|cancel|stop|never mind|nevermind)\b',
                   re.I)


def handle_pending(question, company_id, ai_enabled):
    """If the assistant was waiting on the user, use this message to continue."""
    pending = ctx.peek_pending()
    if not pending:
        return None
    kind = pending.get("kind")

    if kind == "permission":
        if YES_RE.match(question.strip()):
            ctx.take_pending()
            return run_ai_fallback(pending.get("question") or question, company_id)
        if NO_RE.match(question.strip()):
            ctx.take_pending()
            return plain(
                "Understood - I'll leave it. Try rephrasing it in terms of a report "
                "I have, or type <b>help</b> to see what I can answer directly.",
                "declined")
        return None  # Not an answer to the question: treat as a new question.

    if kind == "clarify":
        choice = question.strip()
        options = pending.get("options") or []
        picked = next((o for o in options if o.lower() == choice.lower()), None)
        if picked is None:
            picked = next((o for o in options if choice.lower() in o.lower()), None)
        if picked is None:
            return None
        ctx.take_pending()
        args = dict(pending.get("args") or {})
        param = pending.get("param")
        key = {"ledger": "ledger", "item": "item", "location": "location",
               "cost centre": "cost_center", "group": "group"}.get(param, "ledger")
        args[key] = picked
        return execute(pending["tool"], args, pending.get("question") or question,
                       company_id)

    if kind == "missing":
        param = pending.get("param")
        value = question.strip()
        if not _answers_the_question(param, value, ctx.get()):
            # The user asked something else instead of answering. Drop the
            # question rather than swallowing every later message as a period.
            ctx.take_pending()
            return None
        ctx.take_pending()
        args = dict(pending.get("args") or {})
        args[{"period": "period", "date": "date", "ledger": "ledger", "item": "item",
              "voucher_number": "voucher_number", "text": "text",
              "location": "location", "cost_center": "cost_center",
              "group": "group"}.get(param, param)] = value
        return execute(pending["tool"], args,
                       f"{pending.get('question') or ''} {value}".strip(), company_id,
                       asked_for=param)

    return None


def _answers_the_question(param, value, state):
    """Is this message the answer we asked for, or a change of subject?"""
    if not value:
        return False
    if param in ("period", "date"):
        # Only a phrase that actually parses as a date is an answer.
        return (R.parse_period(value) is not None
                or R.extract_period(value)[0] is not None)
    if param == "voucher_number":
        return R.extract_voucher_number(value) is not None
    # A name or search term: a full question that the rules already recognise
    # is a new request, not the name of an account.
    if match_rules(value, state):
        return False
    return len(value.split()) <= 6


# ============================================================
# The AI fallback, behind a permission gate
# ============================================================

def ask_permission(question, reason=None):
    # No point offering the AI fallback to someone who is not allowed it.
    if not P.can_use_ai_sql():
        return plain(
            "I couldn't match that to one of the reports you have access to. "
            "Type <b>help</b> to see the questions I can answer for you.",
            "permission_denied", {"permission": P.AI_SQL_PERMISSION})

    ctx.set_pending("permission", question=question, reason=reason)
    why = ""
    if reason and not str(reason).startswith(("unknown_tool", "no_tool", "unusable")):
        why = f"<br><small>{reason}</small>"
    return plain(
        "I don't have a built-in report for that one." + why +
        "<br><br>Shall I let the AI query the database directly? Its answer is "
        "generated rather than computed from a checked report, so treat it as "
        "indicative."
        "<br><button type='button' class='rv-pick' data-value='yes'>Yes, use AI</button>"
        "<button type='button' class='rv-pick' data-value='no'>No</button>",
        "need_permission", {"awaiting": "permission", "pending_query": question})


def run_ai_fallback(question, company_id):
    """The text-to-SQL path - only ever reached after the user agreed."""
    from . import ai_sql

    # Free-form SQL reads any business table, so it needs the broad reporting
    # permission - otherwise it would answer what the coded tools just refused.
    if not P.can_use_ai_sql():
        return plain(
            "You don't have access to <b>Reports</b>, so I can't query the "
            "database for that. Ask your administrator if you need it.",
            "permission_denied", {"permission": P.AI_SQL_PERMISSION})
    try:
        result = ai_sql.answer_from_database(question, company_id)
    except Exception as exc:
        return plain(f"The AI query failed: {exc}", "error")

    if result.get("error"):
        return plain(f"The AI query failed: {result['error']}", "error")

    data = result.get("data") or {}
    rows, columns = data.get("rows"), data.get("columns")
    if rows and columns:
        envelope = TK.table(f"AI answer - {question[:40]}", columns, rows,
                            result.get("response"))
        reply, token = answer(envelope, question, "ai_database_query", source="ai")
        ctx.record_turn(question, "ai_database_query", {}, envelope, token)
        return reply

    text = result.get("response") or "The AI query returned nothing."
    return plain(text + "<br><small class='rv-src rv-src-ai'>AI-generated answer</small>",
                 "ai_database_query")


# ============================================================
# Entry point
# ============================================================

def route(question, company_id, ai_enabled=True, ai_only=False):
    """Answer one chat message.

    `ai_only` is the user ticking "AI only" in the chat header: skip the coded
    reports entirely and let the model query the database for everything. No
    permission prompt then - ticking the box *is* the permission.
    """
    question = (question or "").strip()
    if not question:
        return plain("Ask me anything about your masters, vouchers or reports.")

    ai_only = bool(ai_only) and ai_enabled

    if ai_only:
        answer = route_ai_only(question, company_id)
        if answer is not None:
            return answer

    # Conversational odds and ends
    if GREETING_RE.match(question):
        return plain(
            "Hello! Ask me about any master, voucher or report - for example "
            "<i>\"sales by customer in 2024\"</i>, <i>\"statement of ABC Trading\"</i> "
            "or <i>\"closing stock value\"</i>. Type <b>help</b> for the full list.",
            "greeting")
    if THANKS_RE.match(question):
        return plain("You're welcome. Anything else?", "thanks")
    if HELP_RE.match(question):
        return plain(TK.help_text(), "help")
    if re.match(r'^(?:reset|start over|new conversation|forget (?:it|that|everything))'
                r'[\s!.]*$', question, re.I):
        ctx.reset()
        return plain("Context cleared. What would you like to know?", "reset")

    # Something we asked the user about last turn?
    resumed = handle_pending(question, company_id, ai_enabled)
    if resumed is not None:
        return resumed
    # Not an answer to what we asked - the user moved on, so drop the question
    # rather than letting a later "yes" resurrect it against a stale query.
    ctx.take_pending()

    state = ctx.get()

    # A question that opens with a pronoun or "same/what about" is about the
    # previous answer, so resolve it as a follow-up before the generic rules
    # get a chance to read "their statement" as an account called "their".
    leading_followup = re.match(
        r'^(?:and|ok|also|now)?\s*(?:what about|how about|same for|same|his|her|hers|'
        r'their|theirs|its|it|that|this|those|them)\b', question, re.I)

    matched = None
    if leading_followup:
        matched = match_followup(_norm(question), question, state)
    if matched is None:
        matched = match_rules(question, state)
    if matched is None:
        matched = match_followup(_norm(question), question, state)

    if matched:
        tool_name, raw_args = matched
        if tool_name == "__export_last":
            return export_previous(question, state)
        return execute(tool_name, raw_args, question, company_id)

    # 3. The model picks a tool
    if ai_enabled:
        tool_name, raw_or_reason = pick_with_ai(question, state)
        if tool_name:
            args = raw_or_reason if isinstance(raw_or_reason, dict) else {}
            return execute(tool_name, args, question, company_id)
        reason = raw_or_reason
        if reason == "no_api_key":
            return ask_permission(question,
                                  "No OpenRouter API key is configured, so I cannot "
                                  "use AI either.")
        return ask_permission(question, None if str(reason).startswith(
            ("unknown_tool", "no_tool", "unusable_response")) else str(reason))

    # 4. AI is switched off entirely
    return plain(
        "I couldn't match that to one of my built-in reports, and AI is switched "
        "off so I can't interpret it either.<br><br>Type <b>help</b> to see the "
        "questions I answer directly, or turn on AI in the chat header and ask again.",
        "no_match")


def route_ai_only(question, company_id):
    """Everything answered by the model, for the "AI only" checkbox.

    Returns None for the handful of messages that are app controls rather than
    questions about the data - a download link and a cleared conversation are
    things the model cannot produce, so those still run locally.
    """
    from . import chatbot_service as CS

    if re.match(r'^(?:reset|start over|new conversation|forget (?:it|that|everything))'
                r'[\s!.]*$', question, re.I):
        return None
    state = ctx.get()
    export = match_rules(question, state)
    if export and export[0] == "__export_last":
        return None

    if not CS.get_openrouter_api_key():
        return plain(
            "\"AI only\" is on, but no OpenRouter API key is configured, so I "
            "cannot reach the model. Add one in <b>AI Settings</b>, or untick "
            "<b>AI only</b> to use the built-in reports.", "error")

    ctx.take_pending()   # nothing local is waiting on an answer in this mode
    return run_ai_fallback(question, company_id)


def export_previous(question, state):
    """"Give me that in excel" against whatever was answered last."""
    from .chatbot_service import _requested_chart, _chart_note
    token = state.get("last_token")
    if not token:
        return plain("I don't have a previous result to export. Ask the question "
                     "first, then say \"give it in excel\".", "export_chat_result")
    chart = _requested_chart(question)
    turns = state.get("turns") or []
    last = turns[-1] if turns else {}
    link = export_links(token, chart=chart, primary=requested_format(question))
    note = _chart_note(chart) if chart else ""
    return plain(
        f"Here is <b>{last.get('title') or 'the previous result'}</b> "
        f"({last.get('rows', 0)} row(s)) as an Excel file.<br>{link}{note}",
        "export_chat_result", {"export_token": token})
