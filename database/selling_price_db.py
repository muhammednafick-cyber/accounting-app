"""
Selling Price Master module.

Maintains selling prices separately from the inventory (item) master.
Keyed by (company_id, item_code).
"""
from .config import get_connection
from .company_db import get_current_company_id


def _ensure_table(cursor):
    """Create the selling_prices table if it does not exist yet (safe for both PG and SQLite)."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS selling_prices (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            item_code TEXT NOT NULL,
            selling_price DOUBLE PRECISION NOT NULL DEFAULT 0,
            updated_at TEXT,
            UNIQUE(company_id, item_code)
        )
    """)


def upsert_selling_price(item_code, selling_price, company_id=None, db_connection=None):
    """Create or update the Selling Price Master record for an item."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        raise Exception("No company selected")
    if not item_code:
        raise Exception("Item code is required")

    selling_price = round(float(selling_price), 2)
    if selling_price < 0:
        raise Exception("Selling price cannot be negative")

    conn = db_connection or get_connection()
    try:
        cursor = conn.cursor()
        _ensure_table(cursor)
        cursor.execute("""
            INSERT INTO selling_prices (company_id, item_code, selling_price, updated_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (company_id, item_code)
            DO UPDATE SET selling_price = EXCLUDED.selling_price, updated_at = CURRENT_TIMESTAMP
        """, (company_id, str(item_code).strip(), selling_price))
        if db_connection is None:
            conn.commit()
        print(f"upsert_selling_price: {item_code} = {selling_price} (Company: {company_id})")
        return selling_price
    finally:
        if db_connection is None:
            conn.close()


def get_selling_price_map(company_id=None):
    """Return {item_code: selling_price} for the company."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return {}

    conn = get_connection()
    try:
        cursor = conn.cursor()
        _ensure_table(cursor)
        cursor.execute(
            "SELECT item_code, selling_price FROM selling_prices WHERE company_id = %s",
            (company_id,),
        )
        return {row['item_code']: row['selling_price'] for row in cursor.fetchall()}
    finally:
        conn.close()


def get_selling_prices_with_items(company_id=None):
    """All inventory items joined with their selling prices (None if not set)."""
    if company_id is None:
        company_id = get_current_company_id()
    if not company_id:
        return []

    conn = get_connection()
    try:
        cursor = conn.cursor()
        _ensure_table(cursor)
        cursor.execute("""
            SELECT
                i.item_code,
                i.name,
                ig.group_name,
                i.unit_code,
                sp.selling_price,
                sp.updated_at
            FROM inventory i
            LEFT JOIN inventory_groups ig
                ON i.stock_group_code = ig.group_code AND i.company_id = ig.company_id
            LEFT JOIN selling_prices sp
                ON sp.item_code = i.item_code AND sp.company_id = i.company_id
            WHERE i.company_id = %s
            ORDER BY i.name
        """, (company_id,))
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()
