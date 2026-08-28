"""Statement of Account: what a customer owes us, or we owe a supplier.

Listed invoice by invoice, with what has been settled against each one. A
"settlement" here is an allocation recorded through the Settlement / Matching
screen, which attaches an amount to an individual ledger entry.

One accounting caution shapes the whole report: a list of unpaid invoices is
not the same as the amount owed. If a payment has been received but not yet
matched to an invoice, the invoices still look open. So the unmatched payments
are reported alongside, and the two are reconciled against the ledger's own
balance - if the statement does not tie back, the report says so rather than
quietly presenting the larger number.
"""
from datetime import datetime, timedelta

from .config import get_connection
from .company_db import get_current_company_id


def _parse_iso(value):
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return datetime.today()


def _add_days(date_str, days):
    if not days:
        return date_str
    try:
        return (_parse_iso(date_str) + timedelta(days=int(days))).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return date_str


def _days_overdue(due_str, as_of_obj):
    """Days past due, or 0 when it is not yet due."""
    if not due_str:
        return 0
    try:
        return max(0, (as_of_obj - _parse_iso(due_str)).days)
    except (ValueError, TypeError):
        return 0


def _bucket(days):
    if days <= 0:
        return "Not due"
    if days <= 30:
        return "1-30"
    if days <= 60:
        return "31-60"
    if days <= 90:
        return "61-90"
    if days <= 180:
        return "91-180"
    return "180+"


BUCKETS = ["Not due", "1-30", "31-60", "61-90", "91-180", "180+"]


def get_party_ledgers(company_id=None, kind=None):
    """Customers and suppliers to choose from.

    `kind` filters to 'Customer' or 'Supplier'; None returns both.
    """
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT l.ledger_name, COALESCE(g.group_name, ''),
                   COALESCE(g.nature, ''), COALESCE(l.closing_balance, 0)
            FROM ledgers l
            JOIN groups g ON g.group_code = l.group_code
                         AND g.company_id = l.company_id
            WHERE l.company_id = %s
              AND g.nature IN ('Assets', 'Liabilities')
              AND g.group_name NOT IN ('Inventory', 'Fixed Assets',
                                       'Cash Accounts', 'Bank Accounts',
                                       'Duties And Taxes', 'Capital Account')
              AND COALESCE(l.is_active, 1) = 1
            ORDER BY g.group_name, l.ledger_name
        """, (company_id,))
        parties = []
        for name, group_name, nature, balance in cursor.fetchall():
            party_kind = "Supplier" if nature == "Liabilities" else "Customer"
            if kind and party_kind != kind:
                continue
            parties.append({
                "ledger_name": name,
                "group_name": group_name,
                "kind": party_kind,
                "balance": round(float(balance or 0), 2),
            })
        return parties
    except Exception as exc:
        print(f"Error in get_party_ledgers: {exc}")
        return []
    finally:
        conn.close()


def get_statement_of_account(ledger_name, as_of_date=None, company_id=None,
                             include_settled=False):
    """Outstanding invoices for one party, with original / matched / remaining.

    Returns None when the ledger does not exist.
    """
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id or not ledger_name:
        return None

    as_of = str(as_of_date)[:10] if as_of_date else datetime.today().strftime("%Y-%m-%d")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT l.ledger_code, l.ledger_name, COALESCE(g.group_name, ''),
                   COALESCE(g.nature, ''), COALESCE(l.credit_days, 0),
                   COALESCE(l.phone, ''), COALESCE(l.email, ''), COALESCE(l.trn, ''),
                   COALESCE(l.address, ''), COALESCE(l.contact_person, '')
            FROM ledgers l
            LEFT JOIN groups g ON g.group_code = l.group_code
                              AND g.company_id = l.company_id
            WHERE l.company_id = %s AND l.ledger_name = %s
        """, (company_id, ledger_name))
        head = cursor.fetchone()
        if not head:
            return None
        (ledger_code, name, group_name, nature,
         credit_days, phone, email, trn, address, contact_person) = head
        credit_days = int(credit_days or 0)

        # Which side carries the invoices: a receivable is invoiced by debiting
        # the customer, a payable by crediting the supplier. Everything on the
        # other side is a payment, refund or credit note.
        if nature == "Liabilities":
            invoice_side, party_kind = "Credit", "Supplier"
        else:
            invoice_side, party_kind = "Debit", "Customer"

        cursor.execute("""
            SELECT le.id, v.date, COALESCE(v.due_date, ''), v.voucher_number,
                   v.voucher_type, COALESCE(v.original_invoice_ref, ''),
                   COALESCE(v.narration, ''), le.amount, le.type
            FROM ledger_entries le
            JOIN vouchers v ON v.voucher_number = le.voucher_number
                           AND v.company_id = le.company_id
            WHERE le.company_id = %s AND le.ledger_name = %s AND v.date <= %s
            ORDER BY v.date, v.voucher_id, le.id
        """, (company_id, ledger_name, as_of))
        entries = cursor.fetchall()

        # What has been matched against each entry
        cursor.execute("""
            SELECT sa.ledger_entry_id, s.settlement_number, sa.assigned_amount
            FROM settlement_allocations sa
            JOIN settlements s ON s.id = sa.settlement_id
                              AND s.company_id = sa.company_id
            WHERE sa.company_id = %s AND s.settlement_date <= %s
        """, (company_id, as_of))
        matched_total, matched_refs = {}, {}
        for entry_id, settlement_no, amount in cursor.fetchall():
            matched_total[entry_id] = matched_total.get(entry_id, 0.0) + float(amount or 0)
            matched_refs.setdefault(entry_id, set()).add(settlement_no)

        as_of_obj = _parse_iso(as_of)

        # One chronological listing. Invoices add to the balance, credits that
        # have not been matched subtract from it, and the running balance ends
        # at what is actually owed - so the statement reads top to bottom and
        # its last line is the figure the party should recognise.
        rows = []
        ageing = {b: 0.0 for b in BUCKETS}
        total_original = total_matched = total_remaining = 0.0
        total_unallocated = 0.0

        for (entry_id, date_str, due_str, voucher_no, voucher_type,
             invoice_ref, narration, amount, side) in entries:
            amount = float(amount or 0)
            matched = round(min(matched_total.get(entry_id, 0.0), amount), 2)
            remaining = round(amount - matched, 2)
            if remaining <= 0.005 and not include_settled:
                continue

            is_invoice = side == invoice_side
            due = (due_str or _add_days(date_str, credit_days)) if is_invoice else ""
            overdue = _days_overdue(due, as_of_obj) if is_invoice else 0

            rows.append({
                "date": date_str,
                "due_date": due,
                "voucher_number": voucher_no,
                "voucher_type": voucher_type,
                "reference": invoice_ref,
                "description": narration,
                "is_invoice": is_invoice,
                "original_amount": round(amount, 2),
                "matched_amount": matched,
                # Signed, so the running balance is a straight addition.
                "remaining_amount": remaining if is_invoice else -remaining,
                "days_overdue": overdue,
                "bucket": _bucket(overdue) if is_invoice else "",
                "settlement_refs": ", ".join(sorted(matched_refs.get(entry_id, []))),
                "status": ("Open" if matched <= 0.005
                           else ("Settled" if remaining <= 0.005 else "Part settled")),
            })

            if is_invoice:
                ageing[_bucket(overdue)] += remaining
                total_original += amount
                total_matched += matched
                total_remaining += remaining
            else:
                total_unallocated += remaining

        # Date order, with invoices ahead of credits on the same day so a
        # payment is seen to clear the invoice it follows.
        rows.sort(key=lambda r: (str(r["date"]), 0 if r["is_invoice"] else 1,
                                 str(r["voucher_number"])))
        running = 0.0
        for row in rows:
            running = round(running + row["remaining_amount"], 2)
            row["balance"] = running

        # Kept for the Excel sheet and anything else that wants them apart.
        unallocated = [
            {"date": r["date"], "voucher_number": r["voucher_number"],
             "voucher_type": r["voucher_type"], "description": r["description"],
             "amount": -r["remaining_amount"]}
            for r in rows if not r["is_invoice"]
        ]
        total_unallocated = round(total_unallocated, 2)
        net_outstanding = round(total_remaining - total_unallocated, 2)

        # Reconcile against the ledger's own balance.
        from .reports_db import get_ledger_transactions
        _, balance = get_ledger_transactions(ledger_name, to_date=as_of,
                                             company_id=company_id)
        balance_abs = round(abs(float(balance or 0)), 2)

        return {
            "ledger_name": name,
            "ledger_code": ledger_code,
            "group_name": group_name,
            "party_kind": party_kind,
            "credit_days": credit_days,
            "phone": phone,
            "email": email,
            "trn": trn,
            "address": address,
            "contact_person": contact_person,
            "as_of_date": as_of,
            "rows": rows,
            "unallocated": unallocated,
            "ageing": {b: round(ageing[b], 2) for b in BUCKETS},
            "totals": {
                "original": round(total_original, 2),
                "matched": round(total_matched, 2),
                "remaining": round(total_remaining, 2),
                "unallocated": total_unallocated,
                "net_outstanding": net_outstanding,
            },
            "ledger_balance": balance_abs,
            "reconciles": abs(net_outstanding - balance_abs) < 0.05,
            "difference": round(net_outstanding - balance_abs, 2),
        }
    except Exception as exc:
        print(f"Error in get_statement_of_account: {exc}")
        import traceback
        traceback.print_exc()
        return None
    finally:
        conn.close()
