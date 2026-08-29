import os
import io
import re
import json
import base64
import fitz  # PyMuPDF
import pandas as pd
from openai import OpenAI
from database.ai_settings_db import get_ai_setting
from database.item_mapping_db import get_item_mapping
from datetime import datetime

def get_openai_client():
    """OpenRouter is the single AI provider; the OpenAI SDK is used in compatibility mode."""
    from database.master_db import get_system_setting
    api_key = get_system_setting('openrouter_api_key') or os.environ.get('OPENROUTER_API_KEY')
    if not api_key:
        raise Exception("OpenRouter API Key not configured in AI Settings.")
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)

# A vision model reads an invoice the way a person does, so the rendering has
# to be legible: rate columns and VAT percentages are small print. Pages are
# scaled to a target width rather than a fixed zoom, so an A5 delivery note and
# an A4 invoice both arrive readable.
TARGET_WIDTH_PX = 1700
MIN_ZOOM, MAX_ZOOM = 2.0, 4.0

# A multi-page scanned invoice is normal - three pages was an arbitrary cut-off
# that silently dropped the rest of the line items.
MAX_PAGES = 12

# Total budget for the encoded images in one request. Past this, providers
# start truncating or refusing, which comes back as an empty answer.
PAYLOAD_BUDGET_BYTES = 4_500_000

# Never shrink below this - past it the small print stops being readable and
# a wrong figure is worse than a slow request.
MIN_SCALE = 0.5


def _render_pages(doc, page_numbers, scale=1.0):
    images = []
    for page_num in page_numbers:
        page = doc.load_page(page_num)
        width = page.rect.width or 595
        zoom = max(MIN_ZOOM, min(TARGET_WIDTH_PX / width, MAX_ZOOM)) * scale
        zoom = max(1.2, zoom)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        images.append(base64.b64encode(
            pix.tobytes("jpeg", jpg_quality=88)).decode('utf-8'))
    return images


def pdf_page_count(pdf_bytes):
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return 0
    try:
        return len(doc)
    finally:
        doc.close()


def render_page_range(pdf_bytes, first, last):
    """Base64 JPEGs for pages [first, last), shrunk to fit one request."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        first = max(0, first)
        last = min(len(doc), last)
        pages = list(range(first, last))
        if not pages:
            return []
        images = _render_pages(doc, pages)
        scale = 1.0
        for _ in range(4):
            size = sum(len(i) for i in images)
            if size <= PAYLOAD_BUDGET_BYTES or scale <= MIN_SCALE:
                break
            scale = max(MIN_SCALE,
                        scale * ((PAYLOAD_BUDGET_BYTES / size) ** 0.5) * 0.95)
            print(f"[invoice] pages {first + 1}-{last} came to {size/1e6:.1f} MB "
                  f"- re-rendering at {scale:.0%}")
            images = _render_pages(doc, pages, scale)
        return images
    finally:
        doc.close()


def convert_pdf_to_images(pdf_bytes, max_pages=MAX_PAGES):
    """
    Convert PDF bytes to a list of base64 encoded images (JPEG).
    Returns (list of base64 strings, total_pages_in_document).
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        total_pages = len(doc)
        page_count = min(total_pages, max_pages)
        images = _render_pages(doc, range(page_count))

        # Too big for one request: shrink every page rather than dropping any,
        # so no line items go missing. JPEG size does not fall off exactly with
        # the square of the scale, so keep going until it actually fits.
        scale = 1.0
        for _ in range(4):
            size = sum(len(i) for i in images)
            if size <= PAYLOAD_BUDGET_BYTES or scale <= MIN_SCALE:
                break
            scale = max(MIN_SCALE, scale * ((PAYLOAD_BUDGET_BYTES / size) ** 0.5) * 0.95)
            print(f"[invoice] {page_count} page(s) came to {size/1e6:.1f} MB - "
                  f"re-rendering at {scale:.0%}")
            images = _render_pages(doc, range(page_count), scale)

        return images, total_pages
    finally:
        doc.close()


# A PDF exported from an accounting system carries its own text. Reading that
# beats rendering it to a picture and asking a model to read the picture back:
# it is exact, it costs a fraction as much, and - the reason this mattered - it
# works with text-only models, which most of the OpenRouter defaults are.
MIN_TEXT_LAYER = 60


def extract_pdf_text(pdf_bytes, max_pages=5):
    """The PDF's own text layer, or '' when it is a scan."""
    return analyse_pdf(pdf_bytes, max_pages)[0]


# A scanned page is a picture of a document. It often still carries a little
# text - a scanner stamp, or a poor OCR layer full of "1NV0lCE" - and trusting
# that instead of the picture produces confidently wrong figures. So a page
# covered by a large image is treated as a scan unless its text is substantial.
SCAN_IMAGE_COVERAGE = 0.5
TEXT_BEATS_IMAGE = 400


def analyse_pdf(pdf_bytes, max_pages=5):
    """(text, is_scan) for a PDF.

    `is_scan` means: read this with a vision model, not from the text layer.
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        print(f"[invoice] could not open PDF: {exc}")
        return "", True
    try:
        pages, image_heavy = [], 0
        considered = min(len(doc), max_pages)
        for page_num in range(considered):
            page = doc.load_page(page_num)
            pages.append(page.get_text() or "")

            page_area = abs(page.rect.get_area()) or 1
            covered = 0.0
            try:
                for info in page.get_image_info():
                    bbox = fitz.Rect(info["bbox"])
                    covered += abs(bbox.get_area())
            except Exception:
                pass
            if covered / page_area >= SCAN_IMAGE_COVERAGE:
                image_heavy += 1

        text = "\n".join(pages).strip()
        # Every page a picture, and not enough text to stand on its own.
        is_scan = (considered > 0 and image_heavy == considered
                   and len(text) < TEXT_BEATS_IMAGE)
        return text, is_scan
    finally:
        doc.close()


def encode_image(image_bytes):
    return base64.b64encode(image_bytes).decode('utf-8')


# ============================================================
# Making sense of whatever shape the model replies in
# ============================================================
#
# The prompt asks for one schema, but models paraphrase keys - "line_items"
# for "items", "rate" for "unit_rate". Reading only the exact key meant a
# perfectly good extraction was silently dropped and the user was handed an
# empty spreadsheet.

ITEM_LIST_KEYS = ("items", "line_items", "lineitems", "invoice_items",
                  "invoice_lines", "lines", "products", "details", "rows",
                  "particulars")

FIELD_ALIASES = {
    "vendor_name": ("vendor_name", "vendor", "supplier_name", "supplier",
                    "party_name", "party", "seller", "seller_name",
                    "company_name", "from"),
    "invoice_number": ("invoice_number", "invoice_no", "invoiceno", "inv_no",
                       "bill_number", "bill_no", "number", "reference",
                       "reference_number", "document_number"),
    "invoice_date": ("invoice_date", "date", "bill_date", "document_date",
                     "issue_date", "invoice_dt"),
    "description": ("description", "item_name", "item", "name", "particulars",
                    "product", "product_name", "item_description", "details"),
    "quantity": ("quantity", "qty", "units", "unit", "no_of_units", "pcs"),
    "unit_rate": ("unit_rate", "rate", "unit_price", "price", "unitprice",
                  "rate_per_unit", "unit_cost"),
    "vat_percent": ("vat_percent", "vat", "vat_rate", "vat_pct", "tax_percent",
                    "tax_rate", "gst_percent", "gst_rate"),
    "amount": ("amount", "line_total", "total", "net_amount", "value",
               "line_amount", "taxable_amount"),
    "total_amount": ("total_amount", "total", "grand_total", "invoice_total",
                     "amount_due", "net_payable"),
    "vat_amount": ("vat_amount", "vat", "tax_amount", "total_vat", "gst_amount"),
    "narration": ("narration", "description", "notes", "remarks", "particulars"),
}


def _pick(source, field, default=""):
    """The first alias of `field` present in a dict, whatever case it used."""
    if not isinstance(source, dict):
        return default
    lowered = {str(k).strip().lower().replace(" ", "_").replace("-", "_"): v
               for k, v in source.items()}
    for alias in FIELD_ALIASES.get(field, (field,)):
        if alias in lowered and lowered[alias] not in (None, ""):
            return lowered[alias]
    return default


def _to_number(value, default=0.0):
    """A number out of '11 PCS', 'AED 75.37', '1,234.50' or 75.37."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if value in (None, ""):
        return default
    text = str(value).replace(",", "")
    match = re.search(r'-?\d+(?:\.\d+)?', text)
    return float(match.group(0)) if match else default


def _direct_items(data):
    """An item list held by this dict itself, without looking any deeper."""
    if not isinstance(data, dict):
        return []
    lowered = {str(k).strip().lower().replace(" ", "_"): v for k, v in data.items()}
    for key in ITEM_LIST_KEYS:
        value = lowered.get(key)
        if isinstance(value, list) and value:
            return [v for v in value if isinstance(v, dict)]
    return []


def _find_items(data):
    """The line-item list, wherever the model put it."""
    if not isinstance(data, dict):
        return []
    direct = _direct_items(data)
    if direct:
        return direct
    # Some models wrap everything: {"invoice": {...}}
    for value in data.values():
        if isinstance(value, dict):
            nested = _find_items(value)
            if nested:
                return nested
    return []


def _unwrap(data):
    """Reach the invoice object when the model nested it one level down.

    The test has to look at *this* dict's own keys. Asking whether items exist
    anywhere underneath would make a bare {"invoice": {...}} wrapper look like
    the invoice itself, and the vendor and invoice number would be read off the
    wrapper - where they are not.
    """
    if not isinstance(data, dict):
        return {}
    identified = any(_pick(data, f) for f in ("vendor_name", "invoice_number"))
    if identified and _direct_items(data):
        return data
    for value in data.values():
        if isinstance(value, dict):
            inner = _unwrap(value)
            if inner and (any(_pick(inner, f) for f in
                              ("vendor_name", "invoice_number"))
                          or _direct_items(inner)):
                return inner
    return data


def normalise_purchase_data(data):
    """The model's reply reshaped into the exact schema the Excel builder wants."""
    data = _unwrap(data)
    items = []
    for raw in _find_items(data):
        description = str(_pick(raw, "description") or "").strip()
        quantity = _to_number(_pick(raw, "quantity"))
        unit_rate = _to_number(_pick(raw, "unit_rate"))
        amount = _to_number(_pick(raw, "amount"))

        # Invoices often print only quantity and line total. Deriving the rate
        # is exact arithmetic, not a guess.
        if not unit_rate and quantity and amount:
            unit_rate = round(amount / quantity, 4)
        if not quantity and unit_rate and amount:
            quantity = round(amount / unit_rate, 4)

        if not description and not quantity and not unit_rate:
            continue
        items.append({
            "description": description,
            "quantity": quantity,
            "unit_rate": unit_rate,
            "vat_percent": _to_number(_pick(raw, "vat_percent")),
            "amount": amount,
        })

    return {
        "vendor_name": str(_pick(data, "vendor_name") or "").strip(),
        "invoice_number": str(_pick(data, "invoice_number") or "").strip(),
        "invoice_date": str(_pick(data, "invoice_date") or "").strip(),
        "items": items,
    }


def normalise_expense_data(data):
    data = _unwrap(data)
    return {
        "party_name": str(_pick(data, "vendor_name") or "").strip(),
        "invoice_number": str(_pick(data, "invoice_number") or "").strip(),
        "invoice_date": str(_pick(data, "invoice_date") or "").strip(),
        "total_amount": _to_number(_pick(data, "total_amount")),
        "vat_amount": _to_number(_pick(data, "vat_amount")),
        "narration": str(_pick(data, "narration") or "").strip(),
    }


class InvoiceExtractionError(Exception):
    """The invoice could not be read - never hand back an empty spreadsheet."""


# Pages per model call. Batching is what removes the page ceiling: each request
# stays inside the payload and output-token limits, and the line items from
# every batch are merged, so a 40-page invoice is read in full.
PAGES_PER_BATCH = 4

# A rail against a runaway bill, not a capability limit. One call per batch.
MAX_TOTAL_PAGES = 120


def _batch_settings(company_id=None):
    """Pages per call and the overall page cap, overridable in AI Settings."""
    def setting(key, fallback, low, high):
        try:
            value = int(get_ai_setting(key, fallback,
                                       company_id=company_id) or fallback)
        except (TypeError, ValueError):
            return fallback
        return max(low, min(value, high))

    return (setting('invoice_pages_per_batch', PAGES_PER_BATCH, 1, 10),
            setting('invoice_max_pages', MAX_TOTAL_PAGES, 1, 500))


def _ask_model(client, model, messages, budget, pages_described):
    """One call to the model. Returns the raw text, or raises with a reason."""
    try:
        response = client.chat.completions.create(
            model=model, messages=messages, max_completion_tokens=budget)
    except Exception as e:
        error_str = str(e)
        if ("Connection" in error_str or "refused" in error_str
                or "Failed to establish a new connection" in error_str):
            raise Exception(
                "Connection Error: Could not connect to Local LLM. Please "
                "ensure LM Studio is running and the server is started.")
        raise

    choice = response.choices[0] if response.choices else None
    message = getattr(choice, "message", None)
    content = getattr(message, "content", None)
    finish_reason = getattr(choice, "finish_reason", None)

    if not content or not content.strip():
        # Reasoning models put their working in a separate field and sometimes
        # never move the answer across. The JSON is often still in there.
        content = _content_from_reasoning(message)

    if not content or not content.strip():
        raise InvoiceExtractionError(
            _empty_response_message(finish_reason, model, budget,
                                    pages_described))
    return content


def _parse_json_reply(content):
    """The JSON object out of a model reply, markdown fences and prose allowed."""
    print(f"AI Raw Response: {content[:500]}")
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        print(f"JSON Parse Error: {e}")
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if not match:
            raise Exception(
                f"Failed to parse AI response as JSON. Response started with: "
                f"{content[:100]}...")
        return json.loads(match.group(0))


def _content_from_reasoning(message):
    """Dig the answer out of a reasoning model's thinking field.

    Some models return content="" and put everything in `reasoning` or
    `reasoning_content`. If the JSON object is in there, it is still a
    perfectly good answer.
    """
    if message is None:
        return None
    for field in ("reasoning", "reasoning_content"):
        thinking = getattr(message, field, None)
        if not thinking:
            extra = getattr(message, "model_extra", None) or {}
            thinking = extra.get(field)
        if thinking and isinstance(thinking, str):
            match = re.search(r'\{.*\}', thinking, re.DOTALL)
            if match:
                print(f"[invoice] recovered the answer from '{field}'")
                return match.group(0)
    return None


def _extract_scan_in_batches(file_bytes, client, model, system_prompt,
                             invoice_type, total_pages, company_id=None):
    """Read a long scanned document a few pages at a time and merge the result.

    One request per batch keeps every call inside the payload and output-token
    limits, so page count stops being a limit at all. Header details come from
    the first batch; later batches contribute their line items.
    """
    per_batch, max_total = _batch_settings(company_id)
    pages_to_read = min(total_pages, max_total)
    batches = [(first, min(first + per_batch, pages_to_read))
               for first in range(0, pages_to_read, per_batch)]

    header, items, failed = {}, [], []
    print(f"[invoice] {total_pages}-page scan -> {len(batches)} batch(es) "
          f"of up to {per_batch} pages")

    for index, (first, last) in enumerate(batches):
        images = render_page_range(file_bytes, first, last)
        if not images:
            continue

        if index == 0:
            instruction = (
                f"Extract the {invoice_type} Invoice details.\n\n"
                f"This document has {pages_to_read} pages. You are being shown "
                f"pages 1-{last} now. Return the invoice header (vendor, "
                f"invoice number, date) and every line item visible on these "
                f"pages. Do not invent items from pages you cannot see.")
        else:
            instruction = (
                f"This is a continuation of the same invoice: pages "
                f"{first + 1}-{last} of {pages_to_read}.\n\n"
                "Return ONLY the line items visible on these pages, in the "
                "same JSON schema, as {\"items\": [...]}. Do not repeat items "
                "from earlier pages. If these pages contain no line items "
                "(for example terms and conditions), return {\"items\": []}.")

        content = [{"type": "text", "text": instruction}] + [
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            for b64 in images]

        budget = min(1500 + 1200 * len(images), 16000)
        label = f"pages {first + 1}-{last}"
        try:
            reply = _ask_model(
                client, model,
                [{"role": "system", "content": system_prompt},
                 {"role": "user", "content": content}],
                budget, len(images))
            parsed = normalise_purchase_data(_parse_json_reply(reply))
        except Exception as exc:
            # One bad batch must not lose the pages that did read correctly.
            print(f"[invoice] batch {label} failed: {exc}")
            failed.append(label)
            continue

        if index == 0 or not header:
            for field in ("vendor_name", "invoice_number", "invoice_date"):
                if parsed.get(field):
                    header[field] = parsed[field]
        items.extend(parsed.get("items") or [])
        print(f"[invoice] {label}: {len(parsed.get('items') or [])} line item(s)")

    data = {
        "vendor_name": header.get("vendor_name", ""),
        "invoice_number": header.get("invoice_number", ""),
        "invoice_date": header.get("invoice_date", ""),
        "items": items,
    }

    notes = []
    if total_pages > pages_to_read:
        notes.append(f"Only the first {pages_to_read} of {total_pages} pages "
                     f"were read.")
    if failed:
        notes.append("These pages could not be read: " + ", ".join(failed) + ".")
    if notes:
        data["warning"] = " ".join(notes) + " Check for missing line items."
    return data


def _empty_response_message(finish_reason, model, budget, pages):
    """Say what actually went wrong. It is almost never the API key."""
    if finish_reason == "length":
        return (
            f"The AI ran out of room before it finished answering "
            f"({budget} tokens for {pages} page(s)). Split the invoice into "
            f"fewer pages and upload them separately, or choose a model with a "
            f"larger output limit in AI Settings.")
    if finish_reason == "content_filter":
        return ("The AI provider blocked this document with a content filter. "
                "Try a different model in AI Settings.")
    return (
        f"'{model}' returned an empty answer for this invoice"
        + (f" ({pages} page(s))" if pages > 1 else "") + ". "
        "This usually means the document was too large for it, or the model "
        "cannot read images. Try uploading fewer pages at a time, or choose a "
        "different vision model in AI Settings. The API key is working - the "
        "request reached the provider.")

def extract_invoice_data_vision(file_bytes, filename, invoice_type="Purchase",
                                company_id=None):
    """
    Uses OpenAI Vision to extract structured data from invoice.
    """
    client = get_openai_client()

    # Use the default OpenRouter model selected in AI Settings
    # company_id is explicit so this works on a background thread, where
    # there is no Flask session to read the current company from.
    model = get_ai_setting('openrouter_model', 'openai/gpt-oss-120b',
                           company_id=company_id)
    
    is_pdf = filename.lower().endswith('.pdf')

    # Prefer the PDF's own text. Rendering a text PDF to a picture and asking a
    # model to read it back needs a vision model, and the OpenRouter defaults
    # here are text-only - which is how an unreadable invoice turned into an
    # empty spreadsheet instead of an error.
    pdf_text, is_scan = analyse_pdf(file_bytes) if is_pdf else ("", False)
    use_text = is_pdf and not is_scan and len(pdf_text) >= MIN_TEXT_LAYER

    image_contents = []
    total_pages = pdf_page_count(file_bytes) if is_pdf else 1
    skipped_pages = 0

    # A long scan is read in batches instead of one oversized request (see
    # below, once the prompts are built).
    batch_this = (not use_text and is_pdf and invoice_type == "Purchase"
                  and total_pages > _batch_settings(company_id)[0])

    if not use_text and not batch_this:
        if is_pdf:
            base64_images, total_pages = convert_pdf_to_images(file_bytes)
            skipped_pages = max(0, total_pages - len(base64_images))
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
        
        prompt = "Extract the Purchase Invoice details."
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
        prompt = "Extract the Expense details. Output strictly JSON."
        system_prompt = f"You are an expert accountant AI. Extract data from this invoice image strictly matching this JSON schema: {json_schema}. Return ONLY raw JSON, no markdown."

    # Long scanned invoice: read it a few pages at a time and merge. This is
    # what removes the page limit - no pages are dropped, however many there
    # are, because each request stays inside the provider's limits.
    if batch_this:
        data = _extract_scan_in_batches(file_bytes, client, model,
                                        system_prompt, invoice_type, total_pages,
                                        company_id=company_id)
        if not data["items"]:
            raise InvoiceExtractionError(_no_items_message(False, model))
        return data

    if use_text:
        user_content = (
            prompt
            + "\n\nHere is the text of the invoice, exactly as it appears in "
              "the document:\n\n"
            + pdf_text
        )
        print(f"[invoice] using the PDF text layer ({len(pdf_text)} chars)")
    else:
        page_note = prompt
        if len(image_contents) > 1:
            # Without this, models routinely read page 1 and stop.
            page_note += (
                f"\n\nThis invoice has {len(image_contents)} pages, supplied "
                "below in order. Read every page and return ALL line items "
                "from all of them in a single items array. Header details "
                "(vendor, invoice number, date) usually appear on page 1; "
                "line items may continue across pages.")
        user_content = [{"type": "text", "text": page_note}, *image_contents]
        print(f"[invoice] no usable text layer - sending "
              f"{len(image_contents)} page image(s) to a vision model")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    # A multi-page invoice has more line items, so it needs more room to answer
    # - and a reasoning model spends part of this budget thinking before it
    # writes anything. A fixed 1000 was the cause of "AI returned empty
    # response": the model used it all up and emitted nothing.
    pages_in_request = max(1, len(image_contents)) if not use_text else 1
    budget = min(1500 + 1200 * pages_in_request, 16000)

    content = _ask_model(client, model, messages, budget, pages_in_request)
    parsed = _parse_json_reply(content)

    if invoice_type == "Purchase":
        data = normalise_purchase_data(parsed)
        if skipped_pages:
            # Say so rather than quietly returning a partial invoice.
            data["warning"] = (
                f"Only the first {len(image_contents)} of {total_pages} pages "
                f"were read; {skipped_pages} page(s) were skipped. Check for "
                "missing line items.")
        if not data["items"]:
            # Better a plain explanation than a spreadsheet with nothing in it.
            raise InvoiceExtractionError(_no_items_message(use_text, model))
        return data

    return normalise_expense_data(parsed)


def _no_items_message(used_text, model):
    """Why nothing came back, and what the user can do about it."""
    if used_text:
        return (
            "I read the invoice text but the AI returned no line items, so "
            f"there is nothing to put in the spreadsheet. The model in use is "
            f"'{model}' - try a different one in AI Settings, or check that "
            "the invoice actually lists items.")
    return (
        "No line items could be read from this file. It has no text layer, so "
        f"it has to be read as a picture, and the model in use ('{model}') may "
        "not be able to see images. Choose a vision-capable model in AI "
        "Settings (for example a GPT-4o, Claude or Gemini model), or upload a "
        "PDF that contains real text rather than a scan.")

def generate_purchase_excel(data, company_id=None, location=None):
    """
    Generate Excel file matching the Purchase Import Template exactly.
    Columns: Voucher Group ID, Date, Narration, Party Ledger Name, Purchase Ledger,
             Extracted Item Name, System Item Name, Quantity, Rate, VAT %,
             Discount Amount, Location, Cost Center, Reference Number,
             Invoice Date, Weight (KG)

    "Extracted Item Name" is what the invoice calls the item. "System Item
    Name" is filled from this vendor's saved item mapping where one exists;
    otherwise it is left blank for the user to pick, and the import records the
    pairing for next time.
    """
    from accounting_app.import_routes.utils import multiple_locations_enabled

    items = data.get('items', [])
    vendor_name = data.get('vendor_name', '')
    invoice_no = data.get('invoice_number', '')
    invoice_date = data.get('invoice_date', '')
    
    # Template columns in exact order
    columns = [
        "Voucher Group ID", "Date", "Narration", "Party Ledger Name", "Purchase Ledger",
        "Extracted Item Name", "System Item Name", "Quantity", "Rate", "VAT %",
        "Discount Amount", "Location", "Cost Center", "Reference Number",
        "Invoice Date", "Weight (KG)"
    ]
    
    # The import insists on a location once the company runs more than one, and
    # accepts only the location active when the sheet is imported - so it is
    # filled in here rather than left for the user to guess at.
    if not multiple_locations_enabled(company_id):
        location = ""

    rows = []
    for item in items:
        v_item_name = item.get('description', '')
        
        # Mapping Logic - try to map vendor item to app item
        app_item_name = get_item_mapping(vendor_name, v_item_name,
                                         company_id=company_id)
        
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
            "Extracted Item Name": v_item_name,
            # Blank when this vendor's item has not been mapped yet - the user
            # picks it, and uploading the voucher saves the pairing.
            "System Item Name": app_item_name or "",
            "Quantity": quantity,
            "Rate": unit_rate,
            "VAT %": vat_percent,
            "Discount Amount": "",  # Leave blank if not available
            "Location": location or "",
            "Cost Center": "",  # Leave blank
            "Reference Number": invoice_no,
            "Invoice Date": invoice_date,
            "Weight (KG)": ""  # Leave blank if not available
        }
        rows.append(row)
    
    if not rows:
        # Nothing to write. Returning a header-only workbook looks like success
        # and wastes the user's time working out why it is empty.
        raise InvoiceExtractionError(
            "No line items were extracted from the invoice, so there is "
            "nothing to export. Check the file, or try a different model in "
            "AI Settings.")

    # Create DataFrame with exact column order
    df = pd.DataFrame(rows, columns=columns)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Purchase Import')
        # Same pick-lists as the downloaded template, so "System Item Name"
        # can only be filled with an item the import will accept.
        try:
            from accounting_app.import_routes.template_lookups import apply_lookups
            apply_lookups(writer.book, writer.sheets['Purchase Import'],
                          columns, company_id)
        except Exception as exc:
            print(f"[invoice] could not attach template dropdowns: {exc}")
        
    output.seek(0)
    return output

