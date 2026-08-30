"""Domain playbooks the assistant reads before it answers.

The model is good at English and bad at our chart of accounts. A skill is the
missing half: the accounting rules for one subject area, written once, in the
words of this database - which voucher type carries the figure, which side of
the ledger the party sits on, which column is the real amount.

Two consumers, one registry:

  * `picker_guidance` goes to the tool picker in `chat_router`, so a question
    about ageing lands on `outstanding_receivables` instead of falling through
    to free-form SQL.
  * `sql_guidance` goes to `ai_sql`, so the queries it does write use the same
    conventions the coded tools use, and agree with them to the penny.

Adding a subject area means appending one `Skill` to `SKILLS` - there is no
logic to change. Keep the guidance short: it is prepended to every prompt in
its area, and a rule the model skims past is worse than no rule.
"""
import re


class Skill:
    """One subject area: how to recognise it, and what to say about it."""

    # A word that names the subject outright ("vat", "receivable") settles what
    # the question is about. A supporting word ("due", "quarter", "period")
    # only leans that way, and several of them must not outvote one core word.
    CORE_WEIGHT = 3

    def __init__(self, name, core, triggers=(), tools=(), notes="", sql=""):
        self.name = name
        self.core = self._compile(core)
        # Whole words only - "vat" must not fire on "private", and a bare
        # "cost" must not drag the costing skill into every stock question.
        self.triggers = self._compile(triggers)
        self.tools = list(tools)
        self.notes = notes.strip()
        self.sql = sql.strip()

    @staticmethod
    def _compile(patterns):
        return [re.compile(r"\b(?:%s)\b" % p, re.I) for p in patterns]

    def score(self, question):
        """How strongly this question belongs to this skill."""
        return (self.CORE_WEIGHT * sum(1 for t in self.core if t.search(question))
                + sum(1 for t in self.triggers if t.search(question)))


# ============================================================
# Rules that hold everywhere
# ============================================================

# Not matched, always sent. These are the mistakes that produce a plausible
# number rather than an error, which makes them the expensive ones.
BASELINE = """
House rules for this database:
- A voucher's party line is the one ledger_entries row whose ledger is a
  debtor or creditor. The same voucher also carries income, inventory, VAT and
  COGS lines. Summing all of them, or summing vouchers.amount per party,
  double-counts.
- Sales figures come from Sales vouchers, purchases from Purchase vouchers.
  Receipt and Payment vouchers move money against those invoices; they are not
  sales or purchases and must never be added to them.
- Returns (Sales Return, Purchase Return, Credit Note, Debit Note) are stored
  as their own voucher types with positive amounts. A "net" figure has to
  subtract them explicitly; ignoring them overstates the total.
- Every date column is TEXT 'YYYY-MM-DD'. Compare and group as strings.
- Every table is filtered by company_id. There is no global view of the data.
""".strip()


# ============================================================
# The skills
# ============================================================

SKILLS = [

    Skill(
        "receivables_and_payables",
        core=[r"receivable[s]?", r"payable[s]?", r"ageing", r"aging",
              r"debtor[s]?", r"creditor[s]?", r"outstanding", r"overdue",
              r"unpaid",
              # "who owes us" is receivables; a bare "owe" is not - "how much
              # VAT do we owe" belongs to the tax skill.
              r"who owes?", r"owe[sd]? us", r"owing to us", r"owed to us"],
        triggers=[r"due", r"owe[sd]?", r"owing", r"collect(?:ion|ions)?",
                  r"credit days"],
        tools=["outstanding_receivables", "outstanding_payables",
               "customer_statement", "supplier_statement", "party_matching",
               "settlements_by_party"],
        notes="""
Ageing questions are already computed. "Who owes us", "overdue invoices",
"receivables ageing", "aged debtors" -> outstanding_receivables. The supplier
side -> outstanding_payables. Both take an optional period, which is read as
the as-at date, not a range.

One named party's open items -> customer_statement or supplier_statement.
Which invoice a payment was set against -> party_matching.
""",
        sql="""
Ageing:
- A party's balance is the running sum of its ledger_entries across every
  voucher type, not the Sales total. Debits increase a receivable, credits
  reduce it: SUM(CASE WHEN le.type='Debit' THEN le.amount ELSE -le.amount END).
- Receivables sit under group nature 'Assets', payables under 'Liabilities',
  so a payable balance comes out negative from that expression. Report the
  absolute value - the user asked how much is outstanding, not which side of
  the ledger it sits on.
- Drop parties whose balance rounds to zero; a settled account is not an
  outstanding one.
- Age from vouchers.due_date when it is present, otherwise vouchers.date.
""",
    ),

    Skill(
        "sales_analysis",
        core=[r"sales", r"sold", r"revenue", r"turnover", r"customer[s]?",
              r"client[s]?"],
        triggers=[r"selling", r"invoice[sd]?", r"buyer[s]?", r"billing"],
        tools=["sales_total", "sales_by_customer", "sales_by_item",
               "sales_by_month", "sales_by_location", "sales_by_cost_center",
               "top_customers", "voucher_register"],
        notes="""
"Total sales" -> sales_total. Split it by whatever the user named: customer ->
sales_by_customer, item/product -> sales_by_item, month/trend/monthly ->
sales_by_month, branch/location -> sales_by_location, department/cost centre ->
sales_by_cost_center. "Top/best/biggest customers" -> top_customers.

sales_total narrows to one customer or one item if the user named one, so
"sales to ABC Trading" is sales_total with ledger, not sales_by_customer.
""",
        sql="""
Sales:
- Per customer: Sales vouchers, join ledger_entries -> ledgers -> groups, keep
  le.type = 'Debit' AND g.nature = 'Assets' AND g.group_name NOT IN
  ('Inventory', 'Fixed Assets'), then SUM(le.amount). That leaves the debtor
  line and excludes the income, stock and VAT lines on the same invoice.
- Per item: SUM(item_entries.amount) on Sales vouchers, grouped by item_name.
  item_entries.amount is the line value; unit_price * quantity is not, once a
  line carries a discount.
- Company-wide with no grouping: SUM(vouchers.amount) WHERE voucher_type =
  'Sales' is correct, and only then.
- Monthly: GROUP BY LEFT(v.date, 7) and order by that column.
- vouchers.amount is the invoice total including VAT; the item and ledger
  lines are exclusive of it. Never mix the two in one figure.
""",
    ),

    Skill(
        "purchase_analysis",
        core=[r"purchase[sd]?", r"purchasing", r"supplier[s]?", r"vendor[s]?",
              r"procurement"],
        triggers=[r"bought", r"buying", r"bill[s]?"],
        tools=["purchase_total", "purchases_by_supplier", "purchases_by_item",
               "purchases_by_month", "top_suppliers"],
        notes="""
Mirror of the sales skill: purchase_total, then purchases_by_supplier,
purchases_by_item or purchases_by_month for the split the user named.
"Top/main suppliers" -> top_suppliers.
""",
        sql="""
Purchases:
- Per supplier: Purchase vouchers, le.type = 'Credit' AND g.nature =
  'Liabilities', SUM(le.amount). The creditor line is the credit side here -
  the opposite of the sales case.
- Per item: SUM(item_entries.amount) on Purchase vouchers.
""",
    ),

    Skill(
        "inventory_and_stock",
        core=[r"stock", r"inventory", r"item[s]?", r"product[s]?",
              r"closing (?:stock|inventory)", r"slow.?moving", r"dead stock",
              r"batch", r"expiry"],
        triggers=[r"goods", r"quantity", r"qty", r"valuation", r"warehouse",
                  r"reorder", r"movement"],
        tools=["item_stock", "closing_stock_value", "stock_movement",
               "stock_by_location", "stock_by_batch", "inventory_ageing",
               "slow_moving_items", "negative_stock", "no_sales_items",
               "stock_category_summary", "list_items", "item_master_details",
               "price_list", "vouchers_with_item"],
        notes="""
"How much of X do we have" -> item_stock. "Stock value / closing stock /
closing inventory / stock on hand" -> closing_stock_value. "In and out of X" ->
stock_movement.

closing_stock_value answers any date, not just today. If the user named a past
date or period ("closing inventory in 2024", "stock on hand as of 31-03-2025"),
pass it as the period - the report replays the movements to that date. Never
answer a back-dated stock question from the current figure. Per branch ->
stock_by_location; per batch or expiry -> stock_by_batch. "Not moving / no
sales / dead stock" -> slow_moving_items or no_sales_items. Stock showing
below zero -> negative_stock.

Which invoices carried an item -> vouchers_with_item, not stock_movement.
""",
        sql="""
Stock:
- inventory.stock_quantity and inventory.stock_value are TODAY's snapshot
  only. They are the right answer for "what do we have now" and the wrong
  answer for every past date.
- For stock as at a past date, read the last item_entries row on or before that
  date and take its running_qty / running_value - those columns carry the
  balance after each movement:
    SELECT ie.running_qty, ie.running_value FROM item_entries ie
    JOIN vouchers v ON v.voucher_number = ie.voucher_number
                   AND v.company_id = ie.company_id
    WHERE ie.company_id = ? AND ie.item_name = ? AND v.date <= '2024-12-31'
    ORDER BY v.date DESC, v.voucher_id DESC, ie.id DESC LIMIT 1
  Prefer the closing_stock_value tool, which already does this for every item.
- Do not sum item_entries to get a balance - the opening balances live in
  item_opening_balances, so a bare sum misses them.
- item_entries.type marks the direction of the line ('Debit' in, 'Credit'
  out). Quantity is always positive, so direction comes from that column and
  from the voucher type, never from the sign.
- item_entries.cogs_amount is the cost of a sold line. Gross profit per item is
  SUM(amount) - SUM(cogs_amount) over Sales vouchers.
""",
    ),

    Skill(
        "profit_and_performance",
        core=[r"profit", r"p&l", r"pnl", r"income statement",
              r"profitab(?:le|ility)", r"cogs", r"cost of (?:goods|sales)",
              r"margin"],
        triggers=[r"loss", r"gross", r"net (?:profit|income)", r"earning[s]?",
                  r"performance"],
        tools=["profit_and_loss", "net_profit", "item_profitability",
               "kpi_summary", "compare_periods", "fy_comparison",
               "expense_breakdown"],
        notes="""
"Are we profitable / P&L / income statement" -> profit_and_loss. A single
number -> net_profit. Per item or per product -> item_profitability. "How does
this year compare to last" -> compare_periods, or fy_comparison when the user
named financial years rather than dates.
""",
        sql="""
Profit:
- Income sits under group nature 'Income', expenses under 'Expenses'. Income
  ledgers carry credit balances and expense ledgers debit balances, so signed
  with CASE WHEN le.type='Debit' THEN le.amount ELSE -le.amount END, income
  comes out negative. Flip its sign before presenting it.
- Gross profit per item: SUM(ie.amount) - SUM(ie.cogs_amount) over Sales
  vouchers. Margin percent divides that by SUM(ie.amount) - guard against a
  zero denominator.
- Never derive net profit by subtracting purchases from sales. Purchases are
  stock, not cost of sales; use the Income and Expenses natures.
""",
    ),

    Skill(
        "vat_and_tax",
        core=[r"vat", r"tax", r"input vat", r"output vat", r"tax return",
              r"taxable"],
        triggers=[r"trn", r"zero.?rated", r"exempt"],
        tools=["vat_summary", "vat_detailed"],
        notes="""
"VAT for the quarter / how much VAT do we owe" -> vat_summary. Line by line ->
vat_detailed. Both take the period as a range.

Output VAT is charged on sales, input VAT is reclaimed on purchases, and net
VAT is output less input: positive means payable, negative means refundable.
Say which of the two it is - the sign alone is not an answer.
""",
        sql="""
VAT:
- Prefer the vat_summary tool. Only write SQL here if the user asked for a cut
  the report does not offer (by customer, by item, by branch).
- Rates live on inventory.vat_rate for goods; the VAT amount posted on a
  voucher is a ledger_entries line against the VAT ledger, not a column on
  vouchers.
- A ledger's tax registration number is ledgers.trn.
""",
    ),

    Skill(
        "cash_and_bank",
        core=[r"cash", r"bank", r"cash ?flow", r"cash book", r"bank book",
              r"petty cash", r"liquidity"],
        triggers=[r"balance[s]?", r"funds", r"receipt[s]?", r"payment[s]?"],
        tools=["cash_balance", "bank_balance", "cash_bank_book", "cash_flow",
               "ledger_statement", "settlements_by_party"],
        notes="""
"How much cash do we have" -> cash_balance; the bank equivalent ->
bank_balance. Movement through them over a period -> cash_bank_book. A proper
cash flow statement -> cash_flow.

A receipt is money in from a customer, a payment is money out to a supplier.
Neither is a sale or a purchase, and a question about "payments to ABC" is a
ledger_statement filtered to Payment vouchers, not purchases_by_supplier.
""",
        sql="""
Cash and bank:
- These are ledgers under nature 'Assets'. A balance is the opening balance
  from ledger_opening_balances plus the signed movement in ledger_entries -
  the movement alone is not the balance.
- Contra vouchers move money between cash and bank. They are not income or
  expenditure and must be excluded from any inflow/outflow analysis that is
  about trading.
""",
    ),

    Skill(
        "ledgers_and_statements",
        core=[r"ledger[s]?", r"statement", r"soa", r"day ?book", r"register",
              r"voucher[s]?", r"journal", r"audit"],
        triggers=[r"account[s]?", r"transactions?", r"entries", r"narration"],
        tools=["ledger_statement", "customer_statement", "supplier_statement",
               "ledger_balance", "all_ledger_balances", "list_vouchers",
               "voucher_details", "day_book", "voucher_register", "gl_dump",
               "last_vouchers", "audit_trail", "search_ledger"],
        notes="""
One account's transactions over a period -> ledger_statement. Everything
posted on a date -> day_book. One voucher by its number -> voucher_details. All
vouchers of a type -> voucher_register or list_vouchers. Who changed what ->
audit_trail.

If the user names an account you are unsure of, still pass it through exactly
as they typed it - the system resolves near-misses itself and will ask them to
choose. Never silently correct a name to one you find more plausible.
""",
        sql="""
Statements:
- A running balance needs the opening balance from ledger_opening_balances
  (with opening_balance_type telling you the side) before the period's
  entries. A statement that starts at zero is wrong.
- Order by v.date, then voucher_number, so same-day entries are stable.
""",
    ),

    Skill(
        "financial_statements",
        core=[r"trial balance", r"balance sheet", r"chart of accounts", r"coa",
              r"financial position", r"equity"],
        triggers=[r"group[s]?", r"nature", r"assets", r"liabilit(?:y|ies)",
                  r"capital", r"books"],
        tools=["trial_balance", "balance_sheet", "coa_balances", "list_groups",
               "ledgers_in_group", "fixed_asset_register"],
        notes="""
trial_balance, balance_sheet and coa_balances are as-at reports: the period, if
the user gives one, is a cut-off date rather than a range. Report them from the
tool - these are the figures the books are closed on, and a re-derived version
that disagrees by a rounding difference is worse than useless.
""",
        sql="""
Structure:
- ledgers.group_code -> groups.group_code -> groups.nature, which is one of
  Assets, Liabilities, Income, Expenses. groups.master_group_code rolls those
  up further.
- Assets and Expenses are debit-natured; Liabilities and Income are
  credit-natured. Present each on its natural side rather than showing a
  negative.
""",
    ),

    Skill(
        "periods_and_years",
        core=[r"financial year", r"fiscal year", r"fy", r"year to date", r"ytd",
              r"month.?to.?date"],
        triggers=[r"quarter", r"q[1-4]", r"last year", r"this year", r"period"],
        tools=["list_financial_years", "compare_periods", "fy_comparison"],
        notes="""
The company's financial year is not necessarily January to December - it starts
at company_settings.financial_year_start, and the defined years are in
financial_years. If the user says "FY 2024" or "this financial year", do not
translate it into calendar dates yourself; pass the phrase through as the
period and let the resolver apply the company's own calendar.

Two periods side by side -> compare_periods. Named financial years ->
fy_comparison.
""",
        sql="""
Periods:
- Never call EXTRACT, DATE_TRUNC or DATE_PART on a date column - they are TEXT
  and it will fail. Filter with plain string ranges, and group with
  LEFT(date, 4) for a year or LEFT(date, 7) for a month.
- For a financial year, read start_date and end_date from financial_years
  rather than assuming the year runs January to December.
""",
    ),

    Skill(
        "expenses_and_cost_centers",
        core=[r"expense[s]?", r"spend(?:ing)?", r"cost cent(?:er|re)s?",
              r"overhead[s]?"],
        triggers=[r"salar(?:y|ies)", r"rent", r"utilit(?:y|ies)",
                  r"department[s]?", r"branch(?:es)?", r"location[s]?"],
        tools=["expense_total", "expense_breakdown", "sales_by_cost_center",
               "sales_by_location", "list_cost_centers", "list_locations"],
        notes="""
"What did we spend" -> expense_total; "on what" -> expense_breakdown. Split by
department or branch -> sales_by_cost_center or sales_by_location, which read
the cost_center_code and location_name on the voucher.
""",
        sql="""
Expenses:
- Expense ledgers are those whose group nature is 'Expenses'. Sum them signed
  (debits positive, credits negative) so that a credit note against an expense
  reduces it.
- cost_center_code appears on both vouchers and ledger_entries; the entry-level
  one is the more specific and wins when both are set.
""",
    ),

    Skill(
        "masters_and_setup",
        core=[r"master[s]?", r"price list", r"selling price",
              r"company settings", r"fixed asset[s]?", r"depreciation",
              r"recurring"],
        triggers=[r"list", r"unit[s]?", r"user[s]?", r"permission[s]?",
                  r"currency"],
        tools=["list_ledgers", "list_items", "list_groups", "list_units",
               "list_locations", "list_cost_centers", "list_stock_groups",
               "price_list", "list_users", "company_settings",
               "fixed_asset_register", "recurring_vouchers",
               "ledger_master_details", "item_master_details", "party_details"],
        notes="""
A plain "list the X" is a master list, not a report: list_ledgers, list_items,
list_groups, list_units, list_locations, list_cost_centers. The full record for
one of them -> ledger_master_details, item_master_details or party_details.

Contact details, credit terms and TRN of a customer -> party_details.
""",
        sql="""
Masters:
- is_active marks a record as live. A "list of customers" means the active
  ones unless the user asked for everything.
- Never query users, user_permissions or system_settings. They are outside the
  allowed tables and hold credentials.
""",
    ),
]


# ============================================================
# Matching
# ============================================================

MAX_SKILLS = 2   # two playbooks is context; four is a wall the model skims.


def match(question, limit=MAX_SKILLS):
    """The skills that speak to this question, strongest first."""
    question = question or ""
    scored = [(s.score(question), s) for s in SKILLS]
    hits = [(n, s) for n, s in scored if n]
    hits.sort(key=lambda pair: (-pair[0], pair[1].name))
    return [s for _, s in hits[:limit]]


def _block(skills, attr, heading):
    parts = [getattr(s, attr) for s in skills if getattr(s, attr)]
    if not parts:
        return ""
    return "\n\n" + heading + "\n\n" + "\n\n".join(parts)


def picker_guidance(question):
    """Which tools this question's subject area is normally answered by."""
    from . import chat_permissions as P

    skills = match(question)
    if not skills:
        return ""

    lines = []
    for s in skills:
        # The catalogue the model chooses from is already filtered by
        # permission. Recommending a tool that was withheld would only send it
        # after a name it cannot return.
        allowed = [t for t in s.tools if P.can_use(t)]
        if allowed:
            lines.append(f"{s.name}: {', '.join(allowed)}")
    preferred = ""
    if lines:
        preferred = ("\n\nTools that usually answer this subject (still choose "
                     "the one that fits the actual question, and still reply "
                     "null if none of them do):\n" + "\n".join(lines))

    return _block(skills, "notes", "Notes on this subject area:") + preferred


def sql_guidance(question):
    """The accounting rules a query on this subject has to respect."""
    return (("\n\n" + BASELINE)
            + _block(match(question), "sql", "Rules for this subject area:"))


def names():
    """Every skill's name, for diagnostics and the settings screen."""
    return [s.name for s in SKILLS]
