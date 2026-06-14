#!/usr/bin/env python3
"""
Monte Carlo simulation for model-recommended World Cup match trades.

Runs two probability worlds over the same recommended trades:
  1. polymarket: the trade's current Polymarket price is treated as truth.
  2. model: the deterministic model's probability for its predicted outcome is
     treated as truth.

Outputs include trade inputs, distribution summary, histogram bins, and full
cumulative P&L paths.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
INPUT_CSV = PROCESSED / "polymarket_world_cup_games_moneyline.csv"


def read_recommended_trades() -> list[dict]:
    with INPUT_CSV.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    trades = [row for row in rows if row["recommended"] == "YES" and float(row["deploy_amount"]) > 0]
    trades.sort(key=lambda row: (row["start_time_utc"], row["match"]))
    return trades


def model_probability(row: dict) -> float:
    key = f"{row['predicted_outcome']}_probability"
    return float(row[key])


def percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    index = q * (len(sorted_values) - 1)
    low = math.floor(index)
    high = math.ceil(index)
    if low == high:
        return sorted_values[low]
    weight = index - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def summarize(values: list[float]) -> dict:
    sorted_values = sorted(values)
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    total_deployed = None
    return {
        "mean": mean,
        "stdev": math.sqrt(variance),
        "min": sorted_values[0],
        "p01": percentile(sorted_values, 0.01),
        "p05": percentile(sorted_values, 0.05),
        "p10": percentile(sorted_values, 0.10),
        "median": percentile(sorted_values, 0.50),
        "p90": percentile(sorted_values, 0.90),
        "p95": percentile(sorted_values, 0.95),
        "p99": percentile(sorted_values, 0.99),
        "max": sorted_values[-1],
        "probability_loss": sum(1 for value in values if value < 0) / len(values),
        "probability_profit": sum(1 for value in values if value > 0) / len(values),
    }


def histogram(values: list[float], bins: int) -> list[dict]:
    low = min(values)
    high = max(values)
    if low == high:
        return [{"bin_low": low, "bin_high": high, "count": len(values), "frequency": 1.0}]
    width = (high - low) / bins
    counts = [0] * bins
    for value in values:
        index = min(bins - 1, int((value - low) / width))
        counts[index] += 1
    return [
        {
            "bin_low": low + index * width,
            "bin_high": low + (index + 1) * width,
            "count": count,
            "frequency": count / len(values),
        }
        for index, count in enumerate(counts)
    ]


def simulate(trades: list[dict], simulations: int, seed: int, probability_source: str) -> tuple[list[dict], list[float]]:
    rng = random.Random(seed)
    paths = []
    finals = []

    for simulation_id in range(1, simulations + 1):
        cumulative = 0.0
        row = {"probability_source": probability_source, "simulation_id": simulation_id}
        for trade_index, trade in enumerate(trades, start=1):
            price = float(trade["polymarket_price_x"])
            deploy = float(trade["deploy_amount"])
            shares = deploy / price if price > 0 else 0.0
            probability = price if probability_source == "polymarket" else model_probability(trade)
            win = rng.random() < probability
            pnl = shares * (1 - price) if win else shares * (-price)
            cumulative += pnl
            row[f"trade_{trade_index:02d}_pnl"] = round(pnl, 6)
            row[f"trade_{trade_index:02d}_cum_pnl"] = round(cumulative, 6)
        row["final_pnl"] = round(cumulative, 6)
        paths.append(row)
        finals.append(cumulative)

    return paths, finals


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Monte Carlo simulation for recommended World Cup trades.")
    parser.add_argument("--simulations", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=20260614)
    parser.add_argument("--bins", type=int, default=40)
    args = parser.parse_args()

    trades = read_recommended_trades()
    if not trades:
        raise RuntimeError("No recommended trades found. Run polymarket_world_cup_games.py first.")

    trade_inputs = []
    for index, trade in enumerate(trades, start=1):
        price = float(trade["polymarket_price_x"])
        deploy = float(trade["deploy_amount"])
        shares = deploy / price if price > 0 else 0.0
        trade_inputs.append(
            {
                "trade_index": index,
                "match": trade["match"],
                "start_time_utc": trade["start_time_utc"],
                "predicted_trade": trade["predicted_trade"],
                "contract_side": trade["contract_side"],
                "price": price,
                "model_probability": model_probability(trade),
                "polymarket_probability": price,
                "deploy_amount": deploy,
                "shares": shares,
                "profit_if_win": shares * (1 - price),
                "loss_if_lose": -deploy,
                "rating_edge": float(trade["rating_edge"]),
                "rating_edge_bin": trade["rating_edge_bin"],
            }
        )

    all_paths = []
    summaries = {}
    histogram_rows = []
    for offset, source in enumerate(["polymarket", "model"]):
        paths, finals = simulate(trades, args.simulations, args.seed + offset * 100_000, source)
        all_paths.extend(paths)
        summary = summarize(finals)
        summary["expected_return_on_deployed"] = summary["mean"] / sum(float(trade["deploy_amount"]) for trade in trades)
        summaries[source] = summary
        for bin_row in histogram(finals, args.bins):
            histogram_rows.append({"probability_source": source, **bin_row})

    path_fieldnames = ["probability_source", "simulation_id"]
    for index in range(1, len(trades) + 1):
        path_fieldnames.extend([f"trade_{index:02d}_pnl", f"trade_{index:02d}_cum_pnl"])
    path_fieldnames.append("final_pnl")

    write_csv(PROCESSED / "recommended_trade_montecarlo_inputs.csv", trade_inputs)
    write_csv(PROCESSED / "recommended_trade_montecarlo_paths.csv", all_paths, path_fieldnames)
    write_csv(PROCESSED / "recommended_trade_montecarlo_histogram.csv", histogram_rows)

    summary = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "input_file": str(INPUT_CSV),
        "simulations_per_probability_source": args.simulations,
        "recommended_trades": len(trades),
        "total_deployed": sum(float(trade["deploy_amount"]) for trade in trades),
        "probability_sources": {
            "polymarket": "Uses the trade contract price as the probability of winning.",
            "model": "Uses the model probability of the deterministic predicted six-outcome trade.",
        },
        "payout": "For each share bought at price x: win = 1-x, lose = -x.",
        "summary": summaries,
    }
    (PROCESSED / "recommended_trade_montecarlo_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("Recommended Trade Monte Carlo")
    print(f"Trades: {len(trades)}")
    print(f"Total deployed: ${summary['total_deployed']:.2f}")
    print(f"Simulations per source: {args.simulations}")
    for source, stats in summaries.items():
        print(
            f"{source}: mean ${stats['mean']:.2f}, stdev ${stats['stdev']:.2f}, "
            f"p05 ${stats['p05']:.2f}, median ${stats['median']:.2f}, "
            f"p95 ${stats['p95']:.2f}, loss {stats['probability_loss']:.2%}"
        )
    print(f"Wrote {PROCESSED / 'recommended_trade_montecarlo_summary.json'}")
    print(f"Wrote {PROCESSED / 'recommended_trade_montecarlo_paths.csv'}")


if __name__ == "__main__":
    main()
