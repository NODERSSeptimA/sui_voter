import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from voter import read_voted_epoch, write_voted_epoch, do_vote_cycle


class TestStateFile(unittest.TestCase):
    def test_read_nonexistent(self):
        self.assertIsNone(read_voted_epoch("/nonexistent/voted_epoch"))

    def test_write_and_read(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            path = f.name
        try:
            write_voted_epoch(path, 42)
            self.assertEqual(read_voted_epoch(path), 42)
        finally:
            os.unlink(path)

    def test_read_corrupted(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("not_a_number")
            path = f.name
        try:
            self.assertIsNone(read_voted_epoch(path))
        finally:
            os.unlink(path)


class TestDoVoteCycle(unittest.TestCase):
    def _make_config(self):
        return {
            "rpc_url": "http://localhost:9000",
            "trusted_validators": ["0xaaa", "0xbbb", "0xccc"],
            "min_quorum": 2,
            "strategy": "median",
            "sui_bin": "sui",
            "telegram": {"bot_token": "tok", "chat_id": "chat"},
        }

    @patch("voter.submit_vote")
    @patch("voter.get_system_state")
    def test_successful_vote(self, mock_rpc, mock_submit):
        mock_rpc.return_value = {
            "epoch": "10",
            "activeValidators": [
                {"suiAddress": "0xaaa", "nextEpochGasPrice": "100"},
                {"suiAddress": "0xbbb", "nextEpochGasPrice": "200"},
                {"suiAddress": "0xccc", "nextEpochGasPrice": "300"},
            ],
        }
        mock_submit.return_value = {"digest": "ABC123", "status": "Success"}

        result = do_vote_cycle(self._make_config(), current_epoch=9, voted_epoch=None)

        self.assertEqual(result["new_epoch"], 10)
        self.assertTrue(result["voted"])
        self.assertEqual(result["gas_price"], 200)
        self.assertIn("ABC123", result["vote_msg"])
        self.assertIn("suiscan.xyz", result["vote_msg"])
        mock_submit.assert_called_once_with("sui", 200)

    @patch("voter.submit_vote")
    @patch("voter.get_system_state")
    def test_same_epoch_skips(self, mock_rpc, mock_submit):
        mock_rpc.return_value = {
            "epoch": "10",
            "activeValidators": [],
        }

        result = do_vote_cycle(self._make_config(), current_epoch=10, voted_epoch=None)

        self.assertEqual(result["new_epoch"], 10)
        self.assertFalse(result["voted"])
        mock_submit.assert_not_called()

    @patch("voter.submit_vote")
    @patch("voter.get_system_state")
    def test_initial_epoch_none_triggers_vote(self, mock_rpc, mock_submit):
        mock_rpc.return_value = {
            "epoch": "10",
            "activeValidators": [
                {"suiAddress": "0xaaa", "nextEpochGasPrice": "100"},
                {"suiAddress": "0xbbb", "nextEpochGasPrice": "200"},
            ],
        }
        mock_submit.return_value = {"digest": "XYZ", "status": "Success"}

        result = do_vote_cycle(self._make_config(), current_epoch=None, voted_epoch=None)

        self.assertEqual(result["new_epoch"], 10)
        self.assertTrue(result["voted"])

    @patch("voter.submit_vote")
    @patch("voter.get_system_state")
    def test_already_voted_skips(self, mock_rpc, mock_submit):
        mock_rpc.return_value = {
            "epoch": "10",
            "activeValidators": [
                {"suiAddress": "0xaaa", "nextEpochGasPrice": "100"},
                {"suiAddress": "0xbbb", "nextEpochGasPrice": "200"},
            ],
        }

        result = do_vote_cycle(self._make_config(), current_epoch=9, voted_epoch=10)

        self.assertEqual(result["new_epoch"], 10)
        self.assertFalse(result["voted"])
        mock_submit.assert_not_called()

    @patch("voter.submit_vote")
    @patch("voter.get_system_state")
    def test_quorum_not_met(self, mock_rpc, mock_submit):
        mock_rpc.return_value = {
            "epoch": "10",
            "activeValidators": [
                {"suiAddress": "0xaaa", "nextEpochGasPrice": "100"},
            ],
        }

        result = do_vote_cycle(self._make_config(), current_epoch=9, voted_epoch=None)

        self.assertEqual(result["new_epoch"], 10)
        self.assertFalse(result["voted"])
        self.assertTrue(result.get("quorum_failed"))
        self.assertIn("quorum_msg", result)
        mock_submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
