"""Which chat tools a user is allowed to run.

The chatbot reads the same data the reports screens read, so it has to obey
the same permission model - otherwise it is a way around the menu. Every tool
in `chat_toolkit` maps to a menu permission key (the ones in models.MENU_TREE),
and `check` refuses a tool the signed-in user could not have opened by hand.

Two callers, one rule:
  * the browser, where flask_login's `current_user` carries the permissions;
  * the phone app, which authenticates with a bearer token instead - it calls
    `use_user(user_id)` for the request, and the permissions are loaded from
    the database.
"""
from flask import g, has_request_context


class PermissionDenied(Exception):
    """The user may not run this tool. Carries the permission that was missing."""

    def __init__(self, tool_name, permission, label=None):
        self.tool_name = tool_name
        self.permission = permission
        self.label = label or _label_for(permission)
        super().__init__(f"'{tool_name}' requires permission '{permission}'")


# A tool's group decides its permission unless it is listed in TOOL_PERMISSIONS
# below. Balances, statements and analysis are all report-shaped questions.
GROUP_PERMISSIONS = {
    "Masters": "accounting_master",
    "Balances": "reports",
    "Reports": "reports",
    "Statements": "reports",
    "Sales & Purchase": "reports",
    "Inventory": "reports.inventory_reports",
    "Vouchers": "vouchers",
}

# Tools whose subject sits under a different menu than their group suggests.
# The keys are child permissions, so a user granted the whole parent menu
# ("reports") still passes - see User.can_access.
TOOL_PERMISSIONS = {
    # Masters that live under the Inventory menu
    "list_items": "inventory_master.inventory",
    "item_master_details": "inventory_master.inventory",
    "item_opening_stock": "inventory_master.inventory",
    "list_stock_groups": "inventory_master.inventory_groups",
    "list_units": "inventory_master.inventory",
    "price_list": "inventory_master.inventory",

    # Masters that live under Setup or Modules
    "company_settings": "setup.company_settings",
    "list_financial_years": "setup.financial_years",
    "list_users": "setup.user_management",
    "fixed_asset_register": "modules.fixed_assets",
    "recurring_vouchers": "modules.recurring",
    "settlements_by_party": "modules.settlement",

    # Reports, at the granularity the menu offers
    "trial_balance": "reports.financial_statements",
    "balance_sheet": "reports.financial_statements",
    "profit_and_loss": "reports.financial_statements",
    "net_profit": "reports.financial_statements",
    "cash_flow": "reports.financial_statements",
    "fy_comparison": "reports.financial_statements",
    "coa_balances": "reports.financial_statements",
    "vat_summary": "reports.vat_reports",
    "vat_detailed": "reports.vat_reports",
    "ledger_statement": "reports.ledger_books",
    "customer_statement": "reports.ledger_books",
    "supplier_statement": "reports.ledger_books",
    "gl_dump": "reports.ledger_books",
    "cash_bank_book": "reports.ledger_books",
    "party_matching": "reports.ledger_books",
    "outstanding_receivables": "reports.other",
    "outstanding_payables": "reports.other",
    "inventory_ageing": "reports.other",
    "voucher_register": "reports.registers",
}

# The free-form text-to-SQL fallback can read any business table, so it needs
# the broad reporting permission rather than any one report's.
AI_SQL_PERMISSION = "reports"

_LABELS = {
    "accounting_master": "Accounting Master",
    "inventory_master": "Inventory Master",
    "modules": "Modules",
    "vouchers": "Vouchers",
    "reports": "Reports",
    "setup": "Setup",
}

_SESSION_KEY = "_chat_permission_user_id"


def _label_for(permission):
    """A menu name the user will recognise, for the refusal message."""
    from .models import MENU_TREE

    for menu in MENU_TREE:
        if menu["key"] == permission:
            return menu["label"]
        for key, child_label in menu["children"]:
            if key == permission:
                return f"{menu['label']} > {child_label}"
    return _LABELS.get(permission.split(".", 1)[0], permission)


def permission_for(tool_name):
    """The permission key a tool needs, or None when it needs none."""
    from .chat_toolkit import TOOLS

    if tool_name in TOOL_PERMISSIONS:
        return TOOL_PERMISSIONS[tool_name]
    tool_obj = TOOLS.get(tool_name)
    if tool_obj is None:
        return None
    return GROUP_PERMISSIONS.get(tool_obj.group, "reports")


def use_user(user_id):
    """Pin this request's permission checks to a user id (the phone app)."""
    if has_request_context():
        setattr(g, _SESSION_KEY, user_id)


def _user():
    """The user whose permissions apply, or None when nobody is identified."""
    if has_request_context():
        user_id = getattr(g, _SESSION_KEY, None)
        if user_id is not None:
            return _load_user(user_id)

    try:
        from flask_login import current_user
        if current_user and current_user.is_authenticated:
            return current_user
    except Exception:
        pass
    return None


def _load_user(user_id):
    """Build a User for a bearer token's user id, permissions included."""
    cache = getattr(g, "_chat_permission_user", None) if has_request_context() else None
    if cache is not None and cache.id == user_id:
        return cache

    from .models import User
    from database import get_user_by_id
    from database.master_db import get_user_permissions

    row = get_user_by_id(user_id)
    if not row:
        return None
    is_principal = row[5] if len(row) > 5 else 0
    permissions = set()
    if not row[4] and not is_principal:
        try:
            permissions = get_user_permissions(row[0])
        except Exception:
            permissions = set()
    user = User(row[0], row[1], row[2], row[3], row[4], is_principal, permissions)
    if has_request_context():
        g._chat_permission_user = user
    return user


def can_use(tool_name):
    """True when the current user may run this tool."""
    permission = permission_for(tool_name)
    if permission is None:
        return True
    user = _user()
    if user is None:
        # No identified user means no request context to trust (a script, a
        # test). The tools are read-only, so this is not a data leak, and
        # refusing here would break the CLI harnesses.
        return True
    return user.can_access(permission)


def check(tool_name):
    """Raise PermissionDenied unless the current user may run this tool."""
    if not can_use(tool_name):
        raise PermissionDenied(tool_name, permission_for(tool_name))


def can_use_ai_sql():
    """True when the current user may use the free-form AI database query."""
    user = _user()
    if user is None:
        return True
    return user.can_access(AI_SQL_PERMISSION)


def allowed_tool_names():
    """Every tool name the current user may run, sorted."""
    from .chat_toolkit import TOOLS

    return sorted(name for name in TOOLS if can_use(name))
