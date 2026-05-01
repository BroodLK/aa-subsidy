import unittest
from decimal import Decimal

from aasubsidy.contracts.matching import (
    ContractItemData,
    FittingDefinition,
    ItemRuleData,
    MatchProfileData,
    QuantityToleranceData,
    SubstitutionRuleData,
    TypeInfo,
    _select_result,
    evaluate_contract_against_definition,
)


def _profile(*, fitting_id=1, **overrides):
    return MatchProfileData(fitting_id=fitting_id, **overrides)


def _fit_definition(*, fitting_id=1, name="Test Fit", ship_type_id=100, rules=None, substitutions=None, tolerances=None, profile=None, type_info=None):
    profile = profile or _profile(fitting_id=fitting_id)
    return FittingDefinition(
        fitting_id=fitting_id,
        fitting_name=name,
        ship_type_id=ship_type_id,
        ship_type_name="Hull",
        profile=profile,
        item_rules=rules or [],
        substitutions=substitutions or [],
        quantity_tolerances=tolerances or {},
        type_info=type_info or {},
    )


class TestDoctrineMatching(unittest.TestCase):
    def test_exact_fit_matches(self):
        fit = _fit_definition(
            rules=[
                ItemRuleData(100, "Hull", expected_quantity=1, category="hull", is_hull=True, sort_order=-1000),
                ItemRuleData(200, "Module", expected_quantity=1),
            ],
            type_info={
                100: TypeInfo(100, "Hull"),
                200: TypeInfo(200, "Module"),
            },
        )
        contract = {
            100: ContractItemData(100, "Hull", included_qty=1),
            200: ContractItemData(200, "Module", included_qty=1),
        }

        result = evaluate_contract_against_definition(contract, fit)

        self.assertEqual(result.score, Decimal("100.00"))
        self.assertTrue(result.exact_match)
        self.assertEqual(result.hard_failures, [])
        self.assertEqual(result.source_hint, "auto")

    def test_wrong_hull_never_matches(self):
        fit = _fit_definition(
            rules=[ItemRuleData(100, "Hull", expected_quantity=1, category="hull", is_hull=True, sort_order=-1000)],
            type_info={100: TypeInfo(100, "Hull")},
        )
        contract = {101: ContractItemData(101, "Other Hull", included_qty=1)}

        result = evaluate_contract_against_definition(contract, fit)

        self.assertTrue(any(issue["code"] in {"missing_required", "wrong_hull"} for issue in result.hard_failures))
        self.assertFalse(result.viable)

    def test_missing_required_module_blocks_match(self):
        fit = _fit_definition(
            rules=[
                ItemRuleData(100, "Hull", expected_quantity=1, category="hull", is_hull=True, sort_order=-1000),
                ItemRuleData(200, "Module", expected_quantity=1),
            ],
            type_info={100: TypeInfo(100, "Hull"), 200: TypeInfo(200, "Module")},
        )
        contract = {100: ContractItemData(100, "Hull", included_qty=1)}

        result = evaluate_contract_against_definition(contract, fit)

        self.assertTrue(any(issue["code"] == "missing_required" for issue in result.hard_failures))
        self.assertFalse(result.viable)

    def test_extra_cargo_allowed_when_profile_says_so(self):
        fit = _fit_definition(
            profile=_profile(fitting_id=1, allow_extra_items=True),
            rules=[
                ItemRuleData(100, "Hull", expected_quantity=1, category="hull", is_hull=True, sort_order=-1000),
                ItemRuleData(200, "Module", expected_quantity=1),
            ],
            type_info={
                100: TypeInfo(100, "Hull"),
                200: TypeInfo(200, "Module"),
                300: TypeInfo(300, "Ammo"),
            },
        )
        contract = {
            100: ContractItemData(100, "Hull", included_qty=1),
            200: ContractItemData(200, "Module", included_qty=1),
            300: ContractItemData(300, "Ammo", included_qty=200),
        }

        result = evaluate_contract_against_definition(contract, fit)

        self.assertEqual(result.hard_failures, [])
        self.assertTrue(any(issue["code"] == "unexpected_extra_item" for issue in result.warnings))

    def test_ammo_quantity_within_tolerance_matches_with_warning(self):
        fit = _fit_definition(
            rules=[
                ItemRuleData(100, "Hull", expected_quantity=1, category="hull", is_hull=True, sort_order=-1000),
                ItemRuleData(400, "Ammo", expected_quantity=3200, category="ammo"),
            ],
            tolerances={400: [QuantityToleranceData(400, mode="extra_only", lower_bound=0, upper_bound=1000, penalty_points=Decimal("2.00"))]},
            type_info={100: TypeInfo(100, "Hull"), 400: TypeInfo(400, "Ammo", market_group_id=11)},
        )
        contract = {
            100: ContractItemData(100, "Hull", included_qty=1),
            400: ContractItemData(400, "Ammo", included_qty=4000),
        }

        result = evaluate_contract_against_definition(contract, fit)

        self.assertEqual(result.hard_failures, [])
        self.assertTrue(any(issue["code"] == "consumable_quantity_tolerance" for issue in result.warnings))
        self.assertEqual(result.score, Decimal("75.00"))

    def test_specific_substitute_matches_with_penalty(self):
        fit = _fit_definition(
            rules=[
                ItemRuleData(100, "Hull", expected_quantity=1, category="hull", is_hull=True, sort_order=-1000),
                ItemRuleData(500, "T2 Module", expected_quantity=1),
            ],
            substitutions=[SubstitutionRuleData(expected_type_id=500, allowed_type_id=501, rule_type="specific", penalty_points=Decimal("4.00"))],
            type_info={
                100: TypeInfo(100, "Hull"),
                500: TypeInfo(500, "T2 Module", group_id=10),
                501: TypeInfo(501, "Compact Module", group_id=10),
            },
        )
        contract = {
            100: ContractItemData(100, "Hull", included_qty=1),
            501: ContractItemData(501, "Compact Module", included_qty=1),
        }

        result = evaluate_contract_against_definition(contract, fit)

        self.assertEqual(result.hard_failures, [])
        self.assertTrue(any(issue["code"] == "substitution" for issue in result.warnings))
        self.assertEqual(result.score, Decimal("50.00"))
        self.assertEqual(result.source_hint, "learned_rule")

    def test_missing_and_extra_with_matching_metadata_offer_substitution_flow(self):
        fit = _fit_definition(
            rules=[
                ItemRuleData(100, "Hull", expected_quantity=1, category="hull", is_hull=True, sort_order=-1000),
                ItemRuleData(500, "Domination EM Armor Hardener", expected_quantity=1),
            ],
            type_info={
                100: TypeInfo(100, "Hull"),
                500: TypeInfo(500, "Domination EM Armor Hardener", group_id=20),
            },
        )
        contract = {
            100: ContractItemData(100, "Hull", included_qty=1),
            501: ContractItemData(501, "True Sansha EM Armor Hardener", included_qty=1, group_id=20),
        }

        result = evaluate_contract_against_definition(contract, fit)
        item_rows = result.evidence["item_rows"]
        missing_row = next(row for row in item_rows if row["expected_type_id"] == 500)
        extra_row = next(row for row in item_rows if row["actual_type_id"] == 501)

        self.assertTrue(
            any(
                isinstance(action, dict)
                and action.get("name") == "specific_substitute"
                and action.get("expected_type_id") == 500
                and action.get("actual_type_id") == 501
                for action in missing_row["actions"]
            )
        )
        self.assertTrue(
            any(
                isinstance(action, dict)
                and action.get("name") == "specific_substitute"
                and action.get("expected_type_id") == 500
                and action.get("actual_type_id") == 501
                for action in extra_row["actions"]
            )
        )

    def test_substitution_flow_avoids_cross_pairing_same_quantity_items(self):
        fit = _fit_definition(
            rules=[
                ItemRuleData(100, "Hull", expected_quantity=1, category="hull", is_hull=True, sort_order=-1000),
                ItemRuleData(500, "Domination EM Armor Hardener", expected_quantity=1),
                ItemRuleData(600, "Domination Thermal Armor Hardener", expected_quantity=1),
            ],
            type_info={
                100: TypeInfo(100, "Hull"),
                500: TypeInfo(500, "Domination EM Armor Hardener", group_id=20),
                600: TypeInfo(600, "Domination Thermal Armor Hardener", group_id=20),
            },
        )
        contract = {
            100: ContractItemData(100, "Hull", included_qty=1),
            501: ContractItemData(501, "True Sansha EM Armor Hardener", included_qty=1, group_id=20),
            601: ContractItemData(601, "True Sansha Thermal Armor Hardener", included_qty=1, group_id=20),
        }

        result = evaluate_contract_against_definition(contract, fit)
        item_rows = result.evidence["item_rows"]
        missing_em = next(row for row in item_rows if row["expected_type_id"] == 500)
        missing_thermal = next(row for row in item_rows if row["expected_type_id"] == 600)

        em_targets = sorted(
            action["actual_type_id"]
            for action in missing_em["actions"]
            if isinstance(action, dict) and action.get("name") == "specific_substitute"
        )
        thermal_targets = sorted(
            action["actual_type_id"]
            for action in missing_thermal["actions"]
            if isinstance(action, dict) and action.get("name") == "specific_substitute"
        )

        self.assertEqual(em_targets, [501])
        self.assertEqual(thermal_targets, [601])

    def test_forced_fit_and_rerun_keep_same_analysis(self):
        fit = _fit_definition(
            rules=[
                ItemRuleData(100, "Hull", expected_quantity=1, category="hull", is_hull=True, sort_order=-1000),
                ItemRuleData(500, "T2 Module", expected_quantity=1),
            ],
            substitutions=[SubstitutionRuleData(expected_type_id=500, allowed_type_id=501, rule_type="specific", penalty_points=Decimal("4.00"))],
            type_info={
                100: TypeInfo(100, "Hull"),
                500: TypeInfo(500, "T2 Module", group_id=10),
                501: TypeInfo(501, "Compact Module", group_id=10),
            },
        )
        contract = {
            100: ContractItemData(100, "Hull", included_qty=1),
            501: ContractItemData(501, "Compact Module", included_qty=1),
        }
        candidate = evaluate_contract_against_definition(contract, fit)

        forced = _select_result(contract_id=1, candidates=[candidate], forced_fit_id=1, manual_decision=None)
        rerun = _select_result(contract_id=1, candidates=[candidate], forced_fit_id=None, manual_decision=None)

        self.assertEqual(forced.matched_fitting_id, 1)
        self.assertIsNone(rerun.matched_fitting_id)
        self.assertEqual(rerun.evidence["selected_fit_name"], "Test Fit")
        self.assertEqual(forced.score, rerun.score)
        self.assertEqual(forced.hard_failures, rerun.hard_failures)
        self.assertEqual(forced.warnings, rerun.warnings)

    def test_forced_fit_fallback_keeps_fit_name(self):
        forced = _select_result(
            contract_id=1,
            candidates=[],
            forced_fit_id=77,
            forced_fit_name="Forced Doctrine",
            manual_decision=None,
        )

        self.assertEqual(forced.matched_fitting_id, 77)
        self.assertEqual(forced.matched_fitting_name, "Forced Doctrine")
        self.assertEqual(forced.match_source, "forced")
        self.assertEqual(forced.match_status, "needs_review")

    def test_rejected_result_keeps_top_candidate_item_rows(self):
        fit = _fit_definition(
            rules=[
                ItemRuleData(100, "Hull", expected_quantity=1, category="hull", is_hull=True, sort_order=-1000),
                ItemRuleData(200, "Required Module", expected_quantity=1),
            ],
            type_info={
                100: TypeInfo(100, "Hull"),
                200: TypeInfo(200, "Required Module"),
            },
        )
        contract = {
            100: ContractItemData(100, "Hull", included_qty=1),
        }

        candidate = evaluate_contract_against_definition(contract, fit)
        result = _select_result(contract_id=42, candidates=[candidate], manual_decision=None)

        self.assertEqual(result.match_status, "no_match")
        self.assertEqual(result.evidence["selected_fit_name"], "Test Fit")
        self.assertTrue(result.evidence.get("item_rows"))
        self.assertTrue(any(row["status"] == "error" for row in result.evidence["item_rows"]))
        self.assertTrue(any(issue["code"] == "missing_required" for issue in result.hard_failures))

    def test_close_match_above_threshold_commits_candidate(self):
        fit = _fit_definition(
            rules=[
                ItemRuleData(100, "Hull", expected_quantity=1, category="hull", is_hull=True, sort_order=-1000),
                ItemRuleData(200, "Module A", expected_quantity=1),
                ItemRuleData(201, "Module B", expected_quantity=1),
                ItemRuleData(202, "Module C", expected_quantity=1),
                ItemRuleData(203, "Missing Module", expected_quantity=1),
            ],
            type_info={
                100: TypeInfo(100, "Hull"),
                200: TypeInfo(200, "Module A"),
                201: TypeInfo(201, "Module B"),
                202: TypeInfo(202, "Module C"),
                203: TypeInfo(203, "Missing Module"),
            },
        )
        contract = {
            100: ContractItemData(100, "Hull", included_qty=1),
            200: ContractItemData(200, "Module A", included_qty=1),
            201: ContractItemData(201, "Module B", included_qty=1),
            202: ContractItemData(202, "Module C", included_qty=1),
        }

        candidate = evaluate_contract_against_definition(contract, fit)
        result = _select_result(contract_id=42, candidates=[candidate], manual_decision=None)

        self.assertEqual(result.score, Decimal("80.00"))
        self.assertEqual(result.match_status, "needs_review")
        self.assertEqual(result.matched_fitting_id, 1)
        self.assertEqual(result.evidence["selected_fit_name"], "Test Fit")

    def test_threshold_passing_ambiguous_match_commits_selected_candidate(self):
        base_rules = [
            ItemRuleData(100, "Hull", expected_quantity=1, category="hull", is_hull=True, sort_order=-1000),
            ItemRuleData(200, "Module", expected_quantity=1),
        ]
        contract = {
            100: ContractItemData(100, "Hull", included_qty=1),
            200: ContractItemData(200, "Module", included_qty=1),
        }
        first = evaluate_contract_against_definition(
            contract,
            _fit_definition(
                fitting_id=1,
                name="First Fit",
                rules=base_rules,
                type_info={100: TypeInfo(100, "Hull"), 200: TypeInfo(200, "Module")},
            ),
        )
        second = evaluate_contract_against_definition(
            contract,
            _fit_definition(
                fitting_id=2,
                name="Second Fit",
                rules=base_rules,
                type_info={100: TypeInfo(100, "Hull"), 200: TypeInfo(200, "Module")},
            ),
        )

        result = _select_result(contract_id=42, candidates=[first, second], manual_decision=None)

        self.assertEqual(result.match_status, "needs_review")
        self.assertEqual(result.matched_fitting_id, 1)
        self.assertTrue(any(issue["code"] == "ambiguous_match" for issue in result.warnings))
        self.assertEqual(result.evidence["selected_fit_name"], "First Fit")


if __name__ == "__main__":
    unittest.main()
