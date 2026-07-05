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
