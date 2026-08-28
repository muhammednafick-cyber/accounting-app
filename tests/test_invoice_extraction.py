"""Tests for reading a vendor invoice into the purchase-import spreadsheet.

The bug these lock down: a text-only model could not see the rendered page
image, returned no line items, and the code wrote a header-only workbook that
looked like a successful import.
"""
import io
import json
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd

from accounting_app import ai_invoice_services as S


def fake_openai(reply, captured=None, finish_reason="stop", reasoning=None):
    """A stand-in OpenAI client that returns `reply` and records the request."""
    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(model, messages, **kwargs):
                    if captured is not None:
                        captured["model"] = model
                        captured["messages"] = messages
                        captured.update(kwargs)
                    message = types.SimpleNamespace(content=reply,
                                                    reasoning=reasoning)
                    return types.SimpleNamespace(
                        choices=[types.SimpleNamespace(
                            message=message, finish_reason=finish_reason)])
    return Client()


def _counting_client(seen, fail_on=None):
    """Records how many page images each call received; can fail a given call."""
    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(model, messages, **kwargs):
                    content = messages[1]["content"]
                    pages = sum(1 for part in content
                                if part.get("type") == "image_url")
                    seen.append(pages)
                    if fail_on and len(seen) in fail_on:
                        return types.SimpleNamespace(choices=[
                            types.SimpleNamespace(
                                message=types.SimpleNamespace(content="",
                                                              reasoning=None),
                                finish_reason="length")])
                    body = {"items": [{"description": f"ITEM {len(seen)}",
                                       "quantity": 1, "unit_rate": 5,
                                       "vat_percent": 5}]}
                    if len(seen) == 1:
                        body.update({"vendor_name": "BAQAR MOHEBI",
                                     "invoice_number": "INV-555087",
                                     "invoice_date": "21-01-2026"})
                    return types.SimpleNamespace(choices=[
                        types.SimpleNamespace(
                            message=types.SimpleNamespace(
                                content=json.dumps(body), reasoning=None),
                            finish_reason="stop")])
    return Client()


def text_pdf(body):
    """A small PDF that really contains text, built the way an ERP exports one."""
    from reportlab.pdfgen import canvas
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    y = 800
    for line in body.splitlines():
        pdf.drawString(50, y, line)
        y -= 14
    pdf.save()
    return buffer.getvalue()


SAMPLE_INVOICE = """TAX INVOICE
Invoice No: INV-555087
Invoice Date: 21-01-2026
Vendor: BAQAR MOHEBI
Item Name Qty Unit Rate Amount
HAYAT SPAGHETTI PASTA 500GM 11 75.37 829.07
Subtotal 829.07
VAT 5% 41.45
Total 870.52"""

GOOD_REPLY = json.dumps({
    "vendor_name": "BAQAR MOHEBI",
    "invoice_number": "INV-555087",
    "invoice_date": "21-01-2026",
    "items": [{"description": "HAYAT SPAGHETTI PASTA 500GM",
               "quantity": 11, "unit_rate": 75.37, "vat_percent": 5}],
})


class TestPdfTextLayer(unittest.TestCase):

    def test_a_text_pdf_is_read_without_rendering_it(self):
        text = S.extract_pdf_text(text_pdf(SAMPLE_INVOICE))
        self.assertIn("HAYAT SPAGHETTI PASTA 500GM", text)
        self.assertGreaterEqual(len(text), S.MIN_TEXT_LAYER)

    def test_a_broken_file_returns_no_text_rather_than_raising(self):
        self.assertEqual(S.extract_pdf_text(b"not a pdf at all"), "")


def scanned_pdf(body, stamp=None):
    """A picture of a document - what a scanner or a phone camera produces.

    `stamp` adds a thin text layer on top, the way scanner software and bad OCR
    do; that text must not be mistaken for a real text layer.
    """
    import fitz
    source = fitz.open(stream=text_pdf(body), filetype="pdf")
    pix = source.load_page(0).get_pixmap(matrix=fitz.Matrix(2, 2))
    out = fitz.open()
    page = out.new_page(width=pix.width / 2, height=pix.height / 2)
    page.insert_image(page.rect, stream=pix.tobytes("png"))
    if stamp:
        page.insert_text((40, 30), stamp, fontsize=9)
    return out.tobytes()


class TestScanDetection(unittest.TestCase):
    """Choosing between the text layer and the vision model."""

    def route(self, pdf_bytes):
        text, is_scan = S.analyse_pdf(pdf_bytes)
        return "text" if (not is_scan and len(text) >= S.MIN_TEXT_LAYER) else "vision"

    def test_a_real_text_pdf_uses_its_text(self):
        self.assertEqual(self.route(text_pdf(SAMPLE_INVOICE)), "text")

    def test_a_pure_scan_goes_to_vision(self):
        self.assertEqual(self.route(scanned_pdf(SAMPLE_INVOICE)), "vision")

    def test_a_scanner_stamp_does_not_count_as_a_text_layer(self):
        pdf = scanned_pdf(SAMPLE_INVOICE,
                          "Scanned by CamScanner on 21-01-2026  Page 1 of 1")
        self.assertEqual(self.route(pdf), "vision")

    def test_a_garbled_ocr_layer_does_not_beat_the_image(self):
        """The regression this guards: 65 characters of "1NV0lCE" nonsense
        was long enough to win, so the scan never reached the vision model."""
        pdf = scanned_pdf(
            SAMPLE_INVOICE,
            "TAX 1NV0lCE  lnv0ice N0 1NV-5S5O87  BAQAR M0HEBl  HAYAT SPAGH37TI")
        self.assertEqual(self.route(pdf), "vision")

    def test_a_scan_is_rendered_legibly_for_the_model(self):
        images, total = S.convert_pdf_to_images(scanned_pdf(SAMPLE_INVOICE))
        self.assertEqual(total, 1)
        self.assertEqual(len(images), 1)
        self.assertGreater(len(images[0]), 10000)   # a real page, not a blank

    def test_a_scan_reaches_the_model_as_an_image(self):
        captured = {}
        S.get_openai_client = lambda: fake_openai(GOOD_REPLY, captured)
        S.get_ai_setting = lambda key, default=None, company_id=None: "z-ai/glm-5.3-flash"
        data = S.extract_invoice_data_vision(scanned_pdf(SAMPLE_INVOICE),
                                             "scan.pdf", "Purchase")
        content = captured["messages"][1]["content"]
        self.assertIsInstance(content, list)
        self.assertIn("image_url", [part.get("type") for part in content])
        self.assertEqual(data["items"][0]["quantity"], 11)


class TestMultiPageScans(unittest.TestCase):
    """A multi-page scanned invoice - the case that returned an empty answer."""

    def multipage(self, pages, lines=25):
        """A scan of `pages` real pages - showPage() is what starts a new one."""
        import fitz
        from reportlab.pdfgen import canvas
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer)
        for page in range(pages):
            for line in range(lines):
                pdf.drawString(40, 780 - line * 20,
                               f"LINE {page}-{line} ITEM DESCRIPTION 12 75.37 904.44")
            pdf.showPage()
        pdf.save()

        source = fitz.open(stream=buffer.getvalue(), filetype="pdf")
        out = fitz.open()
        for index in range(len(source)):
            pix = source.load_page(index).get_pixmap(matrix=fitz.Matrix(2, 2))
            page = out.new_page(width=pix.width / 2, height=pix.height / 2)
            page.insert_image(page.rect, stream=pix.tobytes("png"))
        return out.tobytes()

    def calls_for(self, pages, captured):
        S.get_ai_setting = lambda key, default=None, company_id=None: "z-ai/glm-5.3-flash"
        S.get_openai_client = lambda: fake_openai(GOOD_REPLY, captured)
        return S.extract_invoice_data_vision(self.multipage(pages), "scan.pdf",
                                             "Purchase")

    def test_more_than_three_pages_are_sent(self):
        """Three pages used to be the hard limit, silently dropping the rest."""
        images, total = S.convert_pdf_to_images(self.multipage(6))
        self.assertEqual(total, 6)
        self.assertEqual(len(images), 6)

    def test_a_long_scan_is_read_in_batches_with_no_page_limit(self):
        """40 pages: batched into separate calls, every page read."""
        seen = []
        S.get_ai_setting = lambda key, default=None, company_id=None: "z-ai/glm-5.3-flash"
        S.get_openai_client = lambda: _counting_client(seen)
        data = S.extract_invoice_data_vision(self.multipage(40), "scan.pdf",
                                             "Purchase")
        pages_sent = sum(seen)
        self.assertEqual(len(seen), 10)      # 40 pages / 4 per batch
        self.assertEqual(pages_sent, 40)     # nothing dropped
        self.assertIsNone(data.get("warning"))

    def test_batches_are_merged_into_one_invoice(self):
        seen = []
        S.get_ai_setting = lambda key, default=None, company_id=None: "z-ai/glm-5.3-flash"
        S.get_openai_client = lambda: _counting_client(seen)
        data = S.extract_invoice_data_vision(self.multipage(12), "scan.pdf",
                                             "Purchase")
        # header from the first batch, items from all of them
        self.assertEqual(data["vendor_name"], "BAQAR MOHEBI")
        self.assertEqual(data["invoice_number"], "INV-555087")
        self.assertEqual(len(data["items"]), 3)   # one per batch

    def test_a_short_document_still_uses_a_single_call(self):
        captured = {}
        self.calls_for(3, captured)
        content = captured["messages"][1]["content"]
        self.assertEqual(sum(1 for p in content if p.get("type") == "image_url"), 3)

    def test_one_failed_batch_does_not_lose_the_others(self):
        seen = []
        S.get_ai_setting = lambda key, default=None, company_id=None: "z-ai/glm-5.3-flash"
        S.get_openai_client = lambda: _counting_client(seen, fail_on={2})
        data = S.extract_invoice_data_vision(self.multipage(12), "scan.pdf",
                                             "Purchase")
        self.assertEqual(len(data["items"]), 2)          # 3 batches, 1 failed
        self.assertIn("pages 5-8", data["warning"])

    def test_a_large_document_stays_within_the_payload_budget(self):
        images, _ = S.convert_pdf_to_images(self.multipage(12))
        self.assertLessEqual(sum(len(i) for i in images), S.PAYLOAD_BUDGET_BYTES)
        self.assertEqual(len(images), 12)      # shrunk, not truncated

    def test_twenty_pages_are_read_in_full_without_a_warning(self):
        seen = []
        S.get_ai_setting = lambda key, default=None, company_id=None: "z-ai/glm-5.3-flash"
        S.get_openai_client = lambda: _counting_client(seen)
        data = S.extract_invoice_data_vision(self.multipage(20), "big.pdf",
                                             "Purchase")
        self.assertEqual(sum(seen), 20)
        self.assertIsNone(data.get("warning"))

    def test_beyond_the_overall_cap_the_user_is_warned(self):
        """The cap is a cost rail, not a capability limit - but say when it bites."""
        seen = []
        S.get_ai_setting = lambda key, default=None, company_id=None: "z-ai/glm-5.3-flash"
        S.get_openai_client = lambda: _counting_client(seen)
        original = S._batch_settings
        S._batch_settings = lambda company_id=None: (4, 8)   # pretend the cap is 8
        try:
            data = S.extract_invoice_data_vision(self.multipage(12), "big.pdf",
                                                 "Purchase")
        finally:
            S._batch_settings = original
        self.assertEqual(sum(seen), 8)
        self.assertIn("first 8 of 12 pages", data["warning"])

    def test_the_batch_size_can_be_configured(self):
        seen = []
        S.get_openai_client = lambda: _counting_client(seen)
        S.get_ai_setting = lambda key, default=None, company_id=None: (
            2 if key == "invoice_pages_per_batch" else
            (120 if key == "invoice_max_pages" else "z-ai/glm-5.3-flash"))
        S.extract_invoice_data_vision(self.multipage(6), "scan.pdf", "Purchase")
        self.assertEqual(seen, [2, 2, 2])

    def test_the_token_budget_grows_with_the_page_count(self):
        captured = {}
        S.get_openai_client = lambda: fake_openai(GOOD_REPLY, captured)
        S.get_ai_setting = lambda key, default=None, company_id=None: "z-ai/glm-5.3-flash"
        S.extract_invoice_data_vision(self.multipage(6), "scan.pdf", "Purchase")
        self.assertGreater(captured["max_completion_tokens"], 1000)

    def test_the_model_is_told_to_read_every_page(self):
        captured = {}
        S.get_openai_client = lambda: fake_openai(GOOD_REPLY, captured)
        S.get_ai_setting = lambda key, default=None, company_id=None: "z-ai/glm-5.3-flash"
        S.extract_invoice_data_vision(self.multipage(4), "scan.pdf", "Purchase")
        instruction = captured["messages"][1]["content"][0]["text"]
        self.assertIn("Read every page", instruction)


class TestEmptyModelAnswer(unittest.TestCase):
    """"AI returned empty response" blamed the API key. It was never the key."""

    def setUp(self):
        S.get_ai_setting = lambda key, default=None, company_id=None: "z-ai/glm-5.3-flash"

    def test_running_out_of_output_room_says_so(self):
        S.get_openai_client = lambda: fake_openai("", finish_reason="length")
        with self.assertRaises(S.InvoiceExtractionError) as caught:
            S.extract_invoice_data_vision(text_pdf(SAMPLE_INVOICE), "i.pdf",
                                          "Purchase")
        message = str(caught.exception)
        self.assertIn("ran out of room", message)
        self.assertNotIn("API key", message.split("API key is working")[0])

    def test_an_empty_answer_does_not_blame_the_api_key(self):
        S.get_openai_client = lambda: fake_openai("", finish_reason="stop")
        with self.assertRaises(S.InvoiceExtractionError) as caught:
            S.extract_invoice_data_vision(text_pdf(SAMPLE_INVOICE), "i.pdf",
                                          "Purchase")
        self.assertIn("API key is working", str(caught.exception))

    def test_an_answer_left_in_the_reasoning_field_is_recovered(self):
        S.get_openai_client = lambda: fake_openai(
            "", reasoning="Let me read the invoice... " + GOOD_REPLY)
        data = S.extract_invoice_data_vision(text_pdf(SAMPLE_INVOICE), "i.pdf",
                                             "Purchase")
        self.assertEqual(data["items"][0]["quantity"], 11)


class TestNormalisation(unittest.TestCase):
    """Models paraphrase the schema; the reply still has to be usable."""

    def normalise(self, blob):
        return S.normalise_purchase_data(blob)

    def test_the_exact_schema(self):
        data = self.normalise(json.loads(GOOD_REPLY))
        self.assertEqual(data["vendor_name"], "BAQAR MOHEBI")
        self.assertEqual(data["items"][0]["unit_rate"], 75.37)

    def test_alias_keys(self):
        data = self.normalise({
            "vendor": "BAQAR MOHEBI", "invoice_no": "INV-555087",
            "date": "21-01-2026",
            "line_items": [{"item_name": "PASTA", "qty": 11, "rate": 75.37,
                            "vat": 5}]})
        self.assertEqual(data["vendor_name"], "BAQAR MOHEBI")
        self.assertEqual(data["invoice_number"], "INV-555087")
        self.assertEqual(data["items"][0]["quantity"], 11)

    def test_units_and_currency_are_stripped_from_numbers(self):
        data = self.normalise({
            "vendor": "V", "items": [{"item": "X", "qty": "11 PCS",
                                      "rate": "AED 1,075.37", "vat": "5%"}]})
        item = data["items"][0]
        self.assertEqual(item["quantity"], 11)
        self.assertEqual(item["unit_rate"], 1075.37)
        self.assertEqual(item["vat_percent"], 5)

    def test_the_rate_is_derived_when_only_an_amount_is_given(self):
        data = self.normalise({
            "vendor": "V", "items": [{"item": "X", "quantity": 11,
                                      "amount": 829.07}]})
        self.assertEqual(data["items"][0]["unit_rate"], 75.37)

    def test_a_nested_wrapper_keeps_the_header_fields(self):
        data = self.normalise({"invoice": json.loads(GOOD_REPLY)})
        self.assertEqual(data["vendor_name"], "BAQAR MOHEBI")
        self.assertEqual(data["invoice_number"], "INV-555087")
        self.assertEqual(len(data["items"]), 1)

    def test_blank_rows_are_dropped(self):
        data = self.normalise({
            "vendor": "V",
            "items": [{"item": "X", "quantity": 1, "unit_rate": 5},
                      {"item": "", "quantity": 0, "unit_rate": 0}]})
        self.assertEqual(len(data["items"]), 1)


class TestNoBlankSpreadsheet(unittest.TestCase):
    """The original symptom: a workbook with headings and nothing else."""

    def test_no_items_raises_instead_of_writing_an_empty_file(self):
        with self.assertRaises(S.InvoiceExtractionError):
            S.generate_purchase_excel({"vendor_name": "V", "items": []})

    def test_an_empty_extraction_is_reported_with_a_reason(self):
        captured = {}
        S.get_openai_client = lambda: fake_openai(
            '{"vendor_name":"","items":[]}', captured)
        S.get_ai_setting = lambda key, default=None, company_id=None: "openai/gpt-oss-120b"
        with self.assertRaises(S.InvoiceExtractionError) as caught:
            S.extract_invoice_data_vision(text_pdf(SAMPLE_INVOICE),
                                          "invoice.pdf", "Purchase")
        self.assertIn("openai/gpt-oss-120b", str(caught.exception))

    def test_a_scan_names_the_vision_problem(self):
        message = S._no_items_message(used_text=False, model="openai/gpt-oss-120b")
        self.assertIn("vision", message.lower())


class TestEndToEnd(unittest.TestCase):

    def setUp(self):
        self.captured = {}
        S.get_openai_client = lambda: fake_openai(GOOD_REPLY, self.captured)
        S.get_ai_setting = lambda key, default=None, company_id=None: "openai/gpt-oss-120b"

    def test_a_text_pdf_is_sent_as_text_not_as_an_image(self):
        S.extract_invoice_data_vision(text_pdf(SAMPLE_INVOICE), "invoice.pdf",
                                      "Purchase")
        content = self.captured["messages"][1]["content"]
        self.assertIsInstance(content, str)          # not a vision payload
        self.assertIn("HAYAT SPAGHETTI PASTA 500GM", content)

    def test_the_spreadsheet_carries_the_invoice_values(self):
        data = S.extract_invoice_data_vision(text_pdf(SAMPLE_INVOICE),
                                             "invoice.pdf", "Purchase")
        frame = pd.read_excel(S.generate_purchase_excel(data))
        self.assertEqual(len(frame), 1)
        row = frame.iloc[0]
        self.assertEqual(row["Party Ledger Name"], "BAQAR MOHEBI")
        self.assertEqual(row["Item Name"], "HAYAT SPAGHETTI PASTA 500GM")
        self.assertEqual(row["Quantity"], 11)
        self.assertAlmostEqual(row["Rate"], 75.37, places=2)
        self.assertEqual(row["VAT %"], 5)
        self.assertEqual(row["Reference Number"], "INV-555087")
        self.assertEqual(row["Invoice Date"], "21-01-2026")

    def test_json_wrapped_in_prose_is_still_parsed(self):
        S.get_openai_client = lambda: fake_openai(
            "Here is the data you asked for:\n" + GOOD_REPLY, self.captured)
        data = S.extract_invoice_data_vision(text_pdf(SAMPLE_INVOICE),
                                             "invoice.pdf", "Purchase")
        self.assertEqual(data["items"][0]["quantity"], 11)


if __name__ == "__main__":
    unittest.main()
