# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

SUI Gas Price Auto-Voter — a Python daemon that automatically votes on SUI gas price for a validator node by following trusted validators. Polls for epoch changes via JSON-RPC, computes median/average of trusted validators' votes, submits via `sui` CLI.

## Commands

```bash
# Run tests
python3 -m pytest tests/ -v

# Run a single test file
python3 -m pytest tests/test_sui_client.py -v

# Run a single test
python3 -m pytest tests/test_voter.py::TestDoVoteCycle::test_successful_vote -v

# Run manually
python3 voter.py --config config.yaml

# Install deps
pip3 install -r requirements.txt
```

## Architecture

Four modules, each with a single responsibility:

- **voter.py** — Entry point. Polling loop, signal handling (threading.Event for instant shutdown), state file (`voted_epoch`) for restart safety, notification throttling (once per epoch for quorum/CLI errors, 3+/10th for RPC errors). `do_vote_cycle()` is pure logic — returns data, does NOT send notifications. Main loop controls all notification throttling.
- **config.py** — Loads YAML, validates all fields, applies defaults. Raises `ConfigError` on invalid config. No side effects.
- **sui_client.py** — `get_system_state()` (JSON-RPC), `extract_trusted_votes()` (filter by address), `compute_gas_price()` (median: lower-middle for even count; average: floor division), `submit_vote()` (subprocess with timeout, parses tx digest/status from CLI output). Raises `RPCError`/`CLIError`.
- **notifier.py** — `send_telegram()`. Fire-and-forget: catches all exceptions, logs, never blocks.

## Key Design Decisions

- `nextEpochGasPrice` always has a value on every active validator — quorum counts validators found in active set, not "freshness" of votes
- Gas prices are integers in MIST (1 SUI = 10^9 MIST)
- `sui validator update-gas-price` takes gas-price as a **positional** argument, not `--gas-price`
- State file is plain text with a single epoch integer in working directory
- Deployment path is `/home/sui/sui_voter` (not `/opt/sui-voter` as in template service file)

## Testing

42 tests using unittest + pytest. All external calls (RPC, CLI subprocess, Telegram API) are mocked. Tests live in `tests/` mirroring source modules.
