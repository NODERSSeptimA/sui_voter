#!/usr/bin/env python3
"""SUI Gas Price Tracker — shows voting landscape across all validators."""

import argparse
import sys

from config import load_config, ConfigError
from sui_client import get_system_state, extract_trusted_votes, compute_gas_price, RPCError


def build_report(state: dict, config: dict) -> str:
    validators = state["activeValidators"]
    epoch = state["epoch"]
    trusted_addrs = set(config["trusted_validators"])

    # Collect all votes with validator info
    all_votes = []
    for v in validators:
        addr = v["suiAddress"]
        name = v.get("name", addr[:16])
        price = int(v["nextEpochGasPrice"])
        all_votes.append({
            "name": name,
            "address": addr,
            "price": price,
            "trusted": addr in trusted_addrs,
        })

    all_votes.sort(key=lambda x: x["price"])

    # Network-wide stats
    prices = [v["price"] for v in all_votes]
    net_median = compute_gas_price(prices, "median")
    net_avg = compute_gas_price(prices, "average")

    # Trusted stats
    trusted_votes = extract_trusted_votes(validators, config["trusted_validators"])
    our_price = None
    if len(trusted_votes) >= config["min_quorum"]:
        our_price = compute_gas_price(trusted_votes, config["strategy"])

    # Distribution buckets
    buckets = {}
    for p in prices:
        buckets[p] = buckets.get(p, 0) + 1

    lines = []
    lines.append(f"═══ Epoch {epoch} — Gas Price Tracker ═══")
    lines.append("")

    # --- Network overview ---
    lines.append(f"Network ({len(validators)} validators):")
    lines.append(f"  Median: {net_median} MIST")
    lines.append(f"  Average: {net_avg} MIST")
    lines.append(f"  Min: {min(prices)} MIST  Max: {max(prices)} MIST")
    lines.append("")

    # --- Distribution ---
    lines.append("Distribution:")
    for price in sorted(buckets.keys()):
        count = buckets[price]
        pct = count / len(prices) * 100
        bar = "█" * int(pct / 2) or "▏"
        lines.append(f"  {price:>6} MIST: {count:>3} validators ({pct:4.1f}%) {bar}")
    lines.append("")

    # --- Trusted validators ---
    lines.append(f"Trusted validators ({len(trusted_votes)}/{len(config['trusted_validators'])} found):")
    for v in all_votes:
        if v["trusted"]:
            lines.append(f"  {v['name']:<20} {v['price']:>6} MIST  {v['address'][:20]}...")
    # Show missing
    found_addrs = {v["address"] for v in all_votes if v["trusted"]}
    for addr in config["trusted_validators"]:
        if addr not in found_addrs:
            lines.append(f"  {'???':<20} {'N/A':>6}       {addr[:20]}... (NOT IN ACTIVE SET)")
    lines.append("")

    # --- Our vote ---
    lines.append("Our vote calculation:")
    lines.append(f"  Strategy: {config['strategy']}")
    lines.append(f"  Quorum: {len(trusted_votes)}/{config['min_quorum']} required")
    if our_price is not None:
        lines.append(f"  Would vote: {our_price} MIST")
        diff = our_price - net_median
        if diff != 0:
            direction = "above" if diff > 0 else "below"
            lines.append(f"  vs network median: {abs(diff)} MIST {direction} ({net_median})")
        else:
            lines.append(f"  = network median")
    else:
        lines.append(f"  Quorum not met — would NOT vote")

    # --- Reference gas price ---
    ref_price = state.get("referenceGasPrice")
    if ref_price:
        lines.append("")
        lines.append(f"Current reference gas price: {ref_price} MIST")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="SUI Gas Price Tracker")
    parser.add_argument("--config", required=True, help="Path to config.yaml")
    parser.add_argument("--rpc-url", help="Override RPC URL from config")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.rpc_url:
        config["rpc_url"] = args.rpc_url

    try:
        state = get_system_state(config["rpc_url"])
    except RPCError as e:
        print(f"RPC error: {e}", file=sys.stderr)
        sys.exit(1)

    print(build_report(state, config))


if __name__ == "__main__":
    main()
