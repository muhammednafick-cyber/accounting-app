"""Turning the words in a question into arguments a coded tool can run.

Nothing here calls a language model. Periods, names and numbers are resolved
against the company's own data, and anything ambiguous comes back as a
question for the user rather than a guess - a silently wrong ledger match is
worse than one extra click.
"""
import datetime
import difflib
import re

from database.config import get_connection
from database.company_db import get_current_company_id


# ============================================================
# Periods
# ============================================================

MONTHS = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11,
    'december': 12, 'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6,
    'jul': 7, 'aug': 8, 'sept': 9, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

DATE_FORMATS = ("%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y",
                "%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d",
                "%d-%m-%y", "%d/%m/%y")


def _month_end(year, month):
    if month == 12:
        return datetime.date(year, 12, 31)
    return datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)


def parse_single_date(text):
    """A single date written any of the ways users write them, or None."""
    if not text:
        return None
    text = str(text).strip()
    low = text.lower()
    today = datetime.date.today()
    if low in ("today", "now"):
        return today
    if low == "yesterday":
        return today - datetime.timedelta(days=1)
    if low == "tomorrow":
        return today + datetime.timedelta(days=1)
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # "31 December 2024" / "31 Dec 24"
    m = re.match(r'^(\d{1,2})\s+([a-z]+)\.?\s+(\d{2,4})$', low)
    if m and m.group(2) in MONTHS:
        year = int(m.group(3))
        year += 2000 if year < 100 else 0
        try:
            return datetime.date(year, MONTHS[m.group(2)], int(m.group(1)))
        except ValueError:
            return None
    return None


def active_financial_year(company_id=None):
    """(start, end, label) of the company's active FY, or None."""
    company_id = company_id or get_current_company_id()
    if not company_id:
        return None
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT fy_code, start_date, end_date FROM financial_years "
            "WHERE company_id = %s AND COALESCE(is_active, 0) = 1 LIMIT 1",
            (company_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        code = row[0] if not hasattr(row, 'keys') else row['fy_code']
        start = row[1] if not hasattr(row, 'keys') else row['start_date']
        end = row[2] if not hasattr(row, 'keys') else row['end_date']
        return str(start), str(end), f"FY {code}"
    except Exception as exc:
        print(f"[chat] active_financial_year: {exc}")
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def financial_year_by_code(code, company_id=None):
    company_id = company_id or get_current_company_id()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT fy_code, start_date, end_date FROM financial_years "
            "WHERE company_id = %s AND LOWER(fy_code) = LOWER(%s) LIMIT 1",
            (company_id, code),
        )
        row = cur.fetchone()
        if not row:
            return None
        return str(row[1]), str(row[2]), f"FY {row[0]}"
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _fmt(d):
    return d.strftime("%Y-%m-%d") if isinstance(d, (datetime.date, datetime.datetime)) else d


def parse_period(text, company_id=None):
    """(start_str, end_str, label) for a period phrase. None start/end = open.

    Returns None when the phrase names no period at all, so callers can tell
    "the user said nothing about dates" from "the user said all time".
    """
    if not text:
        return None
    raw = str(text).strip()
    q = raw.lower().strip(' ?.!')
    today = datetime.date.today()

    if q in ("all time", "all-time", "alltime", "ever", "inception", "since inception",
             "to date", "all"):
        return None, None, "all time"

    simple = {
        "today": (today, today),
        "yesterday": (today - datetime.timedelta(days=1),) * 2,
        "this week": (today - datetime.timedelta(days=today.weekday()), today),
        "this month": (today.replace(day=1), today),
        "this year": (today.replace(month=1, day=1), today),
        "mtd": (today.replace(day=1), today),
        "ytd": (today.replace(month=1, day=1), today),
        "wtd": (today - datetime.timedelta(days=today.weekday()), today),
    }
    q_norm = q.replace('_', ' ')
    if q_norm in simple:
        s, e = simple[q_norm]
        return _fmt(s), _fmt(e), q_norm

    if q_norm == "last week":
        end = today - datetime.timedelta(days=today.weekday() + 1)
        return _fmt(end - datetime.timedelta(days=6)), _fmt(end), "last week"
    if q_norm == "last month":
        end = today.replace(day=1) - datetime.timedelta(days=1)
        return _fmt(end.replace(day=1)), _fmt(end), "last month"
    if q_norm == "last year":
        year = today.year - 1
        return f"{year}-01-01", f"{year}-12-31", str(year)

    # "last 7 days", "past 3 months", "last 2 years"
    m = re.match(r'^(?:last|past|previous)\s+(\d+)\s+(day|week|month|year)s?$', q_norm)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = {"day": 1, "week": 7, "month": 30, "year": 365}[unit] * n
        start = today - datetime.timedelta(days=days)
        return _fmt(start), _fmt(today), f"last {n} {unit}{'s' if n != 1 else ''}"

    # Quarters: "q1 2024", "2024 q3", "this quarter", "last quarter"
    if q_norm in ("this quarter", "qtd", "current quarter"):
        qn = (today.month - 1) // 3 + 1
        start = datetime.date(today.year, 3 * (qn - 1) + 1, 1)
        return _fmt(start), _fmt(today), f"Q{qn} {today.year}"
    if q_norm == "last quarter":
        qn = (today.month - 1) // 3 + 1
        year = today.year
        qn -= 1
        if qn == 0:
            qn, year = 4, year - 1
        start = datetime.date(year, 3 * (qn - 1) + 1, 1)
        return _fmt(start), _fmt(_month_end(year, 3 * qn)), f"Q{qn} {year}"
    m = re.match(r'^(?:q([1-4])\s*(\d{4})|(\d{4})\s*q([1-4]))$', q_norm)
    if m:
        qn = int(m.group(1) or m.group(4))
        year = int(m.group(2) or m.group(3))
        start = datetime.date(year, 3 * (qn - 1) + 1, 1)
        return _fmt(start), _fmt(_month_end(year, 3 * qn)), f"Q{qn} {year}"

    # Financial years: "fy 2023-24", "financial year", "current fy"
    m = re.match(r'^(?:fy|financial year|fin year)\s*([\d/\-]+)?$', q_norm)
    if m:
        code = m.group(1)
        if code:
            found = financial_year_by_code(code, company_id)
            if found:
                return found
            # No matching master record: fall back to an April-March year, the
            # convention this app's FY codes follow.
            m2 = re.match(r'^(\d{4})(?:[/\-](\d{2,4}))?$', code)
            if m2:
                y = int(m2.group(1))
                return f"{y}-04-01", f"{y + 1}-03-31", f"FY {y}-{str(y + 1)[2:]}"
        return active_financial_year(company_id) or (None, None, "all time")
    if q_norm in ("current fy", "this fy", "current financial year",
                  "this financial year"):
        return active_financial_year(company_id) or (None, None, "all time")

    # A bare month name -> that month of the current year
    if q_norm in MONTHS:
        mn = MONTHS[q_norm]
        return (_fmt(datetime.date(today.year, mn, 1)),
                _fmt(_month_end(today.year, mn)),
                f"{q_norm.title()} {today.year}")

    # "August 2024" / "aug-2024"
    m = re.match(r'^([a-z]+)[\s\-/]+(\d{4})$', q_norm)
    if m and m.group(1) in MONTHS:
        mn, year = MONTHS[m.group(1)], int(m.group(2))
        return (_fmt(datetime.date(year, mn, 1)), _fmt(_month_end(year, mn)),
                f"{m.group(1).title()} {year}")

    # "2024"
    if re.match(r'^\d{4}$', q_norm):
        y = int(q_norm)
        return f"{y}-01-01", f"{y}-12-31", q_norm

    # "2024-07" / "2024/07"
    m = re.match(r'^(\d{4})[\-/](\d{1,2})$', q_norm)
    if m:
        year, mn = int(m.group(1)), int(m.group(2))
        if 1 <= mn <= 12:
            return (_fmt(datetime.date(year, mn, 1)), _fmt(_month_end(year, mn)),
                    f"{year}-{mn:02d}")

    # "X to Y", "from X till Y", "between X and Y"
    m = re.match(r'^(?:from\s+|between\s+)?(.+?)\s+(?:to|till|until|through|-|and)\s+(.+)$',
                 q_norm)
    if m:
        s = parse_single_date(m.group(1))
        e = parse_single_date(m.group(2))
        if s and e:
            return _fmt(s), _fmt(e), f"{_fmt(s)} to {_fmt(e)}"

    # "since April", "from 01-01-2024", "after 2024-03-01"
    m = re.match(r'^(?:since|from|after)\s+(.+)$', q_norm)
    if m:
        s = parse_single_date(m.group(1))
        if not s and m.group(1) in MONTHS:
            s = datetime.date(today.year, MONTHS[m.group(1)], 1)
        if s:
            return _fmt(s), _fmt(today), f"since {_fmt(s)}"

    # "up to 31-12-2024", "as of today", "before 2024-06-30"
    m = re.match(r'^(?:up ?to|until|till|as of|as at|before|by)\s+(.+)$', q_norm)
    if m:
        e = parse_single_date(m.group(1))
        if e:
            return None, _fmt(e), f"up to {_fmt(e)}"

    single = parse_single_date(raw)
    if single:
        return _fmt(single), _fmt(single), _fmt(single)

    return None


PERIOD_PATTERN = re.compile(
    r'\b('
    r'all[\s-]?time|inception|'
    r'today|yesterday|'
    r'(?:this|last|past|previous|current)\s+(?:week|month|year|quarter|fy|financial\s+year)|'
    r'(?:last|past|previous)\s+\d+\s+(?:day|week|month|year)s?|'
    r'mtd|ytd|qtd|wtd|'
    r'q[1-4]\s*\d{4}|\d{4}\s*q[1-4]|'
    r'fy\s*[\d/\-]+|financial\s+year\s*[\d/\-]*|'
    r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{4}|'
    r'(?:from|between|since|after)\s+[\d./\-]{4,10}(?:\s+(?:to|till|until|through|and)\s+[\d./\-]{4,10})?|'
    r'[\d./\-]{6,10}\s+(?:to|till|until|through)\s+[\d./\-]{6,10}|'
    r'(?:as\s+(?:of|at)|up\s?to|until|till|before|on)\s+[\d./\-]{6,10}|'
    r'(?:in\s+|for\s+|during\s+)?\b(?:19|20)\d{2}\b|'
    r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b|'
    r'[\d]{1,4}[./\-][\d]{1,2}[./\-][\d]{2,4}'
    r')\b',
    re.IGNORECASE,
)


def extract_period(text, company_id=None):
    """Pull a period out of a free sentence.

    Returns (period_tuple_or_None, remaining_text). The remainder has the date
    words removed so name matching is not confused by them.
    """
    if not text:
        return None, text
    best = None
    for m in PERIOD_PATTERN.finditer(text):
        phrase = m.group(1).strip()
        cleaned = re.sub(r'^(?:in|for|during|on)\s+', '', phrase, flags=re.I)
        parsed = parse_period(cleaned, company_id)
        if parsed is not None:
            # The longest match wins: "August 2024" beats "August".
            if best is None or len(phrase) > len(best[1]):
                best = (parsed, phrase, m.span())
    if not best:
        return None, text
    parsed, phrase, (a, b) = best
    remainder = (text[:a] + " " + text[b:])
    remainder = re.sub(r'\s+', ' ', remainder).strip()
    remainder = re.sub(r'\s+(on|for|in|of|at|during|as\s+of|as\s+at|from)$', '',
                       remainder, flags=re.I).strip()
    return parsed, remainder


# ============================================================
# Names
# ============================================================

class Ambiguous(Exception):
    """More than one master record fits, and the user has to choose."""

    def __init__(self, kind, term, options):
        super().__init__(f"Ambiguous {kind}: {term}")
        self.kind = kind
        self.term = term
        self.options = options


class NotFound(Exception):
    def __init__(self, kind, term, suggestions=None):
        super().__init__(f"No {kind} matching '{term}'")
        self.kind = kind
        self.term = term
        self.suggestions = suggestions or []


STOP_WORDS = {
    'the', 'a', 'an', 'of', 'for', 'to', 'from', 'show', 'me', 'give', 'get',
    'what', 'is', 'was', 'please', 'account', 'ledger', 'balance', 'total',
}

# Words that name a *category* of record, never a particular one. "purchases
# by vendors" is a breakdown, not a supplier called "vendors" - and left to
# fuzzy matching, "vendors" scores 0.63 against "Inventory" and would be
# answered confidently about the wrong account.
GENERIC_TERMS = {
    'customer', 'customers', 'client', 'clients', 'buyer', 'buyers',
    'debtor', 'debtors', 'supplier', 'suppliers', 'vendor', 'vendors',
    'creditor', 'creditors', 'party', 'parties', 'account', 'accounts',
    'ledger', 'ledgers', 'item', 'items', 'product', 'products', 'stock',
    'goods', 'everyone', 'everybody', 'all', 'each', 'every', 'anyone',
    'month', 'months', 'location', 'locations', 'branch', 'branches',
    'category', 'categories', 'group', 'groups', 'them', 'these', 'those',
}

# A single fuzzy candidate is only accepted outright when it is a close
# spelling of what was typed. Below this the user is asked.
FUZZY_ACCEPT = 0.82


class GenericTerm(Exception):
    """The user named a category, not a particular record."""

    def __init__(self, kind, term):
        super().__init__(f"'{term}' is a category, not one {kind}")
        self.kind = kind
        self.term = term


def is_generic(term):
    words = [w for w in re.split(r'\W+', str(term or '').lower()) if w]
    return bool(words) and all(w in GENERIC_TERMS for w in words)


def _fetch_column(sql, params):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [(r[0] if not hasattr(r, 'keys') else list(r)[0]) for r in cur.fetchall()]
    except Exception as exc:
        print(f"[chat] resolver query failed: {exc}")
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def all_ledger_names(company_id, group_codes=None):
    if group_codes:
        placeholders = ','.join(['%s'] * len(group_codes))
        return _fetch_column(
            f"SELECT ledger_name FROM ledgers WHERE company_id = %s "
            f"AND group_code IN ({placeholders}) ORDER BY ledger_name",
            tuple([company_id] + list(group_codes)),
        )
    return _fetch_column(
        "SELECT ledger_name FROM ledgers WHERE company_id = %s ORDER BY ledger_name",
        (company_id,),
    )


def all_item_names(company_id):
    return _fetch_column(
        "SELECT name FROM inventory WHERE company_id = %s ORDER BY name",
        (company_id,),
    )


def all_location_names(company_id):
    return _fetch_column(
        "SELECT location_name FROM locations WHERE company_id = %s ORDER BY location_name",
        (company_id,),
    )


def all_cost_center_names(company_id):
    return _fetch_column(
        "SELECT center_name FROM cost_centers WHERE company_id = %s ORDER BY center_name",
        (company_id,),
    )


def all_group_names(company_id):
    return _fetch_column(
        "SELECT group_name FROM groups WHERE company_id = %s ORDER BY group_name",
        (company_id,),
    )


def match_name(term, candidates, kind="record", max_options=8):
    """Best single match for a user-typed name.

    Exact, then case-insensitive, then whole-word containment, then fuzzy.
    Several equally good containment hits raise Ambiguous so the user picks.
    """
    if not term:
        return None
    term = str(term).strip().strip('"\'')
    if not term:
        return None
    if is_generic(term):
        raise GenericTerm(kind, term)
    if not candidates:
        raise NotFound(kind, term)

    for c in candidates:
        if c == term:
            return c
    low = term.lower()
    exact_ci = [c for c in candidates if c.lower() == low]
    if len(exact_ci) == 1:
        return exact_ci[0]
    if exact_ci:
        raise Ambiguous(kind, term, exact_ci[:max_options])

    starts = [c for c in candidates if c.lower().startswith(low)]
    if len(starts) == 1:
        return starts[0]

    contains = [c for c in candidates if low in c.lower()]
    if len(contains) == 1:
        return contains[0]
    if contains:
        # Several real accounts contain the words the user typed. Guessing the
        # shortest one produces a confident answer about the wrong party, so
        # ask instead - one extra click beats a wrong figure.
        raise Ambiguous(kind, term, sorted(contains, key=len)[:max_options])

    # Word overlap: "abc trading" should still find "ABC Trading Co LLC"
    words = {w for w in re.split(r'\W+', low) if w and w not in STOP_WORDS}
    if words:
        scored = []
        for c in candidates:
            cw = {w for w in re.split(r'\W+', c.lower()) if w}
            hit = len(words & cw)
            if hit:
                scored.append((hit / len(words), -len(c), c))
        scored.sort(reverse=True)
        if scored and scored[0][0] >= 0.6:
            top = [s for s in scored if s[0] == scored[0][0]]
            if len(top) == 1:
                return top[0][2]
            raise Ambiguous(kind, term, [s[2] for s in top[:max_options]])

    # Spelling similarity is the last resort and the least reliable, so a lone
    # candidate is only taken when it is genuinely close. A weak single match
    # is offered as a suggestion instead of being applied silently.
    close = difflib.get_close_matches(term, candidates, n=max_options, cutoff=0.6)
    if close:
        best = difflib.SequenceMatcher(None, term.lower(), close[0].lower()).ratio()
        if len(close) == 1 and best >= FUZZY_ACCEPT:
            return close[0]
        raise NotFound(kind, term, close)

    # Nothing matched. Offer the nearest names by spelling, topped up with any
    # that share a word - master names are often long ("Nafi-7DAYS CHOCLATE
    # 55G"), which sinks whole-string similarity even for an obvious near miss.
    suggestions = difflib.get_close_matches(term, candidates, n=5, cutoff=0.4)
    for word in sorted(words, key=len, reverse=True) if words else []:
        if len(word) < 4:
            continue
        for c in candidates:
            if len(suggestions) >= 6:
                break
            near = difflib.get_close_matches(word, re.split(r'\W+', c.lower()),
                                             n=1, cutoff=0.7)
            if near and c not in suggestions:
                suggestions.append(c)
    raise NotFound(kind, term, suggestions)


def resolve_ledger(term, company_id, group_codes=None):
    return match_name(term, all_ledger_names(company_id, group_codes), "ledger")


def resolve_item(term, company_id):
    return match_name(term, all_item_names(company_id), "item")


def resolve_location(term, company_id):
    return match_name(term, all_location_names(company_id), "location")


def resolve_cost_center(term, company_id):
    return match_name(term, all_cost_center_names(company_id), "cost centre")


def resolve_group(term, company_id):
    return match_name(term, all_group_names(company_id), "group")


# ============================================================
# Voucher types, numbers, limits
# ============================================================

VOUCHER_TYPES = {
    'sales': 'Sales', 'sale': 'Sales', 'invoice': 'Sales', 'invoices': 'Sales',
    'purchase': 'Purchase', 'purchases': 'Purchase', 'bill': 'Purchase',
    'sales return': 'Sales Return', 'sale return': 'Sales Return',
    'credit note': 'Credit Note', 'purchase return': 'Purchase Return',
    'debit note': 'Debit Note', 'payment': 'Payment', 'payments': 'Payment',
    'receipt': 'Receipt', 'receipts': 'Receipt', 'journal': 'Journal',
    'contra': 'Contra', 'expense': 'Expense', 'expenses': 'Expense',
    'service income': 'Service Income',
}


def resolve_voucher_type(term):
    if not term:
        return None
    low = str(term).strip().lower()
    if low in VOUCHER_TYPES:
        return VOUCHER_TYPES[low]
    for key, val in VOUCHER_TYPES.items():
        if key in low:
            return val
    return str(term).strip().title()


VOUCHER_NUMBER_RE = re.compile(r'\b([A-Za-z]{2,8}[-/]\d{2,})\b')


def extract_voucher_number(text):
    m = VOUCHER_NUMBER_RE.search(text or "")
    return m.group(1).upper().replace('/', '-') if m else None


def extract_limit(text, default=None):
    m = re.search(r'\b(?:top|first|last|latest|best|highest|bottom|lowest)\s+(\d{1,4})\b',
                  (text or "").lower())
    if m:
        return int(m.group(1))
    m = re.search(r'\b(\d{1,4})\s+(?:top|latest|recent)\b', (text or "").lower())
    if m:
        return int(m.group(1))
    return default


def extract_amount_bounds(text):
    """(min_amount, max_amount) from phrases like 'above 5000', 'under 200'."""
    low = (text or "").lower().replace(',', '')
    lo = hi = None
    m = re.search(r'\b(?:above|over|more than|greater than|exceeding|>=?)\s*([\d.]+)', low)
    if m:
        lo = float(m.group(1))
    m = re.search(r'\b(?:below|under|less than|<=?)\s*([\d.]+)', low)
    if m:
        hi = float(m.group(1))
    m = re.search(r'\bbetween\s*([\d.]+)\s*and\s*([\d.]+)', low)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
    return lo, hi
