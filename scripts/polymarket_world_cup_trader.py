#!/usr/bin/env python3
"""
Polymarket World Cup market analyzer.

This script fetches active public Polymarket 2026 FIFA World Cup markets,
estimates model probabilities from the economic backtest model, runs a Monte
Carlo tournament simulation, and sizes candidate YES/NO positions using a
conservative fractional Kelly rule.

It is a research tool, not financial advice and not an order-placement bot.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import ssl
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from world_cup_quant_model import (  # noqa: E402
    CLIMATE_C,
    COUNTRY_ISO3,
    POPULATION_INDICATOR,
    WEALTH_FALLBACK,
    WEALTH_INDICATOR,
    canonical_country,
    climate_score,
    macro_value,
    ranking_score,
    read_all_results_until,
    rolling_strength_ranks,
    world_bank_series,
)


GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets?limit={limit}&offset={offset}&active=true&closed=false"
SUMMARY_PATH = ROOT / "data" / "processed" / "optimized_model_summary.json"
OUT_DIR = ROOT / "data" / "processed"

MARKET_TERMS = ("2026 fifa world cup",)
CONFEDERATION_NAMES = {
    "Africa",
    "Africa (CAF)",
    "Asia",
    "Asia (AFC)",
    "Europe",
    "Europe (UEFA)",
    "North America",
    "North America (CONCACAF)",
    "South America",
    "South America (CONMEBOL)",
    "Oceania",
    "Oceania (OCF)",
}
DEFAULT_BANKROLL = 1000.0


def fetch_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 PolymarketWorldCupResearch/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.load(response)
    except urllib.error.URLError as exc:
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
            raise
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(request, timeout=45, context=context) as response:
            return json.load(response)


def decode_json_list(value):
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return json.loads(value)


def fetch_polymarket_markets(max_markets: int) -> list[dict]:
    markets = []
    page = 100
    for offset in range(0, max_markets, page):
        data = fetch_json(GAMMA_MARKETS_URL.format(limit=page, offset=offset))
        if not data:
            break
        markets.extend(data)
        if len(data) < page:
            break
    return markets


def market_price(market: dict, outcome: str) -> float | None:
    outcomes = decode_json_list(market.get("outcomes"))
    prices = [float(price) for price in decode_json_list(market.get("outcomePrices"))]
    for label, price in zip(outcomes, prices):
        if label == outcome:
            return price
    return None


def parse_world_cup_markets(markets: list[dict]) -> tuple[list[dict], dict[str, list[str]]]:
    parsed = []
    groups: dict[str, list[str]] = defaultdict(list)
    outright_re = re.compile(r"^Will (.+) win the 2026 FIFA World Cup\?$")
    group_re = re.compile(r"^Will (.+) win Group ([A-L]) in the 2026 FIFA World Cup\?$")

    for market in markets:
        question = market.get("question") or ""
        text = " ".join(str(market.get(key, "")) for key in ("question", "slug", "description")).lower()
        if not any(term in text for term in MARKET_TERMS):
            continue

        group_match = group_re.match(question)
        outright_match = outright_re.match(question)
        market_type = None
        team = None
        group = ""
        if group_match:
            raw_team, group = group_match.groups()
            team = canonical_country(raw_team.removeprefix("the ").strip())
            market_type = "group_winner"
            groups[group].append(team)
        elif outright_match:
            raw_team = outright_match.group(1)
            if raw_team in CONFEDERATION_NAMES:
                continue
            team = canonical_country(raw_team.removeprefix("the ").strip())
            market_type = "outright_winner"
        else:
            continue

        yes_price = market_price(market, "Yes")
        no_price = market_price(market, "No")
        if yes_price is None or no_price is None:
            continue
        parsed.append(
            {
                "market_id": market.get("id"),
                "question": question,
                "slug": market.get("slug"),
                "market_type": market_type,
                "team": team,
                "group": group,
                "yes_price": yes_price,
                "no_price": no_price,
                "liquidity": float(market.get("liquidityNum") or market.get("liquidity") or 0),
                "volume": float(market.get("volumeNum") or market.get("volume") or 0),
                "end_date": market.get("endDate"),
            }
        )
    return parsed, {group: sorted(set(teams)) for group, teams in groups.items()}


def load_backtest_settings() -> tuple[dict[str, float], float]:
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    return summary["optimized_weights"], float(summary["accuracy"])


def current_team_features(teams: set[str], weights: dict[str, float]) -> dict[str, dict]:
    missing_iso = sorted(team for team in teams if team not in COUNTRY_ISO3)
    missing_climate = sorted(team for team in teams if team not in CLIMATE_C)
    if missing_iso or missing_climate:
        raise RuntimeError(f"Missing mappings: iso={missing_iso}, climate={missing_climate}")

    year = datetime.now(UTC).year
    population = world_bank_series(POPULATION_INDICATOR, teams)
    wealth = world_bank_series(WEALTH_INDICATOR, teams)
    rank_lookup = rolling_strength_ranks(read_all_results_until(year), [datetime.now(UTC).date()])
    ranks = rank_lookup[datetime.now(UTC).date()]

    pop_values = []
    wealth_values = []
    raw = {}
    for team in teams:
        pop = macro_value(population, team, year)
        gdp = macro_value(wealth, team, year, WEALTH_FALLBACK)
        if pop is None or gdp is None:
            raise RuntimeError(f"Missing World Bank macro data for {team}")
        rank = ranks.get(team, 180)
        raw[team] = {"population": pop, "wealth": gdp, "rank": rank}
        pop_values.append(pop)
        wealth_values.append(gdp)

    def norm_log(value: float, values: list[float]) -> float:
        low, high = min(values), max(values)
        return (math.log(value) - math.log(low)) / (math.log(high) - math.log(low))

    features = {}
    for team in teams:
        components = {
            "population": norm_log(raw[team]["population"], pop_values),
            "climate": climate_score(team),
            "wealth": norm_log(raw[team]["wealth"], wealth_values),
            "ranking": ranking_score(raw[team]["rank"]),
        }
        rating = sum(weights[key] * components[key] for key in weights)
        features[team] = {
            **raw[team],
            **{f"{key}_score": value for key, value in components.items()},
            "rating": rating,
        }
    return features


def match_probs(team_a: str, team_b: str, features: dict[str, dict]) -> tuple[float, float, float]:
    edge = features[team_a]["rating"] - features[team_b]["rating"]
    no_draw_a = 1 / (1 + math.exp(-edge * 8))
    draw = max(0.10, min(0.30, 0.24 * math.exp(-abs(edge) * 3)))
    return (1 - draw) * no_draw_a, draw, (1 - draw) * (1 - no_draw_a)


def simulate_group(group_teams: list[str], features: dict[str, dict], rng: random.Random) -> tuple[str, list[tuple[str, int, float]]]:
    table = {team: {"points": 0, "gd": 0.0, "rating": features[team]["rating"]} for team in group_teams}
    for team_a, team_b in combinations(group_teams, 2):
        p_a, p_draw, _ = match_probs(team_a, team_b, features)
        roll = rng.random()
        if roll < p_a:
            table[team_a]["points"] += 3
            table[team_a]["gd"] += 1
            table[team_b]["gd"] -= 1
        elif roll < p_a + p_draw:
            table[team_a]["points"] += 1
            table[team_b]["points"] += 1
        else:
            table[team_b]["points"] += 3
            table[team_b]["gd"] += 1
            table[team_a]["gd"] -= 1
    standings = sorted(table.items(), key=lambda item: (item[1]["points"], item[1]["gd"], item[1]["rating"], rng.random()), reverse=True)
    return standings[0][0], [(team, values["points"], values["rating"]) for team, values in standings]


def combinations(items: list[str], size: int):
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            yield items[i], items[j]


def pair_winner(team_a: str, team_b: str, features: dict[str, dict], rng: random.Random) -> str:
    p_a, p_draw, p_b = match_probs(team_a, team_b, features)
    if rng.random() < p_a + p_draw * 0.5:
        return team_a
    return team_b


def simulate_tournament(groups: dict[str, list[str]], features: dict[str, dict], simulations: int, seed: int):
    rng = random.Random(seed)
    group_wins = Counter()
    champions = Counter()
    portfolio_paths = []
    group_names = sorted(groups)

    for _ in range(simulations):
        standings_by_group = {}
        qualifiers = []
        third_place = []
        for group in group_names:
            winner, standings = simulate_group(groups[group], features, rng)
            group_wins[(group, winner)] += 1
            standings_by_group[group] = standings
            qualifiers.extend([standings[0][0], standings[1][0]])
            third_place.append(standings[2])

        best_thirds = sorted(third_place, key=lambda row: (row[1], row[2]), reverse=True)[:8]
        qualifiers.extend(team for team, _, _ in best_thirds)
        rng.shuffle(qualifiers)
        field = qualifiers[:32]
        while len(field) > 1:
            next_round = []
            for i in range(0, len(field), 2):
                next_round.append(pair_winner(field[i], field[i + 1], features, rng))
            rng.shuffle(next_round)
            field = next_round
        champions[field[0]] += 1

    group_prob = {(group, team): count / simulations for (group, team), count in group_wins.items()}
    champion_prob = {team: count / simulations for team, count in champions.items()}
    return group_prob, champion_prob


def conservative_probability(model_prob: float, market_prob: float, backtest_accuracy: float) -> float:
    alpha = max(0.0, min(1.0, (backtest_accuracy - 0.5) / 0.5))
    return alpha * model_prob + (1 - alpha) * market_prob


def kelly_fraction(probability: float, price: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    return max(0.0, (probability - price) / (1 - price))


def analyze_markets(
    markets: list[dict],
    group_prob: dict,
    champion_prob: dict,
    backtest_accuracy: float,
    bankroll: float,
    kelly_scale: float,
    max_fraction: float,
    min_edge: float,
    min_ev: float,
) -> list[dict]:
    rows = []
    for market in markets:
        if market["market_type"] == "group_winner":
            model_yes = group_prob.get((market["group"], market["team"]), 0.0)
        elif market["market_type"] == "outright_winner":
            model_yes = champion_prob.get(market["team"], 0.0)
        else:
            continue

        yes_prob = conservative_probability(model_yes, market["yes_price"], backtest_accuracy)
        no_prob = 1 - yes_prob
        candidates = [
            ("YES", yes_prob, market["yes_price"]),
            ("NO", no_prob, market["no_price"]),
        ]
        side, prob, price = max(candidates, key=lambda item: item[1] / item[2] - 1 if item[2] > 0 else -999)
        edge = prob - price
        ev_per_dollar = prob / price - 1 if price > 0 else 0
        full_kelly = kelly_fraction(prob, price)
        deploy_fraction = min(max_fraction, full_kelly * kelly_scale) if edge >= min_edge and ev_per_dollar >= min_ev else 0.0
        deploy_amount = bankroll * deploy_fraction
        rows.append(
            {
                **market,
                "model_probability": round(model_yes, 6),
                "calibrated_yes_probability": round(yes_prob, 6),
                "recommended_side": side if deploy_amount > 0 else "PASS",
                "trade_probability": round(prob, 6),
                "trade_price": round(price, 6),
                "edge": round(edge, 6),
                "ev_per_dollar": round(ev_per_dollar, 6),
                "full_kelly_fraction": round(full_kelly, 6),
                "deploy_fraction": round(deploy_fraction, 6),
                "deploy_amount": round(deploy_amount, 2),
                "max_loss": round(deploy_amount, 2),
                "profit_if_win": round(deploy_amount * (1 / price - 1), 2) if deploy_amount and price > 0 else 0.0,
            }
        )
    return sorted(rows, key=lambda row: (row["deploy_amount"], row["ev_per_dollar"]), reverse=True)


def enforce_portfolio_caps(trades: list[dict], bankroll: float, max_total_fraction: float, min_deploy: float) -> list[dict]:
    active_total = sum(trade["deploy_amount"] for trade in trades)
    total_cap = bankroll * max_total_fraction
    scale = min(1.0, total_cap / active_total) if active_total > 0 else 1.0
    capped = []
    for trade in trades:
        row = dict(trade)
        if row["deploy_amount"] > 0:
            row["deploy_amount"] = round(row["deploy_amount"] * scale, 2)
            if row["deploy_amount"] < min_deploy:
                row["deploy_amount"] = 0.0
                row["deploy_fraction"] = 0.0
                row["recommended_side"] = "PASS"
                row["max_loss"] = 0.0
                row["profit_if_win"] = 0.0
            else:
                row["deploy_fraction"] = round(row["deploy_amount"] / bankroll, 6)
                row["max_loss"] = row["deploy_amount"]
                row["profit_if_win"] = round(row["deploy_amount"] * (1 / row["trade_price"] - 1), 2)
        capped.append(row)
    return sorted(capped, key=lambda row: (row["deploy_amount"], row["ev_per_dollar"]), reverse=True)


def portfolio_risk(trades: list[dict], groups: dict[str, list[str]], features: dict[str, dict], simulations: int, seed: int) -> dict:
    active = [trade for trade in trades if trade["deploy_amount"] > 0]
    if not active:
        return {"active_trades": 0}

    rng = random.Random(seed + 1)
    returns = []
    for _ in range(simulations):
        group_winners = {}
        qualifiers = []
        third_place = []
        for group in sorted(groups):
            winner, standings = simulate_group(groups[group], features, rng)
            group_winners[group] = winner
            qualifiers.extend([standings[0][0], standings[1][0]])
            third_place.append(standings[2])
        qualifiers.extend(team for team, _, _ in sorted(third_place, key=lambda row: (row[1], row[2]), reverse=True)[:8])
        rng.shuffle(qualifiers)
        field = qualifiers[:32]
        while len(field) > 1:
            next_round = []
            for i in range(0, len(field), 2):
                next_round.append(pair_winner(field[i], field[i + 1], features, rng))
            rng.shuffle(next_round)
            field = next_round
        champion = field[0]

        pnl = 0.0
        for trade in active:
            if trade["market_type"] == "group_winner":
                yes_wins = group_winners.get(trade["group"]) == trade["team"]
            else:
                yes_wins = champion == trade["team"]
            side_wins = yes_wins if trade["recommended_side"] == "YES" else not yes_wins
            stake = trade["deploy_amount"]
            price = trade["trade_price"]
            pnl += stake * (1 / price - 1) if side_wins else -stake
        returns.append(pnl)

    returns.sort()
    mean = sum(returns) / len(returns)
    return {
        "active_trades": len(active),
        "total_deployed": round(sum(trade["deploy_amount"] for trade in active), 2),
        "expected_profit": round(mean, 2),
        "expected_return_on_deployed": round(mean / sum(trade["deploy_amount"] for trade in active), 6),
        "probability_of_loss": round(sum(1 for value in returns if value < 0) / len(returns), 6),
        "p05_profit": round(returns[int(0.05 * (len(returns) - 1))], 2),
        "median_profit": round(returns[int(0.50 * (len(returns) - 1))], 2),
        "p95_profit": round(returns[int(0.95 * (len(returns) - 1))], 2),
        "worst_case_simulated": round(returns[0], 2),
        "best_case_simulated": round(returns[-1], 2),
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze tradable Polymarket 2026 World Cup markets.")
    parser.add_argument("--bankroll", type=float, default=DEFAULT_BANKROLL)
    parser.add_argument("--simulations", type=int, default=20000)
    parser.add_argument("--max-markets", type=int, default=5000)
    parser.add_argument("--kelly-scale", type=float, default=0.25)
    parser.add_argument("--max-fraction", type=float, default=0.02)
    parser.add_argument("--max-total-fraction", type=float, default=0.25)
    parser.add_argument("--min-edge", type=float, default=0.02)
    parser.add_argument("--min-ev", type=float, default=0.03)
    parser.add_argument("--min-deploy", type=float, default=5.0)
    parser.add_argument("--seed", type=int, default=20260612)
    args = parser.parse_args()

    weights, backtest_accuracy = load_backtest_settings()
    raw_markets = fetch_polymarket_markets(args.max_markets)
    markets, groups = parse_world_cup_markets(raw_markets)
    teams = {market["team"] for market in markets}
    features = current_team_features(teams, weights)
    group_prob, champion_prob = simulate_tournament(groups, features, args.simulations, args.seed)
    trade_rows = analyze_markets(
        markets,
        group_prob,
        champion_prob,
        backtest_accuracy,
        args.bankroll,
        args.kelly_scale,
        args.max_fraction,
        args.min_edge,
        args.min_ev,
    )
    trade_rows = enforce_portfolio_caps(trade_rows, args.bankroll, args.max_total_fraction, args.min_deploy)
    risk = portfolio_risk(trade_rows, groups, features, args.simulations, args.seed)

    write_csv(OUT_DIR / "polymarket_world_cup_trade_recommendations.csv", trade_rows)
    summary = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": "https://gamma-api.polymarket.com/markets",
        "backtest_accuracy_used": backtest_accuracy,
        "probability_calibration": "calibrated_probability = alpha * model_probability + (1 - alpha) * polymarket_price, alpha=(accuracy-0.5)/0.5",
        "weights": weights,
        "simulations": args.simulations,
        "bankroll": args.bankroll,
        "kelly_scale": args.kelly_scale,
        "max_fraction_per_trade": args.max_fraction,
        "max_total_fraction": args.max_total_fraction,
        "min_edge": args.min_edge,
        "min_ev": args.min_ev,
        "min_deploy": args.min_deploy,
        "markets_analyzed": len(trade_rows),
        "groups_detected": groups,
        "portfolio_risk": risk,
    }
    (OUT_DIR / "polymarket_world_cup_portfolio_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("Polymarket World Cup Analyzer")
    print(f"Markets analyzed: {len(trade_rows)}")
    print(f"Groups detected: {len(groups)}")
    print(f"Backtest accuracy used: {backtest_accuracy:.3%}")
    print(f"Active trades: {risk.get('active_trades', 0)}")
    if risk.get("active_trades", 0):
        print(f"Total deployed: ${risk['total_deployed']:.2f}")
        print(f"Expected profit: ${risk['expected_profit']:.2f}")
        print(f"Probability of loss: {risk['probability_of_loss']:.2%}")
        print(f"5th/50th/95th pct profit: ${risk['p05_profit']:.2f} / ${risk['median_profit']:.2f} / ${risk['p95_profit']:.2f}")
    print(f"Wrote {OUT_DIR / 'polymarket_world_cup_trade_recommendations.csv'}")
    print(f"Wrote {OUT_DIR / 'polymarket_world_cup_portfolio_summary.json'}")


if __name__ == "__main__":
    main()
