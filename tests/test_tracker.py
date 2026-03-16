import unittest
from unittest.mock import patch, MagicMock

from tracker import build_report


def _make_validator(address, gas_price, name="Validator"):
    return {
        "suiAddress": address,
        "name": name,
        "nextEpochGasPrice": str(gas_price),
    }


SAMPLE_CONFIG = {
    "rpc_url": "http://localhost:9000",
    "trusted_validators": ["0xaaa", "0xbbb", "0xccc"],
    "min_quorum": 2,
    "strategy": "median",
    "sui_bin": "sui",
    "poll_interval": 60,
    "telegram": {"bot_token": "fake", "chat_id": "fake"},
}


class TestBuildReport(unittest.TestCase):
    def _make_state(self, validators, epoch="100", ref_price="500"):
        return {
            "epoch": epoch,
            "activeValidators": validators,
            "referenceGasPrice": ref_price,
        }

    def test_basic_report_content(self):
        validators = [
            _make_validator("0xaaa", 500, "Alice"),
            _make_validator("0xbbb", 510, "Bob"),
            _make_validator("0xccc", 505, "Carol"),
            _make_validator("0xddd", 490, "Dave"),
            _make_validator("0xeee", 520, "Eve"),
        ]
        state = self._make_state(validators)
        report = build_report(state, SAMPLE_CONFIG)

        self.assertIn("Epoch 100", report)
        self.assertIn("5 validators", report)
        self.assertIn("Alice", report)
        self.assertIn("Bob", report)
        self.assertIn("Carol", report)
        # Dave and Eve are not trusted, should not be in trusted section
        # but should contribute to network stats

    def test_network_stats(self):
        validators = [
            _make_validator("0xaaa", 500, "A"),
            _make_validator("0xbbb", 600, "B"),
            _make_validator("0xccc", 700, "C"),
        ]
        state = self._make_state(validators)
        report = build_report(state, SAMPLE_CONFIG)

        self.assertIn("Median: 600 MIST", report)
        self.assertIn("Min: 500 MIST", report)
        self.assertIn("Max: 700 MIST", report)

    def test_trusted_vote_calculation(self):
        validators = [
            _make_validator("0xaaa", 500, "A"),
            _make_validator("0xbbb", 600, "B"),
            _make_validator("0xccc", 700, "C"),
        ]
        state = self._make_state(validators)
        report = build_report(state, SAMPLE_CONFIG)

        # median of [500, 600, 700] = 600
        self.assertIn("Would vote: 600 MIST", report)

    def test_quorum_not_met(self):
        validators = [
            _make_validator("0xaaa", 500, "A"),
            # 0xbbb and 0xccc not in active set
            _make_validator("0xddd", 600, "D"),
        ]
        state = self._make_state(validators)
        report = build_report(state, SAMPLE_CONFIG)

        self.assertIn("would NOT vote", report)
        self.assertIn("1/2 required", report)

    def test_missing_trusted_shown(self):
        validators = [
            _make_validator("0xaaa", 500, "A"),
            _make_validator("0xddd", 600, "D"),
        ]
        state = self._make_state(validators)
        report = build_report(state, SAMPLE_CONFIG)

        self.assertIn("NOT IN ACTIVE SET", report)

    def test_distribution_buckets(self):
        validators = [
            _make_validator("0xaaa", 500, "A"),
            _make_validator("0xbbb", 500, "B"),
            _make_validator("0xccc", 600, "C"),
        ]
        state = self._make_state(validators)
        report = build_report(state, SAMPLE_CONFIG)

        self.assertIn("Distribution:", report)
        self.assertIn("500 MIST:", report)
        self.assertIn("600 MIST:", report)

    def test_reference_gas_price_shown(self):
        validators = [_make_validator("0xaaa", 500, "A")]
        state = self._make_state(validators, ref_price="999")
        config = {**SAMPLE_CONFIG, "min_quorum": 1}
        report = build_report(state, config)

        self.assertIn("reference gas price: 999 MIST", report)

    def test_vote_equals_network_median(self):
        validators = [
            _make_validator("0xaaa", 500, "A"),
            _make_validator("0xbbb", 500, "B"),
            _make_validator("0xccc", 500, "C"),
        ]
        state = self._make_state(validators)
        report = build_report(state, SAMPLE_CONFIG)

        self.assertIn("= network median", report)

    def test_vote_above_network_median(self):
        # Trusted: 700, 800, 900 → median 800
        # Network includes a low-price validator
        validators = [
            _make_validator("0xaaa", 700, "A"),
            _make_validator("0xbbb", 800, "B"),
            _make_validator("0xccc", 900, "C"),
            _make_validator("0xddd", 100, "D"),  # pulls network median down
        ]
        state = self._make_state(validators)
        report = build_report(state, SAMPLE_CONFIG)

        # Network: [100, 700, 800, 900] → median = 700 (lower-middle)
        # Trusted: [700, 800, 900] → median = 800
        self.assertIn("above", report)

    def test_no_reference_gas_price(self):
        validators = [_make_validator("0xaaa", 500, "A")]
        state = {"epoch": "1", "activeValidators": validators}
        config = {**SAMPLE_CONFIG, "min_quorum": 1}
        report = build_report(state, config)

        self.assertNotIn("reference gas price", report)


if __name__ == "__main__":
    unittest.main()
