import logging
import subprocess

import requests

logger = logging.getLogger(__name__)

RPC_TIMEOUT = 30
CLI_TIMEOUT = 60


class RPCError(Exception):
    pass


class CLIError(Exception):
    pass


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
