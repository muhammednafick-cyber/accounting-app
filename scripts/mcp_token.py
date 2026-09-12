#!/opt/accounting/venv/bin/python
"""Issue, list and revoke the tokens an agent connects with.

A token names one user and one company. The agent it is given to can read
exactly what that person could read by hand in that company, and nothing in
another company - the company is fixed in the token, not chosen per request.

    mcp_token.py issue <username> <company_id> [label]
    mcp_token.py list
    mcp_token.py revoke <token-prefix>

The token is shown once, when it is issued. There is no way to read it back:
only its first characters are stored in view, so a leaked token is replaced
rather than recovered.
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".env"))

from database.config import get_connection          # noqa: E402

# An agent token is longer-lived than a phone's: it sits in a connector's
# settings and nobody wants to re-paste it monthly.
AGENT_TOKEN_DAYS = 365


def _rows(query, params=None):
    # params stays None rather than an empty tuple: with a tuple psycopg2 still
    # runs its own % interpolation, and a LIKE pattern containing a literal %
    # then fails with "tuple index out of range".
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchall()
    finally:
        conn.close()


def _execute(query, params=None):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def issue(username, company_id, label):
    import secrets

    found = _rows("SELECT id, is_admin FROM users WHERE username = %s", (username,))
    if not found:
        sys.exit(f"No user called {username!r}.")
    user_id, is_admin = found[0][0], found[0][1]

    company = _rows("SELECT name FROM companies WHERE id = %s", (company_id,))
    if not company:
        sys.exit(f"No company with id {company_id}.")

    if not is_admin:
        allowed = _rows("SELECT 1 FROM user_company_access "
                        "WHERE user_id = %s AND company_id = %s",
                        (user_id, company_id))
        if not allowed:
            sys.exit(f"{username} has no access to {company[0][0]!r}. "
                     "Grant it in the application first - this tool will not "
                     "hand out access the person does not already have.")

    token = secrets.token_urlsafe(32)
    _execute("INSERT INTO api_tokens (token, user_id, company_id, device, expires_at) "
             "VALUES (%s, %s, %s, %s, %s)",
             (token, user_id, company_id, f"MCP · {label}"[:120],
              datetime.now() + timedelta(days=AGENT_TOKEN_DAYS)))

    print(f"\nToken for {username} on {company[0][0]!r}, valid one year.")
    print("Shown once - copy it now.\n")
    print(f"  {token}\n")
    print("In Claude: Settings -> Connectors -> Add custom connector.")
    print("  URL:   https://account.prodataelevate.com/mcp")
    print("  Token: the value above, as a bearer token\n")
    if is_admin:
        print("Note: this user is an administrator, so the agent can read "
              "everything in that company.\n")


def listing():
    rows = _rows("""
        SELECT t.token, u.username, c.name, t.device, t.expires_at, t.last_used
        FROM api_tokens t
        JOIN users u ON u.id = t.user_id
        LEFT JOIN companies c ON c.id = t.company_id
        WHERE t.device LIKE 'MCP · %'
        ORDER BY t.last_used DESC NULLS LAST, t.id DESC
    """)
    if not rows:
        print("No agent tokens issued.")
        return
    print(f"{'prefix':<12} {'user':<14} {'company':<18} {'label':<18} "
          f"{'expires':<12} last used")
    for token, username, company, device, expires, last_used in rows:
        label = (device or "")[6:]
        print(f"{token[:10]:<12} {username:<14} {(company or '?'):<18} "
              f"{label:<18} {str(expires)[:10]:<12} "
              f"{str(last_used)[:16] if last_used else 'never'}")


def revoke(prefix):
    if len(prefix) < 6:
        sys.exit("Give at least the first six characters of the token.")
    matches = _rows("SELECT token, user_id FROM api_tokens "
                    "WHERE token LIKE %s AND device LIKE 'MCP · %%'",
                    (prefix + "%",))
    if not matches:
        sys.exit("No agent token starts with that.")
    if len(matches) > 1:
        sys.exit(f"{len(matches)} tokens start with that - be more specific.")
    _execute("DELETE FROM api_tokens WHERE token = %s", (matches[0][0],))
    print("Revoked. The agent using it stops working immediately.")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    command = sys.argv[1]
    if command == "issue" and len(sys.argv) >= 4:
        issue(sys.argv[2], int(sys.argv[3]),
              sys.argv[4] if len(sys.argv) > 4 else "agent")
    elif command == "list":
        listing()
    elif command == "revoke" and len(sys.argv) >= 3:
        revoke(sys.argv[2])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
