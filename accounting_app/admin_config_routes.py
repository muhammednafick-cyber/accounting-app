from flask import Blueprint, render_template, request, jsonify, session
from flask_login import login_required, current_user
from .models import setup_access_required as admin_required
from database.voucher_config_db import get_voucher_config, save_voucher_config
from database.accounts_db import get_groups, get_sub_groups

admin_config_bp = Blueprint("admin_config_bp", __name__)

@admin_config_bp.route("/admin/voucher_config")
@login_required
@admin_required
def voucher_config_page():
    """Render the Voucher Configuration page"""
    groups = get_groups()
    sub_groups = get_sub_groups()
    return render_template("admin/voucher_config.html", groups=groups, sub_groups=sub_groups)

@admin_config_bp.route("/api/get_voucher_config", methods=["GET"])
@login_required
@admin_required
def get_config_api():
    voucher_type = request.args.get("voucher_type")
    side = request.args.get("side")
    
    if not voucher_type or not side:
        return jsonify({"success": False, "message": "Missing parameters"}), 400
        
    config = get_voucher_config(voucher_type, side)
    if not config:
        return jsonify({"success": True, "config": None})
        
    return jsonify({"success": True, "config": config})

@admin_config_bp.route("/api/save_voucher_config", methods=["POST"])
@login_required
@admin_required
def save_config_api():
    data = request.get_json()
    voucher_type = data.get("voucher_type")
    side = data.get("side")
    allowed_groups = data.get("allowed_groups", [])
    allowed_sub_groups = data.get("allowed_sub_groups", [])
    
    if not voucher_type or not side:
        return jsonify({"success": False, "message": "Missing parameters"}), 400
        
    try:
        # Convert any potential string representation to list if needed,
        # but JSON input should be list.
        save_voucher_config(voucher_type, side, allowed_groups, allowed_sub_groups)
        return jsonify({"success": True, "message": "Configuration saved"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


VOUCHER_PREFIXES = {
    "Sales": "SAL", "Sales Return": "SR", "Purchase": "PUR", "Purchase Return": "PR",
    "Receipt": "REC", "Payment": "PAY", "Contra": "CON", "Journal": "JOU",
    "Expense": "EXP", "Service Income": "SRV", "Service Income Return": "SER",
    "Stock Adjustment": "SAD", "Inventory Transfer": "ITR", "Opening": "OPEN",
    "Settlement": "S",  # Matching/Settlement vouchers
    "Additional Charge": "ADD",
}


def _current_fy_tag(company_id):
    from database.financial_year_db import get_fy_by_date
    from datetime import datetime
    try:
        fy = get_fy_by_date(datetime.today().strftime('%Y-%m-%d'), company_id=company_id)
        if fy:
            return f"FY{str(fy['start_date'])[2:4]}"
    except Exception:
        pass
    return "FY??"


def _last_used_number(cursor, company_id, voucher_type, full_prefix):
    table, col = ("settlements", "settlement_number") if voucher_type == "Settlement" else ("vouchers", "voucher_number")
    cursor.execute(
        f"SELECT {col} FROM {table} WHERE company_id = %s AND {col} LIKE %s ORDER BY length({col}) DESC, {col} DESC LIMIT 1",
        (company_id, f"{full_prefix}-%")
    )
    r = cursor.fetchone()
    if r:
        try:
            return int(str(r[0]).split('-')[-1])
        except (ValueError, IndexError):
            pass
    return 0


def _voucher_numbering_rows(company_id, fy_tag=None):
    from database.config import get_connection
    if not fy_tag:
        fy_tag = _current_fy_tag(company_id)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT voucher_type, COALESCE(max_number, 0) FROM voucher_number_settings WHERE company_id = %s", (company_id,))
        settings = {r[0]: int(r[1] or 0) for r in cursor.fetchall()}
        rows = []
        for vtype, prefix in VOUCHER_PREFIXES.items():
            full_prefix = f"{fy_tag}-{prefix}"
            last_used = _last_used_number(cursor, company_id, vtype, full_prefix)
            rows.append({
                "voucher_type": vtype,
                "prefix": full_prefix,
                "last_used": last_used,
                "effective_next": last_used + 1,
                "max_number": settings.get(vtype, 0),
            })
        return rows, fy_tag
    finally:
        conn.close()


@admin_config_bp.route("/admin/voucher_numbering")
@login_required
def voucher_numbering_page():
    """Admin-only: assign the next voucher number per type (prefix fixed)."""
    if not current_user.is_admin:
        return jsonify({"success": False, "message": "Admin access only"}), 403
    from database.company_db import get_current_company_id
    from database.financial_year_db import get_all_fys
    company_id = get_current_company_id()
    # All defined FYs (newest first) for the year selector
    fys = get_all_fys() or []
    fy_tags = [f"FY{str(fy['start_date'])[2:4]}" for fy in fys]
    selected = (request.args.get("fy") or "").strip() or _current_fy_tag(company_id)
    if fy_tags and selected not in fy_tags:
        selected = fy_tags[0]
    rows, fy_tag = _voucher_numbering_rows(company_id, fy_tag=selected)
    return render_template("admin/voucher_numbering.html", rows=rows, fy_tag=fy_tag,
                           fy_tags=fy_tags, current_fy=_current_fy_tag(company_id),
                           username=current_user.username)


@admin_config_bp.route("/api/save_voucher_numbering", methods=["POST"])
@login_required
def save_voucher_numbering():
    if not current_user.is_admin:
        return jsonify({"success": False, "message": "Admin access only"}), 403
    data = request.get_json() or {}
    voucher_type = data.get("voucher_type")
    if voucher_type not in VOUCHER_PREFIXES:
        return jsonify({"success": False, "message": "Unknown voucher type"}), 400
    try:
        max_number = int(data.get("max_number") or 0)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Max number must be a whole number (0 or blank = unlimited)"}), 400
    if max_number < 0:
        max_number = 0

    from database.company_db import get_current_company_id
    from database.config import get_connection
    company_id = get_current_company_id()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        fy_tag = _current_fy_tag(company_id)
        full_prefix = f"{fy_tag}-{VOUCHER_PREFIXES[voucher_type]}"
        last_used = _last_used_number(cursor, company_id, voucher_type, full_prefix)
        if max_number and max_number <= last_used:
            return jsonify({"success": False, "message": f"Max number must be above the last used number in the current FY ({full_prefix}-{str(last_used).zfill(6)})."}), 400

        cursor.execute("""
            INSERT INTO voucher_number_settings (company_id, voucher_type, next_number, max_number)
            VALUES (%s, %s, 1, %s)
            ON CONFLICT (company_id, voucher_type) DO UPDATE SET max_number = EXCLUDED.max_number
        """, (company_id, voucher_type, max_number))
        conn.commit()
        limit_txt = f"vouchers allowed up to {full_prefix}-{str(max_number).zfill(6)} per FY" if max_number else "no upper limit"
        return jsonify({"success": True, "message": f"{voucher_type}: {limit_txt}."})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        conn.close()


@admin_config_bp.route("/admin/reset_company", methods=["POST"])
@login_required
@admin_required
def reset_company():
    """
    Hard-reset the currently selected company:
      1. Records the company name and all assigned user IDs.
      2. Deletes the company row — ON DELETE CASCADE wipes every child table
         (vouchers, ledger_entries, item_entries, ledgers, groups, inventory, …).
      3. Re-inserts the company with the same name (new auto-increment id).
      4. Re-assigns every previous user to the new company id.
      5. Re-initialises default master groups, ledgers and units.
      6. Updates the session so the user is switched to the new company id.
    """
    from database.config import get_connection
    from database.accounts_db import ensure_default_master_groups, ensure_default_groups, ensure_default_ledgers
    from database.inventory_db import ensure_default_units
    from database.company_db import get_current_company_id, create_company_profile

    company_id = get_current_company_id()
    if not company_id:
        return jsonify({"success": False, "message": "No company selected"}), 400

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # 1. Fetch company name
        cursor.execute("SELECT name FROM companies WHERE id = %s", (company_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"success": False, "message": "Company not found"}), 404
        company_name = row['name'] if isinstance(row, dict) else row[0]

        # 2. Fetch all users currently assigned to this company
        cursor.execute("SELECT user_id, role FROM user_company_access WHERE company_id = %s", (company_id,))
        user_rows = cursor.fetchall()
        assigned_users = [(r['user_id'], r['role']) if isinstance(r, dict) else (r[0], r[1]) for r in user_rows]

        # 3. Delete company — CASCADE clears every child table
        cursor.execute("DELETE FROM companies WHERE id = %s", (company_id,))

        # 4. Re-create company with same name
        cursor.execute("INSERT INTO companies (name) VALUES (%s) RETURNING id", (company_name,))
        new_row = cursor.fetchone()
        new_company_id = new_row['id'] if isinstance(new_row, dict) else new_row[0]

        # 5. Re-assign all previous users to the new company id
        for user_id, role in assigned_users:
            cursor.execute(
                "INSERT INTO user_company_access (user_id, company_id, role) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (user_id, new_company_id, role)
            )

        conn.commit()

        # 6. Re-initialise default master data for the fresh company
        ensure_default_master_groups(new_company_id)
        ensure_default_groups(new_company_id)
        ensure_default_ledgers(new_company_id)
        ensure_default_units(new_company_id)
        create_company_profile(company_name, company_id=new_company_id)

        # 7. Update session so the current user lands on the new company
        session['company_id'] = new_company_id
        session['company_name'] = company_name

        return jsonify({
            "success": True,
            "message": f"Company '{company_name}' has been reset. All data wiped and defaults restored.",
            "new_company_id": new_company_id
        })

    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        conn.close()
