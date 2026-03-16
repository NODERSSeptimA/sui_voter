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

MSG_ID = 42  # Arbitrary message id for edit tests


class BotTestBase(unittest.TestCase):
    """Base class that captures _send and _edit calls."""

    def setUp(self):
        self.config = _make_config()
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmpdir, "config.yaml")
        self.state_file = os.path.join(self.tmpdir, "voted_epoch")
        self.bot = TelegramBot(self.config, self.config_path, self.state_file)
        self.sent = []
        self.edits = []
        self.bot._send = lambda cid, text, markup=None: self.sent.append(
            {"chat_id": cid, "text": text, "markup": markup}
        )
        self.bot._edit = lambda cid, mid, text, markup=None: self.edits.append(
            {"chat_id": cid, "message_id": mid, "text": text, "markup": markup}
        )

    @property
    def last_edit(self):
        return self.edits[-1] if self.edits else None


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
                "message": {"chat": {"id": 999}, "message_id": MSG_ID},
                "data": "status",
            }
        }
        self.bot._answer_cb = MagicMock()
        self.bot._route(update)
        self.assertEqual(len(self.edits), 0)


class TestMenu(BotTestBase):
    def test_start_sends_new_message(self):
        self.bot._send_menu("123")
        self.assertEqual(len(self.sent), 1)
        self.assertIn("SUI Gas Voter", self.sent[0]["text"])

    def test_callback_menu_edits_existing(self):
        self.bot._edit_menu("123", MSG_ID)
        self.assertEqual(len(self.edits), 1)
        self.assertEqual(len(self.sent), 0)
        self.assertIn("SUI Gas Voter", self.last_edit["text"])
        self.assertEqual(self.last_edit["message_id"], MSG_ID)

    def test_menu_clears_user_state(self):
        self.bot._user_state["123"] = {"state": "waiting_vote"}
        self.bot._edit_menu("123", MSG_ID)
        self.assertNotIn("123", self.bot._user_state)


class TestStatusCommand(BotTestBase):
    @patch("telegram_bot.get_system_state")
    def test_status_edits_message(self, mock_state):
        mock_state.return_value = SAMPLE_STATE
        self.bot._cmd_status("123", MSG_ID)
        self.assertEqual(len(self.edits), 1)
        self.assertEqual(len(self.sent), 0)
        text = self.last_edit["text"]
        self.assertIn("Epoch 100", text)
        self.assertIn("Alice", text)
        self.assertIn("505", text)
        self.assertEqual(self.last_edit["message_id"], MSG_ID)

    @patch("telegram_bot.get_system_state")
    def test_status_shows_network_stats(self, mock_state):
        mock_state.return_value = SAMPLE_STATE
        self.bot._cmd_status("123", MSG_ID)
        text = self.last_edit["text"]
        self.assertIn("Median:", text)
        self.assertIn("Avg:", text)
        self.assertIn("Min:", text)

    @patch("telegram_bot.get_system_state")
    def test_status_rpc_error(self, mock_state):
        mock_state.side_effect = RPCError("connection refused")
        self.bot._cmd_status("123", MSG_ID)
        self.assertIn("RPC error", self.last_edit["text"])

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
        self.bot._cmd_status("123", MSG_ID)
        self.assertIn("Quorum not met", self.last_edit["text"])


class TestRecommended(BotTestBase):
    @patch("telegram_bot.get_system_state")
    def test_recommended_edits_message(self, mock_state):
        mock_state.return_value = SAMPLE_STATE
        self.bot._cmd_show_recommended("123", MSG_ID)
        self.assertEqual(len(self.edits), 1)
        self.assertEqual(len(self.sent), 0)
        text = self.last_edit["text"]
        self.assertIn("✅", text)
        self.assertIn("Alice", text)

    @patch("telegram_bot.get_system_state")
    def test_recommended_stores_addresses(self, mock_state):
        mock_state.return_value = SAMPLE_STATE
        self.bot._cmd_show_recommended("123", MSG_ID)
        state = self.bot._user_state.get("123", {})
        self.assertEqual(state["state"], "recommended")
        self.assertGreater(len(state["addresses"]), 0)

    @patch("telegram_bot.get_system_state")
    def test_apply_recommended_updates_config(self, mock_state):
        self.bot._user_state["123"] = {
            "state": "recommended",
            "addresses": ["0xeee", "0xddd"],
        }
        self.bot._persist_config = MagicMock()
        self.bot._cmd_apply_recommended("123", MSG_ID)
        self.assertEqual(self.config["trusted_validators"], ["0xeee", "0xddd"])
        self.bot._persist_config.assert_called_once()

    def test_apply_recommended_no_state(self):
        self.bot._cmd_apply_recommended("123", MSG_ID)
        self.assertIn("No recommendations", self.last_edit["text"])


class TestEnterAddresses(BotTestBase):
    def test_enter_stores_msg_id(self):
        self.bot._cmd_enter_addresses("123", MSG_ID)
        state = self.bot._user_state["123"]
        self.assertEqual(state["state"], "waiting_addresses")
        self.assertEqual(state["msg_id"], MSG_ID)

    def test_valid_addresses_edits(self):
        self.bot._user_state["123"] = {"state": "waiting_addresses", "msg_id": MSG_ID}
        self.bot._persist_config = MagicMock()
        self.bot._on_addresses_input("123", MSG_ID, "0xaaa\n0xbbb")
        self.assertEqual(self.config["trusted_validators"], ["0xaaa", "0xbbb"])
        self.assertEqual(len(self.edits), 1)
        self.assertEqual(len(self.sent), 0)

    def test_comma_separated(self):
        self.bot._user_state["123"] = {"state": "waiting_addresses", "msg_id": MSG_ID}
        self.bot._persist_config = MagicMock()
        self.bot._on_addresses_input("123", MSG_ID, "0xaaa, 0xbbb, 0xccc")
        self.assertEqual(self.config["trusted_validators"], ["0xaaa", "0xbbb", "0xccc"])

    def test_invalid_addresses(self):
        self.bot._user_state["123"] = {"state": "waiting_addresses", "msg_id": MSG_ID}
        self.bot._on_addresses_input("123", MSG_ID, "not_valid")
        self.assertIn("No valid", self.last_edit["text"])

    def test_quorum_auto_adjusted(self):
        self.config["min_quorum"] = 3
        self.bot._user_state["123"] = {"state": "waiting_addresses", "msg_id": MSG_ID}
        self.bot._persist_config = MagicMock()
        self.bot._on_addresses_input("123", MSG_ID, "0xaaa, 0xbbb")
        self.assertEqual(self.config["min_quorum"], 2)

    def test_no_msg_id_sends_new(self):
        """When msg_id is missing, fall back to sendMessage."""
        self.bot._user_state["123"] = {"state": "waiting_addresses", "msg_id": None}
        self.bot._persist_config = MagicMock()
        self.bot._on_addresses_input("123", None, "0xaaa")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.edits), 0)


class TestChangeQuorum(BotTestBase):
    def test_shows_buttons(self):
        self.bot._cmd_change_quorum("123", MSG_ID)
        self.assertEqual(len(self.edits), 1)
        markup = self.last_edit["markup"]
        # Should have number buttons + back button
        self.assertIn("inline_keyboard", markup)
        all_data = []
        for row in markup["inline_keyboard"]:
            for btn in row:
                all_data.append(btn.get("callback_data", ""))
        self.assertIn("quorum:1", all_data)
        self.assertIn("quorum:2", all_data)
        self.assertIn("quorum:3", all_data)

    def test_current_quorum_marked(self):
        self.config["min_quorum"] = 2
        self.bot._cmd_change_quorum("123", MSG_ID)
        markup = self.last_edit["markup"]
        labels = []
        for row in markup["inline_keyboard"]:
            for btn in row:
                if btn.get("callback_data", "").startswith("quorum:"):
                    labels.append(btn["text"])
        # Current quorum (2) should be marked with dot
        self.assertEqual(labels[0], "1")  # Not current
        self.assertEqual(labels[1], "· 2")  # Current

    def test_apply_quorum(self):
        self.bot._persist_config = MagicMock()
        self.bot._cmd_apply_quorum("123", MSG_ID, 1)
        self.assertEqual(self.config["min_quorum"], 1)
        self.bot._persist_config.assert_called_once()
        self.assertIn("Quorum set to 1", self.last_edit["text"])

    def test_apply_quorum_invalid(self):
        self.bot._cmd_apply_quorum("123", MSG_ID, 99)
        self.assertIn("Must be", self.last_edit["text"])


class TestManualVote(BotTestBase):
    @patch("telegram_bot.get_system_state")
    def test_vote_start_edits_and_stores_msg_id(self, mock_state):
        mock_state.return_value = SAMPLE_STATE
        self.bot._cmd_vote_start("123", MSG_ID)
        self.assertEqual(len(self.edits), 1)
        self.assertIn("Manual Vote", self.last_edit["text"])
        self.assertEqual(self.bot._user_state["123"]["msg_id"], MSG_ID)

    def test_vote_amount_valid_edits(self):
        self.bot._user_state["123"] = {"state": "waiting_vote", "msg_id": MSG_ID}
        self.bot._on_vote_amount("123", MSG_ID, "750")
        self.assertEqual(len(self.edits), 1)
        self.assertIn("750 MIST", self.last_edit["text"])
        self.assertIn("Confirm", str(self.last_edit["markup"]))

    def test_vote_amount_invalid(self):
        self.bot._user_state["123"] = {"state": "waiting_vote", "msg_id": MSG_ID}
        self.bot._on_vote_amount("123", MSG_ID, "abc")
        self.assertIn("number", self.last_edit["text"])

    def test_vote_amount_negative(self):
        self.bot._user_state["123"] = {"state": "waiting_vote", "msg_id": MSG_ID}
        self.bot._on_vote_amount("123", MSG_ID, "-5")
        self.assertIn("positive", self.last_edit["text"])

    @patch("telegram_bot.get_system_state")
    @patch("telegram_bot.submit_vote")
    def test_vote_execute_success(self, mock_submit, mock_state):
        mock_submit.return_value = {"digest": "ABC123", "status": "Success"}
        mock_state.return_value = SAMPLE_STATE
        self.bot._cmd_vote_execute("123", MSG_ID, 750)
        # First edit: "Voting...", second edit: result
        self.assertEqual(len(self.edits), 2)
        self.assertIn("Voted 750 MIST", self.edits[1]["text"])
        self.assertIn("ABC123", self.edits[1]["text"])

    @patch("telegram_bot.submit_vote")
    def test_vote_execute_failure(self, mock_submit):
        mock_submit.side_effect = CLIError("binary not found")
        self.bot._cmd_vote_execute("123", MSG_ID, 750)
        self.assertEqual(len(self.edits), 2)
        self.assertIn("Failed", self.edits[1]["text"])

    @patch("telegram_bot.get_system_state")
    @patch("telegram_bot.submit_vote")
    def test_vote_execute_writes_state_file(self, mock_submit, mock_state):
        mock_submit.return_value = {"digest": "X", "status": "Success"}
        mock_state.return_value = SAMPLE_STATE
        self.bot._cmd_vote_execute("123", MSG_ID, 500)
        epoch = _read_voted_epoch(self.state_file)
        self.assertEqual(epoch, 100)

    def test_vote_no_msg_id_sends_new(self):
        self.bot._user_state["123"] = {"state": "waiting_vote", "msg_id": None}
        self.bot._on_vote_amount("123", None, "500")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.edits), 0)


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
    def test_cancel_edits_to_menu(self):
        self.bot._user_state["123"] = {"state": "waiting_vote", "msg_id": MSG_ID}
        self.bot._on_callback("123", MSG_ID, "cancel")
        self.assertNotIn("123", self.bot._user_state)
        self.assertEqual(len(self.edits), 1)
        self.assertIn("SUI Gas Voter", self.last_edit["text"])

    @patch("telegram_bot.get_system_state")
    def test_status_callback(self, mock_state):
        mock_state.return_value = SAMPLE_STATE
        self.bot._on_callback("123", MSG_ID, "status")
        self.assertEqual(len(self.edits), 1)
        self.assertIn("Epoch 100", self.last_edit["text"])

    def test_quorum_callback(self):
        self.bot._persist_config = MagicMock()
        self.bot._on_callback("123", MSG_ID, "quorum:3")
        self.assertEqual(self.config["min_quorum"], 3)

    def test_unknown_text_sends_menu(self):
        self.bot._on_text("123", "random text")
        self.assertEqual(len(self.sent), 1)
        self.assertIn("SUI Gas Voter", self.sent[0]["text"])

    def test_route_extracts_msg_id(self):
        """Verify route passes message_id from callback to handler."""
        update = {
            "callback_query": {
                "id": "cb1",
                "message": {"chat": {"id": 123}, "message_id": 777},
                "data": "menu",
            }
        }
        self.bot._answer_cb = MagicMock()
        self.bot._route(update)
        self.assertEqual(self.last_edit["message_id"], 777)


if __name__ == "__main__":
    unittest.main()
