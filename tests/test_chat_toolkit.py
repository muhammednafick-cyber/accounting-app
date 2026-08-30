"""Tool result shapes, checked against what the database layer really returns.

Most reporting functions hand back dictionaries. `get_stock_movement_data` is
the exception: it builds fixed 9-field tuples. The stock movement tool read
keys off them, so every "stock movement of <item>" ended in

    I could not complete that: 'tuple' object has no attribute 'keys'

The database is stubbed here - these are about the shape of the rows, not the
figures in them.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from accounting_app import chat_toolkit as TK
from database import reports_db


# (voucher_number, date, voucher_type, qty_in, qty_out, running_qty, wap,
#  running_value, location) - the contract get_stock_movement_data builds.
MOVEMENTS = [
    ("PUR-00001", "2023-03-04", "Purchase", 100.0, 0, 100.0, 12.5, 1250.0, "Main Store"),
    ("SAL-00007", "2023-06-11", "Sales", 0, 40.0, 60.0, 12.5, 750.0, "Main Store"),
    ("SAL-00019", "2023-11-02", "Sales", 0, 25.0, 35.0, 12.5, 437.5, ""),
]

ARGS = {"item_name": "Swimming Glass", "company_id": 1, "start": None, "end": None,
        "period_label": "all time"}


def run(rows, args=None):
    with patch.object(reports_db, "get_stock_movement_data", return_value=rows):
        return TK.TOOLS["stock_movement"].fn(dict(args or ARGS))


class TestStockMovement(unittest.TestCase):

    def test_tuple_rows_do_not_raise(self):
        result = run(MOVEMENTS)          # used to raise AttributeError
        self.assertEqual(len(result["rows"]), 3)

    def test_every_row_matches_the_header(self):
        result = run(MOVEMENTS)
        self.assertEqual(len(result["columns"]), 9)
        for row in result["rows"]:
            self.assertEqual(len(row), len(result["columns"]))

    def test_the_columns_are_named(self):
        # Not "0, 1, 2" - the tuple carries no names of its own.
        self.assertEqual(
            run(MOVEMENTS)["columns"],
            ["Voucher", "Date", "Type", "In", "Out", "Balance Qty", "WAP", "Value",
             "Location"])

    def test_the_totals_add_up(self):
        totals = run(MOVEMENTS)["totals"]
        self.assertEqual(totals["In"], "100.00")
        self.assertEqual(totals["Out"], "65.00")
        # Closing is the last row's running figures, not a sum of the column.
        self.assertEqual(totals["Closing Qty"], "35.00")
        self.assertEqual(totals["Closing Value"], "437.50")

    def test_the_item_is_named_in_the_answer(self):
        self.assertIn("Swimming Glass", run(MOVEMENTS)["title"])
        self.assertIn("Swimming Glass", run(MOVEMENTS)["summary"])

    def test_no_movements_is_not_an_error(self):
        result = run([])
        self.assertEqual(result["rows"], [])
        self.assertIn("No movements", result["summary"])

    def test_a_single_movement_still_closes_correctly(self):
        totals = run(MOVEMENTS[:1])["totals"]
        self.assertEqual(totals["Closing Qty"], "100.00")


def loc_row(item, location, qty, wap, value):
    return {"item_code": "X", "item_name": item, "group_name": "G",
            "location_name": location, "quantity": qty, "wap": wap,
            "cost_amount": value}


CLOSING = ([loc_row("Cement", "Abu Dhabi", 40, 12.5, 500.0),
            loc_row("Cement", "Main Store", 60, 12.5, 750.0),
            loc_row("Swimming Glass", "Abu Dhabi", 35, 10.0, 350.0),
            loc_row("Sand", "Main Store", 0, 0, 0)], 1600.0)


class TestStockByLocation(unittest.TestCase):
    """Stock per location, and per item within a location.

    The old query summed item_entries with `CASE WHEN ie.type = 'In'`, but
    that column holds 'Debit' / 'Credit'. Nothing ever matched 'In', so every
    quantity took the ELSE branch and the report returned minus the gross
    movement rather than the stock on hand - and ignored opening balances.
    It now reads the same replay the Closing Inventory screen uses.
    """

    def run_tool(self, **kw):
        args = {"company_id": 1, "start": None, "end": None, "period_label": "all time"}
        args.update(kw)
        with patch.object(reports_db, "get_closing_inventory_data", return_value=CLOSING):
            return TK.TOOLS["stock_by_location"].fn(args)

    def test_every_location(self):
        result = self.run_tool()
        self.assertEqual(len(result["rows"]), 3)          # the zero row is dropped
        self.assertEqual(result["totals"]["Quantity"], "135.00")

    def test_one_location(self):
        result = self.run_tool(location_name="Abu Dhabi")
        self.assertEqual([r[0] for r in result["rows"]], ["Cement", "Swimming Glass"])
        self.assertEqual(result["totals"]["Quantity"], "75.00")

    def test_one_item_across_locations(self):
        result = self.run_tool(item_name="Cement")
        self.assertEqual([r[1] for r in result["rows"]], ["Abu Dhabi", "Main Store"])
        self.assertEqual(result["totals"]["Quantity"], "100.00")

    def test_one_item_at_one_location(self):
        result = self.run_tool(item_name="Cement", location_name="Abu Dhabi")
        self.assertEqual(result["rows"], [["Cement", "Abu Dhabi", 40, 12.5, 500.0]])
        self.assertEqual(result["totals"]["Quantity"], "40.00")

    def test_quantities_are_positive(self):
        # The symptom of the old expression: every figure came back negative.
        for row in self.run_tool()["rows"]:
            self.assertGreater(row[2], 0, row)

    def test_the_scope_is_stated(self):
        # A named location that matched nothing must not read as a total.
        summary = self.run_tool(item_name="Cement", location_name="Abu Dhabi")["summary"]
        self.assertIn("Cement", summary)
        self.assertIn("Abu Dhabi", summary)

    def test_a_location_with_no_stock_says_so(self):
        result = self.run_tool(location_name="Dubai")
        self.assertEqual(result["rows"], [])
        self.assertIn("Dubai", result["summary"])


if __name__ == "__main__":
    unittest.main()
