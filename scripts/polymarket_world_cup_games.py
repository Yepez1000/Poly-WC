#!/usr/bin/env python3
"""
Individual Polymarket World Cup game moneyline analyzer.

Fetches the games listed at https://polymarket.com/sports/world-cup/games,
joins each event's 1X2 moneyline markets, and scores them with the optimized
World Cup economic model weights.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import ssl
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from polymarket_world_cup_trader import (  # noqa: E402
    conservative_probability,
    current_team_features,
    kelly_fraction,
    match_probs,
)
from world_cup_quant_model import canonical_country  # noqa: E402


GAMES_PAGE_URL = "https://polymarket.com/sports/world-cup/games"
GAMMA_EVENT_URL = "https://gamma-api.polymarket.com/events/slug/{slug}"
SUMMARY_PATH = ROOT / "data" / "processed" / "optimized_model_summary.json"
OUT_DIR = ROOT / "data" / "processed"


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 PolymarketWorldCupGames/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
            raise
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(request, timeout=45, context=context) as response:
            return response.read().decode("utf-8", errors="replace")


def fetch_json(url: str) -> dict:
    return json.loads(fetch_text(url))


def decode_json_list(value):
    if isinstance(value, list):
        return value
    return json.loads(value or "[]")


def yes_price(market: dict) -> float | None:
    outcomes = decode_json_list(market.get("outcomes"))
    prices = [float(price) for price in decode_json_list(market.get("outcomePrices"))]
    for outcome, price in zip(outcomes, prices):
        if outcome == "Yes":
            return price
    return None


def slugs_from_games_page() -> list[str]:
    html = fetch_text(GAMES_PAGE_URL)
    next_data = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html)
    if next_data:
        payload = json.loads(next_data.group(1))
        queries = payload.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", [])
        for query in queries:
            if query.get("queryKey") == ["parentToChildEventIds"]:
                data = query.get("state", {}).get("data", {})
                slugs = [slug for slug in data if slug.startswith("fifwc-")]
                if slugs:
                    return slugs

    slugs = sorted(set(re.findall(r"/sports/world-cup/(fifwc-[a-z0-9-]+-2026-\d{2}-\d{2})", html)))
    return slugs


def parse_teams(title: str) -> tuple[str, str]:
    if " vs. " not in title:
        raise ValueError(f"Cannot parse teams from title: {title}")
    team_a, team_b = title.split(" vs. ", 1)
    return canonical_country(team_a.strip()), canonical_country(team_b.strip())


def classify_market(market: dict, team_a: str, team_b: str) -> str | None:
    question = market.get("question") or ""
    if " end in a draw?" in question:
        return "Draw"
    if question.startswith(f"Will {team_a} win "):
        return team_a
    if question.startswith(f"Will {team_b} win "):
        return team_b
    # Polymarket sometimes spells aliases differently in questions than titles.
    raw = question.removeprefix("Will ").split(" win on ", 1)[0].strip()
    canonical = canonical_country(raw)
    if canonical in {team_a, team_b}:
        return canonical
    return None


def implied_no_vig(prices: dict[str, float]) -> dict[str, float]:
    total = sum(prices.values()) or 1
    return {key: value / total for key, value in prices.items()}


def load_settings() -> tuple[dict[str, float], float]:
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    return summary["optimized_weights"], float(summary["accuracy"])


def analyze_games(bankroll: float, kelly_scale: float, max_fraction: float) -> tuple[list[dict], dict]:
    weights, backtest_accuracy = load_settings()
    slugs = slugs_from_games_page()
    events = [fetch_json(GAMMA_EVENT_URL.format(slug=slug)) for slug in slugs]

    teams = set()
    parsed_events = []
    for event in events:
        team_a, team_b = parse_teams(event["title"])
        teams.update([team_a, team_b])
        parsed_events.append((event, team_a, team_b))

    features = current_team_features(teams, weights)
    rows = []
    for event, team_a, team_b in parsed_events:
        p_a, p_draw, p_b = match_probs(team_a, team_b, features)
        model_probs = {team_a: p_a, "Draw": p_draw, team_b: p_b}
        market_prices = {}
        market_ids = {}
        market_questions = {}
        best_bids = {}
        best_asks = {}

        for market in event.get("markets", []):
            outcome = classify_market(market, team_a, team_b)
            if not outcome:
                continue
            price = yes_price(market)
            if price is None:
                continue
            market_prices[outcome] = price
            market_ids[outcome] = market.get("id")
            market_questions[outcome] = market.get("question")
            best_bids[outcome] = market.get("bestBid")
            best_asks[outcome] = market.get("bestAsk")

        if set(market_prices) != {team_a, "Draw", team_b}:
            continue

        no_vig = implied_no_vig(market_prices)
        edge_rows = []
        for outcome in (team_a, "Draw", team_b):
            model_probability = model_probs[outcome]
            market_probability = market_prices[outcome]
            calibrated_probability = conservative_probability(model_probability, market_probability, backtest_accuracy)
            edge = calibrated_probability - market_probability
            ev_per_dollar = calibrated_probability / market_probability - 1 if market_probability > 0 else 0
            full_kelly = kelly_fraction(calibrated_probability, market_probability)
            deploy_fraction = min(max_fraction, full_kelly * kelly_scale) if edge > 0 else 0
            deploy_amount = bankroll * deploy_fraction
            edge_rows.append(
                {
                    "outcome": outcome,
                    "model_probability": model_probability,
                    "calibrated_probability": calibrated_probability,
                    "market_price": market_probability,
                    "edge": edge,
                    "ev_per_dollar": ev_per_dollar,
                    "full_kelly_fraction": full_kelly,
                    "deploy_fraction": deploy_fraction,
                    "deploy_amount": deploy_amount,
                    "profit_if_win": deploy_amount * (1 / market_probability - 1) if deploy_amount else 0,
                }
            )

        best_trade = max(edge_rows, key=lambda row: row["ev_per_dollar"])
        model_pick = max(model_probs.items(), key=lambda item: item[1])[0]
        market_pick = max(no_vig.items(), key=lambda item: item[1])[0]
        for outcome_row in edge_rows:
            outcome = outcome_row["outcome"]
            rows.append(
                {
                    "event_id": event.get("id"),
                    "slug": event.get("slug"),
                    "match": event.get("title"),
                    "start_time_utc": event.get("endDate"),
                    "team_a": team_a,
                    "team_b": team_b,
                    "outcome": outcome,
                    "market_id": market_ids[outcome],
                    "market_question": market_questions[outcome],
                    "market_yes_price": round(outcome_row["market_price"], 6),
                    "market_no_vig_probability": round(no_vig[outcome], 6),
                    "model_probability": round(outcome_row["model_probability"], 6),
                    "calibrated_probability": round(outcome_row["calibrated_probability"], 6),
                    "edge": round(outcome_row["edge"], 6),
                    "ev_per_dollar": round(outcome_row["ev_per_dollar"], 6),
                    "full_kelly_fraction": round(outcome_row["full_kelly_fraction"], 6),
                    "deploy_fraction": round(outcome_row["deploy_fraction"], 6),
                    "deploy_amount": round(outcome_row["deploy_amount"], 2),
                    "max_loss": round(outcome_row["deploy_amount"], 2),
                    "profit_if_win": round(outcome_row["profit_if_win"], 2),
                    "best_bid": best_bids[outcome],
                    "best_ask": best_asks[outcome],
                    "model_pick": model_pick,
                    "market_pick": market_pick,
                    "recommended": "YES" if outcome == best_trade["outcome"] and best_trade["edge"] > 0 else "PASS",
                }
            )

    summary = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_page": GAMES_PAGE_URL,
        "events_found": len(slugs),
        "events_scored": len({row["event_id"] for row in rows}),
        "outcome_rows": len(rows),
        "weights": weights,
        "backtest_accuracy_used": backtest_accuracy,
        "bankroll": bankroll,
        "kelly_scale": kelly_scale,
        "max_fraction_per_outcome": max_fraction,
    }
    return rows, summary


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze individual Polymarket World Cup game moneyline markets.")
    parser.add_argument("--bankroll", type=float, default=1000.0)
    parser.add_argument("--kelly-scale", type=float, default=0.25)
    parser.add_argument("--max-fraction", type=float, default=0.02)
    args = parser.parse_args()

    rows, summary = analyze_games(args.bankroll, args.kelly_scale, args.max_fraction)
    write_csv(OUT_DIR / "polymarket_world_cup_games_moneyline.csv", rows)
    (OUT_DIR / "polymarket_world_cup_games_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    active = [row for row in rows if row["recommended"] == "YES"]
    print("Polymarket World Cup Games Analyzer")
    print(f"Events found: {summary['events_found']}")
    print(f"Events scored: {summary['events_scored']}")
    print(f"Outcome rows: {summary['outcome_rows']}")
    print(f"Recommended YES trades: {len(active)}")
    print(f"Wrote {OUT_DIR / 'polymarket_world_cup_games_moneyline.csv'}")
    print(f"Wrote {OUT_DIR / 'polymarket_world_cup_games_summary.json'}")


if __name__ == "__main__":
    main()
