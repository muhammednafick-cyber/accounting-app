import os
import sys
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, session, request, redirect, url_for
from flask_login import LoginManager, current_user, logout_user
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash
from database import initialize_db, get_user_by_id, get_company_settings

login_manager = LoginManager()
csrf = CSRFProtect()

def create_app():
    # Base path (handles PyInstaller and normal run)
    if getattr(sys, "frozen", False):
        # Running as compiled exe
        resource_path = sys._MEIPASS
        data_path = os.path.dirname(sys.executable)
    else:
        # Running from source
        resource_path = os.path.abspath(".")
        data_path = os.path.abspath(".")
    
    app = Flask(
        __name__,
        template_folder=os.path.join(resource_path, "templates"),
        static_folder=os.path.join(resource_path, "static"),
    )
    
    # Secret key: prefer SECRET_KEY env var; otherwise generate one once and
    # persist it locally so sessions survive restarts without a hardcoded value.
    secret = os.environ.get("SECRET_KEY")
    if not secret:
        key_file = os.path.join(resource_path, ".secret_key")
        try:
            if os.path.exists(key_file):
                with open(key_file, "r") as f:
                    secret = f.read().strip()
            if not secret:
                import secrets as _secrets
                secret = _secrets.token_hex(32)
                with open(key_file, "w") as f:
                    f.write(secret)
        except OSError:
            import secrets as _secrets
            secret = _secrets.token_hex(32)
    app.secret_key = secret
    # Set session lifetime to 10 minutes (though we check manually too)
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=10)
    
    # Database path - Unified DB
    # The DB_PATH is set in database.config, but we set it here for Flask config as well
    from database.config import DB_PATH
    app.config["DB_PATH"] = DB_PATH
    app.config["BASE_PATH"] = resource_path
    print(f"DB_PATH resolved to: {DB_PATH}")
    
    # Login manager
    login_manager.init_app(app)
    # Use blueprint endpoint for login view
    login_manager.login_view = "auth_bp.signin"
    
    # Initialize CSRF Protection
    csrf.init_app(app)

    @app.after_request
    def add_security_headers(response):
        """Add security headers to every response"""
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        # HSTS - Enable in production with SSL
        # response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        return response
    
    # Ensure DB and default admin
    ensure_db_exists(app)
    
    @app.before_request
    def check_auth_and_company():
        """Check authentication and company selection"""
        # Make session temporary
        session.permanent = False
        
        # 1. Allow static files and public auth routes
        if request.endpoint in ['static', 'auth_bp.signin', 'auth_bp.logout']:
            return None
        
        # 2. Check Authentication
        if not current_user.is_authenticated:
            return redirect(url_for('auth_bp.signin'))

        # Check Inactivity Timeout (10 minutes)
        last_active = session.get('last_active')
        if last_active:
            now_ts = datetime.now().timestamp()
            # 10 minutes = 600 seconds
            if (now_ts - last_active) > 600:
                logout_user()
                session.clear()
                return redirect(url_for('auth_bp.signin'))
        
        # 3. Check Company Selection
        # Allowed endpoints for authenticated users without company
        allowed_company_endpoints = [
            'company_bp.company_gateway',
            'company_bp.select_company',
            'company_bp.create_new_company',
            'auth_bp.admin', # Admin might need to access this without a company selected
            'auth_bp.logout',
            'static'
        ]
        
        if request.endpoint in allowed_company_endpoints:
            return None
        
        # Check if session has a company_id
        session_company_id = session.get('company_id')

        if not session_company_id:
            # No company selected, redirect to gateway
            return redirect(url_for('company_bp.company_gateway'))

        # 4. Menu permission enforcement (admin/principal bypass inside can_access)
        endpoint = request.endpoint or ''
        bp_name = endpoint.split('.')[0]
        BP_PERMS = {
            'voucher_bp': 'vouchers',
            'report_bp': 'reports',
            'export_bp': 'reports',
            'print_bp': 'print',
            'import_bp': 'import_queue',
            'fixed_asset_bp': 'modules',
            'recurring_bp': 'modules',
            'settlement_bp': 'modules',
            'financial_year_bp': 'setup',
            'admin_config_bp': 'setup',
        }
        required_perm = None
        if bp_name == 'master_bp':
            view = endpoint.split('.')[-1]
            required_perm = 'inventory_master' if ('inventory' in view or 'vendor' in view) else 'accounting_master'
        elif bp_name in BP_PERMS:
            required_perm = BP_PERMS[bp_name]
        elif endpoint == 'company_bp.company_settings':
            required_perm = 'setup'
        elif bp_name == 'ai_settings_bp' or endpoint == 'auth_bp.admin':
            # Admin-only areas
            if not current_user.is_admin:
                return redirect(url_for('dashboard_bp.dashboard'))

        if required_perm and hasattr(current_user, 'can_access') and not current_user.can_access(required_perm):
            return redirect(url_for('dashboard_bp.dashboard'))
        
        # We don't need to switch DB files anymore.
        # But we might want to verify if company exists/user has access occasionally?
        # For performance, we assume session is valid. 
        # The individual routes/DB queries should handle company_id context.
        
        return None
    
    @app.errorhandler(Exception)
    def handle_uncaught_exception(e):
        """AJAX callers always get a JSON error instead of a broken response."""
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return e
        app.logger.exception("Unhandled exception")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            from flask import jsonify
            return jsonify({"success": False, "message": str(e)}), 500
        return "Internal Server Error: " + str(e), 500

    @app.context_processor
    def inject_permissions():
        def can_access(perm_key):
            try:
                if not current_user.is_authenticated:
                    return False
                return current_user.can_access(perm_key)
            except Exception:
                return False
        return dict(can_access=can_access)

    @app.context_processor
    def inject_company_settings():
        """Inject company settings into all templates"""
        # Skip for static files or if no DB selected yet
        if request.endpoint == 'static' or not app.config.get('DB_PATH'):
            return dict(company=None)
            
        try:
            company = get_company_settings()
            return dict(company=company)
        except Exception:
            return dict(company=None)

    # Register blueprints
    from .company_routes import company_bp
    from .auth_routes import auth_bp
    from .dashboard_routes import dashboard_bp
    from .master_routes import master_bp
    from .voucher_routes import voucher_bp
    from .report_routes import report_bp
    from .export_routes import export_bp
    from .import_routes import import_bp
    from .print_routes import print_bp
    from .financial_year_routes import financial_year_bp
    from .chat_routes import chat_bp
    from .fixed_asset_routes import fixed_asset_bp
    from .recurring_routes import recurring_bp
    from .admin_config_routes import admin_config_bp
    from .settlement_routes import settlement_bp
    from .api_routes import api_bp
    from .ai_settings_routes import ai_settings_bp

    # Register custom filters
    from .models import format_date
    app.jinja_env.filters['format_date'] = format_date

    app.register_blueprint(company_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(master_bp)
    app.register_blueprint(voucher_bp)
    app.register_blueprint(report_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(import_bp)
    app.register_blueprint(print_bp)
    app.register_blueprint(financial_year_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(fixed_asset_bp)
    app.register_blueprint(recurring_bp)
    app.register_blueprint(admin_config_bp)
    app.register_blueprint(settlement_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(ai_settings_bp)
    
    @app.after_request
    def update_last_active(response):
        # Update last_active timestamp after request is processed
        # This ensures long-running requests (like imports) don't cause timeout
        try:
            if current_user.is_authenticated:
                session['last_active'] = datetime.now().timestamp()
        except Exception:
            pass
        return response
    
    return app

def get_db_connection():
    """Get database connection from unified config"""
    from database.config import get_connection
    return get_connection()

def ensure_db_exists(app: Flask):
    """Ensure database exists and has default admin user"""
    db_path = app.config["DB_PATH"]
    
    # Initialize Master DB (Global Users) - Handled by initialize_db now
    # from database import init_master_db
    # init_master_db()
    
    # Always run initialize_db to ensure schema is up-to-date (CREATE TABLE IF NOT EXISTS)
    with app.app_context():
        import database.config as db_config
        db_config.DB_PATH = db_path
        initialize_db()
        from database.vouchers_db import ensure_item_entries_cogs_populated
        ensure_item_entries_cogs_populated()
    
    if not os.path.exists(db_path):
        print(f"Creating new database at: {db_path}")
    
    # Check for default admin user
    # With the new centralized user system (master.db), we don't need per-company users.
    # But we might want to ensure the users table exists in company DB if we ever decide to use it for something else
    # or just rely on master.db completely. 
    # For now, we skip creating default admin in individual company databases.
    pass

@login_manager.user_loader
def load_user(user_id):
    """Load user for Flask-Login"""
    from .models import User
    try:
        user = get_user_by_id(user_id)
        if user:
            # user is tuple: (id, username, email, password_hash, is_admin, is_principal)
            is_principal = user[5] if len(user) > 5 else 0
            permissions = set()
            if not user[4] and not is_principal:
                try:
                    from database.master_db import get_user_permissions
                    permissions = get_user_permissions(user[0])
                except Exception as pe:
                    print(f"Could not load permissions for user {user_id}: {pe}")
            return User(user[0], user[1], user[2], user[3], user[4], is_principal, permissions)
        print(f"No user found for ID: {user_id}")
        return None
    except Exception as e:
        print(f"⚠ Error loading user {user_id}: {str(e)}")
        return None
