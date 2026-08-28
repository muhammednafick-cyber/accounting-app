"""Text-to-SQL over the accounting database, powered by OpenRouter.

The model only ever writes a SELECT; `validate_sql` re-checks every query
against an allow-list before it reaches the database, and the connection runs
read-only. Table names such as `users` or `system_settings` are deliberately
unreachable, so no prompt can talk the model into reading password hashes or
API keys.
"""
import datetime
import re


# Reasoning models (gpt-oss, o-series, R1) spend completion tokens thinking
# before they emit a single character, so a tight cap truncates the answer to
# nothing. Keep this generous - SQL itself is short.
DEFAULT_MAX_TOKENS = 2000


def _chat(messages, temperature=0.0, max_tokens=DEFAULT_MAX_TOKENS):
    """One OpenRouter chat completion, returning the raw assistant text."""
    from .chatbot_service import (
        OPENROUTER_URL,
        get_openrouter_api_key,
        get_openrouter_model,
        openrouter_request,
    )

    api_key = get_openrouter_api_key()
    if not api_key:
        raise AiUnavailable("OpenRouter API key is not configured.")

    payload = {
        "model": get_openrouter_model(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:5000",
    }

    result, err = openrouter_request(payload, headers)
    if err:
        raise AiUnavailable(err)
    try:
        choice = result["choices"][0]
        content = choice["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise AiUnavailable("OpenRouter returned no usable content.")

    # Truncated mid-answer - retrying with the same cap would truncate again,
    # so say so plainly rather than letting broken SQL reach the guard.
    if choice.get("finish_reason") == "length" and not content.strip():
        raise AiUnavailable(
            "the model used its entire " + str(max_tokens) + "-token budget on "
            "reasoning and returned nothing - raise the limit or pick a "
            "non-reasoning model in AI Settings"
        )

    return content


class AiUnavailable(RuntimeError):
    """OpenRouter could not be reached or is not configured."""


# ----------------------------------------------------------------- SQL safety

# Only business data. `users`, `user_permissions`, `system_settings` and friends
# are deliberately absent - the model must never be able to read password
# hashes or API keys, no matter what the user asks it.
ALLOWED_TABLES = {
    "vouchers", "ledger_entries", "item_entries", "ledgers", "groups",
    "master_groups", "sub_groups", "inventory", "inventory_groups",
    "item_opening_balances", "ledger_opening_balances", "locations",
    "cost_centers", "units", "financial_years", "fixed_assets",
    "depreciation_log", "settlements", "settlement_allocations",
    "selling_prices", "recurring_templates", "additional_charge_entries",
    "company_settings", "companies", "audit_trail",
}

# Anything that writes, changes structure, or chains statements.
FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|"
    r"vacuum|merge|call|do|execute|prepare|listen|notify|set|reset|begin|"
    r"commit|rollback|savepoint|pg_sleep|pg_read_file|pg_ls_dir|lo_import|"
    r"lo_export|dblink|current_setting|pg_shadow|pg_authid)\b",
    re.IGNORECASE,
)

MAX_ROWS = 200
DEFAULT_LIMIT = 50


class SqlGuardError(ValueError):
    """The generated SQL was rejected before it ever reached the database."""


SCHEMA_SUMMARY = """
IMPORTANT: every date column in this database is stored as TEXT in
'YYYY-MM-DD' format (never a real DATE type). Compare them as strings.

vouchers(company_id, voucher_number, voucher_type, date, amount, narration,
         location_name, cost_center_code, due_date, created_by)
    voucher_type is one of: Sales, Purchase, Receipt, Payment, Contra, Journal,
    Expense, Credit Note, Debit Note.

ledger_entries(company_id, voucher_number, ledger_name, amount, type, cost_center_code)
    type is 'Debit' or 'Credit'. Joins to vouchers on voucher_number + company_id.

item_entries(company_id, voucher_number, item_name, quantity, unit_price, amount,
             ledger_name, type, location_name, cogs_amount, batch_number, expiry_date)

ledgers(company_id, ledger_code, ledger_name, group_code, opening_balance,
        opening_balance_type, closing_balance, credit_days, is_active,
        address, contact_person, phone, email, trn)

groups(company_id, group_code, group_name, nature, master_group_code)
    nature is one of: Assets, Liabilities, Income, Expenses.

inventory(company_id, item_code, name, stock_group_code, unit_code, unit_price,
          stock_quantity, vat_rate, stock_value, is_active)

locations(company_id, location_code, location_name, is_default, is_active)
cost_centers(company_id, center_code, center_name, is_active)
financial_years(company_id, fy_code, start_date, end_date, is_active, is_locked)
fixed_assets(company_id, asset_name, asset_code, purchase_date, purchase_cost,
             accumulated_depreciation, depreciation_rate, status)
settlements(company_id, settlement_number, settlement_date, ledger_name, total_amount)
company_settings(company_id, company_name, currency_code, financial_year_start)
""".strip()


SQL_SYSTEM_PROMPT = """You write a single read-only PostgreSQL SELECT query.

Schema:
{schema}

Today is {today}.

Rules - all mandatory:
1. Output ONLY the SQL. No explanation, no markdown fences, no trailing semicolon.
2. SELECT queries only. Never INSERT, UPDATE, DELETE, or any DDL.
3. EVERY table you reference must be filtered by company_id = {company_id}.
4. Always add a LIMIT (at most {max_rows}).
5. Use only the tables and columns listed above. Invent nothing.
6. Amounts:
   - Grouping by ledger, party, customer, supplier or cost centre? Sum
     ledger_entries.amount. Never SUM(vouchers.amount) in a query that joins
     ledger_entries - the voucher total belongs to the whole voucher, so it is
     both the wrong figure for one party and double-counted once a voucher has
     more than one matching line.
   - Grouping by item? Sum item_entries.amount the same way.
   - Only for a single company-wide total with no per-party grouping may you
     use SUM(vouchers.amount) with the right voucher_type.
7. Give computed columns readable aliases (e.g. AS total_sales).
8. Dates are TEXT ('YYYY-MM-DD'). Never use EXTRACT, DATE_TRUNC, DATE_PART or
   any date function on them - it will fail. Filter with plain string ranges:
     a whole year   -> date >= '2023-01-01' AND date <= '2023-12-31'
     a single month -> date >= '2023-07-01' AND date <= '2023-07-31'
   To group by year or month use LEFT(date, 4) or LEFT(date, 7).
9. Customers and suppliers are ledgers, identified by the group nature of the
   ledger on the party side of the voucher:
     sales by customer -> Sales vouchers, ledger_entries.type = 'Debit', the
       ledger's group nature is 'Assets', and the group_name is NOT
       'Inventory' or 'Fixed Assets' (leaving debtors, cash and bank).
     purchases by supplier -> Purchase vouchers, ledger_entries.type =
       'Credit', and the ledger's group nature is 'Liabilities'.
   Join ledger_entries -> ledgers (on ledger_name + company_id) -> groups (on
   group_code + company_id) to reach nature. This excludes the income,
   inventory, VAT and cost-of-goods lines on the same voucher.
"""


def _strip_sql(text):
    """Pull the bare SQL out of whatever the model wrapped it in."""
    text = (text or "").strip()

    if "```" in text:
        blocks = re.findall(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if blocks:
            text = blocks[0].strip()

    # Drop any leading prose before the first SELECT / WITH.
    match = re.search(r"\b(select|with)\b", text, re.IGNORECASE)
    if match:
        text = text[match.start():]

    return text.strip().rstrip(";").strip()


# `FROM` also shows up inside EXTRACT/SUBSTRING/TRIM/POSITION, where the word
# after it is a column or a literal - not a table. Scrubbed before scanning.
_FROM_IN_FUNCTION = re.compile(
    r"\b(extract|substring|trim|position|overlay)\s*\(([^()]*)\)",
    re.IGNORECASE | re.DOTALL,
)


def _referenced_tables(sql):
    """Table names appearing after FROM or JOIN."""
    # Blank out the argument list of functions that use FROM as a separator, so
    # EXTRACT(YEAR FROM v.date) doesn't read as a table called "v".
    scrubbed = _FROM_IN_FUNCTION.sub(
        lambda m: m.group(1) + "(" + " " * len(m.group(2)) + ")", sql
    )

    # CTE names are query-local, not real tables.
    cte_names = {
        m.group(1).lower()
        for m in re.finditer(
            r"(?:\bwith\b|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(",
            scrubbed,
            re.IGNORECASE,
        )
    }

    found = set()
    for match in re.finditer(
        r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*(\.?)",
        scrubbed,
        re.IGNORECASE,
    ):
        # `FROM v.date` - a qualified column reference, not a table.
        if match.group(2) == ".":
            continue
        name = match.group(1).lower()
        if name not in cte_names:
            found.add(name)
    return found


def validate_sql(sql, company_id):
    """Reject anything that is not a safe, company-scoped, single SELECT.

    Returns the cleaned SQL (with a LIMIT applied). Raises SqlGuardError.
    """
    sql = _strip_sql(sql)

    if not sql:
        raise SqlGuardError("no SQL was produced")

    if ";" in sql:
        raise SqlGuardError("multiple statements are not allowed")

    if not re.match(r"^\s*(select|with)\b", sql, re.IGNORECASE):
        raise SqlGuardError("only SELECT queries are allowed")

    # Strip string literals before keyword scanning so a ledger named
    # "Update Charges" doesn't trip the filter.
    scannable = re.sub(r"'[^']*'", "''", sql)

    if FORBIDDEN_SQL.search(scannable):
        raise SqlGuardError("the query contains a forbidden operation")

    if "--" in scannable or "/*" in scannable:
        raise SqlGuardError("SQL comments are not allowed")

    tables = _referenced_tables(scannable)
    if not tables:
        raise SqlGuardError("could not identify which tables the query reads")

    blocked = tables - ALLOWED_TABLES
    if blocked:
        raise SqlGuardError(
            f"access to table(s) {', '.join(sorted(blocked))} is not permitted"
        )

    if f"company_id" not in scannable.lower():
        raise SqlGuardError("the query is not scoped to a company")

    # Enforce a row cap regardless of what the model asked for.
    limit_match = re.search(r"\blimit\s+(\d+)\s*$", sql, re.IGNORECASE)
    if limit_match:
        if int(limit_match.group(1)) > MAX_ROWS:
            sql = sql[: limit_match.start()] + f"LIMIT {MAX_ROWS}"
    else:
        sql = f"{sql} LIMIT {DEFAULT_LIMIT}"

    return sql


def generate_sql(question, company_id, feedback=None):
    """Ask the model for a SELECT, then validate it. Returns clean SQL.

    `feedback` carries the previous attempt's SQL and error back to the model
    so it can repair its own mistake instead of failing outright.
    """
    system = SQL_SYSTEM_PROMPT.format(
        schema=SCHEMA_SUMMARY,
        today=datetime.date.today().isoformat(),
        company_id=int(company_id),
        max_rows=MAX_ROWS,
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]
    if feedback:
        prev_sql, error = feedback
        messages.append({"role": "assistant", "content": prev_sql})
        messages.append({
            "role": "user",
            "content": (
                "That query failed with:\n" + str(error) + "\n\n"
                "Rewrite it so it runs. Output only the corrected SQL."
            ),
        })

    return validate_sql(_chat(messages, max_tokens=DEFAULT_MAX_TOKENS), company_id)


def run_sql(sql, timeout_ms=15000):
    """Execute validated SQL read-only. Returns (columns, rows)."""
    from database.config import get_connection

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Belt and braces: even if the guard were bypassed, the transaction
        # itself cannot write.
        try:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
        except Exception:
            conn.rollback()  # SQLite and older servers - guard still applies.

        cursor.execute(sql)
        columns = [d[0] for d in (cursor.description or [])]
        rows = cursor.fetchmany(MAX_ROWS)
        return columns, [list(r) for r in rows]
    finally:
        try:
            conn.rollback()
        except Exception:
            pass
        cursor.close()
        conn.close()


def _format_value(value):
    if value is None:
        return "-"
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.strftime("%d-%m-%Y")
    if isinstance(value, float):
        return f"{value:,.2f}"
    try:
        from decimal import Decimal
        if isinstance(value, Decimal):
            return f"{float(value):,.2f}"
    except ImportError:
        pass
    return str(value)


def rows_to_html_table(columns, rows, max_display=15):
    """Render results as the simple HTML the chat widget already renders."""
    if not rows:
        return "The query ran successfully but returned no rows."

    head = " | ".join(f"<b>{c}</b>" for c in columns)
    lines = [head]
    for row in rows[:max_display]:
        lines.append(" | ".join(_format_value(v) for v in row))
    if len(rows) > max_display:
        lines.append(f"<i>...and {len(rows) - max_display} more row(s).</i>")
    return "<br>".join(lines)


def rows_to_text(columns, rows, max_display=25):
    """Compact text rendering, used as context for the summarising model."""
    if not rows:
        return "(no rows)"
    lines = [" | ".join(str(c) for c in columns)]
    for row in rows[:max_display]:
        lines.append(" | ".join(_format_value(v) for v in row))
    return "\n".join(lines)


SUMMARY_SYSTEM_PROMPT = """You state what a database result shows, for an accountant.

Rules:
- Use ONLY the numbers in the result table. Never compute a figure that is not
  shown, and never round or adjust one.
- One or two short sentences. State only what the rows show.
- Do not add currency symbols - the figures are already in the company currency.
- Do not comment on what is missing, absent, or not included.
- Write plain prose only. Never output a table, a bulleted list, markdown, or
  pipe characters - the full table is already displayed directly beneath your
  sentence, so repeating the rows is duplication.
- If the result is empty, say no matching records were found."""


def _numbers_in(text):
    """Every numeric token in a string, normalised (thousands separators and
    trailing zeros removed) so 1,234.50 and 1234.5 compare equal."""
    found = set()
    for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", str(text)):
        cleaned = raw.replace(",", "")
        try:
            found.add(f"{float(cleaned):.4f}")
        except ValueError:
            continue
    return found


def verify_summary(summary, columns, rows):
    """Drop a summary that cites figures the result set does not contain.

    A model summarising an accounting result must never invent a number. If it
    does, the sentence is discarded and the user still gets the real table.
    """
    if not summary:
        return ""

    # Any table-ish formatting is duplication of the real table below it.
    if "|" in summary or re.search(r"^\s*[-*]\s", summary, re.MULTILINE):
        return ""

    grounded = _numbers_in(rows_to_text(columns, rows, max_display=len(rows) or 1))
    grounded.update(_numbers_in(" ".join(str(c) for c in columns)))
    # Small integers are ordinary prose ("the top 3 ledgers"), not claims.
    for n in range(0, 101):
        grounded.add(f"{float(n):.4f}")

    if _numbers_in(summary) - grounded:
        return ""

    return summary


def summarise_rows(question, columns, rows):
    """One grounded sentence describing the result. Never invents figures."""
    try:
        return _chat(
            [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Question: " + question + "\n\n"
                        "Result:\n" + rows_to_text(columns, rows)
                    ),
                },
            ],
            temperature=0.0,
            max_tokens=200,
        ).strip()
    except AiUnavailable:
        return ""


def _export_title(question):
    """A short, filesystem-safe name derived from the question."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", (question or "").strip()).strip("_")
    return (slug[:60] or "chat_result").lower()


MAX_SQL_ATTEMPTS = 3


def answer_from_database(question, company_id):
    """Question -> guarded SELECT -> rows -> plain-English answer.

    Retries on failure, feeding the real error back to the model each time.
    """
    feedback = None
    last_error = None
    sql = None

    for attempt in range(1, MAX_SQL_ATTEMPTS + 1):
        try:
            sql = generate_sql(question, company_id, feedback=feedback)
        except AiUnavailable as exc:
            return {
                "intent": "database_query",
                "response": (
                    "The AI service is unavailable, so I couldn't build that "
                    "query. Check the OpenRouter API key in AI Settings."
                ),
                "data": None,
                "explanation": "OpenRouter error: " + str(exc),
            }
        except SqlGuardError as exc:
            last_error = "rejected by the safety guard: " + str(exc)
            print("[ai-sql] attempt " + str(attempt) + " " + last_error)
            feedback = (sql or "", last_error)
            continue

        try:
            columns, rows = run_sql(sql)
        except Exception as exc:
            last_error = str(exc)
            print("[ai-sql] attempt " + str(attempt) + " failed: " + last_error)
            print("[ai-sql] sql was: " + sql)
            feedback = (sql, last_error)
            continue

        summary = verify_summary(
            summarise_rows(question, columns, rows), columns, rows
        )
        table = rows_to_html_table(columns, rows)
        response = summary + "<br><br>" + table if summary else table

        # Park the rows so a follow-up ("give it in excel") can export them.
        if rows:
            from .chat_export_store import remember

            remember(columns, rows, title=_export_title(question))

        return {
            "intent": "database_query",
            "response": response,
            "data": {
                "sql": sql,
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
            },
            "explanation": "Answered by querying the database directly.",
        }

    return {
        "intent": "database_query",
        "response": (
            "I couldn't build a working query for that after "
            + str(MAX_SQL_ATTEMPTS) + " attempts. "
            "Try naming the ledger, item, or period explicitly."
        ),
        "data": {"sql": sql},
        "explanation": "Last error: " + str(last_error),
    }
