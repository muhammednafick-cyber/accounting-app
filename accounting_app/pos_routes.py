"""Point of sale.

A till screen for over-the-counter selling: scan or search, take payment, print
a receipt. It posts nothing of its own - a sale becomes an ordinary Sales
voucher, and a cash or card sale adds the matching Receipt - so POS takings
appear in the same ledgers, VAT return and stock as anything typed in by hand.
"""
from datetime import datetime

from flask import (Blueprint, flash, jsonify, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from database import get_current_company_id
from database.inventory_db import find_item_by_scan, search_items_for_pos

pos_bp = Blueprint("pos_bp", __name__)


def pos_hidden():
    """Has this user had the till switched off in User Management?

    Separate from hiding the dashboard: a counter operator usually wants the
    POS and no dashboard, a bookkeeper the other way round. Admins always keep
    it, as with every other access flag.
    """
    return (getattr(current_user, "hide_pos", False)
            and not getattr(current_user, "is_admin", False))

# Where the money lands. "credit" leaves it on the customer's account.
PAYMENT_MODES = ("cash", "card", "credit")

VAT_LEDGER = "Output VAT 5%"


def _ledgers_by_group(group_code, company_id):
    from database import get_ledgers
    return get_ledgers(group_code=group_code, company_id=company_id) or []


def _default_sales_ledger(company_id):
    """The income ledger POS sales are booked to (first Sales-group ledger)."""
    ledgers = _ledgers_by_group("G001", company_id)
    return ledgers[0]["ledger_name"] if ledgers else "Sales"


@pos_bp.route("/pos")
@login_required
def pos():
    company_id = get_current_company_id()
    from database import get_company_settings

    if pos_hidden():
        flash("The Point of Sale is not enabled for your user.", "error")
        return redirect(url_for("dashboard_bp.dashboard"))

    company = get_company_settings(company_id=company_id) or {}
    if not company.get("inventory_applicable"):
        flash("POS sells stock items, so Inventory must be enabled for this "
              "company.", "error")
        return redirect(url_for("dashboard_bp.dashboard"))

    return render_template(
        "pos/pos.html",
        customers=_ledgers_by_group("G007", company_id),
        cash_ledgers=_ledgers_by_group("G005", company_id),
        bank_ledgers=_ledgers_by_group("G006", company_id),
        sales_ledgers=_ledgers_by_group("G001", company_id),
        default_sales_ledger=_default_sales_ledger(company_id),
        vat_applicable=bool(company.get("vat_applicable")),
        username=current_user.username,
    )


@pos_bp.route("/api/pos/scan")
@login_required
def scan():
    """One scanned (or typed) code -> one item. What the scanner gun hits."""
    if pos_hidden():
        return jsonify({"success": False, "message": "POS is not enabled for "
                                                     "your user."}), 403
    code = request.args.get("code", "")
    item = find_item_by_scan(code)
    if not item:
        return jsonify({"success": False,
                        "message": f"Nothing found for '{code}'"}), 404
    return jsonify({"success": True, "item": item})


@pos_bp.route("/api/pos/search")
@login_required
def search():
    """Typed search, for when the barcode will not read."""
    if pos_hidden():
        return jsonify({"success": False, "message": "POS is not enabled for "
                                                     "your user."}), 403
    return jsonify({"success": True,
                    "items": search_items_for_pos(request.args.get("q", ""))})


@pos_bp.route("/pos/sale", methods=["POST"])
@login_required
def sale():
    """Post one till sale: a Sales voucher, plus a Receipt if it was paid now."""
    from database import add_voucher

    if pos_hidden():
        return jsonify({"success": False, "message": "POS is not enabled for "
                                                     "your user."}), 403

    company_id = get_current_company_id()
    data = request.get_json(silent=True) or {}
    mode = (data.get("payment_mode") or "cash").lower()
    if mode not in PAYMENT_MODES:
        return jsonify({"success": False,
                        "message": f"Unknown payment mode '{mode}'"}), 400

    # Over the counter there is usually nobody to name, so the customer is
    # optional: a cash or card sale with no customer is posted straight against
    # the cash or bank ledger, which is what a till sale actually is. Only a
    # credit sale has to say who owes the money.
    customer = (data.get("customer") or "").strip()

    payment_ledger = (data.get("payment_ledger") or "").strip()
    if mode in ("cash", "card") and not payment_ledger:
        return jsonify({"success": False, "message":
                        "Choose where the money was received."}), 400
    if mode == "credit" and not customer:
        return jsonify({"success": False, "message":
                        "A credit sale needs a customer - somebody has to owe "
                        "it. Choose one, or take payment as cash or card."}), 400

    sales_ledger = (data.get("sales_ledger")
                    or _default_sales_ledger(company_id))

    lines = data.get("lines") or []
    item_entries = []
    net_total = vat_total = 0.0
    for line in lines:
        name = (line.get("item_name") or "").strip()
        if not name:
            continue
        try:
            quantity = float(line.get("quantity") or 0)
            rate = float(line.get("unit_price") or 0)
            vat_percent = float(line.get("vat_percent") or 0)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message":
                            f"'{name}' has an invalid quantity or price."}), 400
        if quantity <= 0:
            return jsonify({"success": False, "message":
                            f"Quantity must be more than 0 for '{name}'."}), 400

        amount = round(quantity * rate, 2)
        vat = round(amount * vat_percent / 100, 2)
        net_total += amount
        vat_total += vat
        item_entries.append({
            "item_name": name,
            "quantity": quantity,
            "unit_price": round(rate, 2),
            "amount": amount,
            "ledger_name": sales_ledger,
            "type": "Credit",
        })

    if not item_entries:
        return jsonify({"success": False,
                        "message": "There is nothing in the sale."}), 400

    net_total = round(net_total, 2)
    vat_total = round(vat_total, 2)
    gross_total = round(net_total + vat_total, 2)

    # Same shape the Sales voucher screen builds: the gross is debited, the
    # items take the net and VAT the difference. With no customer named, the
    # debit is the till itself - one voucher, no debtor, nothing to settle.
    debit_ledger = customer or payment_ledger
    ledger_entries = [{"ledger_name": debit_ledger, "amount": gross_total,
                       "type": "Debit"}]
    if vat_total:
        ledger_entries.append({"ledger_name": VAT_LEDGER, "amount": vat_total,
                               "type": "Credit"})

    date = datetime.now().strftime("%Y-%m-%d")
    narration = data.get("narration") or "POS sale"

    try:
        voucher_number = add_voucher(
            "Sales", date, ledger_entries, item_entries,
            narration=narration, company_id=company_id)
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    # Paid at the till against a named customer: settle it straight away, so
    # their account is not left carrying a balance that was actually paid in
    # cash. With no customer the money is already in the till ledger, so there
    # is nothing to settle and no Receipt to raise.
    receipt_number = None
    if mode in ("cash", "card") and customer:
        try:
            receipt_number = add_voucher(
                "Receipt", date,
                [{"ledger_name": payment_ledger, "amount": gross_total,
                  "type": "Debit"},
                 {"ledger_name": customer, "amount": gross_total,
                  "type": "Credit"}],
                [], narration=f"{narration} ({voucher_number})",
                company_id=company_id)
        except Exception as exc:
            # The sale itself is posted; only the settlement failed. Say so
            # plainly rather than pretending the sale did not happen.
            return jsonify({
                "success": True, "voucher_number": voucher_number,
                "receipt_number": None,
                "message": (f"Sale {voucher_number} posted, but the payment "
                            f"could not be recorded: {exc}. Enter a Receipt "
                            f"for it manually."),
                "receipt_url": url_for("pos_bp.receipt",
                                       voucher_number=voucher_number),
            })

    return jsonify({
        "success": True,
        "voucher_number": voucher_number,
        "receipt_number": receipt_number,
        "total": gross_total,
        "message": f"Sale {voucher_number} posted.",
        "receipt_url": url_for("pos_bp.receipt", voucher_number=voucher_number),
    })


@pos_bp.route("/pos/receipt/<voucher_number>")
@login_required
def receipt(voucher_number):
    """The 80mm till receipt. `?auto=1` opens the print dialogue itself."""
    from database import get_company_settings, get_voucher_details

    if pos_hidden():
        flash("The Point of Sale is not enabled for your user.", "error")
        return redirect(url_for("dashboard_bp.dashboard"))

    voucher = get_voucher_details(voucher_number)
    if not voucher:
        flash(f"Voucher {voucher_number} not found.", "error")
        return redirect(url_for("pos_bp.pos"))

    return render_template(
        "pos/receipt.html",
        voucher=voucher,
        company=get_company_settings() or {},
        auto_print=request.args.get("auto") == "1",
        served_by=current_user.username,
    )
