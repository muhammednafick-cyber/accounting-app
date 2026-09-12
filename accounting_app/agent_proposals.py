"""The one thing an agent may do besides read: suggest a voucher.

Kept out of `chat_toolkit` on purpose. That module is provably free of write
statements, a test enforces it, and that proof is what makes read-only access
defensible. Proposing writes a row - to a queue of suggestions, never to the
books - so it lives here instead and the proof stays intact.

Nothing in this module posts anything. It validates a suggestion enough to give
the agent useful feedback, describes it in words a person can check, and files
it. The posting happens only when someone presses Approve, and then through the
ordinary voucher path.
"""
from database.agent_proposals_db import create_proposal

MAX_LINES = 200

# Only types that are purely ledger entries. A Purchase or a Sales voucher also
# moves stock, and this tool carries no item lines - so proposing one produced a
# voucher whose ledgers moved while its inventory did not, with the cost landing
# in a purchase account instead of Inventory. Balanced, and wrong.
#
# An allowlist rather than a blocklist: a voucher type added to the application
# later should have to be considered here, not silently inherit permission to be
# posted without its items.
LEDGER_ONLY_TYPES = {
    "Payment", "Receipt", "Contra", "Journal",
    "Expense", "Service Income", "Service Income Return",
}

# What to say instead, so the answer is useful rather than just a refusal.
STOCK_TYPES_ADVICE = (
    "{kind} vouchers move stock as well as money, and this tool can only "
    "propose ledger entries - a {kind} posted through it would record the cost "
    "but receive none of the goods. Enter it on the {kind} screen, or through "
    "the import queue for a whole invoice."
)


class ProposalRejected(Exception):
    """The suggestion is malformed; tell the agent why so it can correct it."""


def _number(value, label):
    try:
        amount = float(value)
    except (TypeError, ValueError):
        raise ProposalRejected(f"{label} must be a number, got {value!r}.")
    if amount != amount or amount in (float("inf"), float("-inf")):
        raise ProposalRejected(f"{label} must be a real number.")
    if amount < 0:
        raise ProposalRejected(f"{label} cannot be negative; use the other side "
                               "of the entry instead.")
    return round(amount, 2)


def validate_voucher_proposal(payload, company_id):
    """Check a suggestion before filing it, and return it tidied.

    Not a substitute for the checks that run on posting - those happen again,
    on the real path, when a person approves. This exists so an agent finds out
    immediately that it has, say, unbalanced an entry, instead of a person
    discovering it on the approval screen.
    """
    voucher_type = (payload.get("voucher_type") or "").strip()
    if not voucher_type:
        raise ProposalRejected("A voucher type is required, e.g. Payment.")
    if voucher_type.title() not in LEDGER_ONLY_TYPES:
        raise ProposalRejected(STOCK_TYPES_ADVICE.format(kind=voucher_type))
    voucher_type = voucher_type.title()

    date = (payload.get("date") or "").strip()
    if not date:
        raise ProposalRejected("A date is required, as YYYY-MM-DD.")

    entries = payload.get("ledger_entries") or []
    if not isinstance(entries, list) or not entries:
        raise ProposalRejected("At least one ledger entry is required.")
    if len(entries) > MAX_LINES:
        raise ProposalRejected(
            f"{len(entries)} lines is more than this accepts ({MAX_LINES}). "
            "A file that large belongs in the import screens, where it can be "
            "reviewed as a whole.")

    known = {row["ledger_name"] for row in _ledgers(company_id)}
    tidied, debit, credit = [], 0.0, 0.0
    for index, entry in enumerate(entries, start=1):
        name = (entry.get("ledger_name") or "").strip()
        if not name:
            raise ProposalRejected(f"Line {index} has no ledger name.")
        if known and name not in known:
            raise ProposalRejected(
                f"Line {index}: there is no ledger called {name!r}. "
                "Use list_ledgers to see the exact names.")
        side = (entry.get("type") or "").strip().title()
        if side not in ("Debit", "Credit"):
            raise ProposalRejected(
                f"Line {index}: type must be Debit or Credit, got {side!r}.")
        amount = _number(entry.get("amount"), f"Line {index} amount")
        if not amount:
            raise ProposalRejected(f"Line {index} has no amount.")
        if side == "Debit":
            debit += amount
        else:
            credit += amount
        tidied.append({"ledger_name": name, "type": side, "amount": amount})

    if abs(debit - credit) > 0.05:
        raise ProposalRejected(
            f"The entry does not balance: debits {debit:,.2f} against credits "
            f"{credit:,.2f}. Every voucher must have equal sides.")

    return {
        "voucher_type": voucher_type,
        "date": date,
        "narration": (payload.get("narration") or "").strip()[:500],
        "ledger_entries": tidied,
        "totals": {"debit": round(debit, 2), "credit": round(credit, 2)},
    }


def _ledgers(company_id):
    try:
        from database import get_ledgers
        return get_ledgers(company_id=company_id) or []
    except Exception:
        # An unreadable ledger list must not stop a proposal being filed; the
        # posting path checks the names again anyway.
        return []


def describe(proposal):
    """One line a person can check at a glance."""
    total = proposal["totals"]["debit"]
    lines = len(proposal["ledger_entries"])
    narration = proposal.get("narration") or ""
    text = (f"{proposal['voucher_type']} dated {proposal['date']}, "
            f"{total:,.2f} across {lines} line{'s' if lines != 1 else ''}")
    if narration:
        text += f" - {narration[:80]}"
    return text


def propose_voucher(payload, company_id, user_id):
    """File a suggested voucher for a person to approve. Posts nothing."""
    checked = validate_voucher_proposal(payload, company_id)
    summary = describe(checked)
    proposal_id = create_proposal(company_id, user_id, "voucher", summary, checked)
    return proposal_id, summary

# ---------------------------------------------------------------- purchases

# A purchase moves stock as well as money, and the two must agree. Rather than
# ask an agent to assemble the accounting - Inventory debit, VAT line, party
# credit, in the right directions - it supplies only what an invoice actually
# says: who sold what, how many, at what price. The double entry is built here,
# the same shape the import queue builds, so it cannot be got wrong.

MAX_ITEM_LINES = 100
DEFAULT_VAT_PERCENT = 5.0
INVENTORY_LEDGER = "Inventory"
INPUT_VAT_LEDGER = "Input VAT 5%"


def _items_for(company_id):
    try:
        from database import get_items
        return {row["name"] for row in (get_items(company_id=company_id) or [])}
    except Exception:
        return set()


def _ledger_names(company_id):
    return {row["ledger_name"] for row in _ledgers(company_id)}


def validate_purchase_proposal(payload, company_id):
    """Turn what an invoice says into the voucher it should become."""
    supplier = (payload.get("supplier") or "").strip()
    if not supplier:
        raise ProposalRejected("The supplier's ledger name is required.")

    known_ledgers = _ledger_names(company_id)
    if known_ledgers and supplier not in known_ledgers:
        raise ProposalRejected(
            f"There is no ledger called {supplier!r}. Use search_ledger to find "
            "the supplier's exact name, and if they are new, create them in the "
            "application first - an agent must not invent a supplier.")

    date = (payload.get("date") or "").strip()
    if not date:
        raise ProposalRejected("The purchase date is required, as YYYY-MM-DD.")
    invoice_date = (payload.get("invoice_date") or "").strip() or date
    invoice_ref = (payload.get("invoice_number") or "").strip()
    if not invoice_ref:
        raise ProposalRejected(
            "The supplier's invoice number is required on a purchase.")

    rows = payload.get("items") or []
    if not isinstance(rows, list) or not rows:
        raise ProposalRejected(
            "At least one item line is required. A purchase with no items "
            "records the cost but receives none of the goods.")
    if len(rows) > MAX_ITEM_LINES:
        raise ProposalRejected(
            f"{len(rows)} item lines is more than this accepts "
            f"({MAX_ITEM_LINES}). Use the import queue for an invoice that "
            "large, where it can be reviewed as a whole.")

    known_items = _items_for(company_id)
    items, goods_total = [], 0.0
    for index, row in enumerate(rows, start=1):
        name = (row.get("item_name") or "").strip()
        if not name:
            raise ProposalRejected(f"Item line {index} has no item name.")
        if known_items and name not in known_items:
            raise ProposalRejected(
                f"Item line {index}: there is no stock item called {name!r}. "
                "Use list_items to find the exact name. If it is genuinely new, "
                "create it in the application first - an agent must not add "
                "items to the master.")
        quantity = _number(row.get("quantity"), f"Item line {index} quantity")
        if not quantity:
            raise ProposalRejected(f"Item line {index} has no quantity.")
        rate = _number(row.get("rate"), f"Item line {index} rate")
        amount = round(quantity * rate, 2)
        goods_total += amount
        items.append({
            "item_name": name,
            "quantity": quantity,
            "unit_price": rate,
            "amount": amount,
            # A purchase debits Inventory; the agent does not get to choose.
            "ledger_name": INVENTORY_LEDGER,
            "type": "Debit",
        })

    goods_total = round(goods_total, 2)
    vat_percent = payload.get("vat_percent")
    vat_percent = (DEFAULT_VAT_PERCENT if vat_percent is None
                   else _number(vat_percent, "VAT percent"))
    vat_amount = round(goods_total * vat_percent / 100.0, 2)
    total = round(goods_total + vat_amount, 2)

    # If the invoice total is known, it is the authority: a mismatch means the
    # lines were misread, and posting it would put a wrong figure in the books.
    stated = payload.get("invoice_total")
    if stated is not None:
        stated = _number(stated, "Invoice total")
        if abs(stated - total) > 0.05:
            raise ProposalRejected(
                f"The lines come to {total:,.2f} ({goods_total:,.2f} plus "
                f"{vat_amount:,.2f} VAT) but the invoice total given is "
                f"{stated:,.2f}. Re-read the invoice rather than posting a "
                "figure that does not match it.")

    ledger_entries = []
    if vat_amount:
        ledger_entries.append({"ledger_name": INPUT_VAT_LEDGER,
                               "type": "Debit", "amount": vat_amount})
    ledger_entries.append({"ledger_name": supplier, "type": "Credit",
                           "amount": total})

    return {
        "voucher_type": "Purchase",
        "date": date,
        "supplier": supplier,
        "invoice_number": invoice_ref,
        "invoice_date": invoice_date,
        "narration": (payload.get("narration")
                      or f"Purchase Invoice {invoice_ref} from {supplier}")[:500],
        "item_entries": items,
        "ledger_entries": ledger_entries,
        "totals": {"goods": goods_total, "vat": vat_amount, "debit": total,
                   "credit": total},
    }


def describe_purchase(proposal):
    lines = len(proposal["item_entries"])
    return (f"Purchase {proposal['invoice_number']} from "
            f"{proposal['supplier']} dated {proposal['date']}, "
            f"{proposal['totals']['debit']:,.2f} across {lines} "
            f"item line{'s' if lines != 1 else ''}")


def propose_purchase(payload, company_id, user_id):
    """File a suggested purchase, items included. Posts nothing."""
    checked = validate_purchase_proposal(payload, company_id)
    summary = describe_purchase(checked)
    proposal_id = create_proposal(company_id, user_id, "purchase", summary, checked)
    return proposal_id, summary
