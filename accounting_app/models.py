from datetime import datetime
from functools import wraps

from flask import redirect, url_for
from flask_login import UserMixin, current_user

from . import get_db_connection
from database.company_db import get_current_company_id


# Assignable menu permissions, mirroring the navigation bar.
# A top-level menu can be granted as a whole (parent key, e.g. "reports")
# or item-by-item using dotted child keys (e.g. "reports.registers").
MENU_TREE = [
    {"key": "accounting_master", "label": "Accounting Master", "children": [
        ("accounting_master.chart_of_accounts", "Chart of Accounts"),
        ("accounting_master.groups", "Manage Groups"),
        ("accounting_master.sub_groups", "Manage Sub Groups"),
        ("accounting_master.ledgers", "Manage Ledgers"),
        ("accounting_master.credit_terms", "Manage Credit Terms"),
        ("accounting_master.cost_centers", "Manage Cost Centers"),
    ]},
    {"key": "modules", "label": "Modules", "children": [
        ("modules.fixed_assets", "Fixed Assets"),
        ("modules.recurring", "Recurring"),
        ("modules.settlement", "Settlement / Matching"),
    ]},
    {"key": "inventory_master", "label": "Inventory Master", "children": [
        ("inventory_master.inventory_groups", "Manage Inventory Groups"),
        ("inventory_master.inventory", "Manage Inventory"),
        ("inventory_master.vendor_item_mappings", "Vendor Item Mappings"),
    ]},
    {"key": "vouchers", "label": "Vouchers", "children": []},
    {"key": "reports", "label": "Reports", "children": [
        ("reports.all_reports", "All Reports"),
        ("reports.financial_statements", "Financial Statements"),
        ("reports.registers", "Registers"),
        ("reports.summaries", "Summaries"),
        ("reports.ledger_books", "Ledger Books"),
        ("reports.inventory_reports", "Inventory Reports"),
        ("reports.vat_reports", "VAT Reports"),
        ("reports.other", "Other (Ageing)"),
        ("reports.report_builder", "Custom Report Builder"),
    ]},
    {"key": "print", "label": "Print", "children": [
        ("print.jv_print", "JV Print"),
    ]},
    {"key": "import_queue", "label": "Import Queue", "children": []},
    {"key": "setup", "label": "Setup", "children": [
        ("setup.company_settings", "Company Settings"),
        ("setup.financial_years", "Manage Financial Years"),
        ("setup.voucher_config", "Voucher Configuration"),
        ("setup.user_management", "User Management"),
    ]},
]

# Legacy flat list (perm_key, display label) of top-level menus
PERMISSIONS = [(m["key"], m["label"]) for m in MENU_TREE]
PERMISSION_KEYS = (
    {m["key"] for m in MENU_TREE}
    | {key for m in MENU_TREE for key, _label in m["children"]}
)


class User(UserMixin):
    def __init__(self, id, username, email, password_hash, is_admin, is_principal=0, permissions=None, hide_dashboard=0):
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.is_admin = bool(is_admin)
        self.is_principal = bool(is_principal)
        self.permissions = set(permissions or [])
        self.hide_dashboard = bool(hide_dashboard)

    def can_access(self, perm_key):
        """Admin and Principal users can access every assignable area;
        other users need an explicit permission.

        Hierarchy rules:
        - Granting a top-level key (e.g. "reports") implies every child
          ("reports.registers", ...), so existing grants keep working.
        - Asking about a top-level key succeeds when any of its children
          is granted, so the parent dropdown stays visible.
        """
        if self.is_admin or self.is_principal:
            return True
        if perm_key in self.permissions:
            return True
        if "." in perm_key:
            return perm_key.split(".", 1)[0] in self.permissions
        prefix = perm_key + "."
        return any(p.startswith(prefix) for p in self.permissions)


def format_date(date_str):
    if date_str and isinstance(date_str, str) and "-" in date_str:
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            return date_obj.strftime("%d-%m-%Y")
        except ValueError:
            return date_str
    return date_str


def parse_date(date_str):
    """
    Parses a date string from DD-MM-YYYY to YYYY-MM-DD.
    If already YYYY-MM-DD, returns as is.
    If invalid, returns original string (letting database or other logic fail/handle it).
    """
    if not date_str or not isinstance(date_str, str):
        return date_str
    
    # Check if already YYYY-MM-DD
    if "-" in date_str:
        parts = date_str.split("-")
        if len(parts) == 3:
            # Simple heuristic: if first part is 4 digits, assume YYYY-MM-DD
            if len(parts[0]) == 4:
                return date_str
    
    try:
        # Try DD-MM-YYYY
        date_obj = datetime.strptime(date_str, "%d-%m-%Y")
        return date_obj.strftime("%Y-%m-%d")
    except ValueError:
        pass
        
    try:
        # Try DD/MM/YYYY just in case
        date_obj = datetime.strptime(date_str, "%d/%m/%Y")
        return date_obj.strftime("%Y-%m-%d")
    except ValueError:
        pass

    try:
        date_obj = datetime.strptime(date_str, "%d-%m-%y")
        return date_obj.strftime("%Y-%m-%d")
    except ValueError:
        pass

    try:
        date_obj = datetime.strptime(date_str, "%d/%m/%y")
        return date_obj.strftime("%Y-%m-%d")
    except ValueError:
        pass

    return date_str


def get_sales_group_code(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT group_code FROM groups WHERE group_name = 'Sales' AND company_id = ?", (company_id,))
        result = cursor.fetchone()
        conn.close()
        print(f"get_sales_group_code: {result[0] if result else None}")
        return result[0] if result else None
    except Exception as e:
        print(f"Error in get_sales_group_code: {str(e)}")
        return None


def get_purchase_group_code(company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT group_code FROM groups WHERE group_name = 'Purchase' AND company_id = ?",
            (company_id,)
        )
        result = cursor.fetchone()
        conn.close()
        print(f"get_purchase_group_code: {result[0] if result else None}")
        return result[0] if result else None
    except Exception as e:
        print(f"Error in get_purchase_group_code: {str(e)}")
        return None


def get_cost_center_code(cost_center_name, company_id=None):
    if company_id is None:
        company_id = get_current_company_id()
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT center_code FROM cost_centers WHERE center_name = ? AND company_id = ?",
            (cost_center_name, company_id),
        )
        result = cursor.fetchone()
        conn.close()
        code = result[0] if result else None
        print(f"Cost center code for {cost_center_name}: {code}")
        return code
    except Exception as e:
        print(f"Error in get_cost_center_code: {str(e)}")
        return None


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            print(
                f"Access denied: User "
                f"{current_user.username if current_user.is_authenticated else 'None'} "
                f"is not admin"
            )
            return redirect(url_for("auth_bp.signin"))
        return f(*args, **kwargs)

    return decorated_function


def permission_required(perm_key):
    """Allow admins, principal users, or users explicitly granted perm_key."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth_bp.signin"))
            if not current_user.can_access(perm_key):
                print(f"Access denied: {current_user.username} lacks permission '{perm_key}'")
                return redirect(url_for("dashboard_bp.dashboard"))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def setup_access_required(f):
    """Setup pages: Company Settings, Financial Years, Voucher Configuration."""
    return permission_required("setup")(f)


# ----------------------------------------------------------------------



# ----------------------------------------------------------------------
# Ledger group helpers and voucher group rules
# ----------------------------------------------------------------------

def get_ledger_group_name_map(company_id=None):
    """
    Returns a dict {ledger_name: group_name}.
    Assumes ledgers.ledger_name is unique.
    """
    if company_id is None:
        company_id = get_current_company_id()
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT l.ledger_name, g.group_name
            FROM ledgers l
            JOIN groups g ON l.group_code = g.group_code AND l.company_id = g.company_id
            WHERE l.company_id = ?
            """,
            (company_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        mapping = {name: group for (name, group) in rows}
        print(f"Ledger->Group map: {mapping}")
        return mapping
    except Exception as e:
        print(f"Error in get_ledger_group_name_map: {str(e)}")
        return {}


# Rules the application always applies, whatever the per-company configuration
# says. Money received has to land in cash or a bank; money paid has to leave
# one. These were enforced only by filtering the dropdown in voucher.html, so
# an Excel import - or any POST that did not come from that page - bypassed
# them entirely.
#   G005 = Cash Accounts, G006 = Bank Accounts
CASH_BANK_GROUPS = ("G005", "G006")

BUILT_IN_SIDE_RULES = {
    ("Receipt", "Debit"): (CASH_BANK_GROUPS,
                           "money received must be debited to a Cash or Bank ledger"),
    ("Payment", "Credit"): (CASH_BANK_GROUPS,
                            "money paid must be credited from a Cash or Bank ledger"),
    ("Contra", "Debit"): (CASH_BANK_GROUPS,
                          "a contra entry moves money between Cash and Bank"),
    ("Contra", "Credit"): (CASH_BANK_GROUPS,
                           "a contra entry moves money between Cash and Bank"),
}


def _ledger_group_codes(ledger_names, company_id):
    """{ledger_name: group_code} for the named ledgers."""
    names = [n for n in {str(n or "").strip() for n in ledger_names} if n]
    if not names:
        return {}
    from database.config import get_connection
    conn = get_connection()
    try:
        cursor = conn.cursor()
        placeholders = ",".join(["%s"] * len(names))
        cursor.execute(
            f"SELECT ledger_name, group_code FROM ledgers "
            f"WHERE company_id = %s AND ledger_name IN ({placeholders})",
            tuple([company_id] + names))
        return {r[0]: r[1] for r in cursor.fetchall()}
    except Exception as exc:
        print(f"[voucher-rules] could not read ledger groups: {exc}")
        return {}
    finally:
        conn.close()


def check_built_in_side_rules(voucher_type, ledger_entries, company_id):
    """The always-on rules above. Returns (ok, message)."""
    vt = (voucher_type or "").strip()
    sides = {side for (typ, side) in BUILT_IN_SIDE_RULES if typ == vt}
    if not sides:
        return True, ""

    relevant = [e for e in ledger_entries
                if (e.get("type") or "").strip() in sides]
    if not relevant:
        return True, ""

    group_of = _ledger_group_codes([e.get("ledger_name") for e in relevant],
                                   company_id)
    for entry in relevant:
        side = (entry.get("type") or "").strip()
        allowed, reason = BUILT_IN_SIDE_RULES[(vt, side)]
        name = str(entry.get("ledger_name") or "").strip()
        group_code = group_of.get(name)
        if group_code is None:
            continue        # unknown ledger - reported by the other checks
        if group_code not in allowed:
            return False, (f"{vt} ({side}): '{name}' is not a Cash or Bank "
                           f"ledger - {reason}.")
    return True, ""


def validate_voucher_ledger_groups(voucher_type, ledger_entries, company_id=None):
    """
    Enforce group rules per voucher type using dynamic database configuration,
    plus the built-in rules above that apply to every company.

    voucher_type: string like 'Receipt', 'Payment', etc.
    ledger_entries: list of dicts:
        {
            "ledger_name": str,
            "amount": float,
            "type": "Debit" or "Credit",
        }
    company_id: optional, defaults to session company_id if not provided.

    Returns (True, "") if OK, or (False, "error message") if invalid.
    """
    if not ledger_entries:
        return True, ""

    if company_id is None:
        company_id = get_current_company_id()

    vt = voucher_type.strip()

    # Built-in rules first: these hold regardless of how a company has
    # configured its voucher types, and they are what the voucher screen has
    # always shown users.
    ok, err = check_built_in_side_rules(vt, ledger_entries, company_id)
    if not ok:
        return False, err

    # Lazy import to avoid circular dependency issues at top-level
    try:
        from database.voucher_config_db import get_allowed_ledgers, get_voucher_config
    except ImportError:
        print("Warning: Could not import voucher_config_db")
        return True, ""

    # Check Credit Side
    credit_ledgers = [e for e in ledger_entries if e["type"] == "Credit"]
    if credit_ledgers:
        config_cr = get_voucher_config(vt, "Credit", company_id=company_id)
        if config_cr: # If config exists, enforce it
            # Fetch allowed ledger names for Credit
            allowed_rows = get_allowed_ledgers(vt, "Credit", company_id=company_id)
            allowed_names = {r["name"] for r in allowed_rows}
            for e in credit_ledgers:
                if e["ledger_name"] not in allowed_names:
                    return False, f"{vt} (Credit): Ledger '{e['ledger_name']}' is not allowed."

    # Check Debit Side
    debit_ledgers = [e for e in ledger_entries if e["type"] == "Debit"]
    if debit_ledgers:
        config_dr = get_voucher_config(vt, "Debit", company_id=company_id)
        if config_dr: # If config exists, enforce it
            # Fetch allowed ledger names for Debit
            allowed_rows = get_allowed_ledgers(vt, "Debit", company_id=company_id)
            allowed_names = {r["name"] for r in allowed_rows}
            for e in debit_ledgers:
                if e["ledger_name"] not in allowed_names:
                    return False, f"{vt} (Debit): Ledger '{e['ledger_name']}' is not allowed."

    print(f"Voucher ledger group validation passed for {voucher_type}")
    return True, ""
