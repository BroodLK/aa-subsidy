import unittest

from aasubsidy.contracts.filters import (
    normalize_title_patterns,
    should_ignore_contract,
    title_matches_patterns,
    wildcard_pattern_to_regex,
)


class TestContractFilters(unittest.TestCase):
    def test_normalize_title_patterns(self):
        self.assertEqual(
            normalize_title_patterns("INDY-*\n \nHAUL-*"),
            ["INDY-*", "HAUL-*"],
        )

    def test_wildcard_pattern_to_regex(self):
        self.assertEqual(wildcard_pattern_to_regex("INDY-*"), r"^INDY\-.*$")

    def test_title_matches_patterns(self):
        self.assertTrue(title_matches_patterns("INDY-BULK-01", ["INDY-*"]))
        self.assertFalse(title_matches_patterns("PVP-BULK-01", ["INDY-*"]))

    def test_should_ignore_zero_isk_contract(self):
        self.assertTrue(
            should_ignore_contract(
                title="Combat Contract",
                price=0,
                title_patterns=[],
                ignore_zero_isk_contracts=True,
            )
        )

    def test_should_ignore_title_pattern(self):
        self.assertTrue(
            should_ignore_contract(
                title="INDY-Freighter",
                price=1000,
                title_patterns="INDY-*",
                ignore_zero_isk_contracts=False,
            )
        )
        self.assertFalse(
            should_ignore_contract(
                title="Nova - [Sleip] DPS",
                price=1000,
                title_patterns="INDY-*",
                ignore_zero_isk_contracts=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
