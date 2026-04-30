import unittest

from aasubsidy.helpers.contract_import import (
    claim_clearance_completed,
    plan_claim_clearance,
    resolve_corptools_force_refresh,
)


class TestContractImportHelpers(unittest.TestCase):
    def test_corptools_force_refresh_defaults_to_true(self):
        self.assertTrue(resolve_corptools_force_refresh(None))
        self.assertTrue(resolve_corptools_force_refresh(True))
        self.assertFalse(resolve_corptools_force_refresh(False))

    def test_positive_clearance_marks_contract_complete(self):
        self.assertTrue(claim_clearance_completed(1))
        self.assertFalse(claim_clearance_completed(0))
        self.assertFalse(claim_clearance_completed(None))

    def test_zero_quantity_clearance_stays_retryable(self):
        plan = plan_claim_clearance(existing_clearance_quantity=0, claim_quantity=2)

        self.assertEqual(plan["status"], "clear")
        self.assertFalse(plan["delete_claim"])
        self.assertEqual(plan["remaining_claim_quantity"], 1)

    def test_missing_claim_retries_later(self):
        plan = plan_claim_clearance(existing_clearance_quantity=None, claim_quantity=0)

        self.assertEqual(plan["status"], "retry_later")
        self.assertFalse(plan["delete_claim"])
        self.assertEqual(plan["remaining_claim_quantity"], 0)

    def test_existing_completed_clearance_never_decrements_again(self):
        plan = plan_claim_clearance(existing_clearance_quantity=1, claim_quantity=3)

        self.assertEqual(plan["status"], "already_cleared")
        self.assertFalse(plan["delete_claim"])
        self.assertEqual(plan["remaining_claim_quantity"], 3)

    def test_single_claim_is_deleted_on_clear(self):
        plan = plan_claim_clearance(existing_clearance_quantity=None, claim_quantity=1)

        self.assertEqual(plan["status"], "clear")
        self.assertTrue(plan["delete_claim"])
        self.assertEqual(plan["remaining_claim_quantity"], 0)


if __name__ == "__main__":
    unittest.main()
