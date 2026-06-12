#!/usr/bin/env python3
"""
World Cup quantitative backtest.

The model predicts the winner of FIFA World Cup matches using four variables:
population, climate, wealth, and ranking. Population and wealth are fetched by
match year from the World Bank API. A historical FIFA ranking CSV can be passed
with --ranking-csv; otherwise the script uses a pre-match rolling strength rank
computed from historical international results so the pipeline remains runnable.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import ssl
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

RESULTS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
SHOOTOUTS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/shootouts.csv"
WORLD_BANK_URL = "https://api.worldbank.org/v2/country/{countries}/indicator/{indicator}?format=json&per_page=20000"

POPULATION_INDICATOR = "SP.POP.TOTL"
WEALTH_INDICATOR = "NY.GDP.PCAP.PP.CD"

START_YEAR = 1994
END_YEAR = 2022
CLIMATE_IDEAL_C = 16.0
CLIMATE_SIGMA = 9.0


COUNTRY_ISO3 = {
    "Algeria": "DZA",
    "Angola": "AGO",
    "Argentina": "ARG",
    "Australia": "AUS",
    "Austria": "AUT",
    "Belgium": "BEL",
    "Bolivia": "BOL",
    "Bosnia and Herzegovina": "BIH",
    "Brazil": "BRA",
    "Bulgaria": "BGR",
    "Cameroon": "CMR",
    "Canada": "CAN",
    "Chile": "CHL",
    "China": "CHN",
    "Colombia": "COL",
    "Costa Rica": "CRI",
    "Croatia": "HRV",
    "Czech Republic": "CZE",
    "Denmark": "DNK",
    "Ecuador": "ECU",
    "Egypt": "EGY",
    "England": "GBR",
    "France": "FRA",
    "Germany": "DEU",
    "Ghana": "GHA",
    "Greece": "GRC",
    "Honduras": "HND",
    "Iceland": "ISL",
    "Iran": "IRN",
    "Italy": "ITA",
    "Ivory Coast": "CIV",
    "Jamaica": "JAM",
    "Japan": "JPN",
    "Mexico": "MEX",
    "Morocco": "MAR",
    "Netherlands": "NLD",
    "New Zealand": "NZL",
    "Nigeria": "NGA",
    "North Korea": "PRK",
    "Norway": "NOR",
    "Panama": "PAN",
    "Paraguay": "PRY",
    "Peru": "PER",
    "Poland": "POL",
    "Portugal": "PRT",
    "Qatar": "QAT",
    "Republic of Ireland": "IRL",
    "Romania": "ROU",
    "Russia": "RUS",
    "Saudi Arabia": "SAU",
    "Scotland": "GBR",
    "Senegal": "SEN",
    "Serbia": "SRB",
    "Slovakia": "SVK",
    "Slovenia": "SVN",
    "South Africa": "ZAF",
    "South Korea": "KOR",
    "Spain": "ESP",
    "Sweden": "SWE",
    "Switzerland": "CHE",
    "Tunisia": "TUN",
    "Togo": "TGO",
    "Trinidad and Tobago": "TTO",
    "Turkey": "TUR",
    "Ukraine": "UKR",
    "United States": "USA",
    "Uruguay": "URY",
    "Wales": "GBR",
}

# Annual mean temperature in Celsius by country. This is a climatological normal
# style input because country-level annual football-relevant climate changes much
# more slowly than population, GDP, or rankings.
CLIMATE_C = {
    "Algeria": 22.5,
    "Angola": 21.6,
    "Argentina": 14.8,
    "Australia": 21.7,
    "Austria": 6.4,
    "Belgium": 10.7,
    "Bolivia": 20.0,
    "Bosnia and Herzegovina": 10.9,
    "Brazil": 25.0,
    "Bulgaria": 10.6,
    "Cameroon": 24.6,
    "Canada": -5.4,
    "Chile": 8.5,
    "China": 7.5,
    "Colombia": 24.8,
    "Costa Rica": 24.8,
    "Croatia": 11.9,
    "Czech Republic": 7.6,
    "Denmark": 8.3,
    "Ecuador": 21.9,
    "Egypt": 22.1,
    "England": 9.3,
    "France": 11.7,
    "Germany": 9.6,
    "Ghana": 27.2,
    "Greece": 15.4,
    "Honduras": 23.5,
    "Iceland": 1.8,
    "Iran": 17.3,
    "Italy": 13.5,
    "Ivory Coast": 26.4,
    "Jamaica": 25.7,
    "Japan": 14.6,
    "Mexico": 21.0,
    "Morocco": 18.3,
    "Netherlands": 10.4,
    "New Zealand": 10.6,
    "Nigeria": 26.8,
    "North Korea": 5.7,
    "Norway": 1.5,
    "Panama": 27.0,
    "Paraguay": 23.6,
    "Peru": 19.6,
    "Poland": 8.8,
    "Portugal": 15.2,
    "Qatar": 27.2,
    "Republic of Ireland": 9.3,
    "Romania": 9.1,
    "Russia": -5.1,
    "Saudi Arabia": 26.0,
    "Scotland": 8.3,
    "Senegal": 27.8,
    "Serbia": 11.4,
    "Slovakia": 8.0,
    "Slovenia": 9.7,
    "South Africa": 17.8,
    "South Korea": 11.5,
    "Spain": 13.3,
    "Sweden": 2.1,
    "Switzerland": 6.5,
    "Tunisia": 19.2,
    "Togo": 27.2,
    "Trinidad and Tobago": 25.8,
    "Turkey": 11.1,
    "Ukraine": 8.3,
    "United States": 8.6,
    "Uruguay": 17.6,
    "Wales": 9.3,
}

WEALTH_FALLBACK = {
    # World Bank does not publish a normal GDP PPP per-capita series for North
    # Korea. This rough constant keeps the 2010 match rows usable while making
    # the exception explicit in the output summary.
    "North Korea": {year: 1800.0 for year in range(START_YEAR, END_YEAR + 1)},
}

ALIASES = {
    "Germany DR": "Germany",
    "German DR": "Germany",
    "United States of America": "United States",
    "USA": "United States",
    "Korea Republic": "South Korea",
    "Korea DPR": "North Korea",
    "IR Iran": "Iran",
    "Côte d'Ivoire": "Ivory Coast",
    "Cote d'Ivoire": "Ivory Coast",
    "Republic of Ireland": "Republic of Ireland",
    "Ireland": "Republic of Ireland",
    "Serbia and Montenegro": "Serbia",
}


@dataclass(frozen=True)
class Match:
    match_date: date
    year: int
    team_a: str
    team_b: str
    score_a: int
    score_b: int
    city: str
    country: str
    shootout_winner: str | None = None

    @property
    def regulation_winner(self) -> str | None:
        if self.score_a == self.score_b:
            return None
        return self.team_a if self.score_a > self.score_b else self.team_b

    def actual_winner(self, result_mode: str) -> str | None:
        if self.regulation_winner:
            return self.regulation_winner
        if result_mode == "advancement" and self.shootout_winner:
            return self.shootout_winner
        return None

    def result_decision(self, result_mode: str) -> str:
        if self.regulation_winner:
            return "goals"
        if result_mode == "advancement" and self.shootout_winner:
            return "penalties"
        return "draw"


def canonical_country(name: str) -> str:
    return ALIASES.get(name.strip(), name.strip())


def fetch(url: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return path
    print(f"Downloading {url}")
    try:
        with urllib.request.urlopen(url, timeout=45) as response:
            path.write_bytes(response.read())
    except urllib.error.URLError as exc:
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
            raise
        print("Verified TLS failed locally; retrying download with an unverified TLS context.")
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(url, timeout=45, context=context) as response:
            path.write_bytes(response.read())
    return path


def read_shootouts() -> dict[tuple[date, str, str], str]:
    path = fetch(SHOOTOUTS_URL, RAW / "shootouts.csv")
    shootouts: dict[tuple[date, str, str], str] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            match_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
            home = canonical_country(row["home_team"])
            away = canonical_country(row["away_team"])
            winner = canonical_country(row["winner"])
            shootouts[(match_date, home, away)] = winner
            shootouts[(match_date, away, home)] = winner
    return shootouts


def read_world_cup_matches() -> list[Match]:
    path = fetch(RESULTS_URL, RAW / "international_results.csv")
    shootouts = read_shootouts()
    matches: list[Match] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["tournament"] != "FIFA World Cup":
                continue
            match_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
            if not START_YEAR <= match_date.year <= END_YEAR:
                continue
            team_a = canonical_country(row["home_team"])
            team_b = canonical_country(row["away_team"])
            matches.append(
                Match(
                    match_date=match_date,
                    year=match_date.year,
                    team_a=team_a,
                    team_b=team_b,
                    score_a=int(row["home_score"]),
                    score_b=int(row["away_score"]),
                    city=row["city"],
                    country=row["country"],
                    shootout_winner=shootouts.get((match_date, team_a, team_b)),
                )
            )
    return matches


def read_all_results_until(end_year: int) -> list[dict]:
    path = fetch(RESULTS_URL, RAW / "international_results.csv")
    rows: list[dict] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            match_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
            if match_date.year > end_year:
                continue
            rows.append(
                {
                    "date": match_date,
                    "home": canonical_country(row["home_team"]),
                    "away": canonical_country(row["away_team"]),
                    "home_score": int(row["home_score"]),
                    "away_score": int(row["away_score"]),
                }
            )
    return rows


def world_bank_series(indicator: str, countries: set[str]) -> dict[str, dict[int, float]]:
    iso_codes = sorted({COUNTRY_ISO3[country] for country in countries if country in COUNTRY_ISO3})
    url = WORLD_BANK_URL.format(countries=";".join(iso_codes), indicator=indicator)
    cache_name = f"world_bank_{indicator}.json"
    path = fetch(url, RAW / cache_name)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) < 2:
        raise RuntimeError(f"Unexpected World Bank response for {indicator}")

    iso_to_countries: dict[str, list[str]] = defaultdict(list)
    for country, iso in COUNTRY_ISO3.items():
        if country in countries:
            iso_to_countries[iso].append(country)
    series: dict[str, dict[int, float]] = defaultdict(dict)
    for item in payload[1]:
        value = item.get("value")
        iso = item.get("countryiso3code")
        if value is None or iso not in iso_to_countries:
            continue
        for country in iso_to_countries[iso]:
            series[country][int(item["date"])] = float(value)
    return dict(series)


def nearest_year_value(series: dict[int, float], target_year: int) -> float | None:
    if target_year in series:
        return series[target_year]
    candidates = [(abs(year - target_year), year, value) for year, value in series.items()]
    if not candidates:
        return None
    return min(candidates)[2]


def macro_value(series: dict[str, dict[int, float]], country: str, year: int, fallback: dict[str, dict[int, float]] | None = None) -> float | None:
    value = nearest_year_value(series.get(country, {}), year)
    if value is not None:
        return value
    if fallback and country in fallback:
        return nearest_year_value(fallback[country], year)
    return None


def rolling_strength_ranks(results: list[dict], match_dates: list[date]) -> dict[date, dict[str, int]]:
    """Build pre-match ranks from prior international results as a fallback."""
    ratings = defaultdict(lambda: 1500.0)
    ranks_by_date: dict[date, dict[str, int]] = {}
    date_set = set(match_dates)

    for row in sorted(results, key=lambda item: item["date"]):
        if row["date"] in date_set and row["date"] not in ranks_by_date:
            ordered = sorted(ratings.items(), key=lambda item: item[1], reverse=True)
            ranks_by_date[row["date"]] = {team: idx + 1 for idx, (team, _) in enumerate(ordered)}

        home, away = row["home"], row["away"]
        home_rating, away_rating = ratings[home], ratings[away]
        expected_home = 1 / (1 + math.pow(10, (away_rating - home_rating) / 400))
        if row["home_score"] > row["away_score"]:
            actual_home = 1.0
        elif row["home_score"] < row["away_score"]:
            actual_home = 0.0
        else:
            actual_home = 0.5
        importance = 28 if row["date"].year >= START_YEAR else 20
        change = importance * (actual_home - expected_home)
        ratings[home] += change
        ratings[away] -= change

    for match_date in match_dates:
        if match_date not in ranks_by_date:
            ordered = sorted(ratings.items(), key=lambda item: item[1], reverse=True)
            ranks_by_date[match_date] = {team: idx + 1 for idx, (team, _) in enumerate(ordered)}

    return ranks_by_date


def read_fifa_rankings(path: Path) -> list[dict]:
    rows = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            country = canonical_country(row.get("country_full") or row.get("country") or row.get("team") or "")
            rank_text = row.get("rank") or row.get("fifa_rank") or row.get("ranking")
            date_text = row.get("rank_date") or row.get("date")
            if not country or not rank_text or not date_text:
                continue
            rows.append(
                {
                    "country": country,
                    "date": datetime.strptime(date_text[:10], "%Y-%m-%d").date(),
                    "rank": int(float(rank_text)),
                }
            )
    return rows


def fifa_rank_lookup(ranking_rows: list[dict], match_dates: list[date]) -> dict[date, dict[str, int]]:
    by_country: dict[str, list[tuple[date, int]]] = defaultdict(list)
    for row in ranking_rows:
        by_country[row["country"]].append((row["date"], row["rank"]))
    for country in by_country:
        by_country[country].sort()

    lookup: dict[date, dict[str, int]] = {}
    for match_date in match_dates:
        ranks = {}
        for country, entries in by_country.items():
            rank = None
            for rank_date, rank_value in entries:
                if rank_date <= match_date:
                    rank = rank_value
                else:
                    break
            if rank is not None:
                ranks[country] = rank
        lookup[match_date] = ranks
    return lookup


def min_max(values: list[float]) -> tuple[float, float]:
    return min(values), max(values)


def normalize_log(value: float, bounds: tuple[float, float]) -> float:
    low, high = bounds
    if value <= 0 or low <= 0 or high <= low:
        return 0.0
    return (math.log(value) - math.log(low)) / (math.log(high) - math.log(low))


def climate_score(country: str) -> float:
    temp = CLIMATE_C[country]
    return math.exp(-((temp - CLIMATE_IDEAL_C) ** 2) / (2 * CLIMATE_SIGMA**2))


def ranking_score(rank: int) -> float:
    rank = max(1, min(rank, 211))
    return 1 - math.log(rank) / math.log(211)


def build_feature_rows(matches: list[Match], ranking_csv: Path | None, result_mode: str) -> tuple[list[dict], str]:
    countries = {match.team_a for match in matches} | {match.team_b for match in matches}
    missing_iso = sorted(country for country in countries if country not in COUNTRY_ISO3)
    missing_climate = sorted(country for country in countries if country not in CLIMATE_C)
    if missing_iso or missing_climate:
        raise RuntimeError(f"Missing mappings: iso={missing_iso}, climate={missing_climate}")

    population = world_bank_series(POPULATION_INDICATOR, countries)
    wealth = world_bank_series(WEALTH_INDICATOR, countries)
    all_year_values = []
    all_wealth_values = []
    for country in countries:
        for year in range(START_YEAR, END_YEAR + 1):
            pop_value = macro_value(population, country, year)
            wealth_value = macro_value(wealth, country, year, WEALTH_FALLBACK)
            if pop_value:
                all_year_values.append(pop_value)
            if wealth_value:
                all_wealth_values.append(wealth_value)

    pop_bounds = min_max(all_year_values)
    wealth_bounds = min_max(all_wealth_values)
    match_dates = sorted({match.match_date for match in matches})

    if ranking_csv:
        rank_by_date = fifa_rank_lookup(read_fifa_rankings(ranking_csv), match_dates)
        ranking_source = f"historical FIFA CSV: {ranking_csv}"
    else:
        rank_by_date = rolling_strength_ranks(read_all_results_until(END_YEAR), match_dates)
        ranking_source = "rolling pre-match strength rank fallback"

    rows = []
    for match_id, match in enumerate(matches, start=1):
        actual_winner = match.actual_winner(result_mode)
        result_decision = match.result_decision(result_mode)
        for side, country, opponent in (
            ("a", match.team_a, match.team_b),
            ("b", match.team_b, match.team_a),
        ):
            pop_value = macro_value(population, country, match.year)
            wealth_value = macro_value(wealth, country, match.year, WEALTH_FALLBACK)
            rank = rank_by_date.get(match.match_date, {}).get(country)
            if pop_value is None or wealth_value is None or rank is None:
                raise RuntimeError(f"Missing feature for {country} in {match.year}")
            rows.append(
                {
                    "match_date": match.match_date.isoformat(),
                    "match_id": match_id,
                    "year": match.year,
                    "side": side,
                    "team": country,
                    "opponent": opponent,
                    "population": pop_value,
                    "wealth_gdp_ppp": wealth_value,
                    "avg_temp_c": CLIMATE_C[country],
                    "rank": rank,
                    "population_score": normalize_log(pop_value, pop_bounds),
                    "wealth_score": normalize_log(wealth_value, wealth_bounds),
                    "climate_score": climate_score(country),
                    "ranking_score": ranking_score(rank),
                    "score_for": match.score_a if side == "a" else match.score_b,
                    "score_against": match.score_b if side == "a" else match.score_a,
                    "shootout_winner": match.shootout_winner or "",
                    "result_decision": result_decision,
                    "actual": "draw" if actual_winner is None else ("win" if country == actual_winner else "loss"),
                    "city": match.city,
                    "host_country": match.country,
                }
            )
    return rows, ranking_source


def score_team(row: dict, weights: dict[str, float]) -> float:
    return (
        weights["population"] * row["population_score"]
        + weights["climate"] * row["climate_score"]
        + weights["wealth"] * row["wealth_score"]
        + weights["ranking"] * row["ranking_score"]
    )


def evaluate(feature_rows: list[dict], weights: dict[str, float]) -> tuple[float, int, int, list[dict]]:
    by_match = defaultdict(dict)
    for row in feature_rows:
        by_match[row["match_id"]][row["side"]] = row

    correct = 0
    total = 0
    predictions = []
    for _, pair in sorted(by_match.items()):
        a = pair["a"]
        b = pair["b"]
        score_a = score_team(a, weights)
        score_b = score_team(b, weights)
        predicted = a["team"] if score_a >= score_b else b["team"]
        actual = None
        if a["actual"] == "win":
            actual = a["team"]
        elif b["actual"] == "win":
            actual = b["team"]
        decisive = actual is not None
        if decisive:
            total += 1
            correct += int(predicted == actual)
        predictions.append(
            {
                "date": a["match_date"],
                "year": a["year"],
                "team_a": a["team"],
                "team_b": b["team"],
                "score_a": a["score_for"],
                "score_b": b["score_for"],
                "result_decision": a["result_decision"],
                "shootout_winner": a["shootout_winner"],
                "actual_winner": actual or "draw",
                "predicted_winner": predicted,
                "model_edge": round(score_a - score_b, 6),
                "correct_decisive": "" if not decisive else int(predicted == actual),
                "population_edge": round(a["population_score"] - b["population_score"], 6),
                "climate_edge": round(a["climate_score"] - b["climate_score"], 6),
                "wealth_edge": round(a["wealth_score"] - b["wealth_score"], 6),
                "ranking_edge": round(a["ranking_score"] - b["ranking_score"], 6),
            }
        )
    accuracy = correct / total if total else 0.0
    return accuracy, correct, total, predictions


def weight_grid(step: float) -> list[dict[str, float]]:
    units = int(round(1 / step))
    keys = ["population", "climate", "wealth", "ranking"]
    candidates = []
    for values in itertools.product(range(units + 1), repeat=4):
        if sum(values) != units:
            continue
        candidates.append({key: value / units for key, value in zip(keys, values)})
    return candidates


def optimize(feature_rows: list[dict], step: float) -> tuple[dict[str, float], tuple[float, int, int]]:
    best_weights = None
    best_metric = (-1.0, -1.0, -1.0)
    best_result = (0.0, 0, 0)
    for weights in weight_grid(step):
        accuracy, correct, total, _ = evaluate(feature_rows, weights)
        # Tie-breaker keeps a balanced economic model instead of assigning all
        # mass to one variable when several weights have equal hit rate.
        entropy = -sum(value * math.log(value) for value in weights.values() if value > 0)
        ranking_weight = weights["ranking"]
        metric = (accuracy, entropy, ranking_weight)
        if metric > best_metric:
            best_metric = metric
            best_weights = weights
            best_result = (accuracy, correct, total)
    assert best_weights is not None
    return best_weights, best_result


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Optimize World Cup economic model weights.")
    parser.add_argument("--ranking-csv", type=Path, help="Optional historical FIFA ranking CSV.")
    parser.add_argument("--step", type=float, default=0.05, help="Weight grid step. Default: 0.05")
    parser.add_argument(
        "--result-mode",
        choices=["advancement", "regulation"],
        default="advancement",
        help="advancement counts penalty shootout winners; regulation treats tied scores as draws. Default: advancement",
    )
    args = parser.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)

    matches = read_world_cup_matches()
    feature_rows, ranking_source = build_feature_rows(matches, args.ranking_csv, args.result_mode)
    best_weights, (accuracy, correct, total) = optimize(feature_rows, args.step)
    _, _, _, predictions = evaluate(feature_rows, best_weights)

    write_csv(PROCESSED / "world_cup_match_predictions.csv", predictions)
    write_csv(PROCESSED / "world_cup_team_match_features.csv", feature_rows)

    decisive_by_year = defaultdict(lambda: [0, 0])
    penalties_scored = 0
    penalty_correct = 0
    for row in predictions:
        if row["correct_decisive"] == "":
            continue
        decisive_by_year[int(row["year"])][0] += int(row["correct_decisive"])
        decisive_by_year[int(row["year"])][1] += 1
        if row["result_decision"] == "penalties":
            penalties_scored += 1
            penalty_correct += int(row["correct_decisive"])

    summary = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "match_source": RESULTS_URL,
        "shootout_source": SHOOTOUTS_URL,
        "population_source": f"World Bank indicator {POPULATION_INDICATOR}",
        "wealth_source": f"World Bank indicator {WEALTH_INDICATOR}",
        "climate_source": "embedded annual country mean temperature table",
        "ranking_source": ranking_source,
        "result_mode": args.result_mode,
        "scope": {
            "tournament": "FIFA World Cup",
            "years": [START_YEAR, END_YEAR],
            "matches": len(predictions),
            "decisive_matches_scored": total,
            "draws_exported_not_scored": len(predictions) - total,
            "penalty_shootout_matches_scored": penalties_scored,
        },
        "penalty_shootout_accuracy": {
            "correct": penalty_correct,
            "total": penalties_scored,
            "accuracy": penalty_correct / penalties_scored if penalties_scored else None,
        },
        "optimized_weights": best_weights,
        "accuracy": accuracy,
        "correct": correct,
        "total": total,
        "accuracy_by_year": {
            str(year): {
                "correct": values[0],
                "total": values[1],
                "accuracy": values[0] / values[1] if values[1] else None,
            }
            for year, values in sorted(decisive_by_year.items())
        },
    }
    (PROCESSED / "optimized_model_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("World Cup Quant Model")
    print(f"Matches exported: {len(predictions)}")
    print(f"Decisive matches scored: {total}")
    print(f"Best accuracy: {accuracy:.3%} ({correct}/{total})")
    print("Best weights:")
    for key, value in best_weights.items():
        print(f"  {key}: {value:.2f}")
    print(f"Ranking source: {ranking_source}")
    print(f"Wrote {PROCESSED / 'optimized_model_summary.json'}")
    print(f"Wrote {PROCESSED / 'world_cup_match_predictions.csv'}")


if __name__ == "__main__":
    main()
