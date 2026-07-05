from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    session,
    make_response,
    flash
)
from flask_login import (
    login_user,
    login_required,
    logout_user,
    current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3

from database import (
    get_user_by_username,
    get_user_by_login_id,
    get_all_users,
    add_user,
    update_user,
    delete_user,
    master_get_user_companies,
    master_assign_company_to_user
)
from database.master_db import get_system_setting, set_system_setting
from .models import User, admin_required, permission_required, PERMISSIONS, PERMISSION_KEYS, MENU_TREE
from . import get_db_connection

auth_bp = Blueprint("auth_bp", __name__)


@auth_bp.route("/signin", methods=["GET", "POST"])
def signin():
    if current_user.is_authenticated:
        print(
            f"User already authenticated: {current_user.username}, "
            f"redirecting to dashboard"
        )
        # Use blueprint endpoint for dashboard
        return redirect(url_for("dashboard_bp.dashboard"))

    if request.method == "POST":
        login_id = request.form.get("login_id") or request.form.get("username")
        password = request.form["password"]
        user_data = get_user_by_login_id(login_id)
        if user_data and check_password_hash(user_data[3], password):
            user = User(
                user_data[0],
                user_data[1],
                user_data[2],
                user_data[3],
                user_data[4],
            )
            login_user(user)
            print(f"User {user_data[1]} signed in successfully")
            # Redirect to blueprint endpoint after login
            return redirect(url_for("dashboard_bp.dashboard"))

        print(f"Signin failed for {login_id}: Invalid credentials")
        return render_template("signin.html", error="Invalid username/email or password")

    print("Rendering signin page")
    return render_template("signin.html")


@auth_bp.route("/logout")
@login_required
def logout():
    print(f"Logging out user: {current_user.username}")
    logout_user()
    session.clear()
    # Redirect to signin via blueprint endpoint
    response = make_response(redirect(url_for("auth_bp.signin")))
    for key in list(request.cookies.keys()):
        response.set_cookie(key, "", expires=0)
    print(
        f"Session after logout: {session.get('_user_id')}, "
        f"Authenticated: {current_user.is_authenticated}"
    )
    return response


def _is_admin_user_id(user_id):
    """True when user_id belongs to an admin account."""
    try:
        return any(str(u[0]) == str(user_id) and u[3] for u in get_all_users())
    except Exception:
        return False


@auth_bp.route("/admin", methods=["GET", "POST"])
@permission_required("setup.user_management")
def admin():
    """User Management: regular (non-admin) users of the current company only."""
    from database.company_db import get_current_company_id
    company_id = get_current_company_id()
    if not company_id:
        flash("Select a company before managing its users", "error")
        return redirect(url_for("company_bp.company_gateway"))

    if request.method == "POST":
        action = request.form.get("action")

        # This page must never touch admin accounts (use Admin Management)
        # or users that belong to a different company.
        target_id = request.form.get("user_id")
        if target_id:
            if _is_admin_user_id(target_id):
                flash("Admin accounts are managed under Admin > Admin Management", "error")
                return redirect(url_for("auth_bp.admin"))
            assigned_ids = [c['id'] for c in master_get_user_companies(target_id)]
            if company_id not in assigned_ids:
                flash("That user does not belong to this company", "error")
                return redirect(url_for("auth_bp.admin"))

        if action == "create":
            username = request.form["username"]
            email = request.form["email"]
            password = request.form["password"]
            is_principal = 1 if request.form.get("is_principal") == "on" else 0

            if get_user_by_username(username):
                flash(f"Username {username} already exists", "error")
            elif get_user_by_login_id(email):
                flash(f"Email {email} is already used by another user", "error")
            else:
                password_hash = generate_password_hash(password)
                try:
                    add_user(username, email, password_hash, 0, is_principal)
                    # New users belong to the company they were created under
                    new_user = get_user_by_username(username)
                    if new_user:
                        master_assign_company_to_user(new_user[0], company_id)
                    flash(f"User {username} created successfully", "success")
                except Exception as e:
                    flash(f"Error creating user: {str(e)}", "error")

        elif action == "set_access":
            user_id = request.form["user_id"]
            is_principal = 1 if request.form.get("is_principal") == "on" else 0
            selected_perms = [p for p in request.form.getlist("perms") if p in PERMISSION_KEYS]
            try:
                from database.master_db import set_user_principal, set_user_permissions
                set_user_principal(user_id, is_principal)
                set_user_permissions(user_id, selected_perms)
                flash("User access updated successfully", "success")
            except Exception as e:
                flash(f"Error updating access: {str(e)}", "error")

        elif action == "edit":
            user_id = request.form["user_id"]
            email = request.form["email"]
            new_password = request.form.get("new_password")

            other = get_user_by_login_id(email)
            if user_id == str(current_user.id):
                flash("Cannot edit your own account here", "error")
            elif other and str(other[0]) != str(user_id):
                flash(f"Email {email} is already used by another user", "error")
            else:
                try:
                    if new_password:
                        password_hash = generate_password_hash(new_password)
                        update_user(user_id, email, password_hash)
                    else:
                        update_user(user_id, email)
                    flash("User updated successfully", "success")
                except Exception as e:
                    flash(f"Error updating user: {str(e)}", "error")

        elif action == "delete":
            user_id = request.form["user_id"]
            if user_id == str(current_user.id):
                flash("Cannot delete your own account", "error")
            else:
                try:
                    delete_user(user_id)
                    flash("User deleted successfully", "success")
                except Exception as e:
                    flash(f"Error deleting user: {str(e)}", "error")
        
        return redirect(url_for("auth_bp.admin"))

    # Only regular users assigned to the current company;
    # admins live in Admin Management
    users = []
    user_perms = {}
    from database.master_db import get_user_permissions
    for user in get_all_users():
        if user[3]:
            continue
        assigned_ids = [c['id'] for c in master_get_user_companies(user[0])]
        if company_id not in assigned_ids:
            continue
        users.append(user)
        try:
            user_perms[user[0]] = get_user_permissions(user[0])
        except Exception:
            user_perms[user[0]] = set()

    return render_template(
        "admin.html",
        users=users,
        user_perms=user_perms,
        permissions=PERMISSIONS,
        menu_tree=MENU_TREE,
        username=current_user.username
    )


@auth_bp.route("/admin-management", methods=["GET", "POST"])
@admin_required
def admin_management():
    """Admin Management: create and manage admin accounts only."""
    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            username = request.form["username"]
            email = request.form["email"]
            password = request.form["password"]

            if get_user_by_username(username):
                flash(f"Username {username} already exists", "error")
            elif get_user_by_login_id(email):
                flash(f"Email {email} is already used by another user", "error")
            else:
                password_hash = generate_password_hash(password)
                try:
                    add_user(username, email, password_hash, 1, 0)
                    flash(f"Admin {username} created successfully", "success")
                except Exception as e:
                    flash(f"Error creating admin: {str(e)}", "error")

        elif action == "edit":
            user_id = request.form["user_id"]
            email = request.form["email"]
            new_password = request.form.get("new_password")

            other = get_user_by_login_id(email)
            if user_id == str(current_user.id):
                flash("Cannot edit your own account here", "error")
            elif not _is_admin_user_id(user_id):
                flash("Only admin accounts are managed here", "error")
            elif other and str(other[0]) != str(user_id):
                flash(f"Email {email} is already used by another user", "error")
            else:
                try:
                    if new_password:
                        password_hash = generate_password_hash(new_password)
                        update_user(user_id, email, password_hash)
                    else:
                        update_user(user_id, email)
                    flash("Admin updated successfully", "success")
                except Exception as e:
                    flash(f"Error updating admin: {str(e)}", "error")

        elif action == "delete":
            user_id = request.form["user_id"]
            if user_id == str(current_user.id):
                flash("Cannot delete your own account", "error")
            elif not _is_admin_user_id(user_id):
                flash("Only admin accounts are managed here", "error")
            else:
                try:
                    delete_user(user_id)
                    flash("Admin deleted successfully", "success")
                except Exception as e:
                    flash(f"Error deleting admin: {str(e)}", "error")

        return redirect(url_for("auth_bp.admin_management"))

    admins = [u for u in get_all_users() if u[3]]
    return render_template(
        "admin_management.html",
        users=admins,
        username=current_user.username
    )
