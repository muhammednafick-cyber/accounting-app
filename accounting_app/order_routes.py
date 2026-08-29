"""Sales and Purchase Orders.

Orders sit alongside vouchers but post nothing: they record what was agreed,
and are worked off by the Sales or Purchase vouchers billed against them. The
voucher form does the converting - an order is loaded into it, its pending
quantities become the voucher's lines, and saving the voucher bills them back.
"""
from datetime import datetime

from flask import (Blueprint, flash, jsonify, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from database import get_current_company_id
from database.orders_db import (DUE_FILTERS, ORDER_TYPES, STATUS_CANCELLED, STATUS_CLOSED,
                                STATUS_OPEN, STATUS_PARTIAL,
                                VOUCHER_TYPE_FOR_ORDER, cancel_order,
                                close_order, create_order, delete_order,
                                get_open_orders_for_party, get_order,
                                get_orders, get_pending_lines, reopen_order)

order_bp = Blueprint("order_bp", __name__)

# URL slug -> order type
_SLUGS = {
    "sales": "Sales Order",
    "sales_order": "Sales Order",
    "purchase": "Purchase Order",
    "purchase_order": "Purchase Order",
}

STATUSES = [STATUS_OPEN, STATUS_PARTIAL, STATUS_CLOSED, STATUS_CANCELLED]


def _order_type(slug):
    order_type = _SLUGS.get((slug or "").strip().lower().replace("-", "_"))
    if not order_type:
        raise ValueError(f"Unknown order type '{slug}'")
    return order_type


def _slug(order_type):
    return "sales" if order_type == "Sales Order" else "purchase"


# Sales orders are raised on customers (Debtors), purchase orders on vendors
# (Creditors) - the same groups the matching voucher's party list uses.
_PARTY_GROUP_CODE = {"Sales Order": "G007", "Purchase Order": "G008"}


def _party_ledgers(order_type, company_id):
    """The parties an order of this type can be raised on - customers for a
    sales order, vendors for a purchase order, and nothing else.

    Blocked parties are left out: an order is a new commitment, and a ledger
    you have blocked should not take one. The list is deliberately not padded
    with the rest of the chart of accounts when it comes back empty - a bank
    account is not a customer, and offering one only invites a wrong entry.
    """
    from database.accounts_db import get_party_ledgers
    parties = get_party_ledgers(_PARTY_GROUP_CODE[order_type],
                                company_id=company_id) or []
    return [p for p in parties if p.get("is_active", 1)]


@order_bp.route("/orders/<order_slug>")
@login_required
def orders(order_slug):
    try:
        order_type = _order_type(order_slug)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("voucher_bp.vouchers"))

    status = request.args.get("status") or None
    search = (request.args.get("search") or "").strip() or None
    due = request.args.get("due") or None
    rows = get_orders(order_type=order_type, status=status, search=search,
                      due=due)

    counts = {s: 0 for s in STATUSES}
    for row in get_orders(order_type=order_type):
        counts[row["status"]] = counts.get(row["status"], 0) + 1

    # How many orders each delivery-date filter would show, so the counts are
    # visible without having to try each one.
    due_counts = {key: len(get_orders(order_type=order_type, due=key))
                  for key in DUE_FILTERS}

    return render_template(
        "orders/order_list.html",
        order_type=order_type,
        order_slug=_slug(order_type),
        orders=rows,
        statuses=STATUSES,
        counts=counts,
        due_filters=DUE_FILTERS,
        due_counts=due_counts,
        selected_due=due or "",
        selected_status=status or "",
        # Expected dates are stored as YYYY-MM-DD text, so the list can mark
        # what is late by comparing strings against today.
        today=datetime.now().strftime("%Y-%m-%d"),
        search=search or "",
        username=current_user.username,
    )


@order_bp.route("/order/<order_slug>/new")
@login_required
def new_order(order_slug):
    try:
        order_type = _order_type(order_slug)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("voucher_bp.vouchers"))

    from database import get_items
    company_id = get_current_company_id()
    return render_template(
        "orders/order_form.html",
        order_type=order_type,
        order_slug=_slug(order_type),
        party_ledgers=_party_ledgers(order_type, company_id),
        party_master_url=url_for(
            "master_bp.manage_parties",
            kind="customers" if order_type == "Sales Order" else "vendors"),
        items=get_items(company_id=company_id) or [],
        username=current_user.username,
    )


@order_bp.route("/order/save", methods=["POST"])
@login_required
def save_order():
    order_slug = request.form.get("order_slug", "")
    try:
        order_type = _order_type(order_slug)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("voucher_bp.vouchers"))

    from accounting_app.models import parse_date
    try:
        items = []
        for name, quantity, price in zip(
                request.form.getlist("item_name[]"),
                request.form.getlist("quantity[]"),
                request.form.getlist("unit_price[]")):
            if not (name or "").strip():
                continue
            items.append({"item_name": name, "quantity": quantity,
                          "unit_price": price})

        order_number = create_order(
            order_type,
            parse_date(request.form.get("date")),
            (request.form.get("party_ledger_name") or "").strip(),
            items,
            reference=(request.form.get("reference") or "").strip() or None,
            expected_date=parse_date(request.form.get("expected_date")) or None,
            narration=request.form.get("narration") or "",
        )
        flash(f"{order_type} {order_number} created.", "success")
        return redirect(url_for("order_bp.view_order", order_number=order_number))
    except Exception as exc:
        flash(f"Could not create the order: {exc}", "error")
        return redirect(url_for("order_bp.new_order", order_slug=order_slug))


@order_bp.route("/order/view/<order_number>")
@login_required
def view_order(order_number):
    order = get_order(order_number)
    if not order:
        flash(f"Order {order_number} not found.", "error")
        return redirect(url_for("voucher_bp.vouchers"))
    return render_template(
        "orders/order_view.html",
        order=order,
        order_slug=_slug(order["order_type"]),
        voucher_type=VOUCHER_TYPE_FOR_ORDER[order["order_type"]],
        username=current_user.username,
    )


@order_bp.route("/order/print/<order_number>")
@login_required
def print_order(order_number):
    from database import get_company_settings
    order = get_order(order_number)
    if not order:
        flash(f"Order {order_number} not found.", "error")
        return redirect(url_for("voucher_bp.vouchers"))
    return render_template(
        "orders/order_print.html",
        order=order,
        company=get_company_settings() or {},
    )


@order_bp.route("/order/close", methods=["POST"])
@login_required
def close():
    """Write off an order's remaining balance - the rest is not coming."""
    order_number = request.form.get("order_number")
    reason = (request.form.get("reason") or "").strip()
    try:
        close_order(order_number, reason)
        flash(f"{order_number} closed.", "success")
    except Exception as exc:
        flash(f"Could not close {order_number}: {exc}", "error")
    return redirect(url_for("order_bp.view_order", order_number=order_number))


@order_bp.route("/order/cancel", methods=["POST"])
@login_required
def cancel():
    order_number = request.form.get("order_number")
    reason = (request.form.get("reason") or "").strip()
    try:
        cancel_order(order_number, reason)
        flash(f"{order_number} cancelled.", "success")
    except Exception as exc:
        flash(f"Could not cancel {order_number}: {exc}", "error")
    return redirect(url_for("order_bp.view_order", order_number=order_number))


@order_bp.route("/order/reopen", methods=["POST"])
@login_required
def reopen():
    order_number = request.form.get("order_number")
    try:
        reopen_order(order_number)
        flash(f"{order_number} reopened.", "success")
    except Exception as exc:
        flash(f"Could not reopen {order_number}: {exc}", "error")
    return redirect(url_for("order_bp.view_order", order_number=order_number))


@order_bp.route("/order/delete", methods=["POST"])
@login_required
def delete():
    order_number = request.form.get("order_number")
    order = get_order(order_number)
    slug = _slug(order["order_type"]) if order else "sales"
    try:
        delete_order(order_number)
        flash(f"{order_number} deleted.", "success")
        return redirect(url_for("order_bp.orders", order_slug=slug))
    except Exception as exc:
        flash(f"Could not delete {order_number}: {exc}", "error")
        return redirect(url_for("order_bp.view_order", order_number=order_number))


# ---------------------------------------------------------------- APIs used
# by the voucher form when an order is converted.

@order_bp.route("/api/open_orders")
@login_required
def api_open_orders():
    """Orders this party still has pending, for the voucher form's picker."""
    voucher_type = request.args.get("voucher_type") or ""
    party = (request.args.get("party") or "").strip()
    order_type = next((k for k, v in VOUCHER_TYPE_FOR_ORDER.items()
                       if v == voucher_type), None)
    if not order_type or not party:
        return jsonify({"success": True, "orders": []})
    return jsonify({"success": True,
                    "orders": get_open_orders_for_party(order_type, party)})


@order_bp.route("/api/order_lines")
@login_required
def api_order_lines():
    """The pending lines of one order, ready to become voucher lines."""
    order_number = (request.args.get("order_number") or "").strip()
    voucher_type = request.args.get("voucher_type") or ""
    order = get_pending_lines(order_number)
    if not order:
        return jsonify({"success": False,
                        "message": f"Order {order_number} not found"}), 404

    expected = VOUCHER_TYPE_FOR_ORDER[order["order_type"]]
    if voucher_type and voucher_type != expected:
        return jsonify({
            "success": False,
            "message": (f"{order_number} is a {order['order_type']} and can "
                        f"only be billed by a {expected} voucher."),
        }), 400
    if order["status"] in (STATUS_CLOSED, STATUS_CANCELLED):
        return jsonify({
            "success": False,
            "message": f"{order_number} is {order['status'].lower()}.",
        }), 400
    if not order["items"]:
        return jsonify({
            "success": False,
            "message": f"{order_number} has nothing left to bill.",
        }), 400

    return jsonify({
        "success": True,
        "order_number": order["order_number"],
        "order_type": order["order_type"],
        "party_ledger_name": order["party_ledger_name"],
        "location_name": order["location_name"],
        "status": order["status"],
        "items": [{
            "order_item_id": item["id"],
            "item_name": item["item_name"],
            "quantity": item["pending_quantity"],
            "ordered_quantity": float(item["quantity"]),
            "billed_quantity": float(item["billed_quantity"] or 0),
            "unit_price": float(item["unit_price"] or 0),
        } for item in order["items"]],
    })
