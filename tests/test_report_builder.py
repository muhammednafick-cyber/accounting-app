"""Tests for the Custom Report Builder's query compiler.

The security tests matter most: this is the one place where a user's choices
shape SQL, so the whitelist and the per-table company_id filter have to hold
for every route through the compiler. None of these tests touch the database.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from accounting_app import report_builder as RB

COMPANY = 42


def compile_ok(definition, company_id=COMPANY):
    return RB.compile_report(definition, company_id)


class TestCompilation(unittest.TestCase):

    def test_simple_listing(self):
        sql, params, columns = compile_ok({
            "dataset": "vouchers",
            "columns": [{"field": "vouchers.voucher_number"},
                        {"field": "vouchers.amount"}],
        })
        self.assertIn("FROM vouchers v", sql)
        self.assertIn("WHERE v.company_id = %s", sql)
        self.assertEqual([c["label"] for c in columns], ["Voucher No", "Voucher Amount"])
        self.assertEqual(params[0], COMPANY)

    def test_joins_are_added_for_related_tables(self):
        sql, _, _ = compile_ok({
            "dataset": "ledger_entries",
            "columns": [{"field": "ledger_entries.amount"},
                        {"field": "groups.nature"}],
        })
        # groups is reached through ledgers, so both joins must appear
        self.assertIn("LEFT JOIN ledgers l", sql)
        self.assertIn("LEFT JOIN groups g", sql)

    def test_aggregate_produces_group_by(self):
        sql, _, columns = compile_ok({
            "dataset": "ledger_entries",
            "columns": [{"field": "ledger_entries.ledger_name"},
                        {"field": "ledger_entries.amount", "aggregate": "sum"}],
        })
        self.assertIn("SUM(COALESCE(le.amount, 0))", sql)
        self.assertIn("GROUP BY le.ledger_name", sql)
        self.assertEqual(columns[1]["label"], "Sum of Entry Amount")

    def test_no_group_by_without_an_aggregate(self):
        sql, _, _ = compile_ok({
            "dataset": "vouchers",
            "columns": [{"field": "vouchers.voucher_number"}],
        })
        self.assertNotIn("GROUP BY", sql)

    def test_filter_values_are_parameters(self):
        sql, params, _ = compile_ok({
            "dataset": "vouchers",
            "columns": [{"field": "vouchers.amount"}],
            "filters": [{"field": "vouchers.voucher_type", "operator": "eq",
                         "values": ["Sales"]}],
        })
        self.assertIn("v.voucher_type = %s", sql)
        self.assertIn("Sales", params)

    def test_between_takes_two_values(self):
        sql, params, _ = compile_ok({
            "dataset": "vouchers",
            "columns": [{"field": "vouchers.amount"}],
            "filters": [{"field": "vouchers.date", "operator": "between",
                         "values": ["2024-01-01", "2024-12-31"]}],
        })
        self.assertIn("BETWEEN %s AND %s", sql)
        self.assertIn("2024-01-01", params)
        self.assertIn("2024-12-31", params)

    def test_in_builds_one_placeholder_per_value(self):
        sql, params, _ = compile_ok({
            "dataset": "vouchers",
            "columns": [{"field": "vouchers.amount"}],
            "filters": [{"field": "vouchers.voucher_type", "operator": "in",
                         "values": ["Sales", "Purchase", "Journal"]}],
        })
        self.assertIn("IN (%s, %s, %s)", sql)

    def test_contains_is_wrapped_and_lowercased(self):
        _, params, _ = compile_ok({
            "dataset": "vouchers",
            "columns": [{"field": "vouchers.amount"}],
            "filters": [{"field": "vouchers.narration", "operator": "contains",
                         "values": ["ABC"]}],
        })
        self.assertIn("%abc%", params)

    def test_row_limit_is_capped(self):
        _, params, _ = compile_ok({
            "dataset": "vouchers",
            "columns": [{"field": "vouchers.amount"}],
            "limit": 10 ** 9,
        })
        self.assertEqual(params[-1], RB.MAX_ROWS)

    def test_heading_does_not_repeat_the_table_name(self):
        _, _, columns = compile_ok({
            "dataset": "ledger_entries",
            "columns": [{"field": "vouchers.date"}],
        })
        self.assertEqual(columns[0]["label"], "Voucher Date")

    def test_a_custom_heading_wins(self):
        _, _, columns = compile_ok({
            "dataset": "vouchers",
            "columns": [{"field": "vouchers.amount", "label": "Invoice Total"}],
        })
        self.assertEqual(columns[0]["label"], "Invoice Total")


class TestCompanyScoping(unittest.TestCase):
    """Every table in the query must be pinned to the caller's company."""

    def test_base_table_is_scoped(self):
        sql, params, _ = compile_ok({
            "dataset": "vouchers", "columns": [{"field": "vouchers.amount"}]})
        self.assertIn("v.company_id = %s", sql)
        self.assertEqual(params.count(COMPANY), 1)

    def test_every_joined_table_is_scoped(self):
        sql, params, _ = compile_ok({
            "dataset": "ledger_entries",
            "columns": [{"field": "ledger_entries.amount"},
                        {"field": "vouchers.date"},
                        {"field": "master_groups.master_group_name"}],
        })
        joins = sql.count("LEFT JOIN")
        for alias in ("v", "l", "g", "mg"):
            self.assertIn(f"{alias}.company_id = %s", sql)
        # one company_id parameter per join, plus one for the base table
        self.assertEqual(params.count(COMPANY), joins + 1)

    def test_a_missing_company_is_refused(self):
        for empty in (None, 0, ""):
            with self.assertRaises(RB.ReportError):
                RB.compile_report(
                    {"dataset": "vouchers",
                     "columns": [{"field": "vouchers.amount"}]}, empty)

    def test_every_exposed_table_has_a_company_column(self):
        """A table without company_id could never be scoped, so none may be listed."""
        from database.config import get_connection
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'company_id'")
            scoped = {r[0] for r in cursor.fetchall()}
        finally:
            conn.close()
        for key, table in RB.TABLES.items():
            self.assertIn(table["table"], scoped,
                          f"{key} is exposed but has no company_id column")


class TestInjectionIsBlocked(unittest.TestCase):

    def attempt(self, definition):
        with self.assertRaises(RB.ReportError):
            compile_ok(definition)

    def test_sql_in_a_field_reference(self):
        self.attempt({"dataset": "vouchers",
                      "columns": [{"field": "vouchers.amount FROM users --"}]})

    def test_a_table_that_is_not_exposed(self):
        self.attempt({"dataset": "vouchers",
                      "columns": [{"field": "users.password_hash"}]})

    def test_a_column_that_is_not_exposed(self):
        self.attempt({"dataset": "vouchers",
                      "columns": [{"field": "vouchers.id"}]})

    def test_a_table_outside_the_chosen_dataset(self):
        self.attempt({"dataset": "vouchers",
                      "columns": [{"field": "ledgers.closing_balance"}]})

    def test_an_invented_aggregate(self):
        self.attempt({"dataset": "vouchers",
                      "columns": [{"field": "vouchers.amount",
                                   "aggregate": "SUM(1)); DROP TABLE vouchers;--"}]})

    def test_an_invented_operator(self):
        self.attempt({"dataset": "vouchers",
                      "columns": [{"field": "vouchers.amount"}],
                      "filters": [{"field": "vouchers.amount",
                                   "operator": "1=1 OR", "values": ["x"]}]})

    def test_an_invented_sort_direction(self):
        self.attempt({"dataset": "vouchers",
                      "columns": [{"field": "vouchers.amount"}],
                      "sort": [{"field": "vouchers.amount",
                                "direction": "asc; DROP TABLE x"}]})

    def test_sorting_by_a_column_that_was_not_selected(self):
        self.attempt({"dataset": "vouchers",
                      "columns": [{"field": "vouchers.amount"}],
                      "sort": [{"field": "vouchers.narration", "direction": "asc"}]})

    def test_an_unknown_dataset(self):
        self.attempt({"dataset": "users",
                      "columns": [{"field": "users.username"}]})

    def test_no_columns_at_all(self):
        self.attempt({"dataset": "vouchers", "columns": []})

    def test_a_quote_in_a_value_stays_a_value(self):
        payload = "' OR '1'='1"
        sql, params, _ = compile_ok({
            "dataset": "vouchers",
            "columns": [{"field": "vouchers.amount"}],
            "filters": [{"field": "vouchers.narration", "operator": "eq",
                         "values": [payload]}]})
        self.assertNotIn(payload, sql)
        self.assertIn(payload, params)

    def test_text_in_a_number_filter_is_rejected(self):
        self.attempt({"dataset": "vouchers",
                      "columns": [{"field": "vouchers.amount"}],
                      "filters": [{"field": "vouchers.amount", "operator": "gt",
                                   "values": ["not a number"]}]})

    def test_summing_a_text_column_is_rejected(self):
        self.attempt({"dataset": "vouchers",
                      "columns": [{"field": "vouchers.narration",
                                   "aggregate": "sum"}]})


class TestRegistryIntegrity(unittest.TestCase):

    def test_every_dataset_points_at_a_known_table(self):
        for key, ds in RB.DATASETS.items():
            self.assertIn(ds["base"], RB.TABLES, key)
            for related in ds["related"]:
                self.assertIn(related, RB.TABLES, f"{key} -> {related}")

    def test_every_related_path_has_a_join_defined(self):
        for key, ds in RB.DATASETS.items():
            for related, path in ds["related"].items():
                previous = ds["base"]
                for hop in path:
                    self.assertIn((previous, hop), RB.JOINS,
                                  f"{key}: no join {previous} -> {hop}")
                    previous = hop

    def test_aliases_are_unique(self):
        aliases = [t["alias"] for t in RB.TABLES.values()]
        self.assertEqual(len(aliases), len(set(aliases)))

    def test_every_dataset_compiles_with_its_first_column(self):
        for key, ds in RB.DATASETS.items():
            first = RB.TABLES[ds["base"]]["fields"][0]
            sql, _, _ = compile_ok({
                "dataset": key,
                "columns": [{"field": f"{ds['base']}.{first['name']}"}]})
            self.assertIn("company_id = %s", sql, key)

    def test_every_related_table_compiles(self):
        for key, ds in RB.DATASETS.items():
            for related in ds["related"]:
                field = RB.TABLES[related]["fields"][0]
                compile_ok({"dataset": key,
                            "columns": [{"field": f"{related}.{field['name']}"}]})

    def test_schema_description_is_ordered_lists(self):
        schema = RB.describe_schema()
        self.assertIsInstance(schema["operators"], list)
        self.assertIsInstance(schema["aggregates"], list)
        self.assertEqual(schema["operators"][0]["key"], "eq")


if __name__ == "__main__":
    unittest.main()
