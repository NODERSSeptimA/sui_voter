# SUI Gas Price Auto-Voter Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python daemon that auto-votes on SUI gas price by following trusted validators.

**Architecture:** Single-process polling daemon split into 4 focused modules: config loading/validation, SUI RPC + CLI interaction, Telegram notifications, and the main polling loop. The spec says "single script" but we split into modules for testability — all modules are co-located and deployed together. Tests use `unittest` with mocking for external calls (RPC, CLI, Telegram API).

**Tech Stack:** Python 3, requests, pyyaml, pytest (dev)

**Spec:** `docs/superpowers/specs/2026-03-13-sui-gas-voter-design.md`

---

## File Structure

```
sui_voter/
├── config.py              # Config loading and validation
├── sui_client.py          # RPC calls, gas price calculation, CLI execution
├── notifier.py            # Telegram notifications
├── voter.py               # Entry point: arg parsing, polling loop, signal handling
├── config.yaml.example    # Example config
├── requirements.txt       # Dependencies
├── sui-voter.service      # systemd unit
└── tests/
    ├── __init__.py
    ├── test_config.py
    ├── test_sui_client.py
    ├── test_notifier.py
    └── test_voter.py
```

**Module responsibilities:**
- `config.py` — load YAML, validate all fields, return a typed dict. No side effects.
- `sui_client.py` — `get_system_state(rpc_url)`, `extract_trusted_votes(state, trusted_list)`, `compute_gas_price(votes, strategy)`, `submit_vote(sui_bin, price)`. Pure logic + external calls.
- `notifier.py` — `send_telegram(bot_token, chat_id, message)`. Fire-and-forget with error logging.
- `voter.py` — `main()` entry point, polling loop, signal handling, state file, orchestration.

---

## Chunk 1: Foundation

### Task 1: Project Setup

**Files:**
- Create: `requirements.txt`
- Create: `config.yaml.example`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create requirements.txt**

```
requests>=2.28.0
pyyaml>=6.0
pytest>=7.0
```

- [ ] **Step 2: Create config.yaml.example**

```yaml
# SUI RPC endpoint (local validator node)
rpc_url: "http://127.0.0.1:9000"

# Trusted validator addresses to follow
trusted_validators:
  - "0xVALIDATOR_ADDRESS_1"
  - "0xVALIDATOR_ADDRESS_2"
  - "0xVALIDATOR_ADDRESS_3"

# Minimum trusted validators that must be in active set to vote
min_quorum: 2

# Polling interval in seconds
poll_interval: 60

# Gas price calculation: "median" or "average"
strategy: "median"

# Telegram notifications
telegram:
  bot_token: "YOUR_BOT_TOKEN"
  chat_id: "YOUR_CHAT_ID"

# Path to SUI CLI binary
sui_bin: "sui"
```

- [ ] **Step 3: Create empty tests/__init__.py**

Empty file.

- [ ] **Step 4: Install dependencies**

Run: `pip install -r requirements.txt`
Expected: Successfully installed requests and pyyaml.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt config.yaml.example tests/__init__.py
git commit -m "chore: project setup with dependencies and example config"
```

---

### Task 2: Config Loading and Validation

**Files:**
- Create: `config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing tests for config validation**

```python
# tests/test_config.py
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

    def tearDown(self):
        pass  # temp files cleaned up by OS

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 3: Implement config.py**

```python
# config.py
import yaml


class ConfigError(Exception):
    pass


DEFAULTS = {
    "poll_interval": 60,
    "strategy": "median",
    "sui_bin": "sui",
}


def load_config(path: str) -> dict:
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}")
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in config: {e}")

    if not isinstance(data, dict):
        raise ConfigError("Config must be a YAML mapping")

    for key, default in DEFAULTS.items():
        data.setdefault(key, default)

    _validate(data)
    return data


def _validate(cfg: dict):
    # rpc_url
    rpc_url = cfg.get("rpc_url", "")
    if not rpc_url or not isinstance(rpc_url, str):
        raise ConfigError("rpc_url must be a non-empty string")
    if not rpc_url.startswith(("http://", "https://")):
        raise ConfigError(f"rpc_url must start with http:// or https://, got '{rpc_url}'")

    # trusted_validators
    validators = cfg.get("trusted_validators", [])
    if not isinstance(validators, list) or len(validators) == 0:
        raise ConfigError("trusted_validators must be a non-empty list")

    # min_quorum
    quorum = cfg.get("min_quorum", 0)
    if not isinstance(quorum, int) or quorum < 1:
        raise ConfigError("min_quorum must be >= 1")
    if quorum > len(validators):
        raise ConfigError(
            f"min_quorum ({quorum}) exceeds trusted_validators count ({len(validators)})"
        )

    # strategy
    strategy = cfg.get("strategy", "")
    if strategy not in ("median", "average"):
        raise ConfigError(f"strategy must be 'median' or 'average', got '{strategy}'")

    # telegram
    tg = cfg.get("telegram", {})
    if not isinstance(tg, dict):
        raise ConfigError("telegram must be a mapping")
    if not tg.get("bot_token"):
        raise ConfigError("telegram.bot_token must be a non-empty string")
    if not tg.get("chat_id"):
        raise ConfigError("telegram.chat_id must be a non-empty string")

    # poll_interval
    poll_interval = cfg.get("poll_interval", 60)
    if not isinstance(poll_interval, (int, float)) or poll_interval <= 0:
        raise ConfigError("poll_interval must be a positive number")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: All 11 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_config.py
git commit -m "feat: add config loading and validation with tests"
```

---

### Task 3: Gas Price Calculation

**Files:**
- Create: `sui_client.py` (partial — calculation functions only)
- Create: `tests/test_sui_client.py` (partial — calculation tests only)

- [ ] **Step 1: Write failing tests for gas price calculation**

```python
# tests/test_sui_client.py
import unittest

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
        # (100 + 200) / 2 = 150.0 -> 150
        self.assertEqual(compute_gas_price([100, 200], "average"), 150)

    def test_average_rounds_down_non_integer(self):
        # (100 + 201) / 2 = 150.5 -> 150
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sui_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sui_client'`

- [ ] **Step 3: Implement calculation functions in sui_client.py**

```python
# sui_client.py
import logging
import subprocess

import requests

logger = logging.getLogger(__name__)

RPC_TIMEOUT = 30
CLI_TIMEOUT = 60


def extract_trusted_votes(active_validators: list, trusted: list[str]) -> list[int]:
    trusted_set = set(trusted)
    votes = []
    for v in active_validators:
        if v["suiAddress"] in trusted_set:
            votes.append(int(v["nextEpochGasPrice"]))
    return votes


def compute_gas_price(votes: list[int], strategy: str) -> int:
    if not votes:
        raise ValueError("No votes to compute gas price from")

    if strategy == "median":
        sorted_votes = sorted(votes)
        n = len(sorted_votes)
        return sorted_votes[(n - 1) // 2]
    elif strategy == "average":
        return sum(votes) // len(votes)
    else:
        raise ValueError(f"Unknown strategy: {strategy}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sui_client.py -v`
Expected: All 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add sui_client.py tests/test_sui_client.py
git commit -m "feat: add gas price calculation and vote extraction with tests"
```

---

## Chunk 2: External Integrations

### Task 4: SUI RPC Client

**Files:**
- Modify: `sui_client.py` — add `get_system_state()` and `submit_vote()`
- Modify: `tests/test_sui_client.py` — add RPC and CLI tests

- [ ] **Step 1: Write failing tests for RPC and CLI**

Append to `tests/test_sui_client.py` (add these imports at the top of the file alongside existing imports):

```python
import requests
import subprocess
from unittest.mock import patch, MagicMock

from sui_client import get_system_state, submit_vote, RPCError, CLIError
```

Then add these test classes at the bottom:

```python
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


class TestSubmitVote(unittest.TestCase):
    @patch("sui_client.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        submit_vote("sui", 750)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertIn("--gas-price", args)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sui_client.py::TestGetSystemState tests/test_sui_client.py::TestSubmitVote -v`
Expected: FAIL — `ImportError: cannot import name 'get_system_state'`

- [ ] **Step 3: Implement RPC and CLI functions**

Add to `sui_client.py`:

```python
class RPCError(Exception):
    pass


class CLIError(Exception):
    pass


def get_system_state(rpc_url: str) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "suix_getLatestSuiSystemState",
        "params": [],
    }
    try:
        resp = requests.post(rpc_url, json=payload, timeout=RPC_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RPCError(f"RPC request failed: {e}")

    data = resp.json()
    if "error" in data:
        raise RPCError(f"RPC error: {data['error']}")

    return data["result"]


def submit_vote(sui_bin: str, gas_price: int):
    cmd = [sui_bin, "validator", "update-gas-price", "--gas-price", str(gas_price)]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise CLIError(f"CLI timed out after {CLI_TIMEOUT}s")

    if result.returncode != 0:
        raise CLIError(f"CLI failed (rc={result.returncode}): {result.stderr.strip()}")

    logger.info("Vote submitted: gas_price=%d, output=%s", gas_price, result.stdout.strip())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sui_client.py -v`
Expected: All 18 tests PASS (12 from Task 3 + 6 new).

- [ ] **Step 5: Commit**

```bash
git add sui_client.py tests/test_sui_client.py
git commit -m "feat: add SUI RPC client and CLI vote submission with tests"
```

---

### Task 5: Telegram Notifier

**Files:**
- Create: `notifier.py`
- Create: `tests/test_notifier.py`

- [ ] **Step 1: Write failing tests for Telegram notifier**

```python
# tests/test_notifier.py
import unittest
from unittest.mock import patch, MagicMock

from notifier import send_telegram


class TestSendTelegram(unittest.TestCase):
    @patch("notifier.requests.post")
    def test_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        send_telegram("123:ABC", "-100", "test message")

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        self.assertIn("123:ABC", call_kwargs[0][0])
        self.assertEqual(call_kwargs[1]["json"]["text"], "test message")

    @patch("notifier.requests.post")
    def test_failure_does_not_raise(self, mock_post):
        mock_post.side_effect = Exception("network error")

        # Should not raise — fire and forget
        send_telegram("123:ABC", "-100", "test message")

    @patch("notifier.requests.post")
    def test_sends_correct_payload(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        send_telegram("token", "chat", "hello")

        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["chat_id"], "chat")
        self.assertEqual(payload["text"], "hello")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_notifier.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'notifier'`

- [ ] **Step 3: Implement notifier.py**

```python
# notifier.py
import logging

import requests

logger = logging.getLogger(__name__)

TELEGRAM_TIMEOUT = 10


def send_telegram(bot_token: str, chat_id: str, message: str):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
    }
    try:
        resp = requests.post(url, json=payload, timeout=TELEGRAM_TIMEOUT)
        resp.raise_for_status()
    except Exception:
        logger.warning("Failed to send Telegram message", exc_info=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_notifier.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add notifier.py tests/test_notifier.py
git commit -m "feat: add Telegram notifier with tests"
```

---

## Chunk 3: Main Loop and Deployment

### Task 6: Main Voter Loop

**Files:**
- Create: `voter.py`
- Create: `tests/test_voter.py`

- [ ] **Step 1: Write failing tests for state file and voting logic**

```python
# tests/test_voter.py
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

        result = do_vote_cycle(self._make_config(), current_epoch=9, voted_epoch=None)

        self.assertEqual(result["new_epoch"], 10)
        self.assertTrue(result["voted"])
        self.assertEqual(result["gas_price"], 200)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_voter.py -v`
Expected: FAIL — `ImportError: cannot import name 'read_voted_epoch'`

- [ ] **Step 3: Implement voter.py**

```python
# voter.py
import argparse
import logging
import os
import signal
import sys
import time

from config import load_config, ConfigError
from sui_client import (
    get_system_state,
    extract_trusted_votes,
    compute_gas_price,
    submit_vote,
    RPCError,
    CLIError,
)
from notifier import send_telegram

logger = logging.getLogger("sui_voter")

_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    logger.info("Received signal %s, shutting down after current cycle...", signum)
    _shutdown = True


def read_voted_epoch(path: str):
    try:
        with open(path, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def write_voted_epoch(path: str, epoch: int):
    with open(path, "w") as f:
        f.write(str(epoch))


def _notify(config: dict, message: str):
    send_telegram(
        config["telegram"]["bot_token"],
        config["telegram"]["chat_id"],
        message,
    )


def do_vote_cycle(config: dict, current_epoch, voted_epoch) -> dict:
    """Check for epoch change and vote if needed.

    Returns a dict with:
      - new_epoch: current epoch number
      - voted: whether a vote was submitted
      - gas_price: the voted price (if voted)
      - quorum_failed: True if quorum was not met
      - quorum_msg: message string (if quorum failed)
      - vote_msg: message string (if voted)

    Notifications are NOT sent here — the caller (main loop) controls throttling.
    """
    state = get_system_state(config["rpc_url"])
    new_epoch = int(state["epoch"])

    result = {"new_epoch": new_epoch, "voted": False}

    if new_epoch == current_epoch:
        return result

    if voted_epoch == new_epoch:
        logger.info("Epoch %d: already voted, skipping", new_epoch)
        return result

    validators = state["activeValidators"]
    votes = extract_trusted_votes(validators, config["trusted_validators"])

    if len(votes) < config["min_quorum"]:
        result["quorum_failed"] = True
        result["quorum_msg"] = (
            f"Epoch {new_epoch}: quorum not met "
            f"({len(votes)}/{config['min_quorum']} required), retrying..."
        )
        logger.warning(result["quorum_msg"])
        return result

    gas_price = compute_gas_price(votes, config["strategy"])
    submit_vote(config["sui_bin"], gas_price)

    result["voted"] = True
    result["gas_price"] = gas_price
    result["vote_msg"] = (
        f"Epoch {new_epoch}: voted gas price {gas_price} "
        f"({config['strategy']} of {len(votes)}/{len(config['trusted_validators'])} "
        f"trusted validators)"
    )
    logger.info(result["vote_msg"])
    return result


def main():
    parser = argparse.ArgumentParser(description="SUI Gas Price Auto-Voter")
    parser.add_argument("--config", required=True, help="Path to config.yaml")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = load_config(args.config)
    except ConfigError as e:
        logger.error("Configuration error: %s", e)
        sys.exit(1)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    state_file = os.path.join(os.getcwd(), "voted_epoch")
    voted_epoch = read_voted_epoch(state_file)
    current_epoch = None
    rpc_fail_count = 0
    quorum_notified_epoch = None
    cli_error_notified_epoch = None

    logger.info("SUI Gas Price Auto-Voter started")
    logger.info("Trusted validators: %d, quorum: %d, strategy: %s",
                len(config["trusted_validators"]),
                config["min_quorum"],
                config["strategy"])

    while not _shutdown:
        try:
            result = do_vote_cycle(config, current_epoch, voted_epoch)
            rpc_fail_count = 0
            current_epoch = result["new_epoch"]

            if result["voted"]:
                voted_epoch = current_epoch
                write_voted_epoch(state_file, voted_epoch)
                _notify(config, result["vote_msg"])

            if result.get("quorum_failed"):
                if quorum_notified_epoch != current_epoch:
                    quorum_notified_epoch = current_epoch
                    _notify(config, result["quorum_msg"])

        except RPCError as e:
            rpc_fail_count += 1
            logger.error("RPC error (attempt %d): %s", rpc_fail_count, e)
            if rpc_fail_count == 3 or (rpc_fail_count > 3 and rpc_fail_count % 10 == 0):
                _notify(config, f"RPC unreachable for {rpc_fail_count} cycles")

        except CLIError as e:
            logger.error("CLI error: %s", e)
            if cli_error_notified_epoch != current_epoch:
                cli_error_notified_epoch = current_epoch
                _notify(config, f"Epoch {current_epoch}: vote failed — {e}")

        except Exception:
            logger.exception("Unexpected error")

        time.sleep(config["poll_interval"])

    logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run all tests to verify they pass**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS (11 config + 12 sui_client + 3 notifier + 8 voter = 34 tests).

- [ ] **Step 5: Commit**

```bash
git add voter.py tests/test_voter.py
git commit -m "feat: add main voter loop with state management, signal handling, and tests"
```

---

### Task 7: Deployment Files

**Files:**
- Create: `sui-voter.service`

- [ ] **Step 1: Create systemd unit file**

```ini
[Unit]
Description=SUI Gas Price Auto-Voter
After=network.target

[Service]
Type=simple
User=sui
WorkingDirectory=/opt/sui-voter
ExecStart=/usr/bin/python3 voter.py --config config.yaml
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Run full test suite one last time**

Run: `python -m pytest tests/ -v`
Expected: All 34 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add sui-voter.service
git commit -m "chore: add systemd unit for deployment"
```
