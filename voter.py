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
