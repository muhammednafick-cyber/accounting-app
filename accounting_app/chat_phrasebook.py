"""The words people actually use, mapped to the report that answers them.

`chat_router.match_rules` grew one hand-written regex at a time, which made it
good at the phrasings someone thought of and blind to the rest: "total sales"
worked, "how much did we sell" did not, and "income statement" was quietly
answered with a ledger statement because the word "statement" matched first.

This module is the vocabulary, kept apart from the routing logic. Two jobs:

  * `match` - a question whose wording we recognise outright, answered with no
    model call at all. Checked BEFORE the older rules, so a specific phrase
    ("cash flow statement") beats a generic one ("statement of <name>").
  * `suggest` - a question we do not recognise, scored against the same
    vocabulary to offer the nearest few as clickable options, rather than
    spending an AI call or telling the user no.

Every entry carries a `label`: the question as we would phrase it. Labels are
what the user is offered, and clicking one sends it straight back to us, so a
label must itself be a phrase this module matches. `test_chat_phrasebook`
enforces that - a label that does not round-trip is a dead button.

Adding a phrasing means adding a pattern to the relevant `Intent`. Adding a
scenario means adding one `Intent` to `INTENTS`. Order matters only in that
the first match wins, so the specific sits above the general.
"""
import re

from . import chat_resolver as R


# A name inside a question: "statement of ABC Trading", "balance of Cash".
# Deliberately loose - the resolver checks it against the real ledgers and asks
# the user when it is not sure, so a wrong guess here costs nothing.
NAME = r"(?P<ledger>[a-z0-9][a-z0-9 &.,'\-/()]*?)"
ITEM = r"(?P<item>[a-z0-9][a-z0-9 &.,'\-/()]*?)"
LOC = r"(?P<location>[a-z0-9][a-z0-9 &.,'\-/()]*?)"


class Intent:
    """One scenario: the ways it is asked, and the report that answers it."""

    def __init__(self, tool, label, patterns, args=None, keywords=""):
        self.tool = tool
        self.label = label
        self.patterns = [re.compile(p, re.I) for p in patterns]
        self.args = args or {}
        # Extra words for scoring only - things a user might say that are too
        # vague to match on, but that point here when nothing else fits.
        self.keywords = set((keywords or "").split())

    def match(self, text):
        for pattern in self.patterns:
            m = pattern.match(text)
            if not m:
                continue
            captured = {k: v.strip() for k, v in (m.groupdict() or {}).items()
                        if v and v.strip()}
            # "opening balance" is not an account called "opening", and
            # "todays entries" is not one called "todays". A pattern that
            # captures one of these has reached past its subject.
            if any(v.lower() in RESERVED_NAMES for v in captured.values()):
                continue
            args = dict(self.args)
            args.update(captured)
            return args
        return None

    @property
    def vocabulary(self):
        """Every word this intent knows, for scoring an unmatched question."""
        words = set(re.findall(r"[a-z]+", self.label.lower()))
        words |= self.keywords
        for pattern in self.patterns:
            words |= set(re.findall(r"[a-z]{3,}", pattern.pattern.lower()))
        return words - PATTERN_NOISE


# Words that are never the name of an account or an item, however much a
# pattern would like them to be.
RESERVED_NAMES = {
    "opening", "closing", "trial", "balance", "account", "accounts", "an",
    "the", "a", "my", "our", "todays", "today", "today's", "recent", "latest",
    "last", "previous", "company", "negative", "minus", "dead", "slow", "old",
    "total", "all", "stock", "inventory", "item", "items", "ledger", "this",
    "that", "one", "an item", "the item", "an account", "the account",
    "balance of an", "how old is my", "customer or supplier",
    "location wise", "location", "each location", "every location",
}


# Regex scaffolding and English filler, which would otherwise make every intent
# look similar to every question.
PATTERN_NOISE = {
    "the", "and", "for", "our", "was", "are", "how", "what", "much", "many",
    "show", "give", "tell", "list", "get", "see", "want", "need", "please",
    "can", "you", "did", "does", "have", "has", "had", "with", "from", "that",
    "this", "there", "any", "all", "some", "now", "not", "who", "whom",
    # regex fragments
    "ledger", "item", "sub", "abc", "def",
}
# "ledger" and "item" are group names, not words - but "ledger" is also a real
# search term, so put it back for the intents that are genuinely about ledgers.
PATTERN_NOISE -= {"ledger"}


# ============================================================
# The vocabulary
# ============================================================
#
# Anchored with ^...$ unless a trailing subject is expected. The period has
# already been taken out of the text before these run, so "total sales in 2024"
# arrives here as "total sales".

INTENTS = [

    # -------------------------------------------------- receivables/payables
    Intent("outstanding_receivables", "Outstanding from customers",
           [r"^(?:total )?(?:customer|debtor)s?[ -]?(?:wise )?outstanding$",
            r"^outstanding (?:from|of) (?:customers?|debtors?)$",
            r"^(?:accounts? )?receivables?$",
            r"^total receivables?$",
            r"^(?:aged? )?debtors?(?: list| report| ageing| aging)?$",
            r"^receivables? (?:ageing|aging|report|list)$",
            r"^who owes us(?: money| anything)?$",
            r"^how much (?:money )?(?:do|does) (?:the )?customers? owe(?: us)?$",
            r"^how much (?:is )?(?:due|owed|outstanding|pending) from (?:the )?customers?$",
            r"^how much (?:do we|to) (?:have to )?collect$",
            r"^(?:which|what) customers? (?:have not|haven'?t|did not|didn'?t) paid?$",
            r"^(?:overdue|unpaid|pending) (?:customers?|invoices?|receivables?)$",
            r"^collections? (?:pending|outstanding|due)$",
            r"^customers? (?:balance|balances|dues?)$"],
           keywords="owe owed owing due overdue collect collection customer debtor "
                    "receivable outstanding unpaid pending money"),

    Intent("outstanding_payables", "Outstanding to suppliers",
           [r"^(?:total )?(?:supplier|vendor|creditor)s?[ -]?(?:wise )?outstanding$",
            r"^outstanding (?:to|of) (?:suppliers?|vendors?|creditors?)$",
            r"^(?:accounts? )?payables?$",
            r"^total payables?$",
            r"^(?:aged? )?creditors?(?: list| report| ageing| aging)?$",
            r"^payables? (?:ageing|aging|report|list)$",
            r"^who (?:do )?we owe$",
            r"^how much (?:do )?we owe(?: the)?(?: suppliers?| vendors?| creditors?)$",
            r"^how much (?:do we have )?to pay (?:the )?(?:suppliers?|vendors?|creditors?)$",
            r"^(?:pending |outstanding )?payments? to (?:suppliers?|vendors?)$",
            r"^suppliers? (?:balance|balances|dues?)$"],
           keywords="owe supplier vendor creditor payable outstanding pay pending"),

    # -------------------------------------------------------- sales
    Intent("sales_total", "Total sales",
           [r"^(?:total|gross|net) sales(?: (?:figure|amount|value|total))?$",
            r"^sales(?: (?:figure|amount|value|total))$",
            r"^(?:total )?(?:revenue|turnover)$",
            r"^what (?:is|are|was|were) (?:my|our|the) sales$",
            r"^how much (?:did we|have we|we) (?:sell|sold)$",
            r"^how much business did we do$",
            r"^what did we sell$",
            r"^sales$"],
           keywords="sales sell sold revenue turnover business income billed total"),

    Intent("sales_by_customer", "Sales by customer",
           [r"^sales (?:by|per|wise) (?:customer|client|party|buyer)s?$",
            r"^(?:customer|client|party|buyer)s?[ -]?wise sales$",
            r"^sales (?:customer|party)[ -]?wise$",
            r"^(?:which|what) customers? (?:bought|buys|purchased) (?:the )?most$",
            r"^who (?:bought|buys) (?:the )?most$"],
           keywords="sales customer party client buyer wise each per"),

    Intent("sales_by_item", "Sales by item",
           [r"^sales (?:by|per|wise) (?:item|product|goods)s?$",
            r"^(?:item|product)s?[ -]?wise sales$",
            r"^(?:which|what) (?:items?|products?) sold (?:the )?most$",
            r"^best sell(?:ing|ers?) (?:items?|products?)$"],
           keywords="sales item product goods wise sold best"),

    Intent("sales_by_month", "Monthly sales",
           [r"^(?:monthly|month[ -]?wise) sales$",
            r"^sales (?:by|per|each) month$",
            r"^sales (?:trend|graph|chart|movement)$",
            r"^month (?:by|on) month sales$"],
           keywords="sales monthly month trend graph chart"),

    Intent("top_customers", "Top customers",
           [r"^(?:top|best|biggest|largest|main|major|key) "
            r"(?:\d+ )?(?:customers?|clients?|buyers?|parties)$",
            r"^who (?:are|is) (?:my|our|the) "
            r"(?:top|best|biggest|main|major) (?:customers?|clients?|buyers?)$",
            r"^(?:customers?|clients?) ranking$"],
           keywords="top best biggest largest main major customer client buyer ranking"),

    # -------------------------------------------------------- purchases
    Intent("purchase_total", "Total purchases",
           [r"^(?:total|gross|net) purchases?(?: (?:figure|amount|value))?$",
            r"^purchases?(?: (?:figure|amount|value|total))$",
            r"^how much (?:did we|have we|we) (?:buy|bought|purchased?)$",
            r"^what did we buy$",
            r"^purchases?$"],
           keywords="purchase buy bought procurement total amount"),

    Intent("purchases_by_supplier", "Purchases by supplier",
           [r"^purchases? (?:by|per|wise) (?:supplier|vendor|party)s?$",
            r"^(?:supplier|vendor|party)s?[ -]?wise purchases?$",
            r"^purchases? (?:supplier|vendor)[ -]?wise$"],
           keywords="purchase supplier vendor party wise each"),

    Intent("purchases_by_item", "Purchases by item",
           [r"^purchases? (?:by|per|wise) (?:item|product|goods)s?$",
            r"^(?:item|product)s?[ -]?wise purchases?$"],
           keywords="purchase item product goods wise"),

    Intent("top_suppliers", "Top suppliers",
           [r"^(?:top|best|biggest|largest|main|major|key) "
            r"(?:\d+ )?(?:suppliers?|vendors?)$",
            r"^who (?:are|is) (?:my|our|the) "
            r"(?:top|best|biggest|main|major) (?:suppliers?|vendors?)$"],
           keywords="top best biggest main supplier vendor"),

    # -------------------------------------------------------- profit
    # Above ledger_statement: "income statement" is a P&L, not a ledger.
    Intent("profit_and_loss", "Profit and loss",
           [r"^(?:the )?p\s*&?\s*l(?: (?:statement|report|account))?$",
            r"^(?:the )?p and l(?: statement| report)?$",
            r"^(?:the )?profit (?:and|&|/) loss(?: (?:statement|report|account|a/c))?$",
            r"^profit loss(?: statement| report)?$",
            r"^income statement$",
            r"^(?:the )?(?:trading|operating) (?:and profit and loss )?account$",
            r"^are we (?:making a )?profit(?:able)?$",
            r"^(?:is|was) (?:the )?business profitable$"],
           keywords="profit loss income statement trading account profitable earnings"),

    Intent("net_profit", "Net profit",
           [r"^(?:the )?net (?:profit|income|earnings?|result)$",
            r"^what (?:is|was) (?:my|our|the) profit$",
            r"^how much profit (?:did we|have we|we) (?:make|made|earn|earned)$",
            r"^(?:the )?bottom line$",
            r"^did we (?:make|earn) (?:any )?(?:money|profit)$",
            r"^profit$"],
           keywords="net profit income earnings bottom line money made earned"),

    Intent("item_profitability", "Profit by item",
           [r"^(?:item|product)s?[ -]?wise profit(?:ability)?$",
            r"^profit (?:by|per) (?:item|product)s?$",
            r"^(?:which|what) (?:items?|products?) (?:are|is) (?:the )?most profitable$",
            r"^(?:item|product) profitability$"],
           keywords="profit item product margin profitable gross"),

    Intent("kpi_summary", "Business summary",
           [r"^(?:kpi|kpis|key performance indicators?)(?: summary)?$",
            r"^(?:business |company )?(?:summary|overview|snapshot|highlights)$",
            r"^how (?:are we|is the business|are things) doing$",
            r"^how(?:'s| is) business$",
            r"^give me (?:an? )?(?:overview|summary)$"],
           keywords="kpi summary overview snapshot business doing performance"),

    Intent("compare_periods", "Compare periods",
           [r"^compare (?:periods?|years?|months?)$",
            r"^(?:year|month) (?:on|over|vs\.?|versus) (?:year|month)(?: comparison)?$",
            r"^(?:this year )?(?:vs\.?|versus|compared to) last year$"],
           keywords="compare comparison versus against previous"),

    # -------------------------------------------------------- statements
    # Above the generic ledger statement so "financial position" is not read
    # as an account called "financial".
    Intent("balance_sheet", "Balance sheet",
           [r"^(?:the )?balance ?sheet(?: report| statement)?$",
            r"^b\.?\s?s\.?$",
            r"^(?:statement of )?financial position$",
            r"^(?:what (?:are|is) )?(?:our|my|the) (?:assets and liabilities)$"],
           keywords="balance sheet financial position assets liabilities equity"),

    Intent("trial_balance", "Trial balance",
           [r"^(?:the )?trial ?balance(?: report)?$",
            r"^t\.?\s?b\.?$"],
           keywords="trial balance tb"),

    Intent("cash_flow", "Cash flow",
           [r"^(?:the )?cash ?flow(?: statement| report)?$",
            r"^(?:where|how) did (?:the |our |my )?(?:cash|money) go$",
            r"^(?:movement of|sources? of) (?:cash|funds)$",
            r"^funds? flow$"],
           keywords="cash flow funds movement sources money went"),

    Intent("coa_balances", "Chart of accounts",
           [r"^chart of accounts(?: with balances?)?$",
            r"^c\.?o\.?a\.?$",
            r"^(?:account|ledger) (?:groups? )?structure$"],
           keywords="chart accounts coa structure groups"),

    # -------------------------------------------------------- cash and bank
    Intent("cash_balance", "Cash balance",
           [r"^(?:the )?cash (?:balance|position|in hand|on hand)$",
            r"^(?:how much )?cash (?:do we have|is there|available)$",
            r"^(?:available|total) cash$",
            r"^money (?:in|on) hand$",
            r"^how much (?:money|cash) do we have(?: in cash)?$",
            r"^cash$"],
           keywords="cash hand position available money balance petty"),

    Intent("bank_balance", "Bank balance",
           [r"^(?:the )?bank (?:balance|position)$",
            r"^(?:how much )?(?:money |cash )?(?:is )?in (?:the )?bank$",
            r"^balance in (?:the )?bank(?: accounts?)?$",
            r"^bank (?:account )?balances?$",
            r"^bank$"],
           keywords="bank balance position account money"),

    Intent("cash_bank_book", "Cash and bank book",
           [r"^cash (?:and|&) bank book$",
            r"^(?:the )?(?:cash|bank) book$",
            r"^(?:cash|bank) (?:movements?|transactions?)$"],
           keywords="cash bank book movements transactions"),

    # -------------------------------------------------------- VAT
    # Above payables: "vat payable" is the VAT return, not the creditors list.
    Intent("vat_summary", "VAT summary",
           [r"^(?:the )?vat(?: summary| return| position| report)?$",
            r"^(?:net |output |input )?vat (?:payable|refundable|due|liability)$",
            r"^tax (?:payable|summary|position|liability|return)$",
            r"^how much vat (?:do we owe|is payable|to pay)$",
            r"^(?:do we|how much do we) owe (?:the )?(?:vat|tax)$"],
           keywords="vat tax payable refundable output input return liability net"),

    Intent("vat_detailed", "VAT detail",
           [r"^(?:the )?vat (?:detail|details|detailed)(?: report)?$",
            r"^detailed vat(?: report)?$",
            r"^vat (?:line by line|transactions?|entries|breakdown)$"],
           keywords="vat detail detailed line breakdown transactions entries"),

    # -------------------------------------------------------- expenses
    Intent("expense_total", "Total expenses",
           [r"^(?:total|all) expenses?$",
            r"^expenses?(?: (?:total|amount|figure))?$",
            r"^how much (?:did we|have we|we) (?:spend|spent)$",
            r"^(?:total )?(?:spend|spending|expenditure|outgoings?)$",
            r"^what (?:did|do) we spend$"],
           keywords="expense expenses spend spending expenditure cost outgoings total"),

    Intent("expense_breakdown", "Expenses by account",
           [r"^expenses? (?:breakdown|by (?:category|account|head|type)|wise)$",
            r"^(?:category|account|head)[ -]?wise expenses?$",
            r"^what (?:did|do) we spend (?:it )?on$",
            r"^where (?:is|does) (?:the |our |my )?money go(?:ing)?$",
            r"^(?:biggest|top|main) expenses?$"],
           keywords="expense breakdown category account head wise spend money going biggest"),

    # -------------------------------------------------------- inventory
    Intent("closing_stock_value", "Closing stock value",
           [r"^closing (?:stock|inventory)(?: value| valuation| report)?$",
            r"^(?:the )?(?:stock|inventory) (?:value|valuation|worth)$",
            r"^value of (?:the )?(?:stock|inventory)$",
            r"^how much (?:stock|inventory) do we have$",
            r"^(?:stock|inventory) (?:in|on) hand$",
            r"^total (?:stock|inventory)$"],
           keywords="closing stock inventory value valuation worth hand total goods"),



    Intent("slow_moving_items", "Slow moving stock",
           [r"^slow[ -]?moving(?: items?| stock| products?)?$",
            r"^(?:dead|stagnant|non[ -]?moving|not moving) (?:stock|items?|products?)$",
            r"^items? (?:not|never) (?:selling|sold|moving)$",
            r"^(?:which|what) (?:items?|products?) (?:are not|aren'?t) selling$"],
           keywords="slow moving dead stagnant stock items selling sold"),

    Intent("negative_stock", "Negative stock",
           [r"^(?:negative|minus|below zero|nil) stock$",
            r"^items? (?:with|in|having) (?:negative|minus) stock$",
            r"^(?:stock|inventory) (?:errors?|problems?|issues?)$"],
           keywords="negative minus stock items below zero errors"),

    Intent("inventory_ageing", "Stock ageing",
           [r"^(?:inventory|stock) (?:ageing|aging)(?: report)?$",
            r"^how old is (?:my|our|the) (?:stock|inventory)$",
            r"^(?:age|ageing) of (?:the )?(?:stock|inventory)$"],
           keywords="inventory stock ageing aging old age held"),

    # -------------------------------------------------------- ledgers





    # -------------------------------------------------------- vouchers
    Intent("day_book", "Day book",
           [r"^(?:the )?day ?book(?: report)?$",
            r"^(?:today'?s?|todays) (?:entries|transactions?|vouchers?|postings?)$",
            r"^what (?:was|were|has been) (?:posted|entered|booked)(?: today)?$",
            r"^entries (?:for|of) (?:the day|today)$"],
           keywords="day book today entries transactions vouchers posted entered"),

    Intent("last_vouchers", "Recent vouchers",
           [r"^(?:the )?(?:last|latest|recent|previous) (?:\d+ )?"
            r"(?:vouchers?|entries|transactions?|postings?)$",
            r"^what (?:did|have) (?:i|we) (?:enter|post)(?:ed)? (?:last|recently)$",
            r"^recent activity$"],
           keywords="last latest recent previous vouchers entries transactions activity"),

    Intent("audit_trail", "Audit trail",
           [r"^(?:the )?audit (?:trail|log|history)$",
            r"^who (?:changed|edited|deleted|modified) (?:what|it|anything)$",
            r"^(?:change|edit|modification) (?:log|history)$"],
           keywords="audit trail log history changed edited deleted who"),

    Intent("voucher_register", "Voucher register",
           [r"^(?:the )?(?:voucher )?register$",
            r"^(?:sales|purchase|receipt|payment|journal|expense) register$"],
           keywords="register vouchers listing"),

    # -------------------------------------------------------- masters
    Intent("list_items", "List of items",
           [r"^(?:list|show|all)(?: of)?(?: the| my| our)? (?:items?|products?|goods|stock items?)$",
            r"^(?:items?|products?) list$",
            r"^what (?:items?|products?|goods) do we (?:have|sell|stock)$",
            r"^(?:how many|which) (?:items?|products?) do we have$"],
           keywords="list items products goods stock master catalogue"),

    Intent("list_ledgers", "List of accounts",
           [r"^(?:list|show|all)(?: of)?(?: the| my| our)? "
            r"(?:ledgers?|accounts?|parties|customers?|suppliers?|vendors?)$",
            r"^(?:ledgers?|accounts?|customers?|suppliers?) list$",
            r"^what (?:accounts?|ledgers?) do we have$",
            r"^(?:chart of )?ledgers?$"],
           keywords="list ledgers accounts parties customers suppliers master"),

    Intent("list_groups", "Account groups",
           [r"^(?:list |show |all )?(?:account |ledger )?groups?$",
            r"^groups? list$"],
           keywords="groups list account ledger nature"),

    Intent("list_locations", "Locations",
           [r"^(?:list |show |all )?(?:locations?|branch(?:es)?|warehouses?|stores?)$",
            r"^locations? list$"],
           keywords="locations branches warehouses stores list"),

    Intent("list_cost_centers", "Cost centres",
           [r"^(?:list |show |all )?cost cent(?:er|re)s?$",
            r"^(?:departments?|divisions?)$"],
           keywords="cost centers centres departments divisions list"),

    Intent("list_users", "Users",
           [r"^(?:list |show |all )?users?(?: list)?$",
            r"^who (?:are|is) (?:the )?users?$",
            r"^who (?:can|has) (?:access|login|log in)$"],
           keywords="users list access login people staff"),

    Intent("list_financial_years", "Financial years",
           [r"^(?:list |show |all )?financial years?$",
            r"^(?:fy|fiscal years?) list$",
            r"^what financial years? (?:are there|do we have)$"],
           keywords="financial fiscal years list periods"),

    Intent("fixed_asset_register", "Fixed assets",
           [r"^(?:list |show |all )?fixed assets?(?: register)?$",
            r"^assets? (?:register|list)$",
            r"^list of assets$",
            r"^what assets do we (?:own|have)$",
            r"^depreciation(?: (?:report|schedule|log))?$"],
           keywords="fixed assets register depreciation own list"),

    Intent("price_list", "Price list",
           [r"^(?:the )?(?:price list|selling prices?|rate list)$",
            r"^(?:what|how much) (?:do|are) we (?:sell|charge)(?:ing)? (?:for|at)$"],
           keywords="price list selling rate charge"),

    Intent("company_settings", "Company details",
           [r"^company (?:settings?|details?|information|info|profile)$",
            r"^what is (?:my|our|the) company(?: name)?$",
            r"^(?:my|our) company$",
            r"^(?:what )?currency(?: do we use)?$"],
           keywords="company settings details name currency profile information"),

    Intent("list_stock_groups", "Stock groups",
           [r"^(?:list |show |all )?(?:stock|item|inventory) groups?$"],
           keywords="stock item inventory groups categories"),

    Intent("list_units", "Units",
           [r"^(?:list |show |all )?units?(?: of measure)?$"],
           keywords="units measure uom list"),

    Intent("stock_by_location", "Stock at a location",
           # Above the plain item lookup: "stock of Cement in Abu Dhabi" is a
           # question about a location, not about an item called
           # "Cement in Abu Dhabi".
           [r"^stock (?:by|per) location$",
            r"^location[ -]?wise stock$",
            r"^stock location[ -]?wise$",
            r"^(?:closing )?stock (?:available |on hand |in hand )?"
            r"(?:in|at) (?:the )?(?:location )?" + LOC + r"$",
            r"^(?:closing )?stock (?:value )?(?:of|for) (?:item |product )?" + ITEM +
            r" (?:in|at) (?:the )?" + LOC + r"$",
            r"^how (?:much|many) " + ITEM + r" (?:do we have |is there )?"
            r"(?:in|at) (?:the )?" + LOC + r"$",
            r"^" + ITEM + r" stock (?:in|at) (?:the )?" + LOC + r"$"],
           keywords="stock inventory location branch warehouse store available "
                    "at in each split"),

    # ---------------------------------------------- one named thing
    # Last, and in this order. These capture a name, so "dead stock" would
    # read as an item called "dead" and "today's entries" as an account
    # called "today's" if they ran before the intents that name a report.

    Intent("customer_statement", "Customer statement",
           # Bare form first, and capturing no name: offered as a chip, it
           # routes here and the tool asks which customer.
           [r"^customer statement$",
            r"^customer statement (?:of|for) " + NAME + r"$",
            r"^statement of account (?:of|for) " + NAME + r"$"],
           keywords="customer statement account soa"),

    Intent("supplier_statement", "Supplier statement",
           [r"^supplier statement$",
            r"^(?:supplier|vendor) statement (?:of|for) " + NAME + r"$"],
           keywords="supplier vendor statement account"),

    Intent("ledger_balance", "Balance of an account",
           [r"^balance of an account$",
            r"^(?:the )?balance (?:of|for|in) " + NAME + r"$",
            r"^" + NAME + r"(?:'s)? balance$",
            r"^what is " + NAME + r"(?:'s)? balance$",
            r"^how much (?:does|do) " + NAME + r" owe(?: us)?$",
            r"^how much (?:do )?we owe " + NAME + r"$"],
           keywords="balance owe account closing"),

    Intent("ledger_statement", "Statement of an account",
           [r"^statement of an account$",
            r"^(?:the )?(?:statement|soa|s\.o\.a\.?) (?:of|for) " + NAME + r"$",
            r"^(?:the )?ledger (?:of|for) " + NAME + r"$",
            r"^(?:the )?account statement (?:of|for) " + NAME + r"$",
            r"^" + NAME + r"(?:'s)? (?:statement|ledger|account statement)$",
            r"^(?:show |give )?(?:me )?" + NAME + r"(?:'s)? (?:account|transactions?|entries)$",
            r"^transactions? (?:of|for|with) " + NAME + r"$"],
           keywords="statement ledger account transactions entries soa history"),


    Intent("party_details", "Customer or supplier details",
           [r"^customer or supplier details$",
            r"^(?:the )?(?:details?|information|info|contact|address|trn) (?:of|for) " + NAME + r"$",
            r"^who is " + NAME + r"$",
            r"^" + NAME + r"(?:'s)? (?:details?|contact|address|trn)$"],
           keywords="details contact address phone email trn credit terms party"),

    Intent("stock_movement", "Stock movement of an item",
           [r"^stock movement of an item$",
            r"^(?:the )?stock movements? (?:of|for) " + ITEM + r"$",
            r"^movements? (?:of|for) " + ITEM + r"$",
            r"^(?:ins? and outs?|in out) (?:of|for) " + ITEM + r"$"],
           keywords="stock movement in out item history"),

    Intent("item_stock", "Stock of an item",
           [r"^stock of an item$",
            # A closing-stock question that names an item is about that item,
            # not the company. Without these it reaches the whole-company
            # report and the item is silently dropped.
            r"^closing (?:stock|inventory)(?: value| valuation)? "
            r"(?:of|for) (?:item |product )?" + ITEM + r"$",
            r"^(?:stock|inventory) (?:value|valuation) "
            r"(?:of|for) (?:item |product )?" + ITEM + r"$",
            r"^(?:the )?stock of (?:item |product )?" + ITEM + r"$",
            r"^how (?:much|many) " + ITEM + r" (?:do we have|is there|are there|in stock)$",
            r"^" + ITEM + r" (?:stock|quantity|qty|balance)$"],
           keywords="stock quantity item balance available"),

]


# ============================================================
# Matching
# ============================================================

# Junk that changes nothing about which report is wanted. Stripped before
# matching so "can you please show me the total sales" reaches "total sales".
POLITENESS = re.compile(
    r"^(?:(?:hi|hey|hello|ok|okay|so|now|and|also|please|pls|kindly|"
    r"can you|could you|would you|will you|i want|i need|i would like|"
    r"id like|i'd like|let me know|tell me|show me|give me|get me|find me|"
    r"fetch me|display|bring|show|tell|give|check|what about|how about)\b[\s,]*)+",
    re.I)
TRAILING = re.compile(r"[\s,]*(?:please|pls|thanks|thank you|for me|now)[\s.!?]*$", re.I)


def _clean(text):
    """A question reduced to the part that says which report is wanted."""
    q = (text or "").strip()
    q = TRAILING.sub("", q)
    q = POLITENESS.sub("", q)
    # "can you show me the total sales" leaves "the total sales" behind.
    q = re.sub(r"^(?:the|my|our)\s+", "", q, flags=re.I)
    q = re.sub(r"\s+", " ", q).strip(" ?.!,")
    return q


def match(text):
    """(tool_name, args) for a question we recognise outright, or None.

    The period is taken out first and handed on already parsed, so "total
    sales in 2024" and "total sales" travel the same path.
    """
    if not text:
        return None

    period, remainder = R.extract_period(text)
    cleaned = _clean(remainder)
    if not cleaned:
        return None

    for intent in INTENTS:
        args = intent.match(cleaned)
        if args is not None:
            if period is not None:
                args["_period"] = period
            return intent.tool, args
    return None


# ============================================================
# Suggesting
# ============================================================

# Words so common across the vocabulary that matching one says nothing about
# which report is wanted.
STOPWORDS = {
    "a", "an", "the", "of", "for", "in", "on", "at", "to", "by", "is", "are",
    "was", "were", "do", "does", "did", "we", "us", "our", "my", "me", "i",
    "you", "it", "and", "or", "how", "what", "which", "who", "when", "much",
    "many", "show", "give", "tell", "list", "get", "please", "can", "any",
    "all", "have", "has", "there", "report", "total",
}

MIN_SCORE = 0.34      # below this the overlap is coincidence, not a near-miss
STRONG_SCORE = 0.75   # a single candidate this close is answered outright
MAX_OPTIONS = 5


def _tokens(text):
    return {w for w in re.findall(r"[a-z]+", (text or "").lower())
            if w not in STOPWORDS and len(w) > 1}


def rank(text):
    """Every intent this question could plausibly mean, best first.

    Scored on how much of the *question* the intent accounts for, so a short
    question is not penalised for missing an intent's other words.
    """
    _, remainder = R.extract_period(text or "")
    asked = _tokens(_clean(remainder))
    if not asked:
        return []

    scored = []
    for intent in INTENTS:
        shared = asked & intent.vocabulary
        if not shared:
            continue
        score = len(shared) / len(asked)
        # A word the intent's own label uses is worth more than one buried in
        # a regex: "ageing" in "Stock ageing" is the subject, not a synonym.
        if shared & _tokens(intent.label):
            score += 0.15
        scored.append((score, intent))

    scored.sort(key=lambda pair: (-pair[0], pair[1].label))
    return [(s, i) for s, i in scored if s >= MIN_SCORE]


def suggest(text, limit=MAX_OPTIONS):
    """The nearest scenarios, as (tool_name, label) for the user to pick from.

    Only intents the user is allowed to run - offering a report and then
    refusing it is worse than not offering it.
    """
    from . import chat_permissions as P

    seen, out = set(), []
    for _, intent in rank(text):
        if intent.tool in seen or not P.can_use(intent.tool):
            continue
        seen.add(intent.tool)
        out.append((intent.tool, intent.label))
        if len(out) >= limit:
            break
    return out


def confident(text):
    """(tool, args) when one scenario is so far ahead it needs no asking."""
    from . import chat_permissions as P

    ranked = [(s, i) for s, i in rank(text) if P.can_use(i.tool)]
    if not ranked:
        return None
    score, intent = ranked[0]
    if score < STRONG_SCORE:
        return None
    # A clear runner-up means the question was ambiguous, not obvious.
    if len(ranked) > 1 and ranked[1][0] > score - 0.2:
        return None

    period, _ = R.extract_period(text or "")
    args = dict(intent.args)
    if period is not None:
        args["_period"] = period
    # Only for intents that need nothing else - we have not captured a name.
    return intent.tool, args


def labels():
    """Every canonical question, for the help panel and for tests."""
    return [(i.tool, i.label) for i in INTENTS]
