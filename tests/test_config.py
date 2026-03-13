import unittest
import os
import tempfile
import yaml

from config import load_config, ConfigError


VALID_CONFIG = {
    "rpc_url": "http://127.0.0.1:9000",
    "trusted_validators": ["0xabc", "0xdef", "0x123"],
    "min_quorum": 2,
    "poll_interval": 60,
    "strategy": "median",
    "telegram": {
        "bot_token": "123:ABC",
        "chat_id": "-100123",
    },
    "sui_bin": "sui",
}


class TestLoadConfig(unittest.TestCase):
    def _write_config(self, data):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        yaml.dump(data, f)
        f.close()
        return f.name

    def test_valid_config(self):
        path = self._write_config(VALID_CONFIG)
        cfg = load_config(path)
        self.assertEqual(cfg["rpc_url"], "http://127.0.0.1:9000")
        self.assertEqual(cfg["min_quorum"], 2)
        self.assertEqual(cfg["strategy"], "median")
        os.unlink(path)

    def test_missing_file(self):
        with self.assertRaises(ConfigError):
            load_config("/nonexistent/path.yaml")

    def test_empty_trusted_validators(self):
        data = {**VALID_CONFIG, "trusted_validators": []}
        path = self._write_config(data)
        with self.assertRaises(ConfigError):
            load_config(path)
        os.unlink(path)

    def test_quorum_exceeds_validators(self):
        data = {**VALID_CONFIG, "min_quorum": 10}
        path = self._write_config(data)
        with self.assertRaises(ConfigError):
            load_config(path)
        os.unlink(path)

    def test_quorum_zero(self):
        data = {**VALID_CONFIG, "min_quorum": 0}
        path = self._write_config(data)
        with self.assertRaises(ConfigError):
            load_config(path)
        os.unlink(path)

    def test_invalid_strategy(self):
        data = {**VALID_CONFIG, "strategy": "mode"}
        path = self._write_config(data)
        with self.assertRaises(ConfigError):
            load_config(path)
        os.unlink(path)

    def test_missing_telegram_token(self):
        data = {**VALID_CONFIG, "telegram": {"bot_token": "", "chat_id": "-100"}}
        path = self._write_config(data)
        with self.assertRaises(ConfigError):
            load_config(path)
        os.unlink(path)

    def test_missing_rpc_url(self):
        data = {**VALID_CONFIG, "rpc_url": ""}
        path = self._write_config(data)
        with self.assertRaises(ConfigError):
            load_config(path)
        os.unlink(path)

    def test_invalid_rpc_url(self):
        data = {**VALID_CONFIG, "rpc_url": "not-a-url"}
        path = self._write_config(data)
        with self.assertRaises(ConfigError):
            load_config(path)
        os.unlink(path)

    def test_invalid_poll_interval(self):
        data = {**VALID_CONFIG, "poll_interval": -1}
        path = self._write_config(data)
        with self.assertRaises(ConfigError):
            load_config(path)
        os.unlink(path)

    def test_defaults_applied(self):
        data = {
            "rpc_url": "http://127.0.0.1:9000",
            "trusted_validators": ["0xabc"],
            "min_quorum": 1,
            "telegram": {"bot_token": "123:ABC", "chat_id": "-100"},
        }
        path = self._write_config(data)
        cfg = load_config(path)
        self.assertEqual(cfg["poll_interval"], 60)
        self.assertEqual(cfg["strategy"], "median")
        self.assertEqual(cfg["sui_bin"], "sui")
        os.unlink(path)


if __name__ == "__main__":
    unittest.main()
