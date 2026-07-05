from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from database.ai_settings_db import get_ai_setting, set_ai_setting
from database.master_db import get_system_setting, set_system_setting
from .models import admin_required

ai_settings_bp = Blueprint('ai_settings_bp', __name__)

@ai_settings_bp.route('/ai_settings', methods=['GET'])
@login_required
@admin_required
def ai_settings():
    # Fetch settings
    # OpenRouter (General Chat) - usually from system_settings or env
    openrouter_key = get_system_setting('openrouter_api_key') or ""
    
    # OpenAI (Invoice Processing) - from new ai_settings table
    openai_key = get_ai_setting('openai_api_key', '')
    openai_model = get_ai_setting('openai_model_name', 'gpt-5-mini')

    # Local LLM Settings
    local_llm_url = get_ai_setting('local_llm_url', 'http://localhost:1234/v1')
    local_llm_model = get_ai_setting('local_llm_model', 'Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled')

    # Providers
    chatbot_provider = get_ai_setting('chatbot_provider', 'openrouter')
    invoice_provider = get_ai_setting('invoice_provider', 'openai')
    
    return render_template(
        'ai_settings.html',
        openrouter_key=openrouter_key,
        openai_key=openai_key,
        openai_model=openai_model,
        local_llm_url=local_llm_url,
        local_llm_model=local_llm_model,
        chatbot_provider=chatbot_provider,
        invoice_provider=invoice_provider
    )

@ai_settings_bp.route('/update_ai_settings', methods=['POST'])
@login_required
@admin_required
def update_ai_settings():
    # OpenRouter Settings
    openrouter_key = request.form.get('openrouter_api_key', '').strip()
    set_system_setting('openrouter_api_key', openrouter_key)

    # OpenAI Settings
    openai_key = request.form.get('openai_api_key', '').strip()
    openai_model = request.form.get('openai_model_name', '').strip()
    
    if not openai_model:
        openai_model = 'gpt-5-mini'
        
    set_ai_setting('openai_api_key', openai_key)
    set_ai_setting('openai_model_name', openai_model)

    # Local LLM Settings
    local_llm_url = request.form.get('local_llm_url', '').strip()
    local_llm_model = request.form.get('local_llm_model', '').strip()
    
    if not local_llm_url:
        local_llm_url = 'http://localhost:1234/v1'
    
    set_ai_setting('local_llm_url', local_llm_url)
    set_ai_setting('local_llm_model', local_llm_model)

    # Providers
    chatbot_provider = request.form.get('chatbot_provider', 'openrouter')
    invoice_provider = request.form.get('invoice_provider', 'openai')

    set_ai_setting('chatbot_provider', chatbot_provider)
    set_ai_setting('invoice_provider', invoice_provider)
    
    flash("AI Settings updated successfully.", "success")
    return redirect(url_for('ai_settings_bp.ai_settings'))

@ai_settings_bp.route('/fetch_local_models', methods=['POST'])
@login_required
@admin_required
def fetch_local_models():
    import requests
    local_llm_url = request.form.get('local_llm_url', '').strip()
    if not local_llm_url:
        return {"error": "Local LLM URL is required"}, 400
        
    if local_llm_url.endswith('/'):
        models_url = local_llm_url + "models"
    else:
        models_url = local_llm_url + "/models"
        
    try:
        response = requests.get(models_url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            # Standard OpenAI format: {"data": [{"id": "model-id", ...}]}
            models = [m['id'] for m in data.get('data', [])]
            return {"models": models}
        else:
            return {"error": f"Failed to fetch models: {response.status_code}"}, 500
    except requests.exceptions.ConnectionError:
        return {"error": "Connection Refused. Please ensure LM Studio is running and the server is started."}, 500
    except Exception as e:
        return {"error": str(e)}, 500
