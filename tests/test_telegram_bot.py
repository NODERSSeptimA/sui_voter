import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from sui_client import RPCError, CLIError
from telegram_bot import TelegramBot, save_config, _read_voted_epoch, _write_voted_epoch


def _make_config(**overrides):
    cfg = {
        "rpc_url": "http://localhost:9000",
        "trusted_validators": ["0xaaa", "0xbbb", "0xccc"],
        "min_quorum": 2,
        "strategy": "median",
        "sui_bin": "sui",
        "poll_interval": 60,
        "telegram": {"bot_token": "fake_token", "chat_id": "123"},
    }
    cfg.update(overrides)
    return cfg


def _make_validator(address, gas_price, name="Validator", vp="100"):
    return {
        "suiAddress": address,
        "name": name,
        "nextEpochGasPrice": str(gas_price),
        "votingPower": vp,
    }


SAMPLE_STATE = {
    "epoch": "100",
    "referenceGasPrice": "500",
    "activeValidators": [
        _make_validator("0xaaa", 505, "Alice", "200"),
        _make_validator("0xbbb", 510, "Bob", "150"),
        _make_validator("0xccc", 505, "Carol", "100"),
        _make_validator("0xddd", 500, "Dave", "300"),
        _make_validator("0xeee", 520, "Eve", "250"),
    ],
}


class BotTestBase(unittest.TestCase):
    """Base class that creates a bot with mocked _send."""

    def setUp(self):
        self.config = _make_config()
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmpdir, "config.yaml")
        self.state_file = os.path.join(self.tmpdir, "voted_epoch")
        self.bot = TelegramBot(self.config, self.config_path, self.state_file)
        self.sent = []
        self.bot._send = lambda cid, text, markup=None: self.sent.append(
            {"chat_id": cid, "text": text, "markup": markup}
        )


class TestAuthorization(BotTestBase):
    def test_authorized_chat(self):
        self.assertTrue(self.bot._authorized("123"))

    def test_unauthorized_chat(self):
        self.assertFalse(self.bot._authorized("999"))

    def test_route_ignores_unauthorized_message(self):
        update = {"message": {"chat": {"id": 999}, "text": "/start"}}
        self.bot._route(update)
        self.assertEqual(len(self.sent), 0)

    def test_route_ignores_unauthorized_callback(self):
        update = {
            "callback_query": {
                "id": "cb1",
                "message": {"chat": {"id": 999}},
                "data": "status",
            }
        }
        self.bot._answer_cb = MagicMock()
        self.bot._route(update)
        self.assertEqual(len(self.sent), 0)


class TestMenu(BotTestBase):
    def test_start_sends_menu(self):
        self.bot._send_menu("123")
        self.assertEqual(len(self.sent), 1)
        self.assertIn("SUI Gas Voter", self.sent[0]["text"])
        self.assertIsNotNone(self.sent[0]["markup"])

    def test_start_clears_user_state(self):
        self.bot._user_state["123"] = {"state": "waiting_vote"}
        self.bot._send_menu("123")
        self.assertNotIn("123", self.bot._user_state)


class TestStatusCommand(BotTestBase):
    @patch("telegram_bot.get_system_state")
    def test_status_shows_epoch_info(self, mock_state):
        mock_state.return_value = SAMPLE_STATE
        self.bot._cmd_status("123")
        self.assertEqual(len(self.sent), 1)
        text = self.sent[0]["text"]
        self.assertIn("Epoch 100", text)
        self.assertIn("Alice", text)
        self.assertIn("Bob", text)
        self.assertIn("505", text)

    @patch("telegram_bot.get_system_state")
    def test_status_shows_network_stats(self, mock_state):
        mock_state.return_value = SAMPLE_STATE
        self.bot._cmd_status("123")
        text = self.sent[0]["text"]
        self.assertIn("Median:", text)
        self.assertIn("Avg:", text)
        self.assertIn("Min:", text)

    @patch("telegram_bot.get_system_state")
    def test_status_rpc_error(self, mock_state):
        mock_state.side_effect = RPCError("connection refused")
        self.bot._cmd_status("123")
        self.assertIn("RPC error", self.sent[0]["text"])

    @patch("telegram_bot.get_system_state")
    def test_status_quorum_not_met(self, mock_state):
        mock_state.return_value = {
            "epoch": "100",
            "referenceGasPrice": "500",
            "activeValidators": [
                _make_validator("0xaaa", 505, "Alice"),
                _make_validator("0xddd", 500, "Dave"),
            ],
        }
        self.bot._cmd_status("123")
        self.assertIn("Quorum not met", self.sent[0]["text"])


class TestRecommended(BotTestBase):
    @patch("telegram_bot.get_system_state")
    def test_recommended_shows_active_voters(self, mock_state):
        mock_state.return_value = SAMPLE_STATE
        self.bot._cmd_show_recommended("123")
        text = self.sent[0]["text"]
        # Validators with price != 500 (ref) should be marked ✅
        self.assertIn("✅", text)
        self.assertIn("Alice", text)  # 505 != 500
        self.assertIn("Eve", text)  # 520 != 500

    @patch("telegram_bot.get_system_state")
    def test_recommended_stores_addresses(self, mock_state):
        mock_state.return_value = SAMPLE_STATE
        self.bot._cmd_show_recommended("123")
        state = self.bot._user_state.get("123", {})
        self.assertEqual(state["state"], "recommended")
        self.assertIsInstance(state["addresses"], list)
        self.assertGreater(len(state["addresses"]), 0)

    @patch("telegram_bot.get_system_state")
    def test_apply_recommended_updates_config(self, mock_state):
        self.bot._user_state["123"] = {
            "state": "recommended",
            "addresses": ["0xeee", "0xddd"],
        }
        self.bot._persist_config = MagicMock()
        self.bot._cmd_apply_recommended("123")
        self.assertEqual(self.config["trusted_validators"], ["0xeee", "0xddd"])
        self.bot._persist_config.assert_called_once()

    def test_apply_recommended_no_state(self):
        self.bot._cmd_apply_recommended("123")
        self.assertIn("No recommendations", self.sent[0]["text"])


class TestEnterAddresses(BotTestBase):
    def test_enter_sets_waiting_state(self):
        self.bot._cmd_enter_addresses("123")
        self.assertEqual(self.bot._user_state["123"]["state"], "waiting_addresses")

    def test_valid_addresses(self):
        self.bot._user_state["123"] = {"state": "waiting_addresses"}
        self.bot._persist_config = MagicMock()
        self.bot._on_addresses_input("123", "0xaaa\n0xbbb")
        self.assertEqual(self.config["trusted_validators"], ["0xaaa", "0xbbb"])
        self.bot._persist_config.assert_called_once()

    def test_comma_separated(self):
        self.bot._user_state["123"] = {"state": "waiting_addresses"}
        self.bot._persist_config = MagicMock()
        self.bot._on_addresses_input("123", "0xaaa, 0xbbb, 0xccc")
        self.assertEqual(self.config["trusted_validators"], ["0xaaa", "0xbbb", "0xccc"])

    def test_invalid_addresses(self):
        self.bot._user_state["123"] = {"state": "waiting_addresses"}
        self.bot._on_addresses_input("123", "not_valid\nalso_bad")
        self.assertIn("No valid", self.sent[0]["text"])

    def test_quorum_auto_adjusted(self):
        self.config["min_quorum"] = 3
        self.bot._user_state["123"] = {"state": "waiting_addresses"}
        self.bot._persist_config = MagicMock()
        self.bot._on_addresses_input("123", "0xaaa, 0xbbb")
        self.assertEqual(self.config["min_quorum"], 2)


class TestChangeQuorum(BotTestBase):
    def test_change_quorum_sets_state(self):
        self.bot._cmd_change_quorum("123")
        self.assertEqual(self.bot._user_state["123"]["state"], "waiting_quorum")

    def test_valid_quorum(self):
        self.bot._user_state["123"] = {"state": "waiting_quorum"}
        self.bot._persist_config = MagicMock()
        self.bot._on_quorum_value("123", "1")
        self.assertEqual(self.config["min_quorum"], 1)
        self.bot._persist_config.assert_called_once()

    def test_quorum_too_high(self):
        self.bot._user_state["123"] = {"state": "waiting_quorum"}
        self.bot._on_quorum_value("123", "99")
        self.assertIn("Must be", self.sent[0]["text"])

    def test_quorum_zero(self):
        self.bot._user_state["123"] = {"state": "waiting_quorum"}
        self.bot._on_quorum_value("123", "0")
        self.assertIn("Must be", self.sent[0]["text"])

    def test_quorum_not_a_number(self):
        self.bot._user_state["123"] = {"state": "waiting_quorum"}
        self.bot._on_quorum_value("123", "abc")
        self.assertIn("number", self.sent[0]["text"])


class TestManualVote(BotTestBase):
    @patch("telegram_bot.get_system_state")
    def test_vote_start_shows_hint(self, mock_state):
        mock_state.return_value = SAMPLE_STATE
        self.bot._cmd_vote_start("123")
        text = self.sent[0]["text"]
        self.assertIn("Manual Vote", text)
        self.assertIn("median", text.lower())
        self.assertEqual(self.bot._user_state["123"]["state"], "waiting_vote")

    def test_vote_amount_valid(self):
        self.bot._user_state["123"] = {"state": "waiting_vote"}
        self.bot._on_vote_amount("123", "750")
        text = self.sent[0]["text"]
        self.assertIn("750 MIST", text)
        self.assertIn("Confirm", str(self.sent[0]["markup"]))

    def test_vote_amount_invalid(self):
        self.bot._user_state["123"] = {"state": "waiting_vote"}
        self.bot._on_vote_amount("123", "abc")
        self.assertIn("number", self.sent[0]["text"])

    def test_vote_amount_negative(self):
        self.bot._user_state["123"] = {"state": "waiting_vote"}
        self.bot._on_vote_amount("123", "-5")
        self.assertIn("positive", self.sent[0]["text"])

    @patch("telegram_bot.get_system_state")
    @patch("telegram_bot.submit_vote")
    def test_vote_execute_success(self, mock_submit, mock_state):
        mock_submit.return_value = {"digest": "ABC123", "status": "Success"}
        mock_state.return_value = SAMPLE_STATE
        self.bot._cmd_vote_execute("123", 750)
        # First message is "Voting...", second is result
        self.assertEqual(len(self.sent), 2)
        self.assertIn("Voted 750 MIST", self.sent[1]["text"])
        self.assertIn("ABC123", self.sent[1]["text"])

    @patch("telegram_bot.submit_vote")
    def test_vote_execute_failure(self, mock_submit):
        mock_submit.side_effect = CLIError("binary not found")
        self.bot._cmd_vote_execute("123", 750)
        self.assertEqual(len(self.sent), 2)
        self.assertIn("Failed", self.sent[1]["text"])

    @patch("telegram_bot.get_system_state")
    @patch("telegram_bot.submit_vote")
    def test_vote_execute_writes_state_file(self, mock_submit, mock_state):
        mock_submit.return_value = {"digest": "X", "status": "Success"}
        mock_state.return_value = SAMPLE_STATE
        self.bot._cmd_vote_execute("123", 500)
        epoch = _read_voted_epoch(self.state_file)
        self.assertEqual(epoch, 100)


class TestSaveConfig(unittest.TestCase):
    def test_save_and_reload(self):
        import yaml
        config = _make_config()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            path = f.name
        try:
            save_config(path, config)
            with open(path) as f:
                loaded = yaml.safe_load(f)
            self.assertEqual(loaded["trusted_validators"], config["trusted_validators"])
            self.assertEqual(loaded["min_quorum"], config["min_quorum"])
            self.assertEqual(loaded["telegram"]["bot_token"], "fake_token")
        finally:
            os.unlink(path)


class TestStateFileHelpers(unittest.TestCase):
    def test_write_and_read(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
        try:
            _write_voted_epoch(path, 42)
            self.assertEqual(_read_voted_epoch(path), 42)
        finally:
            os.unlink(path)

    def test_read_nonexistent(self):
        self.assertIsNone(_read_voted_epoch("/tmp/nonexistent_file_xyz"))


class TestCallbackRouting(BotTestBase):
    def test_cancel_returns_to_menu(self):
        self.bot._user_state["123"] = {"state": "waiting_vote"}
        self.bot._on_callback("123", "cancel")
        self.assertNotIn("123", self.bot._user_state)
        self.assertIn("SUI Gas Voter", self.sent[0]["text"])

    @patch("telegram_bot.get_system_state")
    def test_status_callback(self, mock_state):
        mock_state.return_value = SAMPLE_STATE
        self.bot._on_callback("123", "status")
        self.assertIn("Epoch 100", self.sent[0]["text"])

    def test_unknown_text_shows_menu(self):
        self.bot._on_text("123", "random text")
        self.assertIn("SUI Gas Voter", self.sent[0]["text"])


if __name__ == "__main__":
    unittest.main()
