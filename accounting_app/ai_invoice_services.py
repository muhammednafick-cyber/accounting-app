import os
import io
import json
import base64
import fitz  # PyMuPDF
import pandas as pd
from openai import OpenAI
from database.ai_settings_db import get_ai_setting
from database.item_mapping_db import get_item_mapping
from datetime import datetime

def get_openai_client():
    invoice_provider = get_ai_setting('invoice_provider', 'openai')
    
    if invoice_provider == 'local':
        local_url = get_ai_setting('local_llm_url', 'http://localhost:1234/v1')
        # Ensure base_url ends with /v1 if using OpenAI client compatibility usually
        return OpenAI(base_url=local_url, api_key="lm-studio")
    else:
        api_key = get_ai_setting('openai_api_key')
        if not api_key:
            raise Exception("OpenAI API Key not configured in AI Settings.")
        return OpenAI(api_key=api_key)

def convert_pdf_to_images(pdf_bytes):
    """
    Convert PDF bytes to a list of base64 encoded images (JPEG).
    Returns list of base64 strings.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images = []
    
    # Process first 3 pages max to avoid huge payloads
    for page_num in range(min(len(doc), 3)):
        page = doc.load_page(page_num)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2)) # 2x zoom for better quality
        img_bytes = pix.tobytes("jpeg")
        base64_img = base64.b64encode(img_bytes).decode('utf-8')
        images.append(base64_img)
        
    doc.close()
    return images

def encode_image(image_bytes):
    return base64.b64encode(image_bytes).decode('utf-8')

def extract_invoice_data_vision(file_bytes, filename, invoice_type="Purchase"):
    """
    Uses OpenAI Vision to extract structured data from invoice.
    """
    client = get_openai_client()
    
    invoice_provider = get_ai_setting('invoice_provider', 'openai')
    if invoice_provider == 'local':
        model = get_ai_setting('local_llm_model', 'Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled')
    else:
        model = get_ai_setting('openai_model_name', 'gpt-5-mini')
    
    is_pdf = filename.lower().endswith('.pdf')
    
    image_contents = []
    
    if is_pdf:
        base64_images = convert_pdf_to_images(file_bytes)
        for b64 in base64_images:
            image_contents.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
            })
    else:
        # Assume image
        base64_img = encode_image(file_bytes)
        image_contents.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}
        })

    # Define Schema based on type
    if invoice_type == "Purchase":
        system_prompt = """You are an expert accountant AI.

Extract data from this invoice image and return ONLY raw JSON (no markdown, no explanation).

Follow this JSON schema exactly:

{
  "vendor_name": "string",
  "invoice_number": "string",
  "invoice_date": "DD-MM-YYYY",
  "items": [
    {
      "description": "string",
      "quantity": "number",
      "unit_rate": "number",
      "vat_percent": "number"
    }
  ]
}

Rules:
- invoice_date must be formatted as DD-MM-YYYY.
- description must be the vendor item name exactly as written in the invoice.
- quantity must be numeric (remove text like PCS, CTN, BOX).
- unit_rate must be price per unit BEFORE VAT.
- vat_percent must be the VAT % for that line item (example: 5, 15, 0).
- If VAT % is not shown for an item but invoice VAT is clearly 5% for all items, use 5.
- If any field is missing or unclear, return an empty string "" for vendor_name/invoice_number/invoice_date, and 0 for quantity/unit_rate/vat_percent.
- Do not guess invoice numbers or dates."""
        
        prompt = "Extract Purchase Invoice details from this image."
    else:
        # Expense
        json_schema = """
        {
            "party_name": "string",
            "invoice_number": "string",
            "invoice_date": "DD-MM-YYYY",
            "total_amount": "number",
            "vat_amount": "number",
            "narration": "string (brief description of expense)"
        }
        """
        prompt = "Extract Expense details. Output strictly JSON."
        system_prompt = f"You are an expert accountant AI. Extract data from this invoice image strictly matching this JSON schema: {json_schema}. Return ONLY raw JSON, no markdown."

    messages = [
        {
            "role": "system",
            "content": system_prompt
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                *image_contents
            ]
        }
    ]

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_completion_tokens=1000
        )
    except Exception as e:
        # Check for connection error message in string representation since we don't import APIConnectionError
        error_str = str(e)
        if "Connection" in error_str or "refused" in error_str or "Failed to establish a new connection" in error_str:
            raise Exception("Connection Error: Could not connect to Local LLM. Please ensure LM Studio is running and the server is started.")
        raise e
    
    content = response.choices[0].message.content
    
    # Handle empty response
    if not content or not content.strip():
        raise Exception("AI returned empty response. Please try again or check your API key.")
    
    print(f"AI Raw Response: {content[:500]}")  # Debug logging
    
    # Clean markdown code blocks (various formats)
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    
    content = content.strip()
    
    # Try to parse JSON
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        print(f"JSON Parse Error: {e}")
        print(f"Content was: {content[:500]}")
        raise Exception(f"Failed to parse AI response as JSON. Response started with: {content[:100]}...")

def generate_purchase_excel(data):
    """
    Generate Excel file matching the Purchase Import Template exactly.
    Columns: Voucher Group ID, Date, Narration, Party Ledger Name, Purchase Ledger, 
             Item Name, Quantity, Rate, VAT %, Discount Amount, Location, 
             Cost Center, Reference Number, Invoice Date, Weight (KG)
    Applies Item Mapping logic.
    """
    items = data.get('items', [])
    vendor_name = data.get('vendor_name', '')
    invoice_no = data.get('invoice_number', '')
    invoice_date = data.get('invoice_date', '')
    
    # Template columns in exact order
    columns = [
        "Voucher Group ID", "Date", "Narration", "Party Ledger Name", "Purchase Ledger",
        "Item Name", "Quantity", "Rate", "VAT %", "Discount Amount", "Location",
        "Cost Center", "Reference Number", "Invoice Date", "Weight (KG)"
    ]
    
    rows = []
    for item in items:
        v_item_name = item.get('description', '')
        
        # Mapping Logic - try to map vendor item to app item
        app_item_name = get_item_mapping(vendor_name, v_item_name)
        final_item_name = app_item_name if app_item_name else v_item_name
        
        # Get VAT % directly from AI response
        vat_percent = item.get('vat_percent', 0) or 0
        unit_rate = item.get('unit_rate', 0) or 0
        quantity = item.get('quantity', 0) or 0
        
        row = {
            "Voucher Group ID": 1,  # Default voucher ID
            "Date": invoice_date,
            "Narration": "",  # Optional
            "Party Ledger Name": vendor_name,
            "Purchase Ledger": "Retail Purchase",  # Default purchase ledger
            "Item Name": final_item_name,
            "Quantity": quantity,
            "Rate": unit_rate,
            "VAT %": vat_percent,
            "Discount Amount": "",  # Leave blank if not available
            "Location": "",  # Leave blank - system will use default
            "Cost Center": "",  # Leave blank
            "Reference Number": invoice_no,
            "Invoice Date": invoice_date,
            "Weight (KG)": ""  # Leave blank if not available
        }
        rows.append(row)
    
    # Create DataFrame with exact column order
    df = pd.DataFrame(rows, columns=columns)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Purchase Import')
        
    output.seek(0)
    return output

