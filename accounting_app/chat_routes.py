from flask import Blueprint, request, jsonify
import requests
import json
import os
import datetime
from database.master_db import get_system_setting
from database.company_db import get_current_company_id
from .chatbot_service import (
    process_chat_query,
    parse_voucher_message_rule_based,
    get_openrouter_model,
    openrouter_request,
)

chat_bp = Blueprint('chat_bp', __name__)

OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

@chat_bp.route('/api/chat_query', methods=['POST'])
def chat_query():
    data = request.get_json()
    user_query = data.get('query', '')
    ai_enabled = bool(data.get('ai_enabled', True))

    if not user_query:
        return jsonify({"success": False, "message": "No query provided"}), 400

    company_id = get_current_company_id()

    result = process_chat_query(user_query, company_id, ai_enabled=ai_enabled)

    if "error" in result:
        return jsonify({"success": False, "message": result["error"]}), 500

    return jsonify({"success": True, "data": result})

@chat_bp.route('/api/analyze_voucher_message', methods=['POST'])
def analyze_voucher_message():
    data = request.get_json()
    user_message = data.get('message', '')
    ai_enabled = bool(data.get('ai_enabled', True))

    if not user_message:
        return jsonify({"success": False, "message": "No message provided"}), 400

    # Fast path: rule-based parsing (no AI, no data sent out)
    parsed = parse_voucher_message_rule_based(user_message)
    if parsed:
        return jsonify({"success": True, "data": parsed})

    if not ai_enabled:
        return jsonify({
            "success": False,
            "message": ("Could not understand the voucher (AI is disabled). Use a pattern like: "
                        "'received 5000 from ABC by cash today', 'paid 1200 to XYZ by bank', "
                        "'transfer 1000 from Cash to Bank', 'expense 300 for Fuel by cash' - "
                        "or enable AI for free-form input.")
        }), 400

    current_date = datetime.date.today().strftime("%d-%m-%Y")
    
    # Simple prompt to extract structured data
    system_prompt = f"""
    You are an automated accounting assistant API. 
    Your goal is to extract structured voucher information from the user's natural language input.
    The current date is {current_date}.

    Output strictly valid JSON. Do not include markdown formatting or explanations.
    
    Structure your JSON response exactly like this:
    {{
      "voucher_type": "Receipt" | "Payment" | "Contra" | "Expense" | "Sales" | "Purchase" | "Journal" | null,
      "date": "DD-MM-YYYY",
      "amount": number | null,
      "narration": string,
      "ledger_entries": [
          {{ "ledger": string, "type": "Debit" | "Credit" }}
      ]
    }}
    
    CRITICAL RULES:
    1. Extract ONLY the EXACT amount mentioned by the user. Do NOT add, calculate, or modify the amount.
    2. The 'amount' field is the TOTAL voucher amount. All Debit and Credit entries will use this SAME amount.
    3. Do NOT include separate 'amount' fields in ledger_entries. Only specify ledger name and type.
    4. Infer the 'voucher_type' from the context (e.g., 'paid' -> Payment, 'received' -> Receipt, 'expense' -> Expense).
    5. If the date is 'today', use {current_date}. If 'yesterday', calculate it.
    6. Identify the ledgers involved:
       - For Payment: Credit 'Cash' or 'Bank Account', Debit the party/expense ledger.
       - For Receipt: Debit 'Cash' or 'Bank Account', Credit the party/income ledger.
       - For Expense: Debit the expense ledger, Credit 'Cash' or 'Bank Account'.
    7. Extract "ledger" names from the user's text (e.g., "AL AIN FARMS", "Fuel", "ABC Corp").
    8. If you cannot determine a field, set it to null.
    9. NEVER inflate or modify the amount. Use the EXACT number from the user's message.
    """

    payload = {
        "model": get_openrouter_model(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        "temperature": 0.1,
        "max_tokens": 500  # Limit tokens to stay within free tier
    }

    # Get API key from DB, fallback to env
    api_key = get_system_setting('openrouter_api_key') or OPENROUTER_API_KEY
    if not api_key:
         return jsonify({"success": False, "message": "OpenRouter API Key not configured. Please contact admin."}), 500

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:5000",
    }
    
    try:
        result, err = openrouter_request(payload, headers)
        if err:
            return jsonify({"success": False, "message": err}), 500

        if 'choices' not in result or not result['choices']:
             return jsonify({"success": False, "message": "No response from AI provider"}), 500

        content = result['choices'][0]['message']['content']
        
        # Clean up markdown if present
        clean_content = content.strip()
        if clean_content.startswith("```json"):
            clean_content = clean_content[7:]
        if clean_content.endswith("```"):
            clean_content = clean_content[:-3]

        try:
            voucher_data = json.loads(clean_content.strip())
        except json.JSONDecodeError:
            # Fallback: extract the first JSON object embedded in the text
            import re as _re
            match = _re.search(r'\{.*\}', clean_content, _re.DOTALL)
            if not match:
                raise
            voucher_data = json.loads(match.group(0))

        return jsonify({"success": True, "data": voucher_data})

    except json.JSONDecodeError:
        return jsonify({"success": False, "message": ("The AI response could not be understood. Try rephrasing, "
                        "or use a one-line pattern like 'received 5000 from ABC by cash today'. "
                        "If this keeps happening, select a different model in AI Settings."),
                        "raw_response": content}), 500
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ==================== AI Invoice Processing ====================
from flask import send_file, url_for
import tempfile
import uuid

# Ensure generated directory exists
GEN_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'generated')
os.makedirs(GEN_DIR, exist_ok=True)

@chat_bp.route('/api/upload_and_analyze_invoice', methods=['POST'])
def upload_and_analyze_invoice():
    from .ai_invoice_services import extract_invoice_data_vision, generate_purchase_excel
    
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "No file uploaded"}), 400
        
    file = request.files['file']
    invoice_type = request.form.get('invoice_type', 'Purchase') # Purchase or Expense
    
    if file.filename == '':
        return jsonify({"success": False, "message": "No selected file"}), 400

    try:
        # Read file bytes
        file_bytes = file.read()
        
        # 1. AI Extraction
        data = extract_invoice_data_vision(file_bytes, file.filename, invoice_type)
        
        if invoice_type == 'Purchase':
            # 2. Generate Excel
            excel_io = generate_purchase_excel(data)
            
            # Save to static/generated
            filename = f"parsed_invoice_{uuid.uuid4().hex}.xlsx"
            filepath = os.path.join(GEN_DIR, filename)
            
            with open(filepath, "wb") as f:
                f.write(excel_io.getbuffer())
                
            download_url = url_for('static', filename=f'generated/{filename}')
            
            return jsonify({
                "success": True, 
                "type": "Purchase",
                "download_url": download_url,
                "data": data
            })
            
        elif invoice_type == 'Expense':
            # Return JSON for confirmation
            return jsonify({
                "success": True,
                "type": "Expense",
                "data": data
            })
            
        else:
            return jsonify({"success": False, "message": "Invalid invoice type"}), 400

    except Exception as e:
        print(f"Error processing invoice: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500
