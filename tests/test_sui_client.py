import unittest
import requests
import subprocess
from unittest.mock import patch, MagicMock

from sui_client import (
    compute_gas_price,
    extract_trusted_votes,
    get_system_state,
    submit_vote,
    _parse_tx_output,
    RPCError,
    CLIError,
)


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


class TestGetSystemState(unittest.TestCase):
    @patch("sui_client.requests.post")
    def test_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "result": {
                "epoch": "42",
                "activeValidators": [{"suiAddress": "0xabc"}],
            }
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        state = get_system_state("http://localhost:9000")
        self.assertEqual(state["epoch"], "42")
        self.assertEqual(len(state["activeValidators"]), 1)

    @patch("sui_client.requests.post")
    def test_rpc_connection_error(self, mock_post):
        mock_post.side_effect = requests.ConnectionError("refused")
        with self.assertRaises(RPCError):
            get_system_state("http://localhost:9000")

    @patch("sui_client.requests.post")
    def test_rpc_json_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "error": {"code": -32000, "message": "internal error"}
        }
        mock_post.return_value = mock_resp
        with self.assertRaises(RPCError):
            get_system_state("http://localhost:9000")


class TestParseTxOutput(unittest.TestCase):
    def test_parses_digest_and_status(self):
        stdout = (
            "----- Transaction Digest ----\n"
            "9GQTKwy2iEVbmuje12pfHqErydLDRxJUvyLXL7hULmjK\n"
            "\n"
            '----- Transaction Effects ----\n'
            '{\n'
            '  "V2": {\n'
            '    "status": "Success"\n'
            '  }\n'
            '}'
        )
        info = _parse_tx_output(stdout)
        self.assertEqual(info["digest"], "9GQTKwy2iEVbmuje12pfHqErydLDRxJUvyLXL7hULmjK")
        self.assertEqual(info["status"], "Success")

    def test_no_match(self):
        info = _parse_tx_output("some random output")
        self.assertIsNone(info["digest"])
        self.assertIsNone(info["status"])


class TestSubmitVote(unittest.TestCase):
    @patch("sui_client.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="----- Transaction Digest ----\nABC123\n", stderr="")
        result = submit_vote("sui", 750)
        self.assertEqual(result["digest"], "ABC123")
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertIn("750", args)

    @patch("sui_client.subprocess.run")
    def test_cli_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
        with self.assertRaises(CLIError):
            submit_vote("sui", 750)

    @patch("sui_client.subprocess.run")
    def test_cli_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="sui", timeout=60)
        with self.assertRaises(CLIError):
            submit_vote("sui", 750)


if __name__ == "__main__":
    unittest.main()
