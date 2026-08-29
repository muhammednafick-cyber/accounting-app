"""Mobile API - what the shareholder Android app talks to.

The app never touches PostgreSQL. It signs in over HTTPS, gets a bearer token,
and reads aggregates through these endpoints, so the database password stays
on the server and every request is still a known user against a known company.

Everything here is read-only by construction: there is no endpoint that writes
a voucher, a master or a setting. A shareholder can look, and that is all.
"""
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request
from werkzeug.security import check_password_hash

from database.config import get_connection

mobile_bp = Blueprint("mobile_bp", __name__)

TOKEN_DAYS = 30          # how long a phone stays signed in
API_VERSION = "1.0"


# The app runs from its own origin - https://localhost inside the Android
# WebView, or a file server while testing - so every call here is cross-origin
# and the browser blocks it without these headers. Safe to open to any origin
# because this API authenticates with a bearer token and never with a cookie:
# there is no ambient credential for another site to ride on, which is the
# thing the same-origin rule exists to stop.
@mobile_bp.after_request
def _allow_cross_origin(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Max-Age"] = "86400"
    # Never a cookie on these routes; say so explicitly.
    response.headers.pop("Set-Cookie", None)
    return response


@mobile_bp.route("/api/mobile/<path:_ignored>", methods=["OPTIONS"])
def _preflight(_ignored):
    """Answer the browser's preflight before it will send the real request."""
    return ("", 204)


# ----------------------------------------------------------------- tokens

def init_token_table():
    """One row per signed-in device. Kept out of the session store because a
    phone has no cookies worth trusting and needs a long-lived credential."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_tokens (
                id SERIAL PRIMARY KEY,
                token TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                company_id INTEGER NOT NULL,
                device TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                last_used TIMESTAMP,
                expires_at TIMESTAMP NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_tokens_token "
                       "ON api_tokens(token)")
        conn.commit()
    finally:
        conn.close()


def _issue_token(user_id, company_id, device):
    token = secrets.token_urlsafe(32)
    expires = datetime.now() + timedelta(days=TOKEN_DAYS)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO api_tokens (token, user_id, company_id, device, expires_at)
            VALUES (%s, %s, %s, %s, %s)
        """, (token, user_id, company_id, (device or "")[:120], expires))
        conn.commit()
    finally:
        conn.close()
    return token, expires


def _identify(request_):
    """(user_id, company_id) for the bearer token, or None.

    Also refreshes last_used, which is the only way to tell a live device from
    an abandoned one when revoking.
    """
    header = request_.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    if not token:
        return None

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, company_id, expires_at FROM api_tokens
            WHERE token = %s
        """, (token,))
        row = cursor.fetchone()
        if not row:
            return None
        user_id, company_id, expires_at = row[0], row[1], row[2]
        if expires_at and expires_at < datetime.now():
            return None
        cursor.execute("UPDATE api_tokens SET last_used = NOW() WHERE token = %s",
                       (token,))
        conn.commit()
        return user_id, company_id
    finally:
        conn.close()


def _auth_required(handler):
    """Every endpoint below needs a token; none of them accept a session."""
    from functools import wraps

    @wraps(handler)
    def wrapper(*args, **kwargs):
        identity = _identify(request)
        if not identity:
            return jsonify({"success": False,
                            "message": "Sign in again."}), 401
        return handler(identity[0], identity[1], *args, **kwargs)
    return wrapper


# ------------------------------------------------------------------ auth

@mobile_bp.route("/api/mobile/login", methods=["POST"])
def login():
    """Username/email and password in, bearer token out."""
    data = request.get_json(silent=True) or {}
    login_id = (data.get("login_id") or "").strip()
    password = data.get("password") or ""
    if not login_id or not password:
        return jsonify({"success": False,
                        "message": "Enter your username and password."}), 400

    from database.master_db import get_user_by_login_id, get_user_companies
    user = get_user_by_login_id(login_id)
    if not user or not check_password_hash(user["password_hash"], password):
        # Deliberately one message for both cases - which half was wrong is
        # not something an unauthenticated caller should learn.
        return jsonify({"success": False,
                        "message": "Wrong username or password."}), 401

    companies = get_user_companies(user["id"]) or []
    company_id = data.get("company_id")
    if company_id:
        company_id = int(company_id)
        if companies and company_id not in [c["id"] for c in companies]:
            return jsonify({"success": False,
                            "message": "You do not have access to that "
                                       "company."}), 403
    elif companies:
        company_id = companies[0]["id"]
    else:
        return jsonify({"success": False,
                        "message": "No company is assigned to this user."}), 403

    token, expires = _issue_token(user["id"], company_id,
                                  data.get("device"))
    return jsonify({
        "success": True,
        "token": token,
        "expires_at": expires.strftime("%Y-%m-%d %H:%M"),
        "user": {"id": user["id"], "username": user["username"]},
        "company_id": company_id,
        "companies": [{"id": c["id"], "name": c["name"]} for c in companies],
        "api_version": API_VERSION,
    })


@mobile_bp.route("/api/mobile/logout", methods=["POST"])
def logout():
    """Drop this device's token. Signing out on a lost phone matters."""
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM api_tokens WHERE token = %s",
                           (header[7:].strip(),))
            conn.commit()
        finally:
            conn.close()
    return jsonify({"success": True})


# ------------------------------------------------------------- the data

def _company_name(company_id):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT company_name FROM company_settings "
                       "WHERE company_id = %s", (company_id,))
        row = cursor.fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


def _financial_year(company_id):
    """(start, end) of the open financial year, falling back to this year."""
    from database.financial_year_db import get_fy_by_date
    today = datetime.now().strftime("%Y-%m-%d")
    fy = get_fy_by_date(today, company_id=company_id)
    if fy:
        return str(fy["start_date"])[:10], str(fy["end_date"])[:10]
    year = datetime.now().year
    return f"{year}-01-01", f"{year}-12-31"


def _margin(net_profit, income):
    """Net margin as a percentage, or None when the number would mislead."""
    income = float(income or 0)
    if income <= 0:
        return None
    margin = round((float(net_profit or 0) / income) * 100, 1)
    return margin if -999 <= margin <= 999 else None


MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _trend_series(trend):
    """[{month, total}] in calendar order, whatever shape came back."""
    if isinstance(trend, dict):
        out = []
        for key in sorted(trend.keys()):
            try:
                label = MONTH_NAMES[int(key) - 1]
            except (ValueError, TypeError, IndexError):
                label = str(key)
            out.append({"month": label, "total": round(float(trend[key] or 0), 2)})
        return out
    return [{"month": str(m.get("month") or m.get("label") or ""),
             "total": round(float(m.get("total") or m.get("amount") or 0), 2)}
            for m in (trend or [])]


@mobile_bp.route("/api/mobile/dashboard")
@_auth_required
def dashboard(user_id, company_id):
    """The headline numbers a shareholder opens the app for."""
    from database.reports_db import get_profit_and_loss_data
    from database.analysis_db import (get_kpi_summary, get_monthly_sales_trend,
                                      get_top_customers)

    start, end = _financial_year(company_id)
    kpi = get_kpi_summary(company_id=company_id) or {}
    _, income, expenses, net_profit = get_profit_and_loss_data(
        from_date=start, to_date=end, company_id=company_id)

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COALESCE(SUM(closing_balance), 0) FROM ledgers
            WHERE company_id = %s AND group_code IN ('G005', 'G006')
        """, (company_id,))
        cash = float(cursor.fetchone()[0] or 0)
        cursor.execute("SELECT COALESCE(SUM(stock_value), 0) FROM inventory "
                       "WHERE company_id = %s", (company_id,))
        stock = float(cursor.fetchone()[0] or 0)
    finally:
        conn.close()

    receivable = float(kpi.get("total_receivables") or 0)
    payable = abs(float(kpi.get("total_payables") or 0))

    return jsonify({"success": True, "data": {
        "company": _company_name(company_id),
        "period": {"from": start, "to": end},
        "headline": {
            "income": round(float(income or 0), 2),
            "expenses": round(float(expenses or 0), 2),
            "net_profit": round(float(net_profit or 0), 2),
            # Only meaningful once there is revenue to divide by, and only
            # worth showing while it stays in a believable range - a margin of
            # -1,930,478% on 2.59 of income tells a shareholder nothing.
            "margin_percent": _margin(net_profit, income),
        },
        "position": {
            "cash_and_bank": round(cash, 2),
            "receivable": round(receivable, 2),
            "payable": round(payable, 2),
            "stock_value": round(stock, 2),
            "working_capital": round(cash + receivable + stock - payable, 2),
        },
        # A list of {month, total} rather than the {"01": 0.0, ...} map the
        # analysis layer returns - the app should not have to know that shape,
        # or sort a dictionary to draw a chart in month order.
        "sales_trend": _trend_series(
            get_monthly_sales_trend(company_id=company_id)),
        "top_customers": [
            {"name": c.get("ledger_name") or c.get("name"),
             "total": round(float(c.get("total") or c.get("amount") or 0), 2)}
            for c in (get_top_customers(limit=5, company_id=company_id) or [])
        ],
    }})


@mobile_bp.route("/api/mobile/shareholder")
@_auth_required
def shareholder(user_id, company_id):
    """The fuller picture: how the money was made, where it now sits, and
    what the owners' side of the balance sheet looks like."""
    from database.reports_db import get_profit_and_loss_data, get_balance_sheet_data
    from database.analysis_db import get_financial_comparison

    start, end = _financial_year(company_id)
    statement, income, expenses, net_profit = get_profit_and_loss_data(
        from_date=start, to_date=end, company_id=company_id)

    def flatten(section):
        out = []
        for group, ledgers in (section or {}).items():
            for entry in ledgers or []:
                out.append({"group": group,
                            "name": entry.get("ledger_name"),
                            "amount": round(float(entry.get("amount") or 0), 2)})
        return sorted(out, key=lambda r: -abs(r["amount"]))[:12]

    conn = get_connection()
    try:
        cursor = conn.cursor()
        # The owners' stake: capital introduced, reserves, and the profit the
        # business has made this year but not yet distributed.
        cursor.execute("""
            SELECT g.group_name, l.ledger_name, COALESCE(l.closing_balance, 0)
            FROM ledgers l
            LEFT JOIN groups g ON g.group_code = l.group_code
                              AND g.company_id = l.company_id
            WHERE l.company_id = %s AND l.group_code IN ('G009', 'G016')
            ORDER BY g.group_name, l.ledger_name
        """, (company_id,))
        # Ledger balances are stored debit-positive, so a credit balance - which
        # is what capital and reserves are - comes back negative. Shareholders
        # read their own stake as a positive number, so the sign is flipped
        # here rather than leaving the app to guess.
        equity = [{"group": r[0] or "", "name": r[1],
                   "amount": round(-float(r[2] or 0), 2)}
                  for r in cursor.fetchall()]
    finally:
        conn.close()

    equity_total = round(sum(e["amount"] for e in equity) + float(net_profit or 0), 2)

    return jsonify({"success": True, "data": {
        "company": _company_name(company_id),
        "period": {"from": start, "to": end},
        "profit_and_loss": {
            "income": round(float(income or 0), 2),
            "expenses": round(float(expenses or 0), 2),
            "net_profit": round(float(net_profit or 0), 2),
            "income_lines": flatten((statement or {}).get("income")),
            "expense_lines": flatten((statement or {}).get("expenses")),
        },
        "equity": {
            "lines": equity,
            "retained_this_year": round(float(net_profit or 0), 2),
            "total": equity_total,
        },
        "comparison": get_financial_comparison(company_id=company_id) or {},
        "note": ("Figures are live from the accounting system for the current "
                 "financial year. They are management figures, not audited "
                 "accounts."),
    }})


def _ageing_summary(group_code, company_id, as_of=None):
    """How overdue the money is - the question behind "are we being paid?".

    The ageing report buckets `abs(balance)`, so direction has to come from the
    signed balance it also returns. A customer who has paid in advance carries
    a credit balance: that is money we owe back, not money owed to us, and
    counting it as receivable overstates what is coming in. Those parties are
    reported separately as advances and kept out of the buckets entirely.
    """
    from database.reports_db import get_ageing_report_data
    try:
        rows = get_ageing_report_data(group_code, as_of_date=as_of,
                                      company_id=company_id) or []
    except Exception as exc:
        print(f"[mobile] ageing failed: {exc}")
        return None

    # Debtors sit on the debit side, creditors on the credit side. A balance on
    # the other side is an advance, not an overdue amount.
    expect_debit = group_code == "G007"

    buckets = {"not_due": 0.0, "0_90": 0.0, "91_180": 0.0, "181_270": 0.0,
               "271_365": 0.0, "over_1y": 0.0}
    parties, advances = [], []
    for row in rows:
        balance = float(row.get("balance") or 0)
        if abs(balance) < 0.005:
            continue
        is_debit = balance > 0
        total = abs(float((row.get("buckets") or {}).get("total") or 0))

        if is_debit != expect_debit:
            advances.append({"name": row.get("ledger_name"),
                             "amount": round(total, 2)})
            continue

        b = row.get("buckets") or {}
        buckets["not_due"] += float(b.get("not_due") or 0)
        buckets["0_90"] += float(b.get("0_90") or 0)
        buckets["91_180"] += float(b.get("91_180") or 0)
        buckets["181_270"] += float(b.get("181_270") or 0)
        buckets["271_365"] += float(b.get("271_365") or 0)
        buckets["over_1y"] += (float(b.get("1_2y") or 0)
                               + float(b.get("2_3y") or 0)
                               + float(b.get("3y_plus") or 0))
        parties.append({"name": row.get("ledger_name"), "amount": round(total, 2)})

    parties.sort(key=lambda p: -p["amount"])
    advances.sort(key=lambda p: -p["amount"])
    overdue = round(sum(v for k, v in buckets.items() if k != "not_due"), 2)
    return {
        "total": round(sum(buckets.values()), 2),
        "not_due": round(buckets["not_due"], 2),
        "overdue": overdue,
        "buckets": {k: round(v, 2) for k, v in buckets.items()},
        "top": parties[:5],
        # Credit balances on a customer (or debit balances on a supplier):
        # money sitting the other way round.
        "advances": advances[:5],
        "advances_total": round(sum(a["amount"] for a in advances), 2),
    }


def _requested_period(company_id):
    """(from, to, label) for this request - ?from=&to=, else the open FY.

    Every figure a shareholder is shown is only meaningful against a period, so
    the range is echoed back with the data and shown on screen.
    """
    start = (request.args.get("from") or "").strip()[:10]
    end = (request.args.get("to") or "").strip()[:10]
    if start and end:
        try:
            datetime.strptime(start, "%Y-%m-%d")
            datetime.strptime(end, "%Y-%m-%d")
            if start <= end:
                return start, end, f"{start} to {end}"
        except ValueError:
            pass
    fy_start, fy_end = _financial_year(company_id)
    return fy_start, fy_end, "Current financial year"


@mobile_bp.route("/api/mobile/reports")
@_auth_required
def reports(user_id, company_id):
    """Summaries a shareholder can pull for any period, including last year:
    sales and purchases month by month, and income and expenses by group."""
    from database.reports_db import get_profit_and_loss_data

    start, end, label = _requested_period(company_id)

    conn = get_connection()
    try:
        cursor = conn.cursor()
        # Built from the goods lines and the VAT lines, not from
        # vouchers.amount. That column is the voucher's total debit, which on a
        # Sales voucher also carries the Cost of Goods Sold entry - counting it
        # as revenue overstates sales by the cost of what was sold, and made
        # August 2023 read 106.92 against a real 66.50.
        cursor.execute("""
            SELECT TO_CHAR(v.date::DATE, 'YYYY-MM') AS period,
                   COALESCE(SUM(CASE WHEN v.voucher_type = 'Sales'
                                     THEN goods.net END), 0) AS sales_net,
                   COALESCE(SUM(CASE WHEN v.voucher_type = 'Purchase'
                                     THEN goods.net END), 0) AS purchase_net,
                   COALESCE(SUM(CASE WHEN v.voucher_type = 'Sales'
                                     THEN vat.amount END), 0) AS sales_vat,
                   COALESCE(SUM(CASE WHEN v.voucher_type = 'Purchase'
                                     THEN vat.amount END), 0) AS purchase_vat
            FROM vouchers v
            LEFT JOIN (
                SELECT company_id, voucher_number, SUM(amount) AS net
                FROM item_entries GROUP BY company_id, voucher_number
            ) goods ON goods.company_id = v.company_id
                   AND goods.voucher_number = v.voucher_number
            LEFT JOIN (
                SELECT company_id, voucher_number, SUM(amount) AS amount
                FROM ledger_entries
                WHERE ledger_name IN ('Output VAT 5%%', 'Input VAT 5%%')
                GROUP BY company_id, voucher_number
            ) vat ON vat.company_id = v.company_id
                 AND vat.voucher_number = v.voucher_number
            WHERE v.company_id = %s AND v.voucher_type IN ('Sales', 'Purchase')
              AND v.date >= %s AND v.date <= %s
            GROUP BY period
            ORDER BY period
        """, (company_id, start, end))
        monthly = []
        for r in cursor.fetchall():
            sales_net, purchase_net = float(r[1] or 0), float(r[2] or 0)
            sales_vat, purchase_vat = float(r[3] or 0), float(r[4] or 0)
            monthly.append({
                "period": r[0],
                # Net of VAT, so these tie back to the income and expense
                # figures on the same screen.
                "sales": round(sales_net, 2),
                "purchases": round(purchase_net, 2),
                "sales_gross": round(sales_net + sales_vat, 2),
                "purchases_gross": round(purchase_net + purchase_vat, 2),
            })
    finally:
        conn.close()

    statement, income, expenses, net_profit = get_profit_and_loss_data(
        from_date=start, to_date=end, company_id=company_id)

    def by_group(section):
        out = []
        for group, ledgers in (section or {}).items():
            total = sum(float(entry.get("amount") or 0)
                        for entry in (ledgers or []))
            if abs(total) > 0.005:
                out.append({"group": group, "amount": round(total, 2),
                            "ledgers": sorted(
                                [{"name": e.get("ledger_name"),
                                  "amount": round(float(e.get("amount") or 0), 2)}
                                 for e in ledgers or []],
                                key=lambda x: -abs(x["amount"]))[:8]})
        return sorted(out, key=lambda g: -abs(g["amount"]))

    return jsonify({"success": True, "data": {
        "period": {"from": start, "to": end, "label": label},
        "monthly": monthly,
        "monthly_totals": {
            "sales": round(sum(m["sales"] for m in monthly), 2),
            "purchases": round(sum(m["purchases"] for m in monthly), 2),
            "sales_gross": round(sum(m["sales_gross"] for m in monthly), 2),
            "purchases_gross": round(sum(m["purchases_gross"] for m in monthly), 2),
        },
        "income_by_group": by_group((statement or {}).get("income")),
        "expense_by_group": by_group((statement or {}).get("expenses")),
        "totals": {
            "income": round(float(income or 0), 2),
            "expenses": round(float(expenses or 0), 2),
            "net_profit": round(float(net_profit or 0), 2),
        },
    }})


@mobile_bp.route("/api/mobile/insights")
@_auth_required
def insights(user_id, company_id):
    """The detail behind the headlines: who owes what and how late, what sells,
    where the cash sits, what is owed in VAT, and how busy the books are."""
    from database.analysis_db import get_monthly_purchase_trend

    start, end, period_label = _requested_period(company_id)
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Cash, bank by ledger - one number hides an overdrawn account.
        cursor.execute("""
            SELECT l.ledger_name, COALESCE(g.group_name, ''),
                   COALESCE(l.closing_balance, 0)
            FROM ledgers l
            LEFT JOIN groups g ON g.group_code = l.group_code
                              AND g.company_id = l.company_id
            WHERE l.company_id = %s AND l.group_code IN ('G005', 'G006')
              AND COALESCE(l.is_active, 1) = 1
            ORDER BY COALESCE(l.closing_balance, 0) DESC
        """, (company_id,))
        cash_accounts = [{"name": r[0], "group": r[1],
                          "amount": round(float(r[2] or 0), 2)}
                         for r in cursor.fetchall()]

        # What actually sells, by value - the shareholder's "what earns for us".
        cursor.execute("""
            SELECT ie.item_name,
                   SUM(ie.quantity) AS qty,
                   SUM(ie.amount) AS value
            FROM item_entries ie
            JOIN vouchers v ON v.company_id = ie.company_id
                           AND v.voucher_number = ie.voucher_number
            WHERE ie.company_id = %s AND v.voucher_type = 'Sales'
              AND v.date >= %s AND v.date <= %s
            GROUP BY ie.item_name
            ORDER BY value DESC
            LIMIT 5
        """, (company_id, start, end))
        top_items = [{"name": r[0], "quantity": round(float(r[1] or 0), 2),
                      "value": round(float(r[2] or 0), 2)}
                     for r in cursor.fetchall()]

        # Money held for the tax authority is not the company's money.
        cursor.execute("""
            SELECT l.ledger_name, COALESCE(l.closing_balance, 0)
            FROM ledgers l
            WHERE l.company_id = %s AND l.group_code = 'G011'
        """, (company_id,))
        vat_rows = cursor.fetchall()
        output_vat = sum(-float(r[1] or 0) for r in vat_rows
                         if "output" in (r[0] or "").lower())
        input_vat = sum(float(r[1] or 0) for r in vat_rows
                        if "input" in (r[0] or "").lower())

        # Is anyone still keeping the books up to date?
        cursor.execute("""
            SELECT COUNT(*), MAX(date) FROM vouchers
            WHERE company_id = %s AND date >= %s AND date <= %s
        """, (company_id, start, end))
        row = cursor.fetchone()
        voucher_count, last_entry = int(row[0] or 0), row[1]

        # Stock that is not moving is cash sitting on a shelf.
        cursor.execute("""
            SELECT i.name, COALESCE(i.stock_quantity, 0),
                   COALESCE(i.stock_value, 0)
            FROM inventory i
            WHERE i.company_id = %s AND COALESCE(i.is_active, 1) = 1
              AND COALESCE(i.stock_value, 0) > 0
              AND NOT EXISTS (
                  SELECT 1 FROM item_entries ie
                  JOIN vouchers v ON v.company_id = ie.company_id
                                 AND v.voucher_number = ie.voucher_number
                  WHERE ie.company_id = i.company_id AND ie.item_name = i.name
                    AND v.voucher_type = 'Sales' AND v.date >= %s
              )
            ORDER BY COALESCE(i.stock_value, 0) DESC
            LIMIT 5
        """, (company_id, start))
        idle_stock = [{"name": r[0], "quantity": round(float(r[1] or 0), 2),
                       "value": round(float(r[2] or 0), 2)}
                      for r in cursor.fetchall()]
    finally:
        conn.close()

    return jsonify({"success": True, "data": {
        "period": {"from": start, "to": end, "label": period_label},
        # Ageing is a position, so it is measured at the end of the period
        # rather than over it.
        "receivables": _ageing_summary("G007", company_id, as_of=end),
        "payables": _ageing_summary("G008", company_id, as_of=end),
        "cash_accounts": cash_accounts,
        "top_items": top_items,
        "purchase_trend": _trend_series(
            get_monthly_purchase_trend(company_id=company_id)),
        "vat": {
            "output": round(output_vat, 2),
            "input": round(input_vat, 2),
            "payable": round(output_vat - input_vat, 2),
        },
        "activity": {
            "vouchers_this_year": voucher_count,
            "last_entry": str(last_entry)[:10] if last_entry else None,
        },
        "idle_stock": idle_stock,
    }})


@mobile_bp.route("/api/mobile/chat", methods=["POST"])
@_auth_required
def chat(user_id, company_id):
    """The same chatbot as the web app, asked over the API.

    The engine is shared, so an answer here is the answer there - no second
    implementation to drift out of step.
    """
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"success": False,
                        "message": "Ask a question first."}), 400

    # The web chat is capped at 30 questions a minute because a question that
    # falls through to the model is a billed OpenRouter call. The phone has to
    # be capped too, and keyed on the token's user rather than the IP - a
    # dozen phones on one office connection share an address.
    from database.app_state_db import rate_limit_check
    try:
        allowed, retry_after = rate_limit_check(
            "mobile_chat", f"user:{user_id}", 30, 60)
    except Exception:
        allowed, retry_after = True, 0
    if not allowed:
        response = jsonify({
            "success": False,
            "message": (f"That is a lot of questions at once. Please wait "
                        f"{retry_after}s and ask again.")})
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return response

    # Continuity: the browser identifies a conversation by its session
    # cookie, which this caller does not have. Pin it to the signed-in user
    # instead, so follow-ups and the assistant's own yes/no questions work the
    # same way they do on the web.
    from .chat_context import use_conversation
    use_conversation(str(data.get("session_id") or f"mobile-user-{user_id}"))

    from .chatbot_service import process_chat_query
    try:
        reply = process_chat_query(
            question, company_id,
            ai_enabled=bool(data.get("ai_enabled", True)),
            history=data.get("history") or [])
        if "error" in reply:
            return jsonify({"success": False, "message": reply["error"]}), 500
    except Exception as exc:
        print(f"[mobile] chat failed: {exc}")
        return jsonify({"success": False,
                        "message": f"The assistant could not answer: {exc}"}), 500

    return jsonify({"success": True, "data": reply})


@mobile_bp.route("/api/mobile/ping")
def ping():
    """Used by the app to wake a sleeping free instance before signing in."""
    return jsonify({"success": True, "api_version": API_VERSION})
