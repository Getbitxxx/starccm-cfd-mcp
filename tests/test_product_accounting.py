"""Execute the actual bookkeeping helper without running its live-status renderer."""
import ast
import copy
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'test-evidence/product-acceptance/completion-20261018/update_progress.py'
tree=ast.parse(SOURCE.read_text())
function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='book_invoice')
scope={'copy':copy}
exec(compile(ast.Module(body=[function],type_ignores=[]),str(SOURCE),'exec'),scope)
book=scope['book_invoice']
class ProductAccounting(unittest.TestCase):
    def test_old_and_new_invoice_replays_do_not_rewind_last_round_or_charge_twice(self):
        old={'advanced_stage':False,'previous_rounds_since_stage_advance':3}
        account={'star_hours_logged':2.5,'rounds_since_stage_advance':4,'last_round':copy.deepcopy(old)}
        b={'invoice_id':'prod-mrf-v1-20261018b','measured_native_process_seconds':731.09}
        c={'invoice_id':'prod-mrf-v1-20261018c','measured_native_process_seconds':900.0}
        self.assertTrue(book(account,b,'b/INVOICE.json'))
        self.assertTrue(book(account,c,'c/INVOICE.json'))
        terminal=copy.deepcopy(account)
        for invoice in [b,c,b,c]:self.assertFalse(book(account,invoice,'ignored'))
        self.assertEqual(account,terminal)
        self.assertEqual(account['rounds_since_stage_advance'],6)
        self.assertEqual(account['last_round']['invoice_id'],c['invoice_id'])
        self.assertEqual(account['product_prior_last_rounds'][0]['last_round'],old)
        self.assertEqual(account['product_prior_last_rounds'][1]['last_round']['invoice_id'],b['invoice_id'])
        self.assertAlmostEqual(account['star_hours_logged'],2.5+(731.09+900)/3600,places=7)
    def test_failed_execution_is_still_booked_without_stage_advance(self):
        account={'rounds_since_stage_advance':0,'last_round':{'advanced_stage':True}}
        invoice={'invoice_id':'failure','measured_native_process_seconds':35.76,'state':'GATE_FAILED'}
        self.assertTrue(book(account,invoice,'failure/INVOICE.json'))
        self.assertEqual(account['rounds_since_stage_advance'],1)
        self.assertIs(account['last_round']['advanced_stage'],False)
        self.assertEqual(len(account['product_operation_invoices']),1)
if __name__=='__main__':unittest.main()
