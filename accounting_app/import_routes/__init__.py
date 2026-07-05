from flask import Blueprint

import_bp = Blueprint("import_bp", __name__)

# Import submodules so their route decorators run
from . import queue_routes, template_routes, master_imports  # noqa
