# SUI Gas Price Auto-Voter

Daemon that automatically votes on SUI gas price for your validator by following trusted validators.

## How It Works

```
                          ┌─────────────────────┐
                          │   Polling Loop       │
                          │   (every 60s)        │
                          └──────────┬───────────┘
                                     │
                          ┌──────────▼───────────┐
                          │  suix_getLatest       │
                          │  SuiSystemState       │
                          │  (JSON-RPC)           │
                          └──────────┬───────────┘
                                     │
                          ┌──────────▼───────────┐
                          │  Epoch changed?       │
                          └───┬──────────────┬────┘
                            No│              │Yes
                              │   ┌──────────▼───────────┐
                              │   │  Filter trusted       │
                              │   │  validators votes     │
                              │   └──────────┬───────────┘
                              │              │
                              │   ┌──────────▼───────────┐
                              │   │  Quorum met?          │
                              │   └───┬──────────────┬────┘
                              │     No│              │Yes
                              │       │   ┌──────────▼───────────┐
                              │       │   │  Compute median/avg  │
                              │       │   │  gas price           │
                              │       │   └──────────┬───────────┘
                              │       │              │
                              │       │   ┌──────────▼───────────┐
                              │       │   │  sui validator       │
                              │       │   │  update-gas-price    │
                              │       │   └──────────┬───────────┘
                              │       │              │
                              │       │   ┌──────────▼───────────┐
                              │       │   │  Notify Telegram     │
                              │       │   └──────────────────────┘
                              │       │
                          ┌───▼───────▼──────────┐
                          │  Sleep poll_interval  │
                          └──────────────────────┘
```

## Architecture

```
voter.py           Entry point: polling loop, signal handling, state management
config.py          Config loading and validation
sui_client.py      SUI JSON-RPC calls, gas price calculation, CLI execution
notifier.py        Telegram notifications (fire-and-forget)
```

- **Single process, single thread** — no database, minimal footprint
- Tracks epoch changes via `suix_getLatestSuiSystemState` JSON-RPC
- Computes gas price as **median** (or average) of trusted validators' `nextEpochGasPrice`
- Votes via `sui validator update-gas-price` CLI
- Persists last voted epoch to `voted_epoch` file for restart safety
- Runs as a systemd service with auto-restart

## Requirements

- Python 3.10+
- `sui` CLI installed and configured (keystore on the same machine)
- Telegram bot (for notifications)

## Installation

```bash
# Clone
git clone https://github.com/NODERSSeptimA/sui_voter.git
cd sui_voter

# Install dependencies
pip install -r requirements.txt

# Configure
cp config.yaml.example config.yaml
nano config.yaml  # fill in your values
chmod 600 config.yaml
```

## Configuration

Edit `config.yaml`:

```yaml
# SUI RPC endpoint (local validator node)
rpc_url: "http://127.0.0.1:9000"

# Trusted validator addresses to follow
trusted_validators:
  - "0xVALIDATOR_ADDRESS_1"
  - "0xVALIDATOR_ADDRESS_2"
  - "0xVALIDATOR_ADDRESS_3"

# Minimum trusted validators that must be in active set
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

| Parameter | Description |
|---|---|
| `rpc_url` | JSON-RPC endpoint of your SUI node |
| `trusted_validators` | List of validator addresses to follow |
| `min_quorum` | Minimum validators in active set to vote (must be <= total trusted) |
| `poll_interval` | How often to check for epoch changes (seconds) |
| `strategy` | `"median"` (lower-middle for even count) or `"average"` (floor) |
| `telegram.bot_token` | Telegram Bot API token |
| `telegram.chat_id` | Telegram chat ID for notifications |
| `sui_bin` | Path to `sui` binary (default: `"sui"`) |

## Running

### Manual

```bash
python voter.py --config config.yaml
```

### As a systemd service

```bash
# Copy files to /opt/sui-voter/
sudo mkdir -p /opt/sui-voter
sudo cp voter.py config.py sui_client.py notifier.py config.yaml requirements.txt /opt/sui-voter/
sudo cp sui-voter.service /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable --now sui-voter

# Check status
sudo systemctl status sui-voter
sudo journalctl -u sui-voter -f
```

## Notifications

The daemon sends Telegram messages for:

| Event | Example |
|---|---|
| Successful vote | `Epoch 150: voted gas price 750 (median of 4/5 trusted validators)` |
| Quorum not met | `Epoch 150: quorum not met (2/3 required), retrying...` |
| CLI error | `Epoch 150: vote failed — <error>` |
| RPC unreachable | `RPC unreachable for 3 cycles` |

Notifications are throttled: quorum and CLI errors are sent once per epoch, RPC errors after 3 consecutive failures then every 10th.

## Tests

```bash
pip install pytest
pytest tests/ -v
```
