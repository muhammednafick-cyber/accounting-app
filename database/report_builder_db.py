"""Saved definitions for the Custom Report Builder.

A saved report is just the builder's own JSON - dataset, columns, filters,
sorting - never SQL. It is recompiled through report_builder.compile_report
every time it runs, so a definition saved by one user can never widen what
another user is allowed to see.
"""
import datetime
import json

from .config import get_connection
from .company_db import get_current_company_id


def init_report_builder_tables():
    """Create the saved-report table. Safe to call repeatedly."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS custom_reports (
                id SERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                dataset TEXT NOT NULL,
                definition_json TEXT NOT NULL,
                created_by TEXT,
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(company_id, name)
            )
        """)
        conn.commit()
    finally:
        conn.close()


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def list_reports(company_id=None):
    """Every saved report for this company, newest change first."""
    company_id = company_id or get_current_company_id()
    if not company_id:
        return []
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, COALESCE(description, ''), dataset,
                   COALESCE(created_by, ''), COALESCE(updated_at, created_at, '')
            FROM custom_reports WHERE company_id = %s
            ORDER BY COALESCE(updated_at, created_at) DESC, name
        """, (company_id,))
        return [{"id": r[0], "name": r[1], "description": r[2], "dataset": r[3],
                 "created_by": r[4], "updated_at": r[5]}
                for r in cursor.fetchall()]
    except Exception as exc:
        print(f"[report-builder] list_reports: {exc}")
        return []
    finally:
        conn.close()


def get_report(report_id, company_id=None):
    """One saved report, or None. Scoped to the company - never cross-tenant."""
    company_id = company_id or get_current_company_id()
    if not company_id:
        return None
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, COALESCE(description, ''), dataset, definition_json
            FROM custom_reports WHERE id = %s AND company_id = %s
        """, (report_id, company_id))
        row = cursor.fetchone()
        if not row:
            return None
        try:
            definition = json.loads(row[4])
        except (ValueError, TypeError):
            definition = {}
        return {"id": row[0], "name": row[1], "description": row[2],
                "dataset": row[3], "definition": definition}
    finally:
        conn.close()


def save_report(name, description, definition, company_id=None, created_by=None,
                report_id=None):
    """Insert or update a saved report. Returns its id."""
    company_id = company_id or get_current_company_id()
    if not company_id:
        raise ValueError("No company is selected.")
    name = (name or "").strip()
    if not name:
        raise ValueError("Give the report a name.")
    if len(name) > 120:
        raise ValueError("That name is too long (120 characters maximum).")

    payload = json.dumps(definition)
    dataset = definition.get("dataset") or ""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        if report_id:
            cursor.execute("""
                UPDATE custom_reports
                SET name = %s, description = %s, dataset = %s,
                    definition_json = %s, updated_at = %s
                WHERE id = %s AND company_id = %s
            """, (name, description, dataset, payload, _now(), report_id, company_id))
            if cursor.rowcount == 0:
                raise ValueError("That report no longer exists.")
            conn.commit()
            return report_id

        # A repeated name overwrites its own report rather than failing on the
        # unique key - saving again after a tweak is the common case.
        cursor.execute(
            "SELECT id FROM custom_reports WHERE company_id = %s AND name = %s",
            (company_id, name))
        existing = cursor.fetchone()
        if existing:
            cursor.execute("""
                UPDATE custom_reports
                SET description = %s, dataset = %s, definition_json = %s,
                    updated_at = %s
                WHERE id = %s AND company_id = %s
            """, (description, dataset, payload, _now(), existing[0], company_id))
            conn.commit()
            return existing[0]

        cursor.execute("""
            INSERT INTO custom_reports
                (company_id, name, description, dataset, definition_json,
                 created_by, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """, (company_id, name, description, dataset, payload, created_by,
              _now(), _now()))
        new_id = cursor.fetchone()[0]
        conn.commit()
        return new_id
    finally:
        conn.close()


def delete_report(report_id, company_id=None):
    company_id = company_id or get_current_company_id()
    if not company_id:
        return False
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM custom_reports WHERE id = %s AND company_id = %s",
            (report_id, company_id))
        deleted = cursor.rowcount
        conn.commit()
        return deleted > 0
    finally:
        conn.close()
