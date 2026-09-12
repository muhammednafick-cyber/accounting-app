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
