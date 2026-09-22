"""Reading a purchase invoice into a proposal.

The pieces already existed and had never been joined: the vision extractor
reads a supplier, an invoice number and its line items, and `propose_purchase`
wants exactly that. Between them sits the one hard part - the invoice says
"COCA COLA 330ML x24" and the company's masters say something else, and a
purchase proposed against an item that does not exist is how stock silently
goes missing.

So names are matched here, and anything that cannot be matched with confidence
is reported rather than guessed. A supplier or item the company has not got is
a thing for a person to create, never for this to invent.

Nothing is posted. The result is a proposal, opened and saved by a person on
the purchase screen like any other.
"""
import difflib
import re

# How close a name has to be before it is treated as the same thing. Set high
# deliberately: a wrong match here is a purchase booked against the wrong item,
# which is worse than being asked to confirm.
MATCH_CUTOFF = 0.82

# Above this, one candidate is taken outright; below it the user is asked.
CONFIDENT = 0.93


def _clean(name):
    """A name reduced to what is worth comparing.

    Masters here are prefixed per company ("Nafi-ALMARAI...") and invoices are
    written in every case and punctuation going. Only the legal-form suffixes
    come off: words like "trading" and "general" look like noise but are often
    the only thing telling two suppliers apart.
    """
    text = str(name or "").strip().lower()
    text = re.sub(r"^[a-z]{2,6}-", "", text)          # the company prefix
    # Item masters here carry their own code on the end - "7DAYS CAKE - 86880"
    # - and no invoice prints that, so it would count against every line.
    text = re.sub(r"\s*-\s*[a-z0-9]{4,}\s*$", "", text)
    text = re.sub(r"\b(l\.?l\.?c|llc|ltd|inc|est|w\.?l\.?l)\b", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _score(target, candidate):
    """How alike two cleaned names are, 0 to 1.

    Containment counts for a lot - a master carries extra words the invoice
    leaves off - but only in proportion to how much of the longer name it
    accounts for. Scoring every containment alike is what once made "Gulf
    Trading" confidently match "GULF ARK INTERNATIONAL": one shared word out
    of three, treated as near-certainty.
    """
    if not target or not candidate:
        return 0.0
    if target == candidate:
        return 1.0

    target_words = target.split()
    candidate_words = candidate.split()
    shared = set(target_words) & set(candidate_words)
    if shared:
        # Every word of the shorter name present in the longer one, and the
        # longer one not padded out with much else.
        coverage = len(shared) / max(len(target_words), len(candidate_words))
        letters = difflib.SequenceMatcher(None, target, candidate).ratio()
        return max(coverage * 0.95, letters)
    return difflib.SequenceMatcher(None, target, candidate).ratio()


def match_name(wanted, candidates):
    """(best match, confidence) for one name against the real ones.

    Returns (None, score) when nothing is close enough, and deliberately
    reports a tie as not-confident: two suppliers called Almarai means the
    person picks, not this.
    """
    target = _clean(wanted)
    if not target or not candidates:
        return None, 0.0

    scored = sorted(((_score(target, _clean(c)), c) for c in candidates),
                    key=lambda pair: pair[0], reverse=True)
    best_score, best = scored[0]
    if best_score < MATCH_CUTOFF:
        return None, best_score

    # A near-tie is an ambiguity, not a winner. Reported below CONFIDENT so
    # the caller asks instead of choosing.
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best_score - runner_up < 0.05 and best_score < 1.0:
        return best, min(best_score, CONFIDENT - 0.01)
    return best, best_score


def _ledger_names(company_id):
    from .agent_proposals import _ledger_names as names

    return sorted(names(company_id))


def _item_names(company_id):
    from .agent_proposals import _items_for

    return sorted(_items_for(company_id))


def _vendor_aliases(company_id, vendor_name):
    """What this supplier calls each item, as the company has recorded it.

    The application already keeps this mapping (Inventory Master > Vendor Item
    Mappings) for exactly this problem. It is the company's own answer, so it
    is consulted before any guessing: a mapping someone entered beats a
    similarity score every time.
    """
    from database import get_items
    from database.config import get_connection

    by_code = {row["item_code"]: row["name"]
               for row in (get_items(company_id=company_id) or [])}
    aliases = {}
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT vendor_name, vendor_item_name, app_item_code "
            "FROM vendor_item_mappings WHERE company_id = %s", (company_id,))
        rows = cursor.fetchall()
    except Exception:
        return {}
    finally:
        conn.close()

    wanted = _clean(vendor_name)
    for row_vendor, vendor_item, code in rows:
        # Mappings are per supplier; one supplier's "CC330" is not another's.
        if wanted and _clean(row_vendor) and _clean(row_vendor) != wanted:
            continue
        name = by_code.get(code)
        if name and vendor_item:
            aliases[_clean(vendor_item)] = name
    return aliases


class InvoiceNotUsable(Exception):
    """Read, but not turned into a proposal - and the message says why."""


def invoice_to_proposal(file_bytes, filename, company_id, user_id,
                        supplier_hint=None):
    """Read a purchase invoice and file it as a proposal.

    Raises InvoiceNotUsable, with a message meant for the person who uploaded
    it, when the invoice cannot be matched to this company's masters.
    """
    from .agent_proposals import ProposalRejected, propose_purchase
    from .ai_invoice_services import (InvoiceExtractionError,
                                      extract_invoice_data_vision)

    try:
        data = extract_invoice_data_vision(file_bytes, filename, "Purchase",
                                           company_id=company_id)
    except InvoiceExtractionError as exc:
        raise InvoiceNotUsable("I could not read that invoice: %s" % exc)

    items = data.get("items") or []
    if not items:
        raise InvoiceNotUsable(
            "I read the file but found no line items on it. If it is a scan, a "
            "clearer photo usually helps; if it is an expense rather than "
            "goods, enter it on the Expense screen.")

    # ---------------------------------------------------------- the supplier
    ledgers = _ledger_names(company_id)
    supplier_raw = supplier_hint or data.get("vendor_name") or ""
    supplier, confidence = match_name(supplier_raw, ledgers)
    if not supplier:
        from .agent_proposals import _did_you_mean

        raise InvoiceNotUsable(
            "The invoice is from %r, and no supplier here matches that name."
            % (supplier_raw or "an unnamed party")
            + (_did_you_mean(supplier_raw, ledgers)
               or " Create the supplier first, or tell me the exact name to "
                 "use."))
    if confidence < CONFIDENT:
        raise InvoiceNotUsable(
            "The invoice says %r. The closest supplier here is %r, but I am "
            "not sure enough to use it - tell me the exact name if that is "
            "the one." % (supplier_raw, supplier))

    # ------------------------------------------------------------- the items
    known_items = _item_names(company_id)
    aliases = _vendor_aliases(company_id, supplier)
    lines, unmatched, unsure = [], [], []
    for raw in items:
        description = (raw.get("description") or "").strip()
        mapped = aliases.get(_clean(description))
        if mapped:
            # The company said this is that item. Nothing to second-guess.
            lines.append({
                "item_name": mapped,
                "quantity": raw.get("quantity") or 0,
                "rate": raw.get("unit_rate") or 0,
            })
            continue
        matched, score = match_name(description, known_items)
        if not matched:
            unmatched.append(description or "(unnamed line)")
            continue
        if score < CONFIDENT:
            unsure.append((description, matched))
            continue
        lines.append({
            "item_name": matched,
            "quantity": raw.get("quantity") or 0,
            "rate": raw.get("unit_rate") or 0,
        })

    if unmatched or unsure:
        # Refusing the whole invoice rather than proposing the lines that did
        # match: a purchase missing three of its ten lines still looks like a
        # complete one on the screen, and the stock would be wrong.
        parts = ["I read the invoice, but it cannot be entered yet."]
        if unmatched:
            parts.append("These lines are not items in this company: "
                         + ", ".join(repr(u) for u in unmatched[:10])
                         + (" and %d more" % (len(unmatched) - 10)
                            if len(unmatched) > 10 else "")
                         + ". Create them under Items - or, if you already "
                         "stock them under another name, record what this "
                         "supplier calls them under Inventory Master > Vendor "
                         "Item Mappings, and they will be recognised next "
                         "time. Then upload again.")
        if unsure:
            parts.append("These are close to an existing item but not close "
                         "enough for me to choose: "
                         + ", ".join("%r (did you mean %r?)" % (a, b)
                                     for a, b in unsure[:5]) + ".")
        raise InvoiceNotUsable(" ".join(parts))

    payload = {
        "supplier": supplier,
        "date": data.get("invoice_date") or "",
        "invoice_number": data.get("invoice_number") or "",
        "invoice_date": data.get("invoice_date") or "",
        "items": lines,
        "narration": "Purchase from %s against invoice %s" % (
            supplier, data.get("invoice_number") or "(no number)"),
    }
    if not payload["date"]:
        raise InvoiceNotUsable(
            "I could not find the invoice date on that document. Tell me the "
            "date and I will use it.")
    if not payload["invoice_number"]:
        raise InvoiceNotUsable(
            "I could not find the supplier's invoice number, and a purchase "
            "needs one. Tell me the number and I will use it.")

    try:
        proposal_id, summary = propose_purchase(payload, company_id, user_id)
    except ProposalRejected as rejected:
        raise InvoiceNotUsable(str(rejected))

    return {
        "proposal_id": proposal_id,
        "summary": summary,
        "supplier": supplier,
        "invoice_number": payload["invoice_number"],
        "lines": len(lines),
    }
