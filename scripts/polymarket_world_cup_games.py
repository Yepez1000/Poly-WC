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
    current_team_features,
    kelly_fraction,
)
from optimize_six_outcome_model import deterministic_pick, probabilities, edge_bin  # noqa: E402
from world_cup_quant_model import canonical_country  # noqa: E402


GAMES_PAGE_URL = "https://polymarket.com/sports/world-cup/games"
GAMMA_EVENT_URL = "https://gamma-api.polymarket.com/events/slug/{slug}"
SUMMARY_PATH = ROOT / "data" / "processed" / "six_outcome_optimized_summary.json"
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


def yes_no_prices(market: dict) -> tuple[float | None, float | None]:
    outcomes = decode_json_list(market.get("outcomes"))
    prices = [float(price) for price in decode_json_list(market.get("outcomePrices"))]
    yes = None
    no = None
    for outcome, price in zip(outcomes, prices):
        if outcome == "Yes":
            yes = price
        elif outcome == "No":
            no = price
    return yes, no


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


def load_settings() -> tuple[dict, float]:
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    return summary["optimized_parameters"], float(summary["metrics"]["pooled_accuracy"])


def trade_contract(predicted_outcome: str, team_a: str, team_b: str) -> tuple[str, str]:
    if predicted_outcome == "team_a_win":
        return team_a, "YES"
    if predicted_outcome == "draw":
        return "Draw", "YES"
    if predicted_outcome == "team_b_win":
        return team_b, "YES"
    if predicted_outcome == "team_a_loss":
        return team_a, "NO"
    if predicted_outcome == "no_draw":
        return "Draw", "NO"
    if predicted_outcome == "team_b_loss":
        return team_b, "NO"
    raise ValueError(f"Unknown predicted outcome: {predicted_outcome}")


def expected_value(probability: float, price: float) -> float:
    return probability * (1 - price) + (1 - probability) * (-price)


def analyze_games(bankroll: float, kelly_scale: float, max_fraction: float) -> tuple[list[dict], dict]:
    optimized, backtest_accuracy = load_settings()
    weights = optimized["economic_weights"]
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
        rating_edge = features[team_a]["rating"] - features[team_b]["rating"]
        params = {
            "draw_baseline": optimized["draw_baseline"],
            "draw_decay": optimized["draw_decay"],
            "draw_min": optimized["draw_min"],
            "draw_max": optimized["draw_max"],
            "logistic_slope": optimized["logistic_slope"],
        }
        model_probs = probabilities(rating_edge, params)
        predicted_outcome = deterministic_pick(model_probs)
        contract_outcome, contract_side = trade_contract(predicted_outcome, team_a, team_b)
        outcome_labels = {
            "team_a_win": team_a,
            "draw": "Draw",
            "team_b_win": team_b,
            "team_a_loss": f"{team_a} NO",
            "no_draw": "Draw NO",
            "team_b_loss": f"{team_b} NO",
        }
        yes_prices = {}
        no_prices = {}
        market_ids = {}
        market_questions = {}
        best_bids = {}
        best_asks = {}

        for market in event.get("markets", []):
            outcome = classify_market(market, team_a, team_b)
            if not outcome:
                continue
            yes, no = yes_no_prices(market)
            if yes is None or no is None:
                continue
            yes_prices[outcome] = yes
            no_prices[outcome] = no
            market_ids[outcome] = market.get("id")
            market_questions[outcome] = market.get("question")
            best_bids[outcome] = market.get("bestBid")
            best_asks[outcome] = market.get("bestAsk")

        if set(yes_prices) != {team_a, "Draw", team_b}:
            continue

        market_yes_no_vig = implied_no_vig(yes_prices)
        trade_price = yes_prices[contract_outcome] if contract_side == "YES" else no_prices[contract_outcome]
        ev = expected_value(backtest_accuracy, trade_price)
        full_kelly = kelly_fraction(backtest_accuracy, trade_price)
        deploy_fraction = min(max_fraction, full_kelly * kelly_scale) if ev > 0 else 0
        deploy_amount = bankroll * deploy_fraction
        model_pick_3way = max(
            [(team_a, model_probs["team_a_win"]), ("Draw", model_probs["draw"]), (team_b, model_probs["team_b_win"])],
            key=lambda item: item[1],
        )[0]
        market_pick = max(market_yes_no_vig.items(), key=lambda item: item[1])[0]
        rows.append(
            {
                "event_id": event.get("id"),
                "slug": event.get("slug"),
                "match": event.get("title"),
                "start_time_utc": event.get("endDate"),
                "team_a": team_a,
                "team_b": team_b,
                "predicted_outcome": predicted_outcome,
                "predicted_trade": outcome_labels[predicted_outcome],
                "contract_outcome": contract_outcome,
                "contract_side": contract_side,
                "market_id": market_ids[contract_outcome],
                "market_question": market_questions[contract_outcome],
                "polymarket_price_x": round(trade_price, 6),
                "backtest_accuracy_p": round(backtest_accuracy, 6),
                "expected_value": round(ev, 6),
                "expected_value_simplified": round(backtest_accuracy - trade_price, 6),
                "profit_if_win_per_share": round(1 - trade_price, 6),
                "loss_if_lose_per_share": round(-trade_price, 6),
                "full_kelly_fraction": round(full_kelly, 6),
                "deploy_fraction": round(deploy_fraction, 6),
                "deploy_amount": round(deploy_amount, 2),
                "max_loss": round(deploy_amount, 2),
                "profit_if_win": round(deploy_amount * (1 / trade_price - 1), 2) if deploy_amount else 0.0,
                "rating_edge": round(rating_edge, 6),
                "abs_rating_edge": round(abs(rating_edge), 6),
                "rating_edge_bin": edge_bin(abs(rating_edge)),
                "team_a_win_probability": round(model_probs["team_a_win"], 6),
                "draw_probability": round(model_probs["draw"], 6),
                "team_b_win_probability": round(model_probs["team_b_win"], 6),
                "team_a_loss_probability": round(model_probs["team_a_loss"], 6),
                "no_draw_probability": round(model_probs["no_draw"], 6),
                "team_b_loss_probability": round(model_probs["team_b_loss"], 6),
                "model_pick_3way": model_pick_3way,
                "market_pick_3way": market_pick,
                "best_bid": best_bids[contract_outcome],
                "best_ask": best_asks[contract_outcome],
                "recommended": "YES" if ev > 0 else "PASS",
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
        "ev_formula": "E = p * (1 - x) + (1 - p) * (-x); simplified E = p - x",
        "prediction_source": "deterministic six-outcome model; only the predicted outcome is evaluated",
        "six_outcome_parameters": optimized,
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
