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

from .rate_limit import rate_limit

chat_bp = Blueprint('chat_bp', __name__)

OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

@chat_bp.route('/api/chat_query', methods=['POST'])
@rate_limit(30, 60, message="You're sending questions faster than the AI can be billed for. Wait a moment and try again.")
def chat_query():
    data = request.get_json()
    user_query = data.get('query', '')
    ai_enabled = bool(data.get('ai_enabled', True))
    # "AI only" sends every question straight to the model, skipping the coded
    # reports. It has no meaning with AI switched off.
    ai_only = bool(data.get('ai_only')) and ai_enabled
    history = data.get('history') or []

    if not user_query:
        return jsonify({"success": False, "message": "No query provided"}), 400

    company_id = get_current_company_id()

    result = process_chat_query(
        user_query, company_id, ai_enabled=ai_enabled, history=history,
        ai_only=ai_only
    )

    if "error" in result:
        return jsonify({"success": False, "message": result["error"]}), 500

    return jsonify({"success": True, "data": result})


@chat_bp.route('/api/chat_reset', methods=['POST'])
def chat_reset():
    """Forget the conversation so the next question starts clean."""
    from .chat_context import reset
    reset()
    return jsonify({"success": True})


@chat_bp.route('/api/chat_capabilities', methods=['GET'])
def chat_capabilities():
    """Everything the assistant can answer without AI, for the help panel."""
    from .chat_toolkit import TOOLS
    groups = {}
    for tool in TOOLS.values():
        groups.setdefault(tool.group, []).append({
            "name": tool.name,
            "description": tool.desc,
            "parameters": tool.params,
            "examples": tool.examples,
        })
    return jsonify({"success": True, "data": groups})


@chat_bp.route('/api/analyze_voucher_message', methods=['POST'])
@rate_limit(30, 60)
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

def _process_invoice(file_bytes, filename, invoice_type, company_id, job_id=None,
                     location=None):
    """Read one invoice. Runs on a worker thread, so it takes no Flask globals.

    company_id and location are passed in explicitly: there is no request
    context here, and both are read off the session.
    """
    from .ai_invoice_services import (
        extract_invoice_data_vision, generate_purchase_excel,
    )
    from . import jobs

    if job_id:
        jobs.set_progress(job_id, "Reading the document...")
    data = extract_invoice_data_vision(file_bytes, filename, invoice_type,
                                       company_id=company_id)

    if invoice_type != 'Purchase':
        return {"type": "Expense", "data": data}

    if job_id:
        jobs.set_progress(job_id, "Building the import spreadsheet...")
    excel_io = generate_purchase_excel(data, company_id=company_id,
                                       location=location)
    out_name = f"parsed_invoice_{uuid.uuid4().hex}.xlsx"
    with open(os.path.join(GEN_DIR, out_name), "wb") as f:
        f.write(excel_io.getbuffer())

    return {
        "type": "Purchase",
        "download_url": f"/static/generated/{out_name}",
        "data": data,
    }


@chat_bp.route('/api/upload_and_analyze_invoice', methods=['POST'])
@rate_limit(10, 300, message="Invoice reading is limited to 10 uploads every 5 minutes - each one is a paid AI call, and a long scan is several. Please wait and retry.")
def upload_and_analyze_invoice():
    """Start reading an invoice.

    A multi-page scan is several model calls and can run for minutes. Holding
    the request open for that ties up the single worker and outlasts most proxy
    timeouts, so long documents are handed to a background job and the browser
    polls for the result. Short ones still answer inline, which keeps the common
    case a single round trip.
    """
    from .ai_invoice_services import (
        InvoiceExtractionError, pdf_page_count, PAGES_PER_BATCH,
    )
    from . import jobs

    if 'file' not in request.files:
        return jsonify({"success": False, "message": "No file uploaded"}), 400

    file = request.files['file']
    invoice_type = request.form.get('invoice_type', 'Purchase')

    if file.filename == '':
        return jsonify({"success": False, "message": "No selected file"}), 400
    if invoice_type not in ('Purchase', 'Expense'):
        return jsonify({"success": False, "message": "Invalid invoice type"}), 400

    file_bytes = file.read()
    company_id = get_current_company_id()
    # Resolved here, in the request: the worker thread has no session, and the
    # import will only accept the active location.
    from accounting_app.import_routes.utils import active_location_name
    location = active_location_name(company_id)

    pages = pdf_page_count(file_bytes) if file.filename.lower().endswith('.pdf') else 1
    long_document = pages > PAGES_PER_BATCH

    if long_document and not jobs.busy():
        job_id = jobs.create(f"{file.filename} ({pages} pages)")
        jobs.run(job_id, _process_invoice, file_bytes, file.filename,
                 invoice_type, company_id, job_id, location)
        return jsonify({
            "success": True, "async": True, "job_id": job_id, "pages": pages,
            "message": f"Reading {pages} pages in the background - this takes "
                       f"about a minute.",
        })

    try:
        result = _process_invoice(file_bytes, file.filename, invoice_type,
                                  company_id, location=location)
        return jsonify(dict({"success": True, "async": False}, **result))
    except InvoiceExtractionError as e:
        # The file or the chosen model is the problem, not the server - and the
        # message says which. Previously this produced a header-only workbook
        # that looked like a successful import.
        print(f"Could not read invoice: {e}")
        return jsonify({"success": False, "message": str(e)}), 400
    except Exception as e:
        print(f"Error processing invoice: {str(e)}")
        return jsonify({"success": False, "message": str(e)}), 500


@chat_bp.route('/api/invoice_job/<job_id>')
def invoice_job_status(job_id):
    """Poll a background invoice job."""
    from . import jobs
    job = jobs.get(job_id)
    if not job:
        return jsonify({"success": False,
                        "message": "That job has expired. Please upload again."}), 404
    payload = {"status": job["status"], "progress": job["progress"],
               "description": job["description"]}
    if job["status"] == "done":
        payload.update(job["result"] or {})
    elif job["status"] == "failed":
        return jsonify({"success": False, "message": job["error"],
                        "status": "failed"}), 400
    return jsonify({"success": True, "data": payload})
