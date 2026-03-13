import unittest
import requests
import subprocess
from unittest.mock import patch, MagicMock

from sui_client import compute_gas_price, extract_trusted_votes


class TestComputeGasPrice(unittest.TestCase):
    def test_median_odd(self):
        self.assertEqual(compute_gas_price([100, 200, 300], "median"), 200)

    def test_median_even_takes_lower(self):
        self.assertEqual(compute_gas_price([100, 200, 300, 400], "median"), 200)

    def test_median_single(self):
        self.assertEqual(compute_gas_price([500], "median"), 500)

    def test_median_unsorted_input(self):
        self.assertEqual(compute_gas_price([300, 100, 200], "median"), 200)

    def test_average_exact(self):
        self.assertEqual(compute_gas_price([100, 200, 300], "average"), 200)

    def test_average_rounds_down(self):
        self.assertEqual(compute_gas_price([100, 200], "average"), 150)

    def test_average_rounds_down_non_integer(self):
        self.assertEqual(compute_gas_price([100, 201], "average"), 150)

    def test_empty_votes_raises(self):
        with self.assertRaises(ValueError):
            compute_gas_price([], "median")

    def test_unknown_strategy_raises(self):
        with self.assertRaises(ValueError):
            compute_gas_price([100, 200], "mode")


class TestExtractTrustedVotes(unittest.TestCase):
    def _make_validator(self, address, gas_price):
        return {
            "suiAddress": address,
            "nextEpochGasPrice": str(gas_price),
        }

    def test_filters_trusted(self):
        validators = [
            self._make_validator("0xaaa", 100),
            self._make_validator("0xbbb", 200),
            self._make_validator("0xccc", 300),
        ]
        trusted = ["0xaaa", "0xccc"]
        votes = extract_trusted_votes(validators, trusted)
        self.assertEqual(sorted(votes), [100, 300])

    def test_no_match(self):
        validators = [self._make_validator("0xaaa", 100)]
        votes = extract_trusted_votes(validators, ["0xzzz"])
        self.assertEqual(votes, [])

    def test_empty_validators(self):
        votes = extract_trusted_votes([], ["0xaaa"])
        self.assertEqual(votes, [])


if __name__ == "__main__":
    unittest.main()
