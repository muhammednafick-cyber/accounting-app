"""One date picker for the whole application: the Receipt voucher's.

Before this, three kinds were in use: the Receipt voucher's flatpickr set up
globally in script.js, ten report fields each setting up their own flatpickr,
and twelve native <input type="date"> fields that looked and behaved
differently in every browser. All of them now go through static/date_picker.js.
"""
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def every_template():
    for folder, _dirs, files in os.walk(os.path.join(ROOT, "templates")):
        for name in files:
            if name.endswith(".html"):
                path = os.path.join(folder, name)
                with io.open(path, encoding="utf-8") as f:
                    yield os.path.relpath(path, ROOT), f.read()


class OneComponentTests(unittest.TestCase):

    def test_nothing_sets_up_a_calendar_of_its_own(self):
        # Every calendar comes from the component, so a change there reaches
        # every screen. A page calling flatpickr() directly would drift.
        offenders = []
        for path, text in every_template():
            if "flatpickr(" in text:
                offenders.append(path)
        for name in os.listdir(os.path.join(ROOT, "static")):
            if name.endswith(".js") and name != "date_picker.js":
                if "flatpickr(" in read("static", name):
                    offenders.append("static/" + name)
        self.assertEqual(offenders, [])

    def test_script_js_no_longer_owns_the_setup(self):
        self.assertNotIn("initFlatpickr", read("static", "script.js"))

    def test_the_component_loads_before_any_page_script(self):
        base = read("templates", "base.html")
        head = base[:base.index("</head>")]
        flatpickr_at = head.index("cdn.jsdelivr.net/npm/flatpickr\"></script>")
        component_at = head.index("filename='date_picker.js'")
        self.assertLess(flatpickr_at, component_at)


class ReceiptSettingsTests(unittest.TestCase):
    """The settings are the Receipt voucher's, and they live in one place."""

    def setUp(self):
        self.js = read("static", "date_picker.js")

    def test_dd_mm_yyyy_with_typing_allowed(self):
        self.assertIn("var DISPLAY_FORMAT = 'd-m-Y';", self.js)
        self.assertIn("dateFormat: DISPLAY_FORMAT,", self.js)
        self.assertIn("allowInput: true,", self.js)

    def test_closing_the_calendar_tells_the_page(self):
        # Receipt fires input and change on close; due dates and totals
        # listen for them.
        self.assertIn("onClose: function (selectedDates, dateStr, instance) {", self.js)
        self.assertIn("el.dispatchEvent(new Event('input', { bubbles: true }));", self.js)
        self.assertIn("el.dispatchEvent(new Event('change', { bubbles: true }));", self.js)

    def test_the_file_has_no_hidden_characters(self):
        # The shell once turned a backslash-b into a backspace character here,
        # silently breaking a regex.
        self.assertEqual(sum(self.js.count(c) for c in "\x08\x0c\x07\x0b"), 0)


class NativeDateFieldTests(unittest.TestCase):
    """Native date fields look like Receipt but submit exactly what they did."""

    def setUp(self):
        self.js = read("static", "date_picker.js")

    def test_native_fields_are_picked_up(self):
        self.assertIn("if (isNativeDate(el)) return true;", self.js)

    def test_the_server_still_receives_yyyy_mm_dd(self):
        self.assertIn("var NATIVE_FORMAT = 'Y-m-d';", self.js)
        self.assertIn("options.dateFormat = NATIVE_FORMAT;", self.js)
        self.assertIn("options.altInput = true;", self.js)
        self.assertIn("options.altFormat = DISPLAY_FORMAT;", self.js)

    def test_required_fields_stay_required(self):
        self.assertIn("instance.altInput.required = true;", self.js)

    def test_text_that_is_not_a_date_is_dropped_as_the_browser_did(self):
        self.assertIn("var raw = el.getAttribute('value');", self.js)
        self.assertIn(r"!/^\d{4}-\d{2}-\d{2}$/.test(raw)", self.js)

    def test_every_native_field_left_in_markup_is_one_the_component_handles(self):
        # Native fields stay in the templates - the component converts them on
        # load - so what must not exist is a date input some other way.
        for path, text in every_template():
            for m in re.finditer(r'<input[^>]*type="(datetime-local|month|week)"', text):
                self.fail("%s has an unhandled %s field" % (path, m.group(1)))


class ScreenFixTests(unittest.TestCase):

    def test_the_order_form_sets_today_in_local_time(self):
        form = read("templates", "orders", "order_form.html")
        self.assertNotIn(".valueAsDate =", form)
        self.assertIn("AppDatePicker.setDate(document.getElementById('date'), new Date());", form)

    def test_statement_of_account_styles_the_new_field(self):
        self.assertIn(".soa-bar input.app-date",
                      read("templates", "report_statement_of_account.html"))

    def test_browsers_fetch_the_new_script(self):
        base = read("templates", "base.html")
        self.assertIn("filename='script.js') }}?v=20260926_2", base)
        self.assertIn("filename='date_picker.js') }}?v=", base)


if __name__ == "__main__":
    unittest.main()
