"""Vouchers an AI agent has suggested, waiting for a person to agree.

An agent must not post to the books. Two reasons, and the second is the one
people miss:

  * it can misread the scope of an instruction - "post the ALMARAI invoices"
    might mean five or fifty - and a wrong guess writes real vouchers that can
    only be reversed, never removed;
  * the text it reads is not all ours. Narrations, party names and item names
    arrive from imported supplier spreadsheets and invoices, so an agent
    reading the books is reading text a supplier controls. While it can only
    read, that is harmless. If it could post, an instruction hidden in a
    narration would be an instruction carrying our own credentials.

So an agent writes here instead: a proposal, holding exactly what it would
post, which does nothing until a person approves it. Approval then runs the
ordinary posting path, so every rule that applies to a typed voucher applies to
an approved one - the financial year check, the balance check, all of it.
"""
import json

from .config import get_connection

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
FAILED = "failed"


def init_agent_proposal_table():
    """Create the table. Safe to call repeatedly."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_proposals (
                id BIGSERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL,
                proposed_for_user_id INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                summary TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                decided_at TIMESTAMP,
                decided_by_user_id INTEGER,
                decision_note TEXT,
                voucher_number TEXT
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_agent_proposals_pending
            ON agent_proposals (company_id, status, created_at DESC)
        """)
        conn.commit()
    finally:
        conn.close()


def create_proposal(company_id, user_id, action_type, summary, payload):
    """Record what the agent would like to post. Returns the proposal id."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO agent_proposals
                (company_id, proposed_for_user_id, action_type, summary, payload_json)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (company_id, user_id, action_type, summary,
              json.dumps(payload, default=str)))
        proposal_id = cursor.fetchone()[0]
        conn.commit()
        return proposal_id
    finally:
        conn.close()


def get_proposal(proposal_id, company_id):
    """One proposal, scoped to its company so an id cannot be guessed across."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, company_id, proposed_for_user_id, action_type, summary,
                   payload_json, status, created_at, decided_at,
                   decided_by_user_id, decision_note, voucher_number
            FROM agent_proposals
            WHERE id = %s AND company_id = %s
        """, (proposal_id, company_id))
        return _as_dict(cursor.fetchone())
    finally:
        conn.close()


def list_proposals(company_id, status=None, limit=100):
    """Proposals for one company, newest first."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        if status:
            cursor.execute("""
                SELECT id, company_id, proposed_for_user_id, action_type, summary,
                       payload_json, status, created_at, decided_at,
                       decided_by_user_id, decision_note, voucher_number
                FROM agent_proposals
                WHERE company_id = %s AND status = %s
                ORDER BY created_at DESC LIMIT %s
            """, (company_id, status, limit))
        else:
            cursor.execute("""
                SELECT id, company_id, proposed_for_user_id, action_type, summary,
                       payload_json, status, created_at, decided_at,
                       decided_by_user_id, decision_note, voucher_number
                FROM agent_proposals
                WHERE company_id = %s
                ORDER BY created_at DESC LIMIT %s
            """, (company_id, limit))
        return [_as_dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def count_pending(company_id):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM agent_proposals "
                       "WHERE company_id = %s AND status = %s",
                       (company_id, PENDING))
        return cursor.fetchone()[0]
    except Exception:
        return 0
    finally:
        conn.close()


def claim_proposal(proposal_id, company_id):
    """Move a proposal out of 'pending' so it can only be acted on once.

    The update is the claim: two people pressing Approve at the same moment,
    or one person double-clicking, cannot both go on to post. Returns the
    proposal when this caller won it, None when it was already decided.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE agent_proposals SET status = 'deciding'
            WHERE id = %s AND company_id = %s AND status = %s
        """, (proposal_id, company_id, PENDING))
        won = cursor.rowcount > 0
        conn.commit()
        return get_proposal(proposal_id, company_id) if won else None
    finally:
        conn.close()


def settle_proposal(proposal_id, company_id, status, decided_by_user_id,
                    note=None, voucher_number=None):
    """Record how a claimed proposal turned out."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE agent_proposals
               SET status = %s, decided_at = CURRENT_TIMESTAMP,
                   decided_by_user_id = %s, decision_note = %s,
                   voucher_number = %s
             WHERE id = %s AND company_id = %s
        """, (status, decided_by_user_id, note, voucher_number,
              proposal_id, company_id))
        conn.commit()
    finally:
        conn.close()


def release_proposal(proposal_id, company_id):
    """Put a claimed proposal back, when deciding it failed unexpectedly."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE agent_proposals SET status = %s
             WHERE id = %s AND company_id = %s AND status = 'deciding'
        """, (PENDING, proposal_id, company_id))
        conn.commit()
    finally:
        conn.close()


def _as_dict(row):
    if not row:
        return None
    return {
        "id": row[0],
        "company_id": row[1],
        "proposed_for_user_id": row[2],
        "action_type": row[3],
        "summary": row[4],
        "payload": json.loads(row[5]) if row[5] else {},
        "status": row[6],
        "created_at": row[7],
        "decided_at": row[8],
        "decided_by_user_id": row[9],
        "decision_note": row[10],
        "voucher_number": row[11],
    }
