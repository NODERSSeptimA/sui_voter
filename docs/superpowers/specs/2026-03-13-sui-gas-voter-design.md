# SUI Gas Price Auto-Voter — Design Spec

## Overview

A Python daemon that automatically votes on SUI gas price for a validator node by following the votes of a configurable list of trusted validators. It monitors epoch changes via JSON-RPC polling and submits votes via the `sui` CLI.

## Requirements

- **Language:** Python 3
- **Voting method:** `sui validator update-gas-price --gas-price <value>`
- **Reference logic:** Collect `nextEpochGasPrice` from trusted validators, compute median (configurable to average)
- **Trigger:** Epoch change detected via polling `suix_getLatestSuiSystemState`
- **Authentication:** Local keystore on the validator machine
- **Notifications:** Telegram bot
- **Quorum:** Configurable minimum number of trusted validators who have voted before we vote

## Architecture

Single Python script, single process, single thread. No database — epoch number tracked in memory. Runs as a systemd service.

### File Structure

```
sui_voter/
├── voter.py          # Main script (polling loop + voting logic)
├── config.yaml       # Configuration
├── requirements.txt  # Dependencies (requests, pyyaml)
└── sui-voter.service # systemd unit file
```

## Configuration (`config.yaml`)

```yaml
rpc_url: "http://127.0.0.1:9000"

trusted_validators:
  - "0xabc123..."
  - "0xdef456..."
  - "0x789abc..."

min_quorum: 3

poll_interval: 60

strategy: "median"

telegram:
  bot_token: "123456:ABC-DEF..."
  chat_id: "-1001234567890"

sui_bin: "sui"
```

Secrets (bot_token) are protected by file permissions (`chmod 600`). No need for env vars or vault — the validator machine has restricted access.

## Core Logic

### Polling Loop

1. Every `poll_interval` seconds, call `suix_getLatestSuiSystemState` via JSON-RPC
2. Compare `epoch` from response with the in-memory value
3. If epoch changed — trigger voting procedure

### Voting Procedure

1. From `suix_getLatestSuiSystemState` response, extract `activeValidators` array
2. Filter by `suiAddress` — keep only those in `trusted_validators`
3. Collect their `nextEpochGasPrice` values
4. Check quorum: if count < `min_quorum` — skip voting, notify Telegram, retry on next poll cycle
5. Compute median (or average, per `strategy` config)
6. Execute `sui validator update-gas-price --gas-price <value>`
7. Notify Telegram with the result

### Retry on Insufficient Quorum

The script does not panic — it continues polling. Trusted validators may vote later within the epoch, and on the next check the quorum may be met.

## Error Handling

| Situation | Behavior |
|---|---|
| RPC unreachable | Log + retry next cycle; Telegram after 3+ consecutive failures |
| CLI command failed | Log + Telegram; retry next cycle |
| Quorum not met | Log + Telegram (once per epoch); retry next cycle |
| Telegram API unavailable | Log to stderr; does not block main logic |

## Telegram Notifications

- **Successful vote:** `"Epoch 150: voted gas price 750 (median of 4/5 trusted validators)"`
- **Quorum not met:** `"Epoch 150: quorum not met (2/3 required), retrying..."`
- **CLI error:** `"Epoch 150: vote failed — <error message>"`
- **RPC unreachable (3+ cycles):** `"RPC unreachable for 3 cycles"`

## Logging

Standard Python `logging` module to stdout/stderr. systemd journal captures output automatically. No separate log files.

## Deployment

### systemd unit (`sui-voter.service`)

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

### Installation Steps

1. Copy files to `/opt/sui-voter/`
2. `pip install -r requirements.txt`
3. Fill in `config.yaml` with real values
4. `systemctl enable --now sui-voter`

### Dependencies (`requirements.txt`)

- `requests` — JSON-RPC calls and Telegram API
- `pyyaml` — config file parsing

No heavy frameworks. Minimal footprint.
