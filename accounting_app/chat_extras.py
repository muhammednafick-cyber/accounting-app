"""What an answer offers beyond its table: where to go next.

Shared by both assistants, so an answer looks and behaves the same whichever
one produced it.

  * Clickable cells - a ledger in a result opens its statement, a voucher
    number opens the voucher. Each is a question the rules already understand
    ("statement of X", "show voucher Y"), asked as if typed, so it works the
    same on the web and in the phone app and costs no AI call.
  * Suggested next questions - a few one-tap follow-ups, chosen from what the
    answer was, never from a model.
  * A chart, where the rows are a series worth seeing as one.
  * The thumbs, which feed the insights page.

Everything here escapes what it prints. Cell values come from the company's
own data, and some of that - narrations, party names - arrived in supplier
files nobody here wrote.
"""
import html
import json
import re

from . import chat_resolver as R

# ------------------------------------------------------------------ escaping


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


# The formatting report summaries use on purpose. Nothing with attributes is
# let back: a tag that can carry an attribute can carry an event handler.
ALLOWED_TAGS = ("b", "i", "em", "strong", "small", "br")
_ALLOWED = re.compile(r"&lt;(/?)(" + "|".join(ALLOWED_TAGS) + r")\s*/?&gt;", re.I)


def safe(text):
    """A summary with its own formatting kept and everything else inert.

    Summaries are written by the report code with <b> around the figures, but
    they also carry values from the company's data - a party name, a narration.
    Escaping all of it and then restoring only bare formatting tags keeps the
    bold and drops anything a supplier's file could have smuggled in.
    """
    escaped = html.escape("" if text is None else str(text), quote=False)
    return _ALLOWED.sub(lambda m: "<%s%s>" % (m.group(1), m.group(2).lower()),
                        escaped)


# ------------------------------------------------------------ clickable cells

# Column headings whose values name something with its own screen. Matched on
# the heading, lower-cased, so every report that shows a ledger gets the link.
LEDGER_HEADINGS = {"ledger", "ledger name", "party", "customer", "supplier",
                   "account", "debtor", "creditor", "vendor"}
VOUCHER_HEADINGS = {"voucher", "voucher no", "voucher no.", "voucher number",
                    "voucher_number", "number", "ref"}


def link_kind(heading):
    key = str(heading or "").strip().lower()
    if key in LEDGER_HEADINGS:
        return "ledger"
    if key in VOUCHER_HEADINGS:
        return "voucher"
    return None


def cell_link(kind, text):
    """A cell as a question-asking link, or None when it should stay text."""
    value = str(text or "").strip()
    if not value or value in ("-", "—", "Total", "TOTAL"):
        return None
    if kind == "ledger":
        question = "statement of " + value
    elif kind == "voucher":
        # Only a real voucher number: a column called "Ref" can hold anything.
        # The whole number is asked for, not the shortened form the resolver
        # extracts - REC-000009 can exist in more than one financial year.
        if not R.extract_voucher_number(value):
            return None
        question = "show voucher " + value
    else:
        return None
    return ("<button type='button' class='rv-ask rv-cell-link' "
            f"data-value='{esc(question)}' title='{esc(question)}'>{esc(value)}</button>")


# ------------------------------------------------------- suggested next steps

# A total is one number; its months are the obvious next thing to see.
BY_MONTH = {
    "sales_total": "sales by month",
    "sales_by_customer": "sales by month",
    "sales_by_item": "sales by month",
    "purchase_total": "purchases by month",
    "purchases_by_supplier": "purchases by month",
    "purchases_by_item": "purchases by month",
}

MAX_FOLLOWUPS = 3


def follow_ups(tool_name, result, asked=""):
    """Up to three one-tap next questions for this answer.

    Built from the tool and its result, not from a model: each is a phrasing
    the rules already answer, so tapping one is instant and free.
    """
    from .chat_toolkit import TOOLS

    tool = TOOLS.get(tool_name)
    if tool is None:
        return []
    asked = (asked or "").lower()
    out = []

    if "period" in tool.param_names:
        if "last year" not in asked:
            out.append("what about last year")
        if "this month" not in asked and "month" not in asked:
            out.append("what about this month")

    by_month = BY_MONTH.get(tool_name)
    if by_month and "month" not in asked:
        out.append(by_month)

    rows = result.get("rows") or []
    if "limit" in tool.param_names and len(rows) >= 5 and "top" not in asked:
        out.append("top 10")

    # Most specific first, and never more than fit on one line of a phone.
    return out[:MAX_FOLLOWUPS]


def render_follow_ups(questions):
    if not questions:
        return ""
    chips = "".join(
        f"<button type='button' class='rv-ask rv-followup' data-value='{esc(q)}'>"
        f"{esc(q[0].upper() + q[1:])}</button>" for q in questions)
    return f"<div class='rv-followups'>{chips}</div>"


# -------------------------------------------------------------------- charts

MIN_POINTS, MAX_POINTS = 3, 36
TREND_WORDS = re.compile(r"month|date|period|week|year|day", re.I)


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").replace(",", "").strip()
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return float(text)
    return None


def chart_spec(tool_name, result):
    """A chart for this result, or None when a chart would say nothing.

    Two shapes are worth drawing: a series over time (a line) and a ranking
    (bars). A statement or a list of vouchers is neither, and gets no chart.
    """
    columns = result.get("columns") or []
    rows = result.get("rows") or []
    if len(columns) < 2 or not (MIN_POINTS <= len(rows) <= MAX_POINTS):
        return None

    name = (tool_name or "").lower()
    over_time = "month" in name or bool(TREND_WORDS.search(str(columns[0])))
    ranking = name.startswith("top_") or "_by_" in name
    if not (over_time or ranking):
        return None
    # Statements, registers and day books are lists of events, not a series.
    if any(word in name for word in ("statement", "register", "book", "vouchers",
                                     "details", "list_", "audit")):
        return None

    # The first column that is a number in every row is the one to plot.
    value_col = None
    for idx in range(1, len(columns)):
        if all(_number(r[idx]) is not None for r in rows if idx < len(r)):
            value_col = idx
            break
    if value_col is None:
        return None

    return {
        "type": "line" if over_time else "bar",
        "label": str(columns[value_col]),
        "labels": [str(r[0]) for r in rows],
        "values": [_number(r[value_col]) for r in rows],
    }


def render_chart(spec):
    if not spec:
        return ""
    return ("<div class='rv-chart'><canvas data-chart='"
            + esc(json.dumps(spec)) + "' aria-label='"
            + esc(spec.get("label")) + " chart' role='img'></canvas></div>")


# ------------------------------------------------------------------ feedback


def render_feedback(question, tool_name):
    return ("<div class='rv-feedback' "
            f"data-q='{esc(question)}' data-tool='{esc(tool_name)}'>"
            "<span>Was this right?</span>"
            "<button type='button' data-vote='up' aria-label='Yes, this was right'>&#128077;</button>"
            "<button type='button' data-vote='down' aria-label='No, this was wrong'>&#128078;</button>"
            "</div>")
