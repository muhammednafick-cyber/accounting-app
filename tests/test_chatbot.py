import unittest
from unittest.mock import patch, MagicMock
import sys
import os
import datetime

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from accounting_app.chatbot_service import execute_intent, parse_date_range

class TestChatbotIntents(unittest.TestCase):

    @patch('accounting_app.chatbot_service.get_ledgers')
    def test_get_cash_balance(self, mock_get_ledgers):
        mock_get_ledgers.return_value = [{'ledger_name': 'Cash', 'closing_balance': 1000.0}]
        parsed_data = {
            "intent": "get_cash_balance",
            "parameters": {},
            "explanation": "test"
        }
        result = execute_intent(parsed_data, company_id=1)
        self.assertIn("1000.00", result['response'])
        mock_get_ledgers.assert_called_with(group_code='G005', company_id=1)

    @patch('accounting_app.chatbot_service.get_stock_movement_data')
    @patch('accounting_app.chatbot_service.get_items')
    def test_get_stock_status(self, mock_items, mock_movement):
        mock_items.return_value = [{'item_code': 'I001', 'name': 'Item A'}]
        # mock movement: date, vn, vt, in, out, run_qty, wap, run_val
        mock_movement.return_value = [
            ('2023-01-01', 'VN1', 'Purchase', 10, 0, 10, 100, 1000)
        ]
        
        parsed_data = {
            "intent": "get_stock_status",
            "parameters": {"item_name": "Item A"},
            "explanation": "test"
        }
        result = execute_intent(parsed_data, company_id=1)
        self.assertIn("Current stock of Item A is 10", result['response'])

    @patch('accounting_app.chatbot_service.get_ledger_transactions')
    @patch('accounting_app.chatbot_service.get_ledgers')
    def test_get_customer_sales_total(self, mock_ledgers, mock_trans):
        mock_ledgers.return_value = [{'ledger_name': 'Customer ABC', 'ledger_code': 'C001', 'closing_balance': 500}]
        # Mock transactions: debit is sales
        mock_trans.return_value = ([
            {'date': '2023-01-01', 'voucher_type': 'Sales', 'debit': 100, 'credit': 0},
            {'date': '2023-01-02', 'voucher_type': 'Sales', 'debit': 200, 'credit': 0},
            {'date': '2023-01-03', 'voucher_type': 'Receipt', 'debit': 0, 'credit': 300}
        ], 0)
        
        parsed_data = {
            "intent": "get_customer_sales_total",
            "parameters": {"entity_name": "Customer ABC", "date_range": "this_month"},
            "explanation": "test"
        }
        result = execute_intent(parsed_data, company_id=1)
        # Total Sales = 100 + 200 = 300
        self.assertIn("Total sales to Customer ABC", result['response'])
        self.assertIn("300.00", result['response'])

    @patch('accounting_app.chatbot_service.get_ageing_report_data')
    def test_get_pending_invoices(self, mock_ageing):
        # Mock ageing data
        mock_ageing.return_value = [
            {'ledger_name': 'Customer A', 'balance': 1000},
            {'ledger_name': 'Customer B', 'balance': 0},
            {'ledger_name': 'Customer C', 'balance': 500}
        ]
        
        parsed_data = {
            "intent": "get_pending_invoices",
            "parameters": {},
            "explanation": "Which invoices are overdue?"
        }
        result = execute_intent(parsed_data, company_id=1)
        self.assertIn("Customer A (1000)", result['response'])
        self.assertIn("Customer C (500)", result['response'])
        self.assertNotIn("Customer B", result['response'])

    @patch('accounting_app.chatbot_service.get_slow_moving_items')
    def test_get_no_sales_items(self, mock_get_slow):
        mock_get_slow.return_value = [{'item_name': 'Item X'}]
        parsed_data = {
            "intent": "get_no_sales_items",
            "parameters": {},
            "explanation": "test"
        }
        result = execute_intent(parsed_data, company_id=1)
        self.assertIn("Item X", result['response'])
        # Check call arguments - should use 365 days
        mock_get_slow.assert_called_with(days_threshold=365, company_id=1)

    @patch('accounting_app.chatbot_service.get_monthly_sales_trend')
    def test_compare_monthly_sales(self, mock_trend):
        # Mock trend data: 
        # Current month (say May=05): 5000
        # Last month (April=04): 4000
        
        # We need to know current month to mock correctly
        today = datetime.date.today()
        curr_m_str = str(today.month).zfill(2)
        prev_m_num = today.month - 1 if today.month > 1 else 12
        prev_m_str = str(prev_m_num).zfill(2)
        
        mock_trend.return_value = {
            curr_m_str: 5000.0,
            prev_m_str: 4000.0
        }
        
        parsed_data = {
            "intent": "compare_monthly_sales",
            "parameters": {},
            "explanation": "test"
        }
        result = execute_intent(parsed_data, company_id=1)
        
        self.assertIn(f"This Month ({today.strftime('%B')}): 5000.00", result['response'])
        self.assertIn("Difference: 1000.00 (increase)", result['response'])

if __name__ == '__main__':
    unittest.main()
