from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required
from database.ai_settings_db import get_ai_setting, set_ai_setting
from database.master_db import get_system_setting, set_system_setting
from .models import admin_required

ai_settings_bp = Blueprint('ai_settings_bp', __name__)

DEFAULT_OPENROUTER_MODEL = "openai/gpt-oss-120b"

@ai_settings_bp.route('/ai_settings', methods=['GET'])
@login_required
@admin_required
def ai_settings():
    openrouter_key = get_system_setting('openrouter_api_key') or ""
    openrouter_model = get_ai_setting('openrouter_model', DEFAULT_OPENROUTER_MODEL)

    return render_template(
        'ai_settings.html',
        openrouter_key=openrouter_key,
        openrouter_model=openrouter_model
    )

@ai_settings_bp.route('/update_ai_settings', methods=['POST'])
@login_required
@admin_required
def update_ai_settings():
    openrouter_key = request.form.get('openrouter_api_key', '').strip()
    set_system_setting('openrouter_api_key', openrouter_key)

    openrouter_model = request.form.get('openrouter_model', '').strip()
    if not openrouter_model:
        openrouter_model = DEFAULT_OPENROUTER_MODEL
    set_ai_setting('openrouter_model', openrouter_model)

    flash("AI Settings updated successfully.", "success")
    return redirect(url_for('ai_settings_bp.ai_settings'))

@ai_settings_bp.route('/fetch_openrouter_models', methods=['GET'])
@login_required
def fetch_openrouter_models():
    """
    Fetch the live model list from OpenRouter so newly released models
    appear automatically without app updates.
    """
    import requests
    try:
        response = requests.get("https://openrouter.ai/api/v1/models", timeout=15)
        if response.status_code != 200:
            return jsonify({"error": f"Failed to fetch models: {response.status_code}"}), 502

        data = response.json()
        models = []
        for m in data.get('data', []):
            models.append({
                "id": m.get('id'),
                "name": m.get('name') or m.get('id'),
            })
        # Sort alphabetically by name for the dropdown
        models.sort(key=lambda x: (x['name'] or '').lower())
        return jsonify({"models": models})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
