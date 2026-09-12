import os
import sys
import webbrowser
from threading import Timer

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"  # Disable __pycache__

from accounting_app import create_app

app = create_app()

def open_browser():
    webbrowser.open_new('http://localhost:5000/')

if __name__ == "__main__":
    from database.config import DB_HOST, DB_PORT, DB_NAME
    print(f"Starting app with PostgreSQL: {DB_HOST}:{DB_PORT}/{DB_NAME}")

    port = int(os.environ.get("PORT", 5000))

    if getattr(sys, 'frozen', False):
        Timer(1.5, open_browser).start()
        app.run(host="0.0.0.0", port=port, debug=False)
    else:
        # Debug is opt-in. It was on unless FLASK_DEBUG said otherwise, which
        # meant a deployment that simply forgot the variable served the Werkzeug
        # debugger - a remote console onto the accounting database - to the
        # internet. Local development sets FLASK_DEBUG=1 (run_app.bat does).
        debug_mode = os.environ.get("FLASK_DEBUG", "0").lower() in ("1", "true", "yes")
        if debug_mode:
            print("FLASK_DEBUG is on: interactive debugger enabled. Never use "
                  "this on a public server.")
        app.run(host="0.0.0.0", port=port, debug=debug_mode)
