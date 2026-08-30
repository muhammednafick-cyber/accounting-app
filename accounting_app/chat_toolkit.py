"""Every question the chat assistant can answer with code instead of a model.

Each tool is a plain function against this company's own data. It declares the
arguments it needs, and the router (rules first, model only as a chooser)
supplies them. No figure in an answer is ever produced by a language model:
the model may pick the tool, but the tool computes the number.

A tool returns a dict:
    title    - heading for the answer and the Excel sheet
    columns  - column headers, or None for a scalar answer
    rows     - list of row lists, or None
    summary  - the sentence shown in the chat bubble (may contain <b>/<br>)
    totals   - optional {label: value} shown under the table
    note     - optional caveat
"""
import datetime

from database.config import get_connection
from database.company_db import get_current_company_id

from . import chat_resolver as R

# ============================================================
# Registry
# ============================================================

TOOLS = {}


class Tool:
    def __init__(self, name, fn, params, group, desc, examples):
        self.name = name
        self.fn = fn
        self.params = params          # e.g. "period? ledger limit?"
        self.group = group
        self.desc = desc
        self.examples = examples or []

    @property
    def param_names(self):
        return [p.rstrip('?') for p in self.params.split()] if self.params else []

    @property
    def required(self):
        return [p for p in (self.params or "").split() if not p.endswith('?')]

    def __repr__(self):
        return f"<Tool {self.name}>"


def tool(name, params="", group="Other", desc="", examples=None):
    def wrap(fn):
        TOOLS[name] = Tool(name, fn, params, group, desc or (fn.__doc__ or "").strip(),
                           examples)
        return fn
    return wrap


# ============================================================
# Small helpers
# ============================================================

def num(value):
    """A float from whatever the driver handed back - str, Decimal or None."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def fmt(n, dp=2):
    """A number the way an accountant reads it."""
    try:
        return f"{float(n or 0):,.{dp}f}"
    except (TypeError, ValueError):
        return str(n)


def drcr(value):
    return f"{fmt(abs(value))} {'Dr' if (value or 0) >= 0 else 'Cr'}"


def table(title, columns, rows, summary=None, totals=None, note=None):
    # Every chatbot answer - and every Excel, CSV or PDF exported from one -
    # is built here, so dates are turned into DD-MM-YYYY once, at the point
    # they stop being data and start being something a person reads.
    from accounting_app.models import (format_display_date,
                                        format_display_dates_in_text)
    rows = [[format_display_date(v) for v in r] for r in (rows or [])]
    return {"title": title, "columns": list(columns or []), "rows": rows,
            "summary": format_display_dates_in_text(summary),
            "totals": totals or {},
            "note": format_display_dates_in_text(note)}


def scalar(title, summary, columns=None, rows=None, totals=None, note=None):
    # Same envelope as table(), so a one-number answer reads dates the same way
    return table(title, columns or [], rows or [], summary, totals, note)


def empty(title, message, note=None):
    return table(title, [], [], message, None, note)


def dicts_to_table(records, title, columns=None, summary=None, totals=None,
                   labels=None):
    """Turn a list of dicts from database/* into the standard envelope."""
    records = list(records or [])
    if not records:
        return empty(title, f"No data found for {title.lower()}.")
    cols = columns or list(records[0].keys())
    header = [(labels or {}).get(c, c.replace('_', ' ').title()) for c in cols]
    rows = [[rec.get(c) for c in cols] for rec in records]
    return table(title, header, rows, summary, totals)


def sql(query, params):
    """(columns, rows) for a read-only query. Column names come from the cursor."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        cols = [d[0] for d in (cur.description or [])]
        raw = cur.fetchall()
        rows = []
        for r in raw:
            # Index by position, not by name: two COALESCE(...) columns in one
            # SELECT share the name "coalesce", and looking them up by key
            # silently returns the same value for both.
            if hasattr(r, 'values') and not isinstance(r, (list, tuple)):
                rows.append(list(r.values()))
            else:
                rows.append(list(r))
        return cols, rows
    finally:
        try:
            conn.close()
        except Exception:
            pass


def period_text(a):
    label = a.get("period_label")
    if not label or label == "all time":
        return "all time"
    return label


def date_clause(a, alias="v", column="date"):
    """(' AND ...', params) restricting a voucher query to the period."""
    clause, params = "", []
    if a.get("start"):
        clause += f" AND {alias}.{column} >= %s"
        params.append(a["start"])
    if a.get("end"):
        clause += f" AND {alias}.{column} <= %s"
        params.append(a["end"])
    return clause, params


def as_of(a):
    """The cut-off date for a point-in-time report."""
    return a.get("end") or a.get("as_of")


# The party side of a voucher: the debtor/creditor line, not the income,
# inventory, VAT or COGS lines that sit on the same voucher.
CUSTOMER_SIDE = """
    JOIN ledgers l ON l.ledger_name = le.ledger_name AND l.company_id = le.company_id
    JOIN groups g ON g.group_code = l.group_code AND g.company_id = l.company_id
"""
CUSTOMER_FILTER = ("le.type = 'Debit' AND g.nature = 'Assets' "
                   "AND g.group_name NOT IN ('Inventory', 'Fixed Assets')")
SUPPLIER_FILTER = "le.type = 'Credit' AND g.nature = 'Liabilities'"


# ============================================================
# Argument preparation
# ============================================================

PARAM_ALIASES = {
    "ledger": "ledger", "party": "ledger", "customer": "ledger",
    "supplier": "ledger", "account": "ledger",
    "item": "item", "product": "item",
}


class NeedsArgument(Exception):
    def __init__(self, param, question):
        super().__init__(question)
        self.param = param
        self.question = question


ASK_FOR = {
    "ledger": "Which account, customer or supplier?",
    "item": "Which item?",
    "voucher_number": "Which voucher number? (e.g. SAL-00001)",
    "period": "For which period? (e.g. 'this month', 'August 2025', "
              "'01-01-2025 to 30-06-2025', or 'all time')",
    "text": "What should I search for?",
    "location": "Which location?",
    "cost_center": "Which cost centre?",
    "group": "Which group?",
    "date": "For which date?",
}


def prepare_args(tool_obj, raw, company_id, state=None):
    """Resolve the loose arguments a router produced into runnable ones.

    Raises NeedsArgument when something required is missing, or
    chat_resolver.Ambiguous / NotFound when a name cannot be pinned down.
    """
    raw = dict(raw or {})
    state = state or {}
    a = {"company_id": company_id, "_raw": raw}
    wanted = tool_obj.param_names
    required = [p.rstrip('?') for p in tool_obj.required]

    for param in wanted:
        canon = PARAM_ALIASES.get(param, param)

        if canon == "period":
            value = raw.get("period")
            parsed = R.parse_period(value, company_id) if value else None
            if parsed is None and value:
                parsed = R.extract_period(str(value), company_id)[0]
            if parsed is None and raw.get("inherit_period") and state.get("last_period"):
                parsed = state["last_period"]
            if parsed is None:
                if param in required:
                    raise NeedsArgument("period", ASK_FOR["period"])
                parsed = (None, None, "all time")
            a["start"], a["end"], a["period_label"] = parsed

        elif canon == "date":
            value = raw.get("date") or raw.get("period")
            d = R.parse_single_date(value) if value else None
            if d is None and value:
                parsed = R.parse_period(value, company_id)
                if parsed and parsed[1]:
                    d = R.parse_single_date(parsed[1])
            if d is None:
                if param in required:
                    raise NeedsArgument("date", ASK_FOR["date"])
                d = datetime.date.today()
            a["date"] = d.strftime("%Y-%m-%d")

        elif canon == "ledger":
            value = (raw.get("ledger") or raw.get("party") or raw.get("customer")
                     or raw.get("supplier") or raw.get("account"))
            if not value and raw.get("inherit_entity"):
                value = state.get("last_ledger")
            if not value:
                if param in required:
                    raise NeedsArgument("ledger", ASK_FOR["ledger"])
                a["ledger_name"] = None
            else:
                groups = None
                if raw.get("ledger_groups"):
                    groups = raw["ledger_groups"]
                a["ledger_name"] = R.resolve_ledger(value, company_id, groups)

        elif canon == "item":
            value = raw.get("item") or raw.get("product")
            if not value and raw.get("inherit_entity"):
                value = state.get("last_item")
            if not value:
                if param in required:
                    raise NeedsArgument("item", ASK_FOR["item"])
                a["item_name"] = None
            else:
                a["item_name"] = R.resolve_item(value, company_id)

        elif canon == "location":
            value = raw.get("location")
            a["location_name"] = R.resolve_location(value, company_id) if value else None
            if not a["location_name"] and param in required:
                raise NeedsArgument("location", ASK_FOR["location"])

        elif canon == "cost_center":
            value = raw.get("cost_center")
            a["cost_center"] = R.resolve_cost_center(value, company_id) if value else None
            if not a["cost_center"] and param in required:
                raise NeedsArgument("cost_center", ASK_FOR["cost_center"])

        elif canon == "group":
            value = raw.get("group")
            a["group_name"] = R.resolve_group(value, company_id) if value else None
            if not a["group_name"] and param in required:
                raise NeedsArgument("group", ASK_FOR["group"])

        elif canon == "voucher_type":
            a["voucher_type"] = R.resolve_voucher_type(raw.get("voucher_type"))

        elif canon == "voucher_number":
            value = raw.get("voucher_number")
            if not value and param in required:
                raise NeedsArgument("voucher_number", ASK_FOR["voucher_number"])
            a["voucher_number"] = str(value).upper().replace('/', '-') if value else None

        elif canon == "limit":
            try:
                a["limit"] = int(raw.get("limit")) if raw.get("limit") else None
            except (TypeError, ValueError):
                a["limit"] = None

        elif canon == "text":
            value = raw.get("text") or raw.get("search")
            if not value and param in required:
                raise NeedsArgument("text", ASK_FOR["text"])
            a["text"] = str(value).strip() if value else None

        elif canon == "nature":
            a["nature"] = raw.get("nature")

        elif canon == "days":
            try:
                a["days"] = int(raw.get("days")) if raw.get("days") else None
            except (TypeError, ValueError):
                a["days"] = None

        elif canon == "min_amount":
            a["min_amount"] = raw.get("min_amount")
            a["max_amount"] = raw.get("max_amount")

    return a


def run(tool_name, raw_args, company_id=None, state=None):
    """Resolve arguments then execute. Returns (result, resolved_args).

    Raises chat_permissions.PermissionDenied when the signed-in user is not
    allowed the menu this tool belongs to - the assistant must not be a way
    around the permissions the app enforces on its own screens.
    """
    from . import chat_permissions as P

    company_id = company_id or get_current_company_id()
    P.check(tool_name)
    tool_obj = TOOLS[tool_name]
    a = prepare_args(tool_obj, raw_args, company_id, state)
    result = tool_obj.fn(a)
    result.setdefault("tool", tool_name)
    return result, a


# ============================================================
# MASTERS - ledgers and accounts
# ============================================================

@tool("list_ledgers", "group? nature?", "Masters",
      "All ledger accounts, optionally within one group or nature.",
      ["list all ledgers", "show ledgers under Sundry Debtors"])
def _list_ledgers(a):
    where, params = "l.company_id = %s", [a["company_id"]]
    if a.get("group_name"):
        where += " AND g.group_name = %s"
        params.append(a["group_name"])
    if a.get("nature"):
        where += " AND g.nature = %s"
        params.append(str(a["nature"]).title())
    cols, rows = sql(
        "SELECT l.ledger_code, l.ledger_name, COALESCE(g.group_name, '') AS group_name, "
        "COALESCE(g.nature, '') AS nature, COALESCE(l.closing_balance, 0) AS closing_balance, "
        "CASE WHEN COALESCE(l.is_active, 1) = 1 THEN 'Active' ELSE 'Blocked' END AS status "
        "FROM ledgers l LEFT JOIN groups g ON g.group_code = l.group_code "
        "AND g.company_id = l.company_id "
        f"WHERE {where} ORDER BY l.ledger_name", tuple(params))
    if not rows:
        return empty("Ledgers", "No ledgers found.")
    scope = f" in {a['group_name']}" if a.get("group_name") else ""
    return table("Ledger List", ["Code", "Ledger", "Group", "Nature", "Closing Balance", "Status"],
                 rows, f"<b>{len(rows)}</b> ledger(s){scope}.")


@tool("ledger_master_details", "ledger", "Masters",
      "Full master record of one ledger: group, opening, credit terms, contact, TRN.",
      ["details of ABC Trading", "master details for Cash"])
def _ledger_master(a):
    cols, rows = sql(
        "SELECT l.ledger_code, l.ledger_name, COALESCE(g.group_name,'') AS group_name, "
        "COALESCE(g.nature,'') AS nature, COALESCE(l.opening_balance,0), "
        "COALESCE(l.opening_balance_type,''), COALESCE(l.closing_balance,0), "
        "COALESCE(l.credit_days,0), COALESCE(l.address,''), COALESCE(l.contact_person,''), "
        "COALESCE(l.phone,''), COALESCE(l.email,''), COALESCE(l.trn,''), "
        "CASE WHEN COALESCE(l.is_active,1)=1 THEN 'Active' ELSE 'Blocked' END "
        "FROM ledgers l LEFT JOIN groups g ON g.group_code = l.group_code "
        "AND g.company_id = l.company_id "
        "WHERE l.company_id = %s AND l.ledger_name = %s",
        (a["company_id"], a["ledger_name"]))
    if not rows:
        return empty("Ledger", f"No ledger named '{a['ledger_name']}'.")
    r = rows[0]
    fields = [
        ("Ledger Code", r[0]), ("Ledger Name", r[1]), ("Group", r[2]), ("Nature", r[3]),
        ("Opening Balance", f"{fmt(r[4])} {r[5]}"), ("Closing Balance", drcr(r[6])),
        ("Credit Days", r[7]), ("Address", r[8]), ("Contact Person", r[9]),
        ("Phone", r[10]), ("Email", r[11]), ("TRN", r[12]), ("Status", r[13]),
    ]
    shown = [(k, v) for k, v in fields if v not in ("", None)]
    body = "<br>".join(f"<b>{k}:</b> {v}" for k, v in shown)
    return table(f"Ledger - {r[1]}", ["Field", "Value"], [[k, v] for k, v in shown],
                 f"<b>{r[1]}</b><br>{body}")


@tool("ledger_opening_balance", "ledger?", "Masters",
      "Opening balance of one ledger, or of every ledger that has one.",
      ["opening balance of ABC Trading", "show all opening balances"])
def _ledger_opening(a):
    where, params = "company_id = %s AND COALESCE(opening_balance,0) <> 0", [a["company_id"]]
    if a.get("ledger_name"):
        where = "company_id = %s AND ledger_name = %s"
        params = [a["company_id"], a["ledger_name"]]
    cols, rows = sql(
        "SELECT ledger_name, COALESCE(opening_balance,0), COALESCE(opening_balance_type,'Dr') "
        f"FROM ledgers WHERE {where} ORDER BY ledger_name", tuple(params))
    if not rows:
        return empty("Opening Balances", "No opening balances recorded.")
    if a.get("ledger_name"):
        r = rows[0]
        return table("Opening Balance", ["Ledger", "Opening Balance", "Type"], rows,
                     f"Opening balance of <b>{r[0]}</b> is <b>{fmt(r[1])} {r[2]}</b>.")
    total = sum((num(r[1]) if r[2] == 'Dr' else -num(r[1])) for r in rows)
    return table("Opening Balances", ["Ledger", "Opening Balance", "Type"], rows,
                 f"<b>{len(rows)}</b> ledger(s) carry an opening balance. Net: {drcr(total)}.",
                 {"Net Opening": drcr(total)})


@tool("ledger_balance", "ledger period?", "Balances",
      "Balance of one account, as of today or as of any date.",
      ["balance of ABC Trading", "what is Cash balance as of 31-12-2024"])
def _ledger_balance(a):
    from database.reports_db import get_ledger_transactions
    cutoff = as_of(a)
    _, bal = get_ledger_transactions(a["ledger_name"], to_date=cutoff,
                                     company_id=a["company_id"])
    when = f"as of {cutoff}" if cutoff else "currently"
    return scalar(f"Balance - {a['ledger_name']}",
                  f"Balance of <b>{a['ledger_name']}</b> {when} is <b>{drcr(bal)}</b>.",
                  ["Ledger", "Balance", "Dr/Cr"],
                  [[a["ledger_name"], round(abs(bal), 2), "Dr" if bal >= 0 else "Cr"]])


@tool("all_ledger_balances", "period?", "Balances",
      "Closing balance of every ledger with a balance.",
      ["show all account balances", "list closing balances"])
def _all_balances(a):
    from database.reports_db import get_trial_balance_data
    tb, dr, cr = get_trial_balance_data(as_of(a), company_id=a["company_id"])
    if not tb:
        return empty("Balances", "No ledger balances found.")
    rows = [[t['ledger_name'], t['group_name'], t['debit'], t['credit']] for t in tb]
    return table("All Ledger Balances", ["Ledger", "Group", "Debit", "Credit"], rows,
                 f"<b>{len(rows)}</b> ledger(s) with a balance. "
                 f"Debit {fmt(dr)} / Credit {fmt(cr)}.",
                 {"Total Debit": fmt(dr), "Total Credit": fmt(cr)})


@tool("search_ledger", "text", "Masters",
      "Find a ledger by any part of its name, phone, email or TRN.",
      ["find ledger with phone 555", "search account containing trading"])
def _search_ledger(a):
    like = f"%{a['text'].lower()}%"
    cols, rows = sql(
        "SELECT l.ledger_code, l.ledger_name, COALESCE(g.group_name,'') AS grp, "
        "COALESCE(l.phone,''), COALESCE(l.email,''), COALESCE(l.trn,''), "
        "COALESCE(l.closing_balance,0) "
        "FROM ledgers l LEFT JOIN groups g ON g.group_code = l.group_code "
        "AND g.company_id = l.company_id WHERE l.company_id = %s AND ("
        "LOWER(l.ledger_name) LIKE %s OR LOWER(COALESCE(l.phone,'')) LIKE %s OR "
        "LOWER(COALESCE(l.email,'')) LIKE %s OR LOWER(COALESCE(l.trn,'')) LIKE %s OR "
        "LOWER(COALESCE(l.contact_person,'')) LIKE %s) ORDER BY l.ledger_name",
        (a["company_id"], like, like, like, like, like))
    if not rows:
        return empty("Ledger Search", f"No ledger matches '{a['text']}'.")
    return table("Ledger Search", ["Code", "Ledger", "Group", "Phone", "Email", "TRN", "Balance"],
                 rows, f"<b>{len(rows)}</b> ledger(s) match '{a['text']}'.")


@tool("list_groups", "", "Masters", "All account groups with their nature.",
      ["list groups", "show chart of accounts groups"])
def _list_groups(a):
    cols, rows = sql(
        "SELECT g.group_code, g.group_name, g.nature, COALESCE(mg.group_name, '') AS master_group, "
        "(SELECT COUNT(*) FROM ledgers l WHERE l.company_id = g.company_id "
        " AND l.group_code = g.group_code) AS ledgers "
        "FROM groups g LEFT JOIN master_groups mg ON mg.group_code = g.master_group_code "
        "AND mg.company_id = g.company_id "
        "WHERE g.company_id = %s ORDER BY g.nature, g.group_name", (a["company_id"],))
    if not rows:
        cols, rows = sql(
            "SELECT group_code, group_name, nature, COALESCE(master_group_code,''), 0 "
            "FROM groups WHERE company_id = %s ORDER BY nature, group_name",
            (a["company_id"],))
    if not rows:
        return empty("Groups", "No groups defined.")
    return table("Account Groups", ["Code", "Group", "Nature", "Master Group", "Ledgers"],
                 rows, f"<b>{len(rows)}</b> account group(s).")


@tool("ledgers_in_group", "group", "Masters", "Every ledger filed under one group.",
      ["ledgers under Sundry Debtors", "accounts in Bank Accounts group"])
def _ledgers_in_group(a):
    return _list_ledgers(a)


@tool("list_cost_centers", "", "Masters", "All cost centres.",
      ["list cost centres"])
def _list_cost_centers(a):
    cols, rows = sql(
        "SELECT center_code, center_name, CASE WHEN COALESCE(is_active,1)=1 "
        "THEN 'Active' ELSE 'Inactive' END FROM cost_centers WHERE company_id = %s "
        "ORDER BY center_name", (a["company_id"],))
    if not rows:
        return empty("Cost Centres", "No cost centres defined.")
    return table("Cost Centres", ["Code", "Cost Centre", "Status"], rows,
                 f"<b>{len(rows)}</b> cost centre(s).")


@tool("party_details", "ledger?", "Masters",
      "Contact and credit details for customers and suppliers.",
      ["contact details of ABC Trading", "list all customers with phone numbers"])
def _party_details(a):
    where, params = "l.company_id = %s AND l.group_code IN ('G007','G008')", [a["company_id"]]
    if a.get("ledger_name"):
        where = "l.company_id = %s AND l.ledger_name = %s"
        params = [a["company_id"], a["ledger_name"]]
    cols, rows = sql(
        "SELECT l.ledger_name, COALESCE(g.group_name,''), COALESCE(l.contact_person,''), "
        "COALESCE(l.phone,''), COALESCE(l.email,''), COALESCE(l.trn,''), "
        "COALESCE(l.address,''), COALESCE(l.credit_days,0), COALESCE(l.closing_balance,0) "
        "FROM ledgers l LEFT JOIN groups g ON g.group_code = l.group_code "
        f"AND g.company_id = l.company_id WHERE {where} ORDER BY l.ledger_name",
        tuple(params))
    if not rows:
        return empty("Party Details", "No party records found.")
    return table("Party Details",
                 ["Party", "Group", "Contact", "Phone", "Email", "TRN", "Address",
                  "Credit Days", "Balance"],
                 rows, f"<b>{len(rows)}</b> part(y/ies).")


# ============================================================
# MASTERS - inventory
# ============================================================

@tool("list_items", "group?", "Masters", "The item master list.",
      ["list all items", "show items in Raw Material group"])
def _list_items(a):
    where, params = "i.company_id = %s", [a["company_id"]]
    if a.get("group_name"):
        where += " AND ig.group_name = %s"
        params.append(a["group_name"])
    cols, rows = sql(
        "SELECT i.item_code, i.name, COALESCE(ig.group_name,''), COALESCE(i.unit_code,''), "
        "COALESCE(i.unit_price,0), COALESCE(i.stock_quantity,0), COALESCE(i.vat_rate,0), "
        "CASE WHEN COALESCE(i.is_active,1)=1 THEN 'Active' ELSE 'Blocked' END "
        "FROM inventory i LEFT JOIN inventory_groups ig ON ig.group_code = i.stock_group_code "
        f"AND ig.company_id = i.company_id WHERE {where} ORDER BY i.name", tuple(params))
    if not rows:
        return empty("Items", "No items found.")
    return table("Item List",
                 ["Code", "Item", "Group", "Unit", "Rate", "Stock Qty", "VAT %", "Status"],
                 rows, f"<b>{len(rows)}</b> item(s).")


@tool("item_master_details", "item", "Masters", "Full master record of one item.",
      ["details of item Cement", "item master for SKU-001"])
def _item_master(a):
    cols, rows = sql(
        "SELECT i.item_code, i.name, COALESCE(ig.group_name,''), COALESCE(i.unit_code,''), "
        "COALESCE(i.unit_price,0), COALESCE(i.stock_quantity,0), COALESCE(i.vat_rate,0), "
        "COALESCE(i.opening_price,0), COALESCE(i.opening_location_name,''), "
        "CASE WHEN COALESCE(i.is_active,1)=1 THEN 'Active' ELSE 'Blocked' END "
        "FROM inventory i LEFT JOIN inventory_groups ig ON ig.group_code = i.stock_group_code "
        "AND ig.company_id = i.company_id WHERE i.company_id = %s AND i.name = %s",
        (a["company_id"], a["item_name"]))
    if not rows:
        return empty("Item", f"No item named '{a['item_name']}'.")
    r = rows[0]
    fields = [("Item Code", r[0]), ("Item Name", r[1]), ("Group", r[2]), ("Unit", r[3]),
              ("Selling Rate", fmt(r[4])), ("Stock Quantity", fmt(r[5])),
              ("VAT %", fmt(r[6])), ("Opening Rate", fmt(r[7])),
              ("Opening Location", r[8]), ("Status", r[9])]
    shown = [(k, v) for k, v in fields if v not in ("", None)]
    body = "<br>".join(f"<b>{k}:</b> {v}" for k, v in shown)
    return table(f"Item - {r[1]}", ["Field", "Value"], [[k, v] for k, v in shown],
                 f"<b>{r[1]}</b><br>{body}")


@tool("item_opening_stock", "item?", "Masters", "Opening stock quantities and values.",
      ["opening stock of Cement", "show all opening stock"])
def _item_opening(a):
    where, params = "company_id = %s", [a["company_id"]]
    if a.get("item_name"):
        where += " AND item_name = %s"
        params.append(a["item_name"])
    try:
        cols, rows = sql(
            "SELECT item_name, COALESCE(location_name,''), COALESCE(quantity,0), "
            "COALESCE(rate,0), COALESCE(quantity,0)*COALESCE(rate,0) AS value, "
            "COALESCE(opening_date,'') "
            f"FROM item_opening_balances WHERE {where} ORDER BY item_name", tuple(params))
    except Exception as exc:
        return empty("Opening Stock", f"Opening stock is not available ({exc}).")
    if not rows:
        return empty("Opening Stock", "No opening stock recorded.")
    total = sum(num(r[4]) for r in rows)
    return table("Opening Stock",
                 ["Item", "Location", "Quantity", "Rate", "Value", "Opening Date"], rows,
                 f"<b>{len(rows)}</b> opening stock line(s), total value <b>{fmt(total)}</b>.",
                 {"Total Value": fmt(total)})


@tool("item_stock", "item period?", "Inventory",
      "Quantity, weighted average price and value of one item, now or as of a date.",
      ["stock of Cement", "stock of Cement as of 31-12-2024"])
def _item_stock(a):
    from database.reports_db import get_item_closing_stock
    cutoff = as_of(a)
    qty, value = get_item_closing_stock(a["item_name"], as_of_date=cutoff,
                                        company_id=a["company_id"])
    wap = (value / qty) if qty else 0.0
    when = f" as of {cutoff}" if cutoff else ""
    return scalar(f"Stock - {a['item_name']}",
                  f"Stock of <b>{a['item_name']}</b>{when}: quantity <b>{fmt(qty)}</b>, "
                  f"WAP <b>{fmt(wap)}</b>, value <b>{fmt(value)}</b>.",
                  ["Item", "Quantity", "WAP", "Value"],
                  [[a["item_name"], round(qty, 2), round(wap, 2), round(value, 2)]])


@tool("stock_by_location", "item? location?", "Inventory",
      "Closing stock split by storage location.",
      ["stock by location", "stock of Cement at Main Store"])
def _stock_by_location(a):
    where, params = "ie.company_id = %s", [a["company_id"]]
    if a.get("item_name"):
        where += " AND ie.item_name = %s"
        params.append(a["item_name"])
    if a.get("location_name"):
        where += " AND ie.location_name = %s"
        params.append(a["location_name"])
    if a.get("end"):
        where += " AND v.date <= %s"
        params.append(a["end"])
    cols, rows = sql(
        "SELECT ie.item_name, COALESCE(ie.location_name,'(none)') AS location, "
        "SUM(CASE WHEN ie.type = 'In' THEN ie.quantity ELSE -ie.quantity END) AS quantity "
        "FROM item_entries ie JOIN vouchers v ON v.voucher_number = ie.voucher_number "
        f"AND v.company_id = ie.company_id WHERE {where} "
        "GROUP BY ie.item_name, ie.location_name HAVING "
        "ABS(SUM(CASE WHEN ie.type = 'In' THEN ie.quantity ELSE -ie.quantity END)) > 0.0001 "
        "ORDER BY ie.item_name, location", tuple(params))
    if not rows:
        return empty("Stock by Location", "No stock movements found for that scope.")
    return table("Stock by Location", ["Item", "Location", "Quantity"], rows,
                 f"Stock across <b>{len(rows)}</b> item/location combination(s).")


@tool("stock_by_batch", "item?", "Inventory",
      "Closing stock by batch number and expiry date.",
      ["stock by batch", "batches of Cement"])
def _stock_by_batch(a):
    where, params = "ie.company_id = %s AND COALESCE(ie.batch_number,'') <> ''", [a["company_id"]]
    if a.get("item_name"):
        where += " AND ie.item_name = %s"
        params.append(a["item_name"])
    cols, rows = sql(
        "SELECT ie.item_name, ie.batch_number, COALESCE(ie.expiry_date,'') AS expiry, "
        "SUM(CASE WHEN ie.type = 'In' THEN ie.quantity ELSE -ie.quantity END) AS quantity "
        "FROM item_entries ie JOIN vouchers v ON v.voucher_number = ie.voucher_number "
        f"AND v.company_id = ie.company_id WHERE {where} "
        "GROUP BY ie.item_name, ie.batch_number, ie.expiry_date "
        "ORDER BY ie.item_name, expiry", tuple(params))
    if not rows:
        return empty("Batches", "No batch-tracked stock found.")
    return table("Stock by Batch", ["Item", "Batch", "Expiry", "Quantity"], rows,
                 f"<b>{len(rows)}</b> batch line(s).")


@tool("list_units", "", "Masters", "Units of measure.", ["list units"])
def _list_units(a):
    cols, rows = sql("SELECT unit_code, unit_name FROM units WHERE company_id = %s "
                     "ORDER BY unit_code", (a["company_id"],))
    if not rows:
        return empty("Units", "No units defined.")
    return table("Units of Measure", ["Code", "Unit"], rows, f"<b>{len(rows)}</b> unit(s).")


@tool("list_stock_groups", "", "Masters", "Inventory / stock groups.",
      ["list stock groups", "item groups"])
def _list_stock_groups(a):
    cols, rows = sql(
        "SELECT ig.group_code, ig.group_name, "
        "(SELECT COUNT(*) FROM inventory i WHERE i.company_id = ig.company_id "
        " AND i.stock_group_code = ig.group_code) AS items "
        "FROM inventory_groups ig WHERE ig.company_id = %s ORDER BY ig.group_name",
        (a["company_id"],))
    if not rows:
        return empty("Stock Groups", "No stock groups defined.")
    return table("Stock Groups", ["Code", "Group", "Items"], rows,
                 f"<b>{len(rows)}</b> stock group(s).")


@tool("price_list", "item? group?", "Masters", "Selling prices from the item master.",
      ["price list", "selling price of Cement"])
def _price_list(a):
    where, params = "i.company_id = %s", [a["company_id"]]
    if a.get("item_name"):
        where += " AND i.name = %s"
        params.append(a["item_name"])
    if a.get("group_name"):
        where += " AND ig.group_name = %s"
        params.append(a["group_name"])
    cols, rows = sql(
        "SELECT i.item_code, i.name, COALESCE(ig.group_name,''), COALESCE(i.unit_code,''), "
        "COALESCE(i.unit_price,0), COALESCE(i.vat_rate,0) "
        "FROM inventory i LEFT JOIN inventory_groups ig ON ig.group_code = i.stock_group_code "
        f"AND ig.company_id = i.company_id WHERE {where} ORDER BY i.name", tuple(params))
    if not rows:
        return empty("Price List", "No items found.")
    if a.get("item_name"):
        r = rows[0]
        return table("Price List", ["Code", "Item", "Group", "Unit", "Price", "VAT %"], rows,
                     f"Selling price of <b>{r[1]}</b> is <b>{fmt(r[4])}</b> "
                     f"per {r[3] or 'unit'} (VAT {fmt(r[5], 0)}%).")
    return table("Price List", ["Code", "Item", "Group", "Unit", "Price", "VAT %"], rows,
                 f"Price list for <b>{len(rows)}</b> item(s).")


# ============================================================
# MASTERS - other
# ============================================================

@tool("list_locations", "", "Masters", "Storage / branch locations.", ["list locations"])
def _list_locations(a):
    cols, rows = sql(
        "SELECT location_code, location_name, "
        "CASE WHEN COALESCE(is_default,0)=1 THEN 'Yes' ELSE '' END, "
        "CASE WHEN COALESCE(is_active,1)=1 THEN 'Active' ELSE 'Inactive' END "
        "FROM locations WHERE company_id = %s ORDER BY location_name", (a["company_id"],))
    if not rows:
        return empty("Locations", "No locations defined.")
    return table("Locations", ["Code", "Location", "Default", "Status"], rows,
                 f"<b>{len(rows)}</b> location(s).")


@tool("list_financial_years", "", "Masters", "Financial years, and which is open.",
      ["list financial years", "which financial year is active"])
def _list_fy(a):
    cols, rows = sql(
        "SELECT fy_code, start_date, end_date, "
        "CASE WHEN COALESCE(is_active,0)=1 THEN 'Active' ELSE '' END, "
        "CASE WHEN COALESCE(is_locked,0)=1 THEN 'Locked' ELSE 'Open' END "
        "FROM financial_years WHERE company_id = %s ORDER BY start_date", (a["company_id"],))
    if not rows:
        return empty("Financial Years", "No financial years defined.")
    active = [r[0] for r in rows if r[3] == 'Active']
    note = f" Active: <b>{active[0]}</b>." if active else ""
    return table("Financial Years", ["Code", "From", "To", "Active", "Status"], rows,
                 f"<b>{len(rows)}</b> financial year(s).{note}")


@tool("fixed_asset_register", "", "Masters", "Fixed assets with cost and depreciation.",
      ["fixed asset register", "list fixed assets"])
def _fixed_assets(a):
    from database.fixed_assets_db import get_all_assets
    assets = get_all_assets(company_id=a["company_id"]) or []
    if not assets:
        return empty("Fixed Assets", "No fixed assets recorded.")
    records = [dict(x) if not isinstance(x, dict) else x for x in assets]
    keys = list(records[0].keys())
    rows = [[r.get(k) for k in keys] for r in records]
    header = [k.replace('_', ' ').title() for k in keys]
    cost = sum(num(r.get('purchase_cost')) for r in records)
    dep = sum(num(r.get('accumulated_depreciation')) for r in records)
    return table("Fixed Asset Register", header, rows,
                 f"<b>{len(rows)}</b> asset(s). Cost <b>{fmt(cost)}</b>, "
                 f"accumulated depreciation <b>{fmt(dep)}</b>, "
                 f"net book value <b>{fmt(cost - dep)}</b>.",
                 {"Total Cost": fmt(cost), "Accum. Depreciation": fmt(dep),
                  "Net Book Value": fmt(cost - dep)})


@tool("company_settings", "", "Masters", "Company name, currency and financial year start.",
      ["company details", "what currency is set"])
def _company_settings(a):
    cols, rows = sql("SELECT * FROM company_settings WHERE company_id = %s LIMIT 1",
                     (a["company_id"],))
    if not rows:
        return empty("Company", "No company settings found.")
    pairs = [(c.replace('_', ' ').title(), v) for c, v in zip(cols, rows[0])
             if v not in (None, "")]
    body = "<br>".join(f"<b>{k}:</b> {v}" for k, v in pairs)
    return table("Company Settings", ["Setting", "Value"], [[k, v] for k, v in pairs], body)


@tool("list_users", "", "Masters", "Application users and their roles.", ["list users"])
def _list_users(a):
    try:
        cols, rows = sql(
            "SELECT u.username, COALESCE(u.role,''), COALESCE(u.login_id,'') "
            "FROM users u JOIN user_companies uc ON uc.user_id = u.id "
            "WHERE uc.company_id = %s ORDER BY u.username", (a["company_id"],))
    except Exception:
        cols, rows = sql("SELECT username, COALESCE(role,'') FROM users ORDER BY username", ())
    if not rows:
        return empty("Users", "No users found.")
    header = ["Username", "Role", "Login ID"][:len(rows[0])]
    return table("Users", header, rows, f"<b>{len(rows)}</b> user(s).")


# ============================================================
# VOUCHERS
# ============================================================

@tool("voucher_details", "voucher_number", "Vouchers",
      "Everything on one voucher: ledger lines, item lines, VAT and narration.",
      ["show voucher SAL-00001", "details of PUR-00012"])
def _voucher_details(a):
    vn = a["voucher_number"]
    cols, head = sql(
        "SELECT voucher_number, date, voucher_type, COALESCE(amount,0), "
        "COALESCE(narration,''), COALESCE(location_name,''), COALESCE(due_date,''), "
        "COALESCE(created_by,'') FROM vouchers "
        "WHERE company_id = %s AND UPPER(voucher_number) = %s", (a["company_id"], vn))
    if not head:
        return empty("Voucher", f"Voucher '{vn}' was not found.")
    v = head[0]
    _, entries = sql(
        "SELECT ledger_name, COALESCE(amount,0), type, COALESCE(cost_center_code,'') "
        "FROM ledger_entries WHERE company_id = %s AND UPPER(voucher_number) = %s",
        (a["company_id"], vn))
    _, items = sql(
        "SELECT item_name, COALESCE(quantity,0), COALESCE(unit_price,0), "
        "COALESCE(amount,0), COALESCE(location_name,''), COALESCE(batch_number,'') "
        "FROM item_entries WHERE company_id = %s AND UPPER(voucher_number) = %s",
        (a["company_id"], vn))

    lines = [f"<b>{v[0]}</b> &nbsp;|&nbsp; {v[2]} &nbsp;|&nbsp; {v[1]} "
             f"&nbsp;|&nbsp; Amount <b>{fmt(v[3])}</b>"]
    if v[5]:
        lines.append(f"Location: {v[5]}")
    if v[6]:
        lines.append(f"Due date: {v[6]}")
    if v[4]:
        lines.append(f"Narration: {v[4]}")
    if entries:
        lines.append("<b>Ledger entries:</b>")
        lines += [f"&bull; {e[0]} &mdash; {fmt(e[1])} {e[2]}" for e in entries]
    if items:
        lines.append("<b>Items:</b>")
        lines += [f"&bull; {i[0]} &mdash; {fmt(i[1])} &times; {fmt(i[2])} = {fmt(i[3])}"
                  for i in items]

    rows = [["Voucher", v[0]], ["Date", v[1]], ["Type", v[2]], ["Amount", v[3]],
            ["Location", v[5]], ["Due Date", v[6]], ["Narration", v[4]],
            ["Created By", v[7]]]
    rows += [[f"Ledger: {e[0]}", f"{fmt(e[1])} {e[2]}"] for e in entries]
    rows += [[f"Item: {i[0]}", f"{fmt(i[1])} x {fmt(i[2])} = {fmt(i[3])}"] for i in items]
    return table(f"Voucher {v[0]}", ["Field", "Value"], rows, "<br>".join(lines))


@tool("list_vouchers", "voucher_type? period? ledger? location? cost_center? "
                       "min_amount? limit?", "Vouchers",
      "Vouchers filtered by type, period, party, location, cost centre or amount.",
      ["sales vouchers this month", "payments to ABC Trading in 2024",
       "vouchers above 5000 last quarter"])
def _list_vouchers(a):
    where, params = "v.company_id = %s", [a["company_id"]]
    if a.get("voucher_type") and a["voucher_type"] != "All":
        where += " AND v.voucher_type = %s"
        params.append(a["voucher_type"])
    clause, dp = date_clause(a)
    where += clause
    params += dp
    if a.get("location_name"):
        where += " AND v.location_name = %s"
        params.append(a["location_name"])
    if a.get("cost_center"):
        where += (" AND v.cost_center_code IN (SELECT center_code FROM cost_centers "
                  "WHERE company_id = v.company_id AND center_name = %s)")
        params.append(a["cost_center"])
    if a.get("min_amount") is not None:
        where += " AND v.amount >= %s"
        params.append(a["min_amount"])
    if a.get("max_amount") is not None:
        where += " AND v.amount <= %s"
        params.append(a["max_amount"])
    if a.get("ledger_name"):
        where += (" AND EXISTS (SELECT 1 FROM ledger_entries le WHERE "
                  "le.company_id = v.company_id AND le.voucher_number = v.voucher_number "
                  "AND le.ledger_name = %s)")
        params.append(a["ledger_name"])

    limit = a.get("limit") or 500
    cols, rows = sql(
        "SELECT v.voucher_number, v.date, v.voucher_type, COALESCE(v.amount,0), "
        "COALESCE(v.location_name,''), COALESCE(v.narration,'') "
        f"FROM vouchers v WHERE {where} ORDER BY v.date DESC, v.voucher_number DESC "
        "LIMIT %s", tuple(params + [limit]))
    if not rows:
        return empty("Vouchers", f"No vouchers found for {period_text(a)}.")
    total = sum(num(r[3]) for r in rows)
    what = (a.get("voucher_type") + " ") if a.get("voucher_type") else ""
    who = f" for {a['ledger_name']}" if a.get("ledger_name") else ""
    return table("Voucher List",
                 ["Voucher", "Date", "Type", "Amount", "Location", "Narration"], rows,
                 f"<b>{len(rows)}</b> {what}voucher(s){who} for {period_text(a)}, "
                 f"totalling <b>{fmt(total)}</b>.",
                 {"Total": fmt(total), "Count": len(rows)})


@tool("last_vouchers", "limit? voucher_type?", "Vouchers",
      "The most recently dated vouchers.",
      ["last 10 vouchers", "latest 5 sales invoices"])
def _last_vouchers(a):
    a = dict(a)
    a["limit"] = a.get("limit") or 10
    a["start"] = a["end"] = None
    a["period_label"] = "all time"
    return _list_vouchers(a)


@tool("day_book", "date", "Vouchers", "Every voucher posted on one date.",
      ["day book for 15-08-2025", "what was posted yesterday"])
def _day_book(a):
    cols, rows = sql(
        "SELECT v.voucher_number, v.voucher_type, COALESCE(v.amount,0), "
        "COALESCE(v.narration,''), COALESCE(v.location_name,'') "
        "FROM vouchers v WHERE v.company_id = %s AND v.date = %s "
        "ORDER BY v.voucher_type, v.voucher_number", (a["company_id"], a["date"]))
    if not rows:
        return empty("Day Book", f"No vouchers were posted on {a['date']}.")
    total = sum(num(r[2]) for r in rows)
    return table(f"Day Book {a['date']}",
                 ["Voucher", "Type", "Amount", "Narration", "Location"], rows,
                 f"<b>{len(rows)}</b> voucher(s) on {a['date']}, "
                 f"totalling <b>{fmt(total)}</b>.", {"Total": fmt(total)})


@tool("voucher_type_summary", "period?", "Vouchers",
      "Count and value of vouchers by type.",
      ["voucher summary by type", "how many vouchers of each type this year"])
def _voucher_type_summary(a):
    clause, params = date_clause(a)
    cols, rows = sql(
        "SELECT v.voucher_type, COUNT(*) AS count, COALESCE(SUM(v.amount),0) AS total "
        f"FROM vouchers v WHERE v.company_id = %s{clause} "
        "GROUP BY v.voucher_type ORDER BY total DESC",
        tuple([a["company_id"]] + params))
    if not rows:
        return empty("Voucher Summary", f"No vouchers for {period_text(a)}.")
    count = int(sum(num(r[1]) for r in rows))
    total = sum(num(r[2]) for r in rows)
    return table("Vouchers by Type", ["Type", "Count", "Total Amount"], rows,
                 f"<b>{count}</b> voucher(s) across {len(rows)} type(s) for "
                 f"{period_text(a)}, totalling <b>{fmt(total)}</b>.",
                 {"Count": count, "Total": fmt(total)})


@tool("vouchers_with_item", "item period?", "Vouchers",
      "Every voucher that moved a given item.",
      ["which vouchers contain Cement", "invoices with item Cement in 2024"])
def _vouchers_with_item(a):
    clause, dp = date_clause(a)
    cols, rows = sql(
        "SELECT v.date, v.voucher_number, v.voucher_type, COALESCE(ie.quantity,0), "
        "COALESCE(ie.unit_price,0), COALESCE(ie.amount,0), COALESCE(ie.type,'') "
        "FROM item_entries ie JOIN vouchers v ON v.voucher_number = ie.voucher_number "
        "AND v.company_id = ie.company_id "
        f"WHERE ie.company_id = %s AND ie.item_name = %s{clause} "
        "ORDER BY v.date DESC, v.voucher_number DESC LIMIT 500",
        tuple([a["company_id"], a["item_name"]] + dp))
    if not rows:
        return empty("Item Vouchers",
                     f"No vouchers moved '{a['item_name']}' in {period_text(a)}.")
    total = sum(num(r[5]) for r in rows)
    return table(f"Vouchers with {a['item_name']}",
                 ["Date", "Voucher", "Type", "Quantity", "Rate", "Amount", "In/Out"], rows,
                 f"<b>{len(rows)}</b> voucher line(s) for <b>{a['item_name']}</b> in "
                 f"{period_text(a)}, value <b>{fmt(total)}</b>.", {"Total": fmt(total)})


@tool("audit_trail", "period? limit?", "Vouchers",
      "Who created, edited or deleted vouchers, and when.",
      ["audit trail", "who deleted vouchers last month"])
def _audit_trail(a):
    clause, params = "", []
    if a.get("start"):
        clause += " AND timestamp >= %s"
        params.append(a["start"])
    if a.get("end"):
        clause += " AND timestamp <= %s"
        params.append(str(a["end"]) + " 23:59:59")
    try:
        cols, rows = sql(
            "SELECT timestamp, COALESCE(username,''), action, COALESCE(voucher_number,''), "
            "COALESCE(details,'') FROM audit_trail WHERE company_id = %s"
            f"{clause} ORDER BY timestamp DESC LIMIT %s",
            tuple([a["company_id"]] + params + [a.get("limit") or 200]))
    except Exception as exc:
        return empty("Audit Trail", f"The audit trail is not available ({exc}).")
    if not rows:
        return empty("Audit Trail", f"No audit entries for {period_text(a)}.")
    return table("Audit Trail", ["When", "User", "Action", "Voucher", "Details"], rows,
                 f"<b>{len(rows)}</b> audit entr(y/ies) for {period_text(a)}.")


@tool("settlements_by_party", "ledger?", "Vouchers",
      "Settlements / allocations recorded against a party.",
      ["settlements for ABC Trading", "list all settlements"])
def _settlements(a):
    where, params = "company_id = %s", [a["company_id"]]
    if a.get("ledger_name"):
        where += " AND ledger_name = %s"
        params.append(a["ledger_name"])
    try:
        cols, rows = sql(
            "SELECT settlement_number, settlement_date, ledger_name, "
            "COALESCE(total_amount,0) "
            f"FROM settlements WHERE {where} ORDER BY settlement_date DESC",
            tuple(params))
    except Exception as exc:
        return empty("Settlements", f"Settlements are not available ({exc}).")
    if not rows:
        return empty("Settlements", "No settlements recorded.")
    total = sum(num(r[3]) for r in rows)
    return table("Settlements", ["Number", "Date", "Party", "Amount"], rows,
                 f"<b>{len(rows)}</b> settlement(s), totalling <b>{fmt(total)}</b>.",
                 {"Total": fmt(total)})


@tool("recurring_vouchers", "", "Vouchers", "Recurring voucher templates and when they are due.",
      ["recurring vouchers", "what recurring entries are due"])
def _recurring(a):
    try:
        cols, rows = sql("SELECT * FROM recurring_vouchers WHERE company_id = %s "
                         "ORDER BY next_date", (a["company_id"],))
    except Exception as exc:
        return empty("Recurring Vouchers", f"Recurring vouchers are not available ({exc}).")
    if not rows:
        return empty("Recurring Vouchers", "No recurring vouchers defined.")
    header = [c.replace('_', ' ').title() for c in cols]
    return table("Recurring Vouchers", header, rows, f"<b>{len(rows)}</b> recurring voucher(s).")


@tool("voucher_register", "voucher_type period? location?", "Vouchers",
      "The full voucher register for one voucher type, with party and VAT.",
      ["sales register for 2024", "purchase register this month"])
def _voucher_register(a):
    from database.reports_db import get_voucher_register_data
    data = get_voucher_register_data(a.get("voucher_type") or "All", a.get("start"),
                                     a.get("end"), company_id=a["company_id"],
                                     location_name=a.get("location_name")) or []
    if not data:
        return empty("Register",
                     f"No {a.get('voucher_type') or ''} vouchers for {period_text(a)}.")
    rows = [[d.get('voucher_number'), d.get('date'), d.get('party_name'),
             d.get('amount'), d.get('vat_amount'), d.get('narration')] for d in data]
    total = sum(num(d.get('amount')) for d in data)
    vat = sum(num(d.get('vat_amount')) for d in data)
    return table(f"{a.get('voucher_type') or 'Voucher'} Register",
                 ["Voucher", "Date", "Party", "Amount", "VAT", "Narration"], rows,
                 f"<b>{len(rows)}</b> {a.get('voucher_type') or ''} voucher(s) for "
                 f"{period_text(a)}. Total <b>{fmt(total)}</b>, VAT <b>{fmt(vat)}</b>.",
                 {"Total": fmt(total), "VAT": fmt(vat)})


# ============================================================
# STATEMENTS
# ============================================================

@tool("ledger_statement", "ledger period? location?", "Statements",
      "Every transaction on an account with a running balance.",
      ["statement of ABC Trading", "ledger of Cash for last month"])
def _ledger_statement(a):
    from database.reports_db import get_ledger_transactions
    trans, bal = get_ledger_transactions(a["ledger_name"], a.get("start"), a.get("end"),
                                         company_id=a["company_id"],
                                         location_name=a.get("location_name"))
    if not trans:
        return empty(f"Statement - {a['ledger_name']}",
                     f"No transactions for <b>{a['ledger_name']}</b> in {period_text(a)}. "
                     f"Balance: <b>{drcr(bal)}</b>.")
    rows = [[t['date'], t['voucher_number'], t['voucher_type'], t['narration'],
             t['debit'], t['credit'], t['balance']] for t in trans]
    dr = sum(num(t['debit']) for t in trans)
    cr = sum(num(t['credit']) for t in trans)
    return table(f"Statement - {a['ledger_name']}",
                 ["Date", "Voucher", "Type", "Narration", "Debit", "Credit", "Balance"],
                 rows,
                 f"Statement of <b>{a['ledger_name']}</b> for {period_text(a)}: "
                 f"<b>{len(rows)}</b> transaction(s), debits <b>{fmt(dr)}</b>, "
                 f"credits <b>{fmt(cr)}</b>, closing balance <b>{drcr(bal)}</b>.",
                 {"Total Debit": fmt(dr), "Total Credit": fmt(cr),
                  "Closing Balance": drcr(bal)})


@tool("customer_statement", "ledger period?", "Statements",
      "Account statement for a customer.", ["customer statement for ABC Trading"])
def _customer_statement(a):
    return _ledger_statement(a)


@tool("supplier_statement", "ledger period?", "Statements",
      "Account statement for a supplier.", ["supplier statement for XYZ Suppliers"])
def _supplier_statement(a):
    return _ledger_statement(a)


def _bucket_label(key):
    """'91_180' reads as a range, not as two separate numbers."""
    if key == "not_due":
        return "Not Due"
    if key == "3y_plus":
        return "3Y+"
    text = str(key).replace('_', '-')
    if not any(ch.isdigit() for ch in text):
        return text.title()          # "total" -> "Total"
    return text.upper() if text[-1:].lower() == 'y' else text


def _ageing(a, group_code, label):
    from database.reports_db import get_ageing_report_data
    data = get_ageing_report_data(group_code, as_of(a), company_id=a["company_id"]) or []
    data = [d for d in data if abs(d.get('balance') or 0) > 0.001]
    if not data:
        return empty(label, f"Nothing outstanding for {label.lower()}.")
    buckets = list((data[0].get('buckets') or {}).keys())
    header = ["Party", "Balance"] + [_bucket_label(b) for b in buckets]
    rows = [[d['ledger_name'], d['balance']] + [d['buckets'].get(b, 0) for b in buckets]
            for d in data]
    total = sum(num(d['balance']) for d in data)
    # Payables are credit balances, so the signed sum is negative. The user
    # asked how much is outstanding, not which side of the ledger it sits on.
    total = abs(total)
    bucket_totals = {b.replace('_', ' ').title():
                     fmt(sum(num(d['buckets'].get(b)) for d in data)) for b in buckets}
    return table(label, header, rows,
                 f"<b>{len(rows)}</b> part(y/ies) outstanding, totalling <b>{fmt(total)}</b>"
                 + (f" as of {as_of(a)}" if as_of(a) else "") + ".",
                 dict({"Total": fmt(total)}, **bucket_totals))


@tool("outstanding_receivables", "period?", "Statements",
      "What customers owe, aged into buckets.",
      ["receivables", "outstanding from customers", "which invoices are overdue"])
def _receivables(a):
    return _ageing(a, 'G007', "Receivables Ageing")


@tool("outstanding_payables", "period?", "Statements",
      "What is owed to suppliers, aged into buckets.",
      ["payables", "outstanding to suppliers"])
def _payables(a):
    return _ageing(a, 'G008', "Payables Ageing")


@tool("party_matching", "ledger period?", "Statements",
      "A party's entries with the settlement or invoice each was matched against.",
      ["matching for ABC Trading", "which invoices were settled for ABC"])
def _party_matching(a):
    from database.reports_db import get_party_matching_data
    data = get_party_matching_data(a["ledger_name"], a.get("start"), a.get("end"),
                                   company_id=a["company_id"]) or []
    if not data:
        return empty("Party Matching", f"No matched entries for {a['ledger_name']}.")
    keys = list(data[0].keys())
    rows = [[d.get(k) for k in keys] for d in data]
    return table(f"Matching - {a['ledger_name']}",
                 [k.replace('_', ' ').title() for k in keys], rows,
                 f"<b>{len(rows)}</b> entr(y/ies) for <b>{a['ledger_name']}</b> "
                 f"in {period_text(a)}.")


@tool("gl_dump", "period?", "Statements",
      "Every ledger entry in the period, in date order.",
      ["general ledger dump for 2024", "all entries last month"])
def _gl_dump(a):
    from database.reports_db import get_gl_dump_data
    data = get_gl_dump_data(a.get("start"), a.get("end"), company_id=a["company_id"]) or []
    if not data:
        return empty("General Ledger", f"No entries for {period_text(a)}.")
    keys = ["date", "voucher_number", "voucher_type", "ledger_name", "debit", "credit",
            "cost_center", "narration"]
    rows = [[d.get(k) for k in keys] for d in data]
    dr = sum(num(d.get('debit')) for d in data)
    cr = sum(num(d.get('credit')) for d in data)
    return table("General Ledger",
                 ["Date", "Voucher", "Type", "Ledger", "Debit", "Credit", "Cost Centre",
                  "Narration"], rows,
                 f"<b>{len(rows)}</b> entr(y/ies) for {period_text(a)}. "
                 f"Debits <b>{fmt(dr)}</b>, credits <b>{fmt(cr)}</b>.",
                 {"Total Debit": fmt(dr), "Total Credit": fmt(cr)})


@tool("cash_bank_book", "period? ledger?", "Statements",
      "Cash and bank movements with a running balance.",
      ["cash book this month", "bank book for 2024"])
def _cash_bank_book(a):
    from database.reports_db import get_ledger_transactions
    if a.get("ledger_name"):
        names = [a["ledger_name"]]
    else:
        names = R.all_ledger_names(a["company_id"], ['G005', 'G006'])
    if not names:
        return empty("Cash / Bank Book", "No cash or bank ledgers found.")
    rows, total_dr, total_cr, closing = [], 0.0, 0.0, 0.0
    for name in names:
        trans, bal = get_ledger_transactions(name, a.get("start"), a.get("end"),
                                             company_id=a["company_id"])
        closing += bal
        for t in trans:
            rows.append([name, t['date'], t['voucher_number'], t['voucher_type'],
                         t['narration'], t['debit'], t['credit'], t['balance']])
            total_dr += t['debit'] or 0
            total_cr += t['credit'] or 0
    if not rows:
        return empty("Cash / Bank Book",
                     f"No cash or bank movements in {period_text(a)}. "
                     f"Balance: <b>{fmt(closing)}</b>.")
    rows.sort(key=lambda r: (str(r[1]), str(r[2])))
    return table("Cash / Bank Book",
                 ["Ledger", "Date", "Voucher", "Type", "Narration", "Receipt", "Payment",
                  "Balance"], rows,
                 f"<b>{len(rows)}</b> movement(s) for {period_text(a)}. "
                 f"In <b>{fmt(total_dr)}</b>, out <b>{fmt(total_cr)}</b>, "
                 f"closing <b>{fmt(closing)}</b>.",
                 {"Received": fmt(total_dr), "Paid": fmt(total_cr),
                  "Closing": fmt(closing)})


# ============================================================
# FINANCIAL REPORTS
# ============================================================

@tool("trial_balance", "period? location?", "Reports",
      "Trial balance as of a date, optionally for one location.",
      ["trial balance", "trial balance as of 31-12-2024"])
def _trial_balance(a):
    from database.reports_db import get_trial_balance_data
    tb, dr, cr = get_trial_balance_data(as_of(a), company_id=a["company_id"],
                                        location_name=a.get("location_name"))
    if not tb:
        return empty("Trial Balance", "The trial balance is empty.")
    rows = [[t['ledger_name'], t['group_name'], t['debit'], t['credit']] for t in tb]
    when = f" as of {as_of(a)}" if as_of(a) else ""
    diff = round(dr - cr, 2)
    note = None if abs(diff) < 0.01 else f"Debits and credits differ by {fmt(diff)}."
    return table("Trial Balance", ["Ledger", "Group", "Debit", "Credit"], rows,
                 f"Trial balance{when}: <b>{len(rows)}</b> ledger(s), "
                 f"total debit <b>{fmt(dr)}</b>, total credit <b>{fmt(cr)}</b>.",
                 {"Total Debit": fmt(dr), "Total Credit": fmt(cr)}, note)


@tool("balance_sheet", "period? location?", "Reports",
      "Balance sheet as of a date.", ["balance sheet", "balance sheet as of 31-12-2024"])
def _balance_sheet(a):
    from database.reports_db import get_balance_sheet_data
    bs, assets, liabs = get_balance_sheet_data(as_of(a), company_id=a["company_id"],
                                               location_name=a.get("location_name"))
    rows = []
    for side, key in (("Assets", "assets"), ("Liabilities", "liabilities")):
        for group, entries in (bs.get(key) or {}).items():
            for e in entries:
                rows.append([side, group, e['ledger_name'], e['amount']])
    if not rows:
        return empty("Balance Sheet", "The balance sheet is empty.")
    when = f" as of {as_of(a)}" if as_of(a) else ""
    diff = round(assets - liabs, 2)
    note = None if abs(diff) < 0.01 else f"Assets and liabilities differ by {fmt(diff)}."
    return table("Balance Sheet", ["Side", "Group", "Ledger", "Amount"], rows,
                 f"Balance sheet{when}: total assets <b>{fmt(assets)}</b>, "
                 f"total liabilities &amp; equity <b>{fmt(liabs)}</b>.",
                 {"Total Assets": fmt(assets), "Total Liabilities": fmt(liabs)}, note)


@tool("profit_and_loss", "period? location?", "Reports",
      "Profit and loss statement for a period.",
      ["profit and loss", "p&l for 2024", "income statement last month"])
def _pnl(a):
    from database.reports_db import get_profit_and_loss_data
    pnl, inc, exp, net = get_profit_and_loss_data(a.get("start"), a.get("end"),
                                                  company_id=a["company_id"],
                                                  location_name=a.get("location_name"))
    rows = []
    for side, key in (("Income", "income"), ("Expenses", "expenses")):
        for group, entries in (pnl.get(key) or {}).items():
            for e in entries:
                rows.append([side, group, e['ledger_name'], e['amount']])
    if not rows:
        return empty("Profit & Loss", f"No income or expenses for {period_text(a)}.")
    verdict = "profit" if net >= 0 else "loss"
    return table("Profit & Loss", ["Side", "Group", "Ledger", "Amount"], rows,
                 f"For {period_text(a)}: income <b>{fmt(inc)}</b>, "
                 f"expenses <b>{fmt(exp)}</b>, net {verdict} <b>{fmt(abs(net))}</b>.",
                 {"Total Income": fmt(inc), "Total Expenses": fmt(exp),
                  "Net " + verdict.title(): fmt(abs(net))})


@tool("net_profit", "period?", "Reports", "Net profit or loss for a period.",
      ["net profit", "what is my profit this year"])
def _net_profit(a):
    from database.reports_db import get_profit_and_loss_data
    _, inc, exp, net = get_profit_and_loss_data(a.get("start"), a.get("end"),
                                                company_id=a["company_id"])
    verdict = "profit" if net >= 0 else "loss"
    return scalar("Net Profit",
                  f"Net {verdict} for {period_text(a)} is <b>{fmt(abs(net))}</b> "
                  f"(income {fmt(inc)} less expenses {fmt(exp)}).",
                  ["Period", "Income", "Expenses", "Net"],
                  [[period_text(a), round(inc, 2), round(exp, 2), round(net, 2)]])


@tool("cash_flow", "period", "Reports", "Cash flow statement for a period.",
      ["cash flow for 2024", "cash flow this month"])
def _cash_flow(a):
    from database.reports_db import get_cash_flow_data
    data = get_cash_flow_data(a.get("start"), a.get("end"), company_id=a["company_id"]) or {}
    if not data:
        return empty("Cash Flow", f"No cash flow data for {period_text(a)}.")
    rows = []

    def walk(prefix, value):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(f"{prefix} - {k}" if prefix else str(k), v)
        elif isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict):
                    name = entry.get('ledger_name') or entry.get('name') or ''
                    amount = entry.get('amount', entry.get('value'))
                    rows.append([prefix, name, amount])
        else:
            rows.append([prefix, "", value])

    walk("", data)
    rows = [r for r in rows if isinstance(r[2], (int, float))]
    if not rows:
        return empty("Cash Flow", f"No cash flow data for {period_text(a)}.")
    return table("Cash Flow", ["Section", "Line", "Amount"], rows,
                 f"Cash flow for {period_text(a)}: <b>{len(rows)}</b> line(s).")


@tool("coa_balances", "period?", "Reports",
      "Chart of accounts with movement and balance per ledger.",
      ["chart of accounts balances", "movement per account this year"])
def _coa(a):
    from database.reports_db import get_coa_balances
    data = get_coa_balances(a.get("start"), a.get("end"), company_id=a["company_id"]) or {}
    if not data:
        return empty("Chart of Accounts", "No balances found.")
    rows = []
    for name, value in data.items():
        if isinstance(value, dict):
            rows.append([name, value.get('change', value.get('movement')),
                         value.get('balance')])
        else:
            rows.append([name, None, value])
    return table("Chart of Accounts", ["Ledger", "Movement", "Balance"], rows,
                 f"<b>{len(rows)}</b> account(s) for {period_text(a)}.")


@tool("vat_summary", "period?", "Reports", "Output VAT, input VAT and net VAT payable.",
      ["vat summary", "how much vat do I owe this quarter"])
def _vat_summary(a):
    from database.reports_db import get_vat_summary_data
    d = get_vat_summary_data(a.get("start"), a.get("end"), company_id=a["company_id"]) or {}
    out = float(d.get('output_vat') or 0)
    inp = float(d.get('input_vat') or 0)
    net = float(d.get('net_vat') or (out - inp))
    verdict = "payable" if net >= 0 else "refundable"
    return table("VAT Summary", ["Item", "Amount"],
                 [["Output VAT", round(out, 2)], ["Input VAT", round(inp, 2)],
                  ["Net VAT", round(net, 2)]],
                 f"For {period_text(a)}: output VAT <b>{fmt(out)}</b>, "
                 f"input VAT <b>{fmt(inp)}</b>, net VAT {verdict} <b>{fmt(abs(net))}</b>.",
                 {"Net VAT " + verdict.title(): fmt(abs(net))})


@tool("vat_detailed", "period?", "Reports", "VAT report line by line, output and input.",
      ["detailed vat report", "vat details for 2024"])
def _vat_detailed(a):
    from database.reports_db import get_vat_detailed_report_data
    d = get_vat_detailed_report_data(a.get("start"), a.get("end"),
                                     company_id=a["company_id"]) or {}
    rows = []
    for side, key in (("Output", "output_rows"), ("Input", "input_rows")):
        for r in (d.get(key) or []):
            if isinstance(r, dict):
                rows.append([side] + [r.get(k) for k in list(r.keys())])
    if not rows:
        return empty("VAT Detail", f"No VAT entries for {period_text(a)}.")
    sample = (d.get('output_rows') or d.get('input_rows') or [{}])[0]
    header = ["Side"] + [k.replace('_', ' ').title() for k in sample.keys()]
    out = float(d.get('total_output_vat') or 0)
    inp = float(d.get('total_input_vat') or 0)
    return table("VAT Detail", header, rows,
                 f"<b>{len(rows)}</b> VAT line(s) for {period_text(a)}. "
                 f"Output <b>{fmt(out)}</b>, input <b>{fmt(inp)}</b>, "
                 f"net <b>{fmt(out - inp)}</b>.",
                 {"Output VAT": fmt(out), "Input VAT": fmt(inp),
                  "Net VAT": fmt(out - inp)})


@tool("fy_comparison", "", "Reports", "Key figures compared across financial years.",
      ["compare financial years", "year on year comparison"])
def _fy_comparison(a):
    from database.analysis_db import get_financial_comparison
    data = get_financial_comparison(company_id=a["company_id"])
    if not data:
        return empty("Comparison", "No comparison data available.")
    if isinstance(data, dict):
        rows = [[k, v] for k, v in data.items() if isinstance(v, (int, float))]
        return table("Financial Comparison", ["Measure", "Value"], rows,
                     f"<b>{len(rows)}</b> comparison figure(s).")
    records = [d for d in data if isinstance(d, dict)]
    if not records:
        return empty("Comparison", "No comparison data available.")
    keys = list(records[0].keys())
    return table("Financial Comparison", [k.replace('_', ' ').title() for k in keys],
                 [[r.get(k) for k in keys] for r in records],
                 f"<b>{len(records)}</b> period(s) compared.")


# ============================================================
# INVENTORY REPORTS
# ============================================================

@tool("closing_stock_value", "period?", "Inventory",
      "Closing stock quantity and value for every item.",
      ["closing stock value", "inventory valuation as of 31-12-2024"])
def _closing_stock(a):
    from database.reports_db import get_closing_inventory_data
    data, total = get_closing_inventory_data(as_of(a), company_id=a["company_id"])
    data = [d for d in (data or []) if abs(float(d.get('quantity') or 0)) > 0.0001
            or abs(float(d.get('cost_amount') or 0)) > 0.0001]
    if not data:
        return empty("Closing Stock", "No closing stock.")
    rows = [[d['item_code'], d['item_name'], d.get('group_name'), d.get('quantity'),
             d.get('wap'), d.get('cost_amount')] for d in data]
    when = f" as of {as_of(a)}" if as_of(a) else ""
    return table("Closing Stock", ["Code", "Item", "Group", "Quantity", "WAP", "Value"],
                 rows,
                 f"Closing stock{when}: <b>{len(rows)}</b> item(s), total value "
                 f"<b>{fmt(total)}</b>.", {"Total Value": fmt(total)})


@tool("stock_movement", "item period?", "Inventory",
      "Every in and out movement of one item.",
      ["stock movement of Cement", "movement of Cement in 2024"])
def _stock_movement(a):
    from database.reports_db import get_stock_movement_data
    data = get_stock_movement_data(a["item_name"], a.get("start"), a.get("end"),
                                   company_id=a["company_id"]) or []
    if not data:
        return empty("Stock Movement",
                     f"No movements for '{a['item_name']}' in {period_text(a)}.")
    keys = list(data[0].keys())
    rows = [[d.get(k) for k in keys] for d in data]
    return table(f"Stock Movement - {a['item_name']}",
                 [k.replace('_', ' ').title() for k in keys], rows,
                 f"<b>{len(rows)}</b> movement(s) of <b>{a['item_name']}</b> "
                 f"in {period_text(a)}.")


@tool("inventory_ageing", "period? location?", "Inventory",
      "How long the stock on hand has been held.",
      ["inventory ageing", "how old is my stock"])
def _inventory_ageing(a):
    from database.reports_db import get_inventory_ageing_data
    data = get_inventory_ageing_data(as_of(a), company_id=a["company_id"],
                                     location_name=a.get("location_name")) or []
    if not data:
        return empty("Inventory Ageing", "No stock to age.")
    keys = list(data[0].keys())
    rows = []
    header = []
    for d in data:
        row, hdr = [], []
        for k in keys:
            v = d.get(k)
            if isinstance(v, dict):
                for bk, bv in v.items():
                    row.append(bv)
                    hdr.append(bk.replace('_', ' ').title())
            else:
                row.append(v)
                hdr.append(k.replace('_', ' ').title())
        rows.append(row)
        header = hdr
    return table("Inventory Ageing", header, rows,
                 f"<b>{len(rows)}</b> item(s) aged"
                 + (f" as of {as_of(a)}" if as_of(a) else "") + ".")


@tool("slow_moving_items", "days?", "Inventory",
      "Items that have not moved for a while.",
      ["slow moving items", "items not sold in 180 days"])
def _slow_moving(a):
    from database.reports_db import get_slow_moving_items
    days = a.get("days") or 90
    data = get_slow_moving_items(days_threshold=days, company_id=a["company_id"]) or []
    if not data:
        return empty("Slow Moving", f"No items have been idle for {days} days.")
    keys = list(data[0].keys())
    rows = [[d.get(k) for k in keys] for d in data]
    return table("Slow Moving Items", [k.replace('_', ' ').title() for k in keys], rows,
                 f"<b>{len(rows)}</b> item(s) with no movement in {days} days.")


@tool("negative_stock", "", "Inventory", "Items whose recorded stock has gone negative.",
      ["negative stock", "which items are oversold"])
def _negative_stock(a):
    from database.reports_db import get_negative_stock_items
    data = get_negative_stock_items(company_id=a["company_id"]) or []
    if not data:
        return empty("Negative Stock", "No items are showing negative stock.")
    rows = [list(d) if not isinstance(d, dict) else [d.get(k) for k in d] for d in data]
    header = ["Code", "Item", "Quantity"][:len(rows[0])]
    return table("Negative Stock", header, rows,
                 f"<b>{len(rows)}</b> item(s) showing negative stock.")


@tool("no_sales_items", "period?", "Inventory",
      "Items with no sales at all in the period.",
      ["items with no sales", "which items never sold this year"])
def _no_sales(a):
    clause, params = date_clause(a)
    cols, rows = sql(
        "SELECT i.item_code, i.name, COALESCE(i.stock_quantity,0) "
        "FROM inventory i WHERE i.company_id = %s AND NOT EXISTS ("
        "  SELECT 1 FROM item_entries ie JOIN vouchers v "
        "    ON v.voucher_number = ie.voucher_number AND v.company_id = ie.company_id "
        "  WHERE ie.company_id = i.company_id AND ie.item_name = i.name "
        f"    AND v.voucher_type = 'Sales'{clause}) ORDER BY i.name",
        tuple([a["company_id"]] + params))
    if not rows:
        return empty("No-Sales Items", f"Every item sold at least once in {period_text(a)}.")
    return table("Items With No Sales", ["Code", "Item", "Stock Qty"], rows,
                 f"<b>{len(rows)}</b> item(s) had no sales in {period_text(a)}.")


@tool("stock_category_summary", "", "Inventory", "Stock quantity and value by category.",
      ["stock by category", "inventory summary by group"])
def _stock_category(a):
    from database.analysis_db import get_stock_category_summary
    data = get_stock_category_summary(company_id=a["company_id"]) or []
    if not data:
        return empty("Stock Categories", "No stock category data.")
    keys = list(data[0].keys())
    rows = [[d.get(k) for k in keys] for d in data]
    total = sum(num(d.get('value')) for d in data)
    return table("Stock by Category", [k.replace('_', ' ').title() for k in keys], rows,
                 f"<b>{len(rows)}</b> categor(y/ies), total value <b>{fmt(total)}</b>.",
                 {"Total Value": fmt(total)})


@tool("item_profitability", "period? item? limit?", "Inventory",
      "Sales value less cost of goods sold, per item.",
      ["item profitability", "which items are most profitable this year"])
def _item_profit(a):
    clause, params = date_clause(a)
    where = ""
    if a.get("item_name"):
        where = " AND ie.item_name = %s"
        params = params + [a["item_name"]]
    cols, rows = sql(
        "SELECT ie.item_name, SUM(COALESCE(ie.quantity,0)) AS quantity, "
        "SUM(COALESCE(ie.amount,0)) AS sales_value, "
        "SUM(COALESCE(ie.cogs_amount,0)) AS cost, "
        "SUM(COALESCE(ie.amount,0)) - SUM(COALESCE(ie.cogs_amount,0)) AS gross_profit "
        "FROM item_entries ie JOIN vouchers v ON v.voucher_number = ie.voucher_number "
        "AND v.company_id = ie.company_id "
        f"WHERE ie.company_id = %s AND v.voucher_type = 'Sales'{clause}{where} "
        "GROUP BY ie.item_name ORDER BY gross_profit DESC LIMIT %s",
        tuple([a["company_id"]] + params + [a.get("limit") or 200]))
    if not rows:
        return empty("Item Profitability", f"No sales in {period_text(a)}.")
    sales = sum(num(r[2]) for r in rows)
    cost = sum(num(r[3]) for r in rows)
    margin = ((sales - cost) / sales * 100) if sales else 0
    return table("Item Profitability",
                 ["Item", "Quantity", "Sales Value", "Cost", "Gross Profit"], rows,
                 f"For {period_text(a)}: sales <b>{fmt(sales)}</b>, cost <b>{fmt(cost)}</b>, "
                 f"gross profit <b>{fmt(sales - cost)}</b> ({fmt(margin, 1)}%).",
                 {"Sales": fmt(sales), "Cost": fmt(cost),
                  "Gross Profit": fmt(sales - cost), "Margin %": fmt(margin, 1)},
                 "Cost uses the COGS recorded on each sales line.")


# ============================================================
# SALES / PURCHASE ANALYSIS
# ============================================================

@tool("sales_total", "period? item? ledger?", "Sales & Purchase",
      "Total sales for a period, optionally for one item or one customer.",
      ["total sales", "sales in 2024", "sales of Cement last month"])
def _sales_total(a):
    clause, dp = date_clause(a)
    if a.get("item_name"):
        cols, rows = sql(
            "SELECT COALESCE(SUM(ie.amount),0), COALESCE(SUM(ie.quantity),0) "
            "FROM item_entries ie JOIN vouchers v ON v.voucher_number = ie.voucher_number "
            "AND v.company_id = ie.company_id WHERE ie.company_id = %s "
            f"AND v.voucher_type = 'Sales' AND ie.item_name = %s{clause}",
            tuple([a["company_id"], a["item_name"]] + dp))
        total, qty = rows[0][0] or 0, rows[0][1] or 0
        return scalar("Sales",
                      f"Sales of <b>{a['item_name']}</b> for {period_text(a)}: "
                      f"<b>{fmt(total)}</b> across <b>{fmt(qty)}</b> unit(s).",
                      ["Item", "Period", "Quantity", "Sales Value"],
                      [[a["item_name"], period_text(a), round(qty, 2), round(total, 2)]])

    if a.get("ledger_name"):
        cols, rows = sql(
            "SELECT COALESCE(SUM(le.amount),0), COUNT(DISTINCT v.voucher_number) "
            "FROM ledger_entries le JOIN vouchers v ON v.voucher_number = le.voucher_number "
            "AND v.company_id = le.company_id WHERE le.company_id = %s "
            f"AND v.voucher_type = 'Sales' AND le.ledger_name = %s "
            f"AND le.type = 'Debit'{clause}",
            tuple([a["company_id"], a["ledger_name"]] + dp))
        total, count = rows[0][0] or 0, rows[0][1] or 0
        return scalar("Sales",
                      f"Sales to <b>{a['ledger_name']}</b> for {period_text(a)}: "
                      f"<b>{fmt(total)}</b> across <b>{count}</b> invoice(s).",
                      ["Customer", "Period", "Invoices", "Sales Value"],
                      [[a["ledger_name"], period_text(a), count, round(total, 2)]])

    from database.reports_db import get_profit_and_loss_data
    _, income, _, _ = get_profit_and_loss_data(a.get("start"), a.get("end"),
                                               company_id=a["company_id"])
    cols, rows = sql(
        "SELECT COALESCE(SUM(v.amount),0), COUNT(*) FROM vouchers v "
        f"WHERE v.company_id = %s AND v.voucher_type = 'Sales'{clause}",
        tuple([a["company_id"]] + dp))
    gross, count = rows[0][0] or 0, rows[0][1] or 0
    return scalar("Total Sales",
                  f"Sales revenue for {period_text(a)} is <b>{fmt(income)}</b> "
                  f"(net of VAT), from <b>{count}</b> invoice(s) with a gross "
                  f"invoiced value of <b>{fmt(gross)}</b>.",
                  ["Period", "Invoices", "Gross Invoiced", "Revenue (net of VAT)"],
                  [[period_text(a), count, round(gross, 2), round(income, 2)]])


@tool("purchase_total", "period? item? ledger?", "Sales & Purchase",
      "Total purchases for a period, optionally for one item or one supplier.",
      ["total purchases", "purchases in 2024", "purchases of Cement"])
def _purchase_total(a):
    clause, dp = date_clause(a)
    if a.get("item_name"):
        cols, rows = sql(
            "SELECT COALESCE(SUM(ie.amount),0), COALESCE(SUM(ie.quantity),0) "
            "FROM item_entries ie JOIN vouchers v ON v.voucher_number = ie.voucher_number "
            "AND v.company_id = ie.company_id WHERE ie.company_id = %s "
            f"AND v.voucher_type = 'Purchase' AND ie.item_name = %s{clause}",
            tuple([a["company_id"], a["item_name"]] + dp))
        total, qty = rows[0][0] or 0, rows[0][1] or 0
        return scalar("Purchases",
                      f"Purchases of <b>{a['item_name']}</b> for {period_text(a)}: "
                      f"<b>{fmt(total)}</b> across <b>{fmt(qty)}</b> unit(s).",
                      ["Item", "Period", "Quantity", "Purchase Value"],
                      [[a["item_name"], period_text(a), round(qty, 2), round(total, 2)]])

    if a.get("ledger_name"):
        cols, rows = sql(
            "SELECT COALESCE(SUM(le.amount),0), COUNT(DISTINCT v.voucher_number) "
            "FROM ledger_entries le JOIN vouchers v ON v.voucher_number = le.voucher_number "
            "AND v.company_id = le.company_id WHERE le.company_id = %s "
            f"AND v.voucher_type = 'Purchase' AND le.ledger_name = %s "
            f"AND le.type = 'Credit'{clause}",
            tuple([a["company_id"], a["ledger_name"]] + dp))
        total, count = rows[0][0] or 0, rows[0][1] or 0
        return scalar("Purchases",
                      f"Purchases from <b>{a['ledger_name']}</b> for {period_text(a)}: "
                      f"<b>{fmt(total)}</b> across <b>{count}</b> bill(s).",
                      ["Supplier", "Period", "Bills", "Purchase Value"],
                      [[a["ledger_name"], period_text(a), count, round(total, 2)]])

    cols, rows = sql(
        "SELECT COALESCE(SUM(ie.quantity * ie.unit_price),0) FROM item_entries ie "
        "JOIN vouchers v ON v.voucher_number = ie.voucher_number "
        "AND v.company_id = ie.company_id WHERE ie.company_id = %s "
        f"AND v.voucher_type = 'Purchase'{clause}", tuple([a["company_id"]] + dp))
    item_value = rows[0][0] or 0
    cols, rows = sql(
        "SELECT COALESCE(SUM(v.amount),0), COUNT(*) FROM vouchers v "
        f"WHERE v.company_id = %s AND v.voucher_type = 'Purchase'{clause}",
        tuple([a["company_id"]] + dp))
    gross, count = rows[0][0] or 0, rows[0][1] or 0
    return scalar("Total Purchases",
                  f"Purchases for {period_text(a)}: item value <b>{fmt(item_value)}</b> "
                  f"from <b>{count}</b> bill(s), gross invoiced <b>{fmt(gross)}</b>.",
                  ["Period", "Bills", "Item Value", "Gross Invoiced"],
                  [[period_text(a), count, round(item_value, 2), round(gross, 2)]])


def _by_party(a, voucher_type, side_filter, label, noun):
    clause, dp = date_clause(a)
    cols, rows = sql(
        "SELECT le.ledger_name, COALESCE(SUM(le.amount),0) AS total, "
        "COUNT(DISTINCT v.voucher_number) AS vouchers "
        "FROM ledger_entries le "
        "JOIN vouchers v ON v.voucher_number = le.voucher_number "
        "AND v.company_id = le.company_id "
        + CUSTOMER_SIDE +
        f"WHERE le.company_id = %s AND v.voucher_type = '{voucher_type}' "
        f"AND {side_filter}{clause} "
        "GROUP BY le.ledger_name ORDER BY total DESC LIMIT %s",
        tuple([a["company_id"]] + dp + [a.get("limit") or 200]))
    if not rows:
        return empty(label, f"No {noun} recorded for {period_text(a)}.")
    total = sum(num(r[1]) for r in rows)
    return table(label, [noun.title().rstrip('s') if False else "Party", "Value", "Vouchers"],
                 rows,
                 f"<b>{len(rows)}</b> part(y/ies) for {period_text(a)}, "
                 f"totalling <b>{fmt(total)}</b>.", {"Total": fmt(total)})


@tool("sales_by_customer", "period? limit?", "Sales & Purchase",
      "Sales value per customer.", ["sales by customer", "sales per customer in 2024"])
def _sales_by_customer(a):
    return _by_party(a, "Sales", CUSTOMER_FILTER, "Sales by Customer", "sales")


@tool("purchases_by_supplier", "period? limit?", "Sales & Purchase",
      "Purchase value per supplier.", ["purchases by supplier", "purchase per vendor"])
def _purchases_by_supplier(a):
    return _by_party(a, "Purchase", SUPPLIER_FILTER, "Purchases by Supplier", "purchases")


def _by_item(a, voucher_type, label):
    clause, dp = date_clause(a)
    cols, rows = sql(
        "SELECT ie.item_name, COALESCE(SUM(ie.quantity),0) AS quantity, "
        "COALESCE(SUM(ie.amount),0) AS value "
        "FROM item_entries ie JOIN vouchers v ON v.voucher_number = ie.voucher_number "
        "AND v.company_id = ie.company_id WHERE ie.company_id = %s "
        f"AND v.voucher_type = '{voucher_type}'{clause} "
        "GROUP BY ie.item_name ORDER BY value DESC LIMIT %s",
        tuple([a["company_id"]] + dp + [a.get("limit") or 200]))
    if not rows:
        return empty(label, f"No {voucher_type.lower()} item lines for {period_text(a)}.")
    total = sum(num(r[2]) for r in rows)
    return table(label, ["Item", "Quantity", "Value"], rows,
                 f"<b>{len(rows)}</b> item(s) for {period_text(a)}, "
                 f"totalling <b>{fmt(total)}</b>.", {"Total": fmt(total)})


@tool("sales_by_item", "period? limit?", "Sales & Purchase", "Sales value per item.",
      ["sales by item", "which items sold most this year"])
def _sales_by_item(a):
    return _by_item(a, "Sales", "Sales by Item")


@tool("purchases_by_item", "period? limit?", "Sales & Purchase",
      "Purchase value per item.", ["purchases by item"])
def _purchases_by_item(a):
    return _by_item(a, "Purchase", "Purchases by Item")


def _by_month(a, voucher_type, label):
    clause, dp = date_clause(a)
    cols, rows = sql(
        "SELECT LEFT(v.date, 7) AS month, COUNT(*) AS vouchers, "
        "COALESCE(SUM(v.amount),0) AS total FROM vouchers v "
        f"WHERE v.company_id = %s AND v.voucher_type = '{voucher_type}'{clause} "
        "GROUP BY LEFT(v.date, 7) ORDER BY month", tuple([a["company_id"]] + dp))
    if not rows:
        return empty(label, f"No {voucher_type.lower()} vouchers for {period_text(a)}.")
    total = sum(num(r[2]) for r in rows)
    best = max(rows, key=lambda r: r[2] or 0)
    return table(label, ["Month", "Vouchers", "Total"], rows,
                 f"<b>{len(rows)}</b> month(s) for {period_text(a)}, totalling "
                 f"<b>{fmt(total)}</b>. Best month: <b>{best[0]}</b> ({fmt(best[2])}).",
                 {"Total": fmt(total)})


@tool("sales_by_month", "period?", "Sales & Purchase", "Sales per month.",
      ["monthly sales", "sales by month in 2024"])
def _sales_by_month(a):
    return _by_month(a, "Sales", "Sales by Month")


@tool("purchases_by_month", "period?", "Sales & Purchase", "Purchases per month.",
      ["monthly purchases", "purchases by month"])
def _purchases_by_month(a):
    return _by_month(a, "Purchase", "Purchases by Month")


@tool("sales_by_location", "period?", "Sales & Purchase", "Sales split by location.",
      ["sales by location", "which branch sold most"])
def _sales_by_location(a):
    clause, dp = date_clause(a)
    cols, rows = sql(
        "SELECT COALESCE(v.location_name, '(none)') AS location, COUNT(*) AS vouchers, "
        "COALESCE(SUM(v.amount),0) AS total FROM vouchers v "
        f"WHERE v.company_id = %s AND v.voucher_type = 'Sales'{clause} "
        "GROUP BY v.location_name ORDER BY total DESC", tuple([a["company_id"]] + dp))
    if not rows:
        return empty("Sales by Location", f"No sales for {period_text(a)}.")
    total = sum(num(r[2]) for r in rows)
    return table("Sales by Location", ["Location", "Vouchers", "Total"], rows,
                 f"<b>{len(rows)}</b> location(s) for {period_text(a)}, "
                 f"totalling <b>{fmt(total)}</b>.", {"Total": fmt(total)})


@tool("sales_by_cost_center", "period?", "Sales & Purchase",
      "Sales split by cost centre.", ["sales by cost centre"])
def _sales_by_cc(a):
    clause, dp = date_clause(a)
    cols, rows = sql(
        "SELECT COALESCE(cc.center_name, '(none)') AS cost_centre, "
        "COALESCE(SUM(le.amount),0) AS total FROM ledger_entries le "
        "JOIN vouchers v ON v.voucher_number = le.voucher_number "
        "AND v.company_id = le.company_id "
        "LEFT JOIN cost_centers cc ON cc.center_code = le.cost_center_code "
        "AND cc.company_id = le.company_id "
        f"WHERE le.company_id = %s AND v.voucher_type = 'Sales' "
        f"AND le.type = 'Credit'{clause} "
        "GROUP BY cc.center_name ORDER BY total DESC", tuple([a["company_id"]] + dp))
    if not rows:
        return empty("Sales by Cost Centre", f"No sales for {period_text(a)}.")
    total = sum(num(r[1]) for r in rows)
    return table("Sales by Cost Centre", ["Cost Centre", "Total"], rows,
                 f"<b>{len(rows)}</b> cost centre(s) for {period_text(a)}, "
                 f"totalling <b>{fmt(total)}</b>.", {"Total": fmt(total)})


@tool("top_customers", "limit? period?", "Sales & Purchase",
      "Customers ranked by sales value.", ["top 5 customers", "biggest customers in 2024"])
def _top_customers(a):
    a = dict(a)
    a["limit"] = a.get("limit") or 5
    result = _by_party(a, "Sales", CUSTOMER_FILTER,
                       f"Top {a['limit']} Customers", "sales")
    if result["rows"]:
        top = result["rows"][0]
        result["summary"] = (f"Top {len(result['rows'])} customer(s) for {period_text(a)}. "
                             f"Largest: <b>{top[0]}</b> at <b>{fmt(top[1])}</b>.")
    return result


@tool("top_suppliers", "limit? period?", "Sales & Purchase",
      "Suppliers ranked by purchase value.", ["top 5 suppliers"])
def _top_suppliers(a):
    a = dict(a)
    a["limit"] = a.get("limit") or 5
    result = _by_party(a, "Purchase", SUPPLIER_FILTER,
                       f"Top {a['limit']} Suppliers", "purchases")
    if result["rows"]:
        top = result["rows"][0]
        result["summary"] = (f"Top {len(result['rows'])} supplier(s) for {period_text(a)}. "
                             f"Largest: <b>{top[0]}</b> at <b>{fmt(top[1])}</b>.")
    return result


@tool("expense_total", "ledger period?", "Sales & Purchase",
      "Movement on one expense head (or any ledger) for a period.",
      ["fuel expense this year", "how much rent did I pay"])
def _expense_total(a):
    from database.reports_db import get_ledger_transactions
    trans, _ = get_ledger_transactions(a["ledger_name"], a.get("start"), a.get("end"),
                                       company_id=a["company_id"])
    trans = [t for t in trans if t['voucher_type'] != 'Closing']
    amount = sum((t['debit'] or 0) - (t['credit'] or 0) for t in trans)
    rows = [[t['date'], t['voucher_number'], t['voucher_type'], t['narration'],
             t['debit'], t['credit']] for t in trans]
    if not rows:
        return empty("Expense",
                     f"No movement on <b>{a['ledger_name']}</b> in {period_text(a)}.")
    return table(f"{a['ledger_name']}",
                 ["Date", "Voucher", "Type", "Narration", "Debit", "Credit"], rows,
                 f"<b>{a['ledger_name']}</b> for {period_text(a)}: <b>{fmt(abs(amount))}</b> "
                 f"across <b>{len(rows)}</b> entr(y/ies).",
                 {"Net Movement": fmt(amount)})


@tool("expense_breakdown", "period? limit?", "Sales & Purchase",
      "Every expense head with its total for the period.",
      ["expense breakdown", "what did I spend on this year"])
def _expense_breakdown(a):
    clause, dp = date_clause(a)
    cols, rows = sql(
        "SELECT le.ledger_name, "
        "COALESCE(SUM(CASE WHEN le.type='Debit' THEN le.amount ELSE -le.amount END),0) AS amount, "
        "COUNT(*) AS entries FROM ledger_entries le "
        "JOIN vouchers v ON v.voucher_number = le.voucher_number "
        "AND v.company_id = le.company_id "
        "JOIN ledgers l ON l.ledger_name = le.ledger_name AND l.company_id = le.company_id "
        "JOIN groups g ON g.group_code = l.group_code AND g.company_id = l.company_id "
        "WHERE le.company_id = %s AND g.nature = 'Expenses' "
        f"AND v.voucher_type <> 'Closing'{clause} "
        "GROUP BY le.ledger_name HAVING "
        "ABS(COALESCE(SUM(CASE WHEN le.type='Debit' THEN le.amount ELSE -le.amount END),0)) > 0.001 "
        "ORDER BY amount DESC LIMIT %s",
        tuple([a["company_id"]] + dp + [a.get("limit") or 200]))
    if not rows:
        return empty("Expenses", f"No expenses recorded for {period_text(a)}.")
    total = sum(num(r[1]) for r in rows)
    top = rows[0]
    return table("Expense Breakdown", ["Expense Head", "Amount", "Entries"], rows,
                 f"<b>{len(rows)}</b> expense head(s) for {period_text(a)}, totalling "
                 f"<b>{fmt(total)}</b>. Largest: <b>{top[0]}</b> ({fmt(top[1])}).",
                 {"Total Expenses": fmt(total)})


@tool("kpi_summary", "period?", "Sales & Purchase",
      "Headline figures: sales, purchases, profit, receivables, payables, stock.",
      ["give me a summary", "kpi summary", "how is the business doing"])
def _kpi(a):
    from database.reports_db import get_profit_and_loss_data, get_closing_inventory_data
    clause, dp = date_clause(a)
    _, income, expenses, net = get_profit_and_loss_data(a.get("start"), a.get("end"),
                                                        company_id=a["company_id"])
    _, prows = sql(
        "SELECT COALESCE(SUM(v.amount),0) FROM vouchers v WHERE v.company_id = %s "
        f"AND v.voucher_type = 'Purchase'{clause}", tuple([a["company_id"]] + dp))
    purchases = prows[0][0] or 0
    _, rrows = sql(
        "SELECT COALESCE(SUM(CASE WHEN g.group_code = 'G007' "
        "  THEN l.closing_balance ELSE 0 END),0), "
        "COALESCE(SUM(CASE WHEN g.group_code = 'G008' "
        "  THEN -l.closing_balance ELSE 0 END),0) "
        "FROM ledgers l JOIN groups g ON g.group_code = l.group_code "
        "AND g.company_id = l.company_id WHERE l.company_id = %s", (a["company_id"],))
    receivable, payable = (rrows[0][0] or 0), (rrows[0][1] or 0)
    _, stock_total = get_closing_inventory_data(as_of(a), company_id=a["company_id"])
    _, vrows = sql(
        f"SELECT COUNT(*) FROM vouchers v WHERE v.company_id = %s{clause}",
        tuple([a["company_id"]] + dp))
    vouchers = vrows[0][0] or 0

    figures = [
        ("Revenue", income), ("Purchases", purchases), ("Expenses", expenses),
        ("Net Profit", net), ("Receivables", receivable), ("Payables", payable),
        ("Closing Stock", stock_total), ("Vouchers Posted", vouchers),
    ]
    body = "<br>".join(f"&bull; <b>{k}:</b> {fmt(v)}" for k, v in figures)
    return table("Business Summary", ["Measure", "Value"],
                 [[k, round(float(v), 2)] for k, v in figures],
                 f"Summary for {period_text(a)}:<br>{body}")


@tool("cash_balance", "period?", "Balances", "Total cash in hand.",
      ["cash balance", "how much cash do I have"])
def _cash_balance(a):
    return _group_balance(a, ['G005'], "Cash")


@tool("bank_balance", "period?", "Balances", "Total bank balance.",
      ["bank balance", "how much is in the bank"])
def _bank_balance(a):
    return _group_balance(a, ['G006'], "Bank")


def _group_balance(a, group_codes, label):
    from database.reports_db import get_ledger_transactions
    names = R.all_ledger_names(a["company_id"], group_codes)
    if not names:
        return empty(label, f"No {label.lower()} ledgers are defined.")
    cutoff = as_of(a)
    rows, total = [], 0.0
    for name in names:
        _, bal = get_ledger_transactions(name, to_date=cutoff, company_id=a["company_id"])
        rows.append([name, round(bal, 2)])
        total += bal
    when = f" as of {cutoff}" if cutoff else ""
    return table(f"{label} Balance", ["Ledger", "Balance"], rows,
                 f"Total {label.lower()} balance{when} is <b>{fmt(total)}</b> "
                 f"across <b>{len(rows)}</b> ledger(s).", {"Total": fmt(total)})


@tool("compare_periods", "period?", "Sales & Purchase",
      "This month's sales against last month's.",
      ["compare sales", "how does this month compare to last month"])
def _compare(a):
    today = datetime.date.today()
    this_start = today.replace(day=1)
    last_end = this_start - datetime.timedelta(days=1)
    last_start = last_end.replace(day=1)

    def total(start, end):
        _, rows = sql(
            "SELECT COALESCE(SUM(v.amount),0) FROM vouchers v WHERE v.company_id = %s "
            "AND v.voucher_type = 'Sales' AND v.date >= %s AND v.date <= %s",
            (a["company_id"], start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")))
        return rows[0][0] or 0

    now, prev = total(this_start, today), total(last_start, last_end)
    diff = now - prev
    pct = (diff / prev * 100) if prev else 0
    direction = "up" if diff >= 0 else "down"
    return table("Sales Comparison", ["Period", "Sales"],
                 [[this_start.strftime("%B %Y"), round(now, 2)],
                  [last_start.strftime("%B %Y"), round(prev, 2)],
                  ["Difference", round(diff, 2)]],
                 f"Sales this month (<b>{fmt(now)}</b>) are {direction} "
                 f"<b>{fmt(abs(diff))}</b> ({fmt(abs(pct), 1)}%) on last month "
                 f"(<b>{fmt(prev)}</b>).",
                 {"Difference": fmt(diff), "Change %": fmt(pct, 1)})


# ============================================================
# Catalogue for the picker prompt and the help text
# ============================================================

def catalogue():
    """The tool list the model chooses from, grouped, one line each.

    Only tools this user is allowed: a tool the model cannot see is a tool it
    cannot pick, so a restricted user gets "I can't answer that" rather than a
    refusal for a report they were never offered.
    """
    from . import chat_permissions as P

    lines = []
    names = [n for n in TOOLS if P.can_use(n)]
    for name in sorted(names, key=lambda n: (TOOLS[n].group, n)):
        t = TOOLS[name]
        params = t.params or "-"
        lines.append(f"{name}({params}) [{t.group}] - {t.desc}")
    return "\n".join(lines)


def help_text():
    """What the assistant can answer, for the user."""
    from . import chat_permissions as P

    groups = {}
    for t in TOOLS.values():
        if not P.can_use(t.name):
            continue
        groups.setdefault(t.group, []).append(t)
    parts = ["I can answer these from your data directly - no AI needed:<br>"]
    for group in sorted(groups):
        examples = []
        for t in sorted(groups[group], key=lambda x: x.name):
            if t.examples:
                examples.append(t.examples[0])
        shown = examples[:6]
        parts.append(f"<b>{group}</b><br>" +
                     "<br>".join(f"&bull; {e}" for e in shown))
    parts.append("<br>Ask a follow-up like <i>\"and for last month?\"</i>, "
                 "<i>\"break it by customer\"</i> or <i>\"give me that in excel\"</i> "
                 "and I'll keep the context.")
    return "<br><br>".join(parts)
