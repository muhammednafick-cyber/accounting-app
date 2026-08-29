"""Custom Report Builder - the schema registry and the query compiler.

Users pick a dataset, tick the columns they want, add filters and grouping.
This module turns that choice into SQL.

Nothing the user types ever becomes part of a SQL identifier. Table names,
column names, join conditions, aggregate functions, operators and sort
directions all come from the registry below; the request only ever selects
*which* registry entry to use, by key. Everything else - the values in filters -
is bound as a parameter.

Two rules make the result safe in a shared database:

1. Only tables listed in TABLES can be reached, and every one of them carries a
   company_id. Tables holding credentials or cross-company data (users,
   user_permissions, system_settings, companies) are deliberately absent.
2. Every table in a query, base and joined alike, is filtered by the caller's
   company_id. A join can never widen the result to another company's rows.
"""
import re

MAX_ROWS = 5000
DEFAULT_ROWS = 500


class ReportError(ValueError):
    """A report definition that cannot be compiled."""


# ============================================================
# Field / table registry
# ============================================================

def F(name, label, kind="text"):
    return {"name": name, "label": label, "type": kind}


TABLES = {
    "vouchers": {
        "label": "Voucher", "table": "vouchers", "alias": "v",
        "fields": [
            F("voucher_number", "Voucher No"),
            F("voucher_type", "Voucher Type"),
            F("date", "Voucher Date", "date"),
            F("amount", "Voucher Amount", "number"),
            F("narration", "Narration"),
            F("location_name", "Location"),
            F("due_date", "Due Date", "date"),
            F("original_invoice_ref", "Invoice Ref"),
            F("original_invoice_date", "Invoice Date", "date"),
            F("linked_voucher_number", "Linked Voucher"),
            F("credit_days", "Credit Days", "number"),
            F("posting_date", "Posting Date", "date"),
            F("entry_date", "Entered On"),
            F("created_by", "Created By"),
        ],
    },
    "ledger_entries": {
        "label": "Ledger Entry", "table": "ledger_entries", "alias": "le",
        "fields": [
            F("voucher_number", "Voucher No"),
            F("ledger_name", "Ledger"),
            F("amount", "Entry Amount", "number"),
            F("type", "Dr / Cr"),
            F("cost_center_code", "Cost Centre Code"),
        ],
    },
    "item_entries": {
        "label": "Item Entry", "table": "item_entries", "alias": "ie",
        "fields": [
            F("voucher_number", "Voucher No"),
            F("item_name", "Item"),
            F("quantity", "Quantity", "number"),
            F("unit_price", "Rate", "number"),
            F("amount", "Line Amount", "number"),
            F("type", "In / Out"),
            F("ledger_name", "Ledger"),
            F("location_name", "Location"),
            F("cogs_rate", "COGS Rate", "number"),
            F("cogs_amount", "COGS Amount", "number"),
            F("batch_number", "Batch"),
            F("expiry_date", "Expiry Date", "date"),
            F("running_qty", "Running Qty", "number"),
            F("running_value", "Running Value", "number"),
            F("running_wap", "Running WAP", "number"),
            F("weight_kg", "Weight (kg)", "number"),
            F("landed_cost_per_unit", "Landed Cost / Unit", "number"),
            F("ref_voucher_number", "Reference Voucher"),
        ],
    },
    "ledgers": {
        "label": "Ledger Master", "table": "ledgers", "alias": "l",
        "fields": [
            F("ledger_code", "Ledger Code"),
            F("ledger_name", "Ledger Name"),
            F("group_code", "Group Code"),
            F("opening_balance", "Opening Balance", "number"),
            F("opening_balance_type", "Opening Dr/Cr"),
            F("opening_balance_date", "Opening Date", "date"),
            F("closing_balance", "Closing Balance", "number"),
            F("credit_days", "Credit Days", "number"),
            F("address", "Address"),
            F("contact_person", "Contact Person"),
            F("phone", "Phone"),
            F("email", "Email"),
            F("trn", "TRN"),
            F("is_active", "Active", "bool"),
        ],
    },
    "groups": {
        "label": "Account Group", "table": "groups", "alias": "g",
        "fields": [
            F("group_code", "Group Code"),
            F("group_name", "Group Name"),
            F("nature", "Nature"),
            F("master_group_code", "Master Group Code"),
        ],
    },
    "master_groups": {
        "label": "Master Group", "table": "master_groups", "alias": "mg",
        "fields": [
            F("master_group_code", "Master Group Code"),
            F("master_group_name", "Master Group"),
            F("nature", "Master Nature"),
        ],
    },
    "sub_groups": {
        "label": "Sub Group", "table": "sub_groups", "alias": "sg",
        "fields": [
            F("sub_group_name", "Sub Group"),
            F("group_code", "Sub Group's Group Code"),
        ],
    },
    "inventory": {
        "label": "Item Master", "table": "inventory", "alias": "i",
        "fields": [
            F("item_code", "Item Code"),
            F("name", "Item Name"),
            F("stock_group_code", "Stock Group Code"),
            F("unit_code", "Unit"),
            F("unit_price", "Selling Rate", "number"),
            F("stock_quantity", "Stock Qty", "number"),
            F("stock_value", "Stock Value", "number"),
            F("vat_rate", "VAT %", "number"),
            F("opening_price", "Opening Rate", "number"),
            F("opening_location_name", "Opening Location"),
            F("is_active", "Active", "bool"),
        ],
    },
    "inventory_groups": {
        "label": "Stock Group", "table": "inventory_groups", "alias": "ig",
        "fields": [
            F("group_code", "Stock Group Code"),
            F("group_name", "Stock Group"),
        ],
    },
    "units": {
        "label": "Unit", "table": "units", "alias": "u",
        "fields": [F("unit_code", "Unit Code"), F("unit_name", "Unit Name")],
    },
    "cost_centers": {
        "label": "Cost Centre", "table": "cost_centers", "alias": "cc",
        "fields": [
            F("center_code", "Cost Centre Code"),
            F("center_name", "Cost Centre"),
            F("is_active", "Active", "bool"),
        ],
    },
    "locations": {
        "label": "Location Master", "table": "locations", "alias": "loc",
        "fields": [
            F("location_code", "Location Code"),
            F("location_name", "Location Name"),
            F("address", "Location Address"),
            F("contact_person", "Location Contact"),
            F("phone", "Location Phone"),
            F("is_active", "Active", "bool"),
        ],
    },
    "fixed_assets": {
        "label": "Fixed Asset", "table": "fixed_assets", "alias": "fa",
        "fields": [
            F("asset_code", "Asset Code"),
            F("asset_name", "Asset Name"),
            F("ledger_name", "Asset Ledger"),
            F("purchase_date", "Purchase Date", "date"),
            F("purchase_cost", "Purchase Cost", "number"),
            F("salvage_value", "Salvage Value", "number"),
            F("useful_life_years", "Useful Life (yrs)", "number"),
            F("depreciation_method", "Method"),
            F("depreciation_rate", "Rate %", "number"),
            F("accumulated_depreciation", "Accumulated Depreciation"),
            F("status", "Status"),
        ],
    },
    "depreciation_log": {
        "label": "Depreciation Entry", "table": "depreciation_log", "alias": "dl",
        "fields": [
            F("depreciation_date", "Depreciation Date", "date"),
            F("amount", "Depreciation Amount", "number"),
            F("method_used", "Method Used"),
            F("voucher_number", "Voucher No"),
        ],
    },
    "settlements": {
        "label": "Settlement", "table": "settlements", "alias": "s",
        "fields": [
            F("settlement_number", "Settlement No"),
            F("settlement_date", "Settlement Date", "date"),
            F("ledger_name", "Party"),
            F("total_amount", "Settled Amount", "number"),
            F("description", "Description"),
            F("auto_posted_voucher_number", "Posted Voucher"),
        ],
    },
    "settlement_allocations": {
        "label": "Settlement Allocation", "table": "settlement_allocations",
        "alias": "sa",
        "fields": [
            F("assigned_amount", "Allocated Amount", "number"),
            F("type", "Allocation Dr/Cr"),
        ],
    },
    "audit_trail": {
        "label": "Audit Entry", "table": "audit_trail", "alias": "at",
        "fields": [
            F("created_at", "When"),
            F("username", "User"),
            F("action", "Action"),
            F("voucher_number", "Voucher No"),
            F("details", "Details"),
        ],
    },
    "ledger_opening_balances": {
        "label": "Ledger Opening", "table": "ledger_opening_balances", "alias": "lob",
        "fields": [
            F("ledger_code", "Ledger Code"),
            F("location_name", "Location"),
            F("opening_balance", "Opening Balance", "number"),
            F("opening_balance_type", "Dr / Cr"),
            F("opening_balance_date", "Opening Date", "date"),
        ],
    },
    "item_opening_balances": {
        "label": "Item Opening", "table": "item_opening_balances", "alias": "iob",
        "fields": [
            F("item_code", "Item Code"),
            F("location_name", "Location"),
            F("quantity", "Opening Qty", "number"),
            F("unit_price", "Opening Rate", "number"),
            F("opening_date", "Opening Date", "date"),
        ],
    },
    "financial_years": {
        "label": "Financial Year", "table": "financial_years", "alias": "fy",
        "fields": [
            F("fy_code", "FY Code"),
            F("start_date", "FY Start", "date"),
            F("end_date", "FY End", "date"),
            F("is_active", "Active", "bool"),
            F("is_locked", "Locked", "bool"),
        ],
    },
}


# Join conditions, keyed by (from_table_key, to_table_key). The company_id
# equality is appended by the compiler, so it can never be forgotten here.
JOINS = {
    ("vouchers", "ledger_entries"): "le.voucher_number = v.voucher_number",
    ("vouchers", "item_entries"): "ie.voucher_number = v.voucher_number",
    ("vouchers", "cost_centers"): "cc.center_code = v.cost_center_code",
    ("vouchers", "locations"): "loc.location_name = v.location_name",

    ("ledger_entries", "vouchers"): "v.voucher_number = le.voucher_number",
    ("ledger_entries", "ledgers"): "l.ledger_name = le.ledger_name",
    ("ledger_entries", "cost_centers"): "cc.center_code = le.cost_center_code",

    ("item_entries", "vouchers"): "v.voucher_number = ie.voucher_number",
    ("item_entries", "inventory"): "i.name = ie.item_name",
    ("item_entries", "cost_centers"): "cc.center_code = ie.cost_center_code",

    ("ledgers", "groups"): "g.group_code = l.group_code",
    ("ledgers", "sub_groups"): "sg.id = l.sub_group_id",
    ("ledgers", "ledger_opening_balances"): "lob.ledger_code = l.ledger_code",
    ("groups", "master_groups"): "mg.master_group_code = g.master_group_code",

    ("inventory", "inventory_groups"): "ig.group_code = i.stock_group_code",
    ("inventory", "units"): "u.unit_code = i.unit_code",
    ("inventory", "item_opening_balances"): "iob.item_code = i.item_code",

    ("fixed_assets", "depreciation_log"): "dl.asset_id = fa.id",
    ("settlements", "settlement_allocations"): "sa.settlement_id = s.id",
    ("audit_trail", "vouchers"): "v.voucher_number = at.voucher_number",
}


# A dataset is a starting table plus the tables reachable from it. "path" says
# how each related table is joined - it may hop through another table.
DATASETS = {
    "ledger_entries": {
        "label": "Transactions (ledger entries)",
        "description": "Every debit and credit line, with its voucher, "
                       "account, group and cost centre. The best starting "
                       "point for most financial reports.",
        "base": "ledger_entries",
        "related": {
            "vouchers": ["vouchers"],
            "ledgers": ["ledgers"],
            "groups": ["ledgers", "groups"],
            "master_groups": ["ledgers", "groups", "master_groups"],
            "cost_centers": ["cost_centers"],
        },
    },
    "item_entries": {
        "label": "Stock movements (item entries)",
        "description": "Every item line on every voucher, with quantity, "
                       "rate, COGS, batch and location.",
        "base": "item_entries",
        "related": {
            "vouchers": ["vouchers"],
            "inventory": ["inventory"],
            "inventory_groups": ["inventory", "inventory_groups"],
            "units": ["inventory", "units"],
            "cost_centers": ["cost_centers"],
        },
    },
    "vouchers": {
        "label": "Vouchers",
        "description": "One row per voucher - totals, dates, narration, "
                       "location and who entered it.",
        "base": "vouchers",
        "related": {
            "cost_centers": ["cost_centers"],
            "locations": ["locations"],
        },
    },
    "ledgers": {
        "label": "Accounts (ledger master)",
        "description": "The chart of accounts with balances, credit terms "
                       "and party contact details.",
        "base": "ledgers",
        "related": {
            "groups": ["groups"],
            "master_groups": ["groups", "master_groups"],
            "sub_groups": ["sub_groups"],
            "ledger_opening_balances": ["ledger_opening_balances"],
        },
    },
    "inventory": {
        "label": "Items (inventory master)",
        "description": "The item master with stock, rates, VAT and group.",
        "base": "inventory",
        "related": {
            "inventory_groups": ["inventory_groups"],
            "units": ["units"],
            "item_opening_balances": ["item_opening_balances"],
        },
    },
    "fixed_assets": {
        "label": "Fixed assets",
        "description": "The asset register with each depreciation entry.",
        "base": "fixed_assets",
        "related": {"depreciation_log": ["depreciation_log"]},
    },
    "settlements": {
        "label": "Settlements",
        "description": "Settlements and what each one was allocated against.",
        "base": "settlements",
        "related": {"settlement_allocations": ["settlement_allocations"]},
    },
    "audit_trail": {
        "label": "Audit trail",
        "description": "Who created, edited or deleted each voucher, and when.",
        "base": "audit_trail",
        "related": {"vouchers": ["vouchers"]},
    },
    "financial_years": {
        "label": "Financial years",
        "description": "Financial year master - periods, active and locked flags.",
        "base": "financial_years",
        "related": {},
    },
}


AGGREGATES = {
    "": {"label": "(group by this)", "sql": None, "numeric_only": False},
    "sum": {"label": "Sum", "sql": "SUM", "numeric_only": True},
    "avg": {"label": "Average", "sql": "AVG", "numeric_only": True},
    "min": {"label": "Minimum", "sql": "MIN", "numeric_only": False},
    "max": {"label": "Maximum", "sql": "MAX", "numeric_only": False},
    "count": {"label": "Count", "sql": "COUNT", "numeric_only": False},
    "count_distinct": {"label": "Count (distinct)", "sql": "COUNT_DISTINCT",
                       "numeric_only": False},
}

# operator -> (SQL template, how many values it consumes)
OPERATORS = {
    "eq":        {"label": "is",             "sql": "{f} = %s",                 "args": 1},
    "ne":        {"label": "is not",         "sql": "{f} <> %s",                "args": 1},
    "gt":        {"label": "greater than",   "sql": "{f} > %s",                 "args": 1},
    "gte":       {"label": "at least",       "sql": "{f} >= %s",                "args": 1},
    "lt":        {"label": "less than",      "sql": "{f} < %s",                 "args": 1},
    "lte":       {"label": "at most",        "sql": "{f} <= %s",                "args": 1},
    "contains":  {"label": "contains",       "sql": "LOWER({f}::text) LIKE %s",  "args": 1,
                  "wrap": "%{}%"},
    "not_contains": {"label": "does not contain",
                     "sql": "COALESCE(LOWER({f}::text), '') NOT LIKE %s", "args": 1,
                     "wrap": "%{}%"},
    "starts":    {"label": "starts with",    "sql": "LOWER({f}::text) LIKE %s",  "args": 1,
                  "wrap": "{}%"},
    "ends":      {"label": "ends with",      "sql": "LOWER({f}::text) LIKE %s",  "args": 1,
                  "wrap": "%{}"},
    "between":   {"label": "between",        "sql": "{f} BETWEEN %s AND %s",    "args": 2},
    "in":        {"label": "is one of",      "sql": None,                       "args": -1},
    "not_in":    {"label": "is none of",     "sql": None,                       "args": -1},
    "is_empty":  {"label": "is empty",
                  "sql": "({f} IS NULL OR {f}::text = '')",                     "args": 0},
    "not_empty": {"label": "is not empty",
                  "sql": "({f} IS NOT NULL AND {f}::text <> '')",               "args": 0},
}

SORT_DIRECTIONS = {"asc": "ASC", "desc": "DESC"}

_IDENT = re.compile(r'^[a-z_][a-z0-9_]*$')


# ============================================================
# Lookups
# ============================================================

def field_spec(table_key, column):
    """The registry entry for one column, or None if it isn't exposed."""
    table = TABLES.get(table_key)
    if not table:
        return None
    for f in table["fields"]:
        if f["name"] == column:
            return f
    return None


def parse_ref(ref):
    """'vouchers.date' -> ('vouchers', 'date'), validated against the registry."""
    if not isinstance(ref, str) or ref.count(".") != 1:
        raise ReportError(f"Unknown field '{ref}'.")
    table_key, column = ref.split(".", 1)
    if not _IDENT.match(table_key) or not _IDENT.match(column):
        raise ReportError(f"Unknown field '{ref}'.")
    if not field_spec(table_key, column):
        raise ReportError(f"Unknown field '{ref}'.")
    return table_key, column


def dataset_tables(dataset_key):
    """Every table key a dataset may reference."""
    ds = DATASETS.get(dataset_key)
    if not ds:
        raise ReportError(f"Unknown dataset '{dataset_key}'.")
    return {ds["base"]} | set(ds["related"].keys())


def describe_schema():
    """The whole registry, shaped for the builder UI.

    Aggregates and operators go out as ordered lists, not dicts: the JSON
    encoder sorts object keys, which would put "between" above "is" in the
    condition dropdown.
    """
    out = {
        "datasets": [],
        "aggregates": [dict(spec, key=key) for key, spec in AGGREGATES.items()],
        "operators": [{"key": key, "label": spec["label"], "args": spec["args"]}
                      for key, spec in OPERATORS.items()],
    }
    for key, ds in DATASETS.items():
        tables = []
        for table_key in [ds["base"]] + list(ds["related"].keys()):
            table = TABLES[table_key]
            tables.append({
                "key": table_key,
                "label": table["label"],
                "is_base": table_key == ds["base"],
                "fields": [dict(f, ref=f"{table_key}.{f['name']}")
                           for f in table["fields"]],
            })
        out["datasets"].append({
            "key": key, "label": ds["label"],
            "description": ds["description"], "tables": tables,
        })
    return out


# ============================================================
# Compilation
# ============================================================

def _needed_joins(dataset_key, table_keys):
    """The join hops required to reach every table, in dependency order."""
    ds = DATASETS[dataset_key]
    base = ds["base"]
    ordered, seen = [], {base}
    for table_key in table_keys:
        if table_key == base:
            continue
        path = ds["related"].get(table_key)
        if path is None:
            raise ReportError(
                f"'{TABLES[table_key]['label']}' is not available in the "
                f"'{ds['label']}' dataset.")
        previous = base
        for hop in path:
            if hop not in seen:
                condition = JOINS.get((previous, hop))
                if condition is None:
                    raise ReportError(
                        f"No join is defined between {previous} and {hop}.")
                ordered.append((hop, condition))
                seen.add(hop)
            previous = hop
    return ordered


def _expression(table_key, column, aggregate):
    """The SELECT expression for one chosen column."""
    alias = TABLES[table_key]["alias"]
    qualified = f"{alias}.{column}"
    spec = AGGREGATES.get(aggregate or "")
    if spec is None:
        raise ReportError(f"Unknown aggregate '{aggregate}'.")
    if spec["sql"] is None:
        return qualified, False
    field = field_spec(table_key, column)
    if spec["numeric_only"] and field["type"] != "number":
        raise ReportError(
            f"{spec['label']} needs a number column - '{field['label']}' is not one.")
    if spec["sql"] == "COUNT_DISTINCT":
        return f"COUNT(DISTINCT {qualified})", True
    if spec["sql"] in ("SUM", "AVG"):
        return f"{spec['sql']}(COALESCE({qualified}, 0))", True
    return f"{spec['sql']}({qualified})", True


def _filter_clause(table_key, column, operator, values):
    """(SQL fragment, params) for one filter row."""
    spec = OPERATORS.get(operator)
    if spec is None:
        raise ReportError(f"Unknown filter operator '{operator}'.")
    alias = TABLES[table_key]["alias"]
    field = field_spec(table_key, column)
    qualified = f"{alias}.{column}"

    values = [v for v in (values or []) if v is not None and str(v) != ""]

    if spec["args"] == 0:
        return spec["sql"].format(f=qualified), []

    if operator in ("in", "not_in"):
        if not values:
            raise ReportError(f"'{field['label']}' needs at least one value.")
        placeholders = ", ".join(["%s"] * len(values))
        negate = "NOT " if operator == "not_in" else ""
        return f"{qualified} {negate}IN ({placeholders})", [str(v) for v in values]

    if len(values) < spec["args"]:
        raise ReportError(
            f"'{field['label']}' {spec['label']} needs "
            f"{spec['args']} value{'s' if spec['args'] > 1 else ''}.")

    params = []
    for value in values[:spec["args"]]:
        if field["type"] == "number":
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise ReportError(
                    f"'{field['label']}' is a number - '{value}' is not.")
        elif "wrap" in spec:
            value = spec["wrap"].format(str(value).lower())
        params.append(value)

    return spec["sql"].format(f=qualified), params


def _auto_label(ds, table_key, field, aggregate):
    """A readable heading, without repeating a word the field label already has.

    'Voucher' + 'Voucher Date' would read "Voucher Voucher Date", so the table
    name is only prefixed when it actually adds information.
    """
    label = field["label"]
    if table_key != ds["base"]:
        table_label = TABLES[table_key]["label"]
        words = {w.lower() for w in table_label.split()}
        if not words & {w.lower() for w in label.split()}:
            label = f"{table_label} {label}"

    agg = AGGREGATES[aggregate or ""]
    if not agg["sql"]:
        return label
    if agg["sql"].startswith("COUNT"):
        return f"{agg['label']} of {label}"
    return f"{agg['label']} of {label}"


def compile_report(definition, company_id):
    """(sql, params, columns) for a report definition.

    `columns` is the list of {label, type} describing the result, in order.
    """
    if not company_id:
        raise ReportError("No company is selected.")

    dataset_key = definition.get("dataset")
    if dataset_key not in DATASETS:
        raise ReportError("Choose a dataset first.")
    ds = DATASETS[dataset_key]
    allowed = dataset_tables(dataset_key)

    chosen = definition.get("columns") or []
    if not chosen:
        raise ReportError("Choose at least one column.")
    if len(chosen) > 40:
        raise ReportError("That is more than 40 columns - trim the selection.")

    selects, headers, group_by, used = [], [], [], set()
    has_aggregate = False

    for item in chosen:
        table_key, column = parse_ref(item.get("field"))
        if table_key not in allowed:
            raise ReportError(
                f"'{TABLES[table_key]['label']}' is not part of this dataset.")
        used.add(table_key)
        expression, aggregated = _expression(table_key, column,
                                             item.get("aggregate"))
        has_aggregate = has_aggregate or aggregated
        if not aggregated:
            group_by.append(expression)
        selects.append(expression)

        field = field_spec(table_key, column)
        label = (item.get("label") or "").strip()
        if not label:
            label = _auto_label(ds, table_key, field, item.get("aggregate"))
        headers.append({
            "label": label,
            "type": "number" if aggregated and item.get("aggregate") != "min"
                    and item.get("aggregate") != "max" else field["type"],
        })

    if definition.get("count_rows"):
        selects.append("COUNT(*)")
        headers.append({"label": "Row Count", "type": "number"})
        has_aggregate = True

    # Filters
    where = []
    params = []
    for row in (definition.get("filters") or []):
        table_key, column = parse_ref(row.get("field"))
        if table_key not in allowed:
            raise ReportError(
                f"'{TABLES[table_key]['label']}' is not part of this dataset.")
        used.add(table_key)
        values = row.get("values")
        if values is None:
            values = [row.get("value"), row.get("value2")]
        clause, clause_params = _filter_clause(table_key, column,
                                               row.get("operator") or "eq", values)
        where.append(clause)
        params.extend(clause_params)

    # Sorting - by position, so it works for aggregates too
    order = []
    for row in (definition.get("sort") or []):
        direction = SORT_DIRECTIONS.get((row.get("direction") or "asc").lower())
        if direction is None:
            raise ReportError("Sort direction must be ascending or descending.")
        ref = row.get("field")
        index = None
        for position, item in enumerate(chosen, start=1):
            if item.get("field") == ref:
                index = position
                break
        if index is None:
            raise ReportError("You can only sort by a column you selected.")
        order.append(f"{index} {direction}")

    try:
        limit = int(definition.get("limit") or DEFAULT_ROWS)
    except (TypeError, ValueError):
        limit = DEFAULT_ROWS
    limit = max(1, min(limit, MAX_ROWS))

    # Assemble. Every table gets its own company_id filter.
    base_alias = TABLES[ds["base"]]["alias"]
    sql = [f"SELECT {'DISTINCT ' if definition.get('distinct') and not has_aggregate else ''}"
           + ", ".join(selects)]
    sql.append(f"FROM {TABLES[ds['base']]['table']} {base_alias}")

    join_params = []
    for table_key, condition in _needed_joins(dataset_key, used):
        table = TABLES[table_key]
        sql.append(f"LEFT JOIN {table['table']} {table['alias']} ON {condition} "
                   f"AND {table['alias']}.company_id = %s")
        join_params.append(company_id)

    sql.append(f"WHERE {base_alias}.company_id = %s")
    where_params = [company_id]
    for clause in where:
        sql.append(f"AND {clause}")
    where_params.extend(params)

    if has_aggregate and group_by:
        sql.append("GROUP BY " + ", ".join(group_by))
    if order:
        sql.append("ORDER BY " + ", ".join(order))
    sql.append("LIMIT %s")

    return "\n".join(sql), join_params + where_params + [limit], headers


def run_report(definition, company_id):
    """Compile, execute and return {columns, rows, sql, truncated}."""
    from database.config import get_connection

    from accounting_app.models import format_display_date

    sql, params, headers = compile_report(definition, company_id)
    limit = params[-1]

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, tuple(params))
        raw = cursor.fetchall()
        rows = []
        for r in raw:
            if hasattr(r, "values") and not isinstance(r, (list, tuple)):
                row = list(r.values())
            else:
                row = list(r)
            # DD-MM-YYYY on the way out: the screen, the downloads and the
            # chat exporter all read these same rows. Applied to every value
            # rather than only the columns typed "date", so timestamps like
            # "Entered On" read the same way; anything that is not a whole
            # date is returned untouched.
            rows.append([format_display_date(v) for v in row])
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {
        "columns": [h["label"] for h in headers],
        "types": [h["type"] for h in headers],
        "rows": rows,
        "sql": sql,
        "truncated": len(rows) >= limit,
        "limit": limit,
    }
