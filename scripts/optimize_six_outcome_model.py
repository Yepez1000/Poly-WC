#!/usr/bin/env python3
"""
Optimize the six-outcome deterministic World Cup model.

The six outcomes are overlapping tradable propositions:
  - Team A win
  - Draw
  - Team B win
  - Team A loss / Team A does not win
  - No draw
  - Team B loss / Team B does not win

The optimizer searches economic weights and match-probability shape parameters,
then picks exactly one outcome per match: the highest modeled probability. To
reduce overfit, selection uses tournament-year mean accuracy penalized by
tournament-year volatility, not just raw pooled accuracy.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from world_cup_quant_model import (  # noqa: E402
    PROCESSED,
    build_feature_rows,
    read_world_cup_matches,
    score_team,
)


OUTCOMES = ["team_a_win", "draw", "team_b_win", "team_a_loss", "no_draw", "team_b_loss"]
EDGE_BINS = [0.00, 0.03, 0.06, 0.10, 0.15, 0.22, 0.35, float("inf")]


def simplex_weights(step: float) -> list[dict[str, float]]:
    units = int(round(1 / step))
    keys = ["population", "climate", "wealth", "ranking"]
    weights = []
    for values in itertools.product(range(units + 1), repeat=4):
        if sum(values) != units:
            continue
        weights.append({key: value / units for key, value in zip(keys, values)})
    return weights


def build_matches(feature_rows: list[dict]) -> list[dict]:
    grouped = defaultdict(dict)
    for row in feature_rows:
        grouped[row["match_id"]][row["side"]] = row

    matches = []
    raw_matches = []
    for match_id, pair in sorted(grouped.items()):
        a = pair["a"]
        b = pair["b"]
        if a["score_for"] > b["score_for"]:
            actual = "team_a_win"
        elif a["score_for"] < b["score_for"]:
            actual = "team_b_win"
        else:
            actual = "draw"
        raw_matches.append(
            {
                "match_id": match_id,
                "date": a["match_date"],
                "year": int(a["year"]),
                "team_a": a["team"],
                "team_b": b["team"],
                "score_a": int(a["score_for"]),
                "score_b": int(b["score_for"]),
                "actual": actual,
                "a": a,
                "b": b,
            }
        )

    by_year = defaultdict(list)
    for match in raw_matches:
        by_year[match["year"]].append(match)
    for year, year_matches in by_year.items():
        year_matches.sort(key=lambda row: (row["date"], row["match_id"]))
        group_stage_count = 36 if year == 1994 else 48
        for index, match in enumerate(year_matches, start=1):
            match["match_number_in_tournament"] = index
            match["stage"] = "group" if index <= group_stage_count else "knockout"
            match["is_knockout"] = index > group_stage_count
            matches.append(match)
    return matches


def compile_matches(matches: list[dict]) -> list[dict]:
    compiled = []
    for match in matches:
        a = match["a"]
        b = match["b"]
        compiled.append(
            {
                "year": match["year"],
                "actual": match["actual"],
                "is_knockout": match["is_knockout"],
                "population_edge": a["population_score"] - b["population_score"],
                "climate_edge": a["climate_score"] - b["climate_score"],
                "wealth_edge": a["wealth_score"] - b["wealth_score"],
                "ranking_edge": a["ranking_score"] - b["ranking_score"],
            }
        )
    return compiled


def outcome_truth(actual: str) -> dict[str, bool]:
    return {
        "team_a_win": actual == "team_a_win",
        "draw": actual == "draw",
        "team_b_win": actual == "team_b_win",
        "team_a_loss": actual != "team_a_win",
        "no_draw": actual != "draw",
        "team_b_loss": actual != "team_b_win",
    }


def probabilities(edge: float, params: dict[str, float]) -> dict[str, float]:
    no_draw_a = 1 / (1 + math.exp(-params["logistic_slope"] * edge))
    raw_draw = params["draw_baseline"] * math.exp(-params["draw_decay"] * abs(edge))
    draw = min(params["draw_max"], max(params["draw_min"], raw_draw))
    team_a_win = (1 - draw) * no_draw_a
    team_b_win = (1 - draw) * (1 - no_draw_a)
    return {
        "team_a_win": team_a_win,
        "draw": draw,
        "team_b_win": team_b_win,
        "team_a_loss": 1 - team_a_win,
        "no_draw": 1 - draw,
        "team_b_loss": 1 - team_b_win,
    }


def deterministic_pick(probs: dict[str, float]) -> str:
    # Favor narrower outcomes only when probabilities tie exactly.
    tie_order = {
        "team_a_win": 0,
        "draw": 1,
        "team_b_win": 2,
        "team_a_loss": 3,
        "no_draw": 4,
        "team_b_loss": 5,
    }
    return max(OUTCOMES, key=lambda outcome: (probs[outcome], -tie_order[outcome]))


def skip_reason(pick: str, match: dict, params: dict[str, float]) -> str | None:
    if params.get("exclude_draw_and_no_draw") and pick in {"draw", "no_draw"}:
        return "excluded_draw_or_no_draw"
    if params.get("skip_no_draw_knockout") and pick == "no_draw" and match["is_knockout"]:
        return "no_draw_knockout"
    return None


def edge_bin(abs_edge: float) -> str:
    for low, high in zip(EDGE_BINS, EDGE_BINS[1:]):
        if low <= abs_edge < high:
            if high == float("inf"):
                return f"{low:.2f}+"
            return f"{low:.2f}-{high:.2f}"
    return "unknown"


def edge_from_compiled(match: dict, weights: dict[str, float]) -> float:
    return (
        weights["population"] * match["population_edge"]
        + weights["climate"] * match["climate_edge"]
        + weights["wealth"] * match["wealth_edge"]
        + weights["ranking"] * match["ranking_edge"]
    )


def evaluate_compiled(compiled_matches: list[dict], params: dict[str, float]) -> dict:
    correct = 0
    scored = 0
    by_year = defaultdict(lambda: [0, 0])
    outcome_counts = Counter()
    skipped_counts = Counter()

    for match in compiled_matches:
        edge = edge_from_compiled(match, params["weights"])
        probs = probabilities(edge, params)
        pick = deterministic_pick(probs)
        reason = skip_reason(pick, match, params)
        if reason:
            skipped_counts[reason] += 1
            continue
        is_correct = outcome_truth(match["actual"])[pick]
        correct += int(is_correct)
        scored += 1
        by_year[match["year"]][0] += int(is_correct)
        by_year[match["year"]][1] += 1
        outcome_counts[pick] += 1

    yearly_accuracy = {
        year: values[0] / values[1]
        for year, values in by_year.items()
        if values[1]
    }
    if not yearly_accuracy:
        return {
            "correct": 0,
            "total_matches": len(compiled_matches),
            "scored_predictions": 0,
            "skipped_predictions": len(compiled_matches),
            "skip_reasons": dict(skipped_counts),
            "pooled_accuracy": 0.0,
            "mean_year_accuracy": 0.0,
            "stdev_year_accuracy": 0.0,
            "robust_score": 0.0,
            "accuracy_by_year": {},
            "predicted_outcome_counts": {},
        }
    year_values = list(yearly_accuracy.values())
    pooled_accuracy = correct / scored if scored else 0.0
    mean_year_accuracy = statistics.mean(year_values)
    stdev_year_accuracy = statistics.pstdev(year_values)
    return {
        "correct": correct,
        "total_matches": len(compiled_matches),
        "scored_predictions": scored,
        "skipped_predictions": len(compiled_matches) - scored,
        "skip_reasons": dict(skipped_counts),
        "pooled_accuracy": pooled_accuracy,
        "mean_year_accuracy": mean_year_accuracy,
        "stdev_year_accuracy": stdev_year_accuracy,
        "robust_score": mean_year_accuracy - params["stdev_penalty"] * stdev_year_accuracy,
        "accuracy_by_year": {
            str(year): {
                "correct": by_year[year][0],
                "total": by_year[year][1],
                "accuracy": yearly_accuracy[year],
            }
            for year in sorted(by_year)
        },
        "predicted_outcome_counts": dict(outcome_counts),
    }


def evaluate(matches: list[dict], params: dict[str, float]) -> tuple[dict, list[dict]]:
    correct = 0
    scored = 0
    by_year = defaultdict(lambda: [0, 0])
    outcome_counts = Counter()
    skipped_counts = Counter()
    edge_bins = defaultdict(lambda: [0, 0])
    rows = []

    for match in matches:
        score_a = score_team(match["a"], params["weights"])
        score_b = score_team(match["b"], params["weights"])
        edge = score_a - score_b
        probs = probabilities(edge, params)
        pick = deterministic_pick(probs)
        reason = skip_reason(pick, match, params)
        truth = outcome_truth(match["actual"])
        is_correct = False if reason else truth[pick]
        if reason:
            skipped_counts[reason] += 1
            scored_flag = 0
        else:
            scored_flag = 1
            correct += int(is_correct)
            scored += 1
            by_year[match["year"]][0] += int(is_correct)
            by_year[match["year"]][1] += 1
            outcome_counts[pick] += 1
            edge_bins[edge_bin(abs(edge))][0] += int(is_correct)
            edge_bins[edge_bin(abs(edge))][1] += 1
        rows.append(
            {
                "date": match["date"],
                "year": match["year"],
                "team_a": match["team_a"],
                "team_b": match["team_b"],
                "score_a": match["score_a"],
                "score_b": match["score_b"],
                "stage": match["stage"],
                "match_number_in_tournament": match["match_number_in_tournament"],
                "actual_regular_time": match["actual"],
                "predicted_outcome": pick,
                "prediction_skipped": int(bool(reason)),
                "skip_reason": reason or "",
                "scored": scored_flag,
                "correct": "" if reason else int(is_correct),
                "predicted_probability": round(probs[pick], 6),
                "team_a_win_probability": round(probs["team_a_win"], 6),
                "draw_probability": round(probs["draw"], 6),
                "team_b_win_probability": round(probs["team_b_win"], 6),
                "team_a_loss_probability": round(probs["team_a_loss"], 6),
                "no_draw_probability": round(probs["no_draw"], 6),
                "team_b_loss_probability": round(probs["team_b_loss"], 6),
                "rating_edge": round(edge, 6),
            }
        )

    yearly_accuracy = {
        year: values[0] / values[1]
        for year, values in by_year.items()
        if values[1]
    }
    if not yearly_accuracy:
        metrics = {
            "correct": 0,
            "total_matches": len(matches),
            "scored_predictions": 0,
            "skipped_predictions": len(matches),
            "skip_reasons": dict(skipped_counts),
            "pooled_accuracy": 0.0,
            "mean_year_accuracy": 0.0,
            "stdev_year_accuracy": 0.0,
            "robust_score": 0.0,
            "accuracy_by_year": {},
            "predicted_outcome_counts": {},
            "accuracy_by_abs_rating_edge": {},
        }
        return metrics, rows
    year_values = list(yearly_accuracy.values())
    pooled_accuracy = correct / len(matches)
    pooled_accuracy = correct / scored if scored else 0.0
    mean_year_accuracy = statistics.mean(year_values)
    stdev_year_accuracy = statistics.pstdev(year_values)
    metrics = {
        "correct": correct,
        "total_matches": len(matches),
        "scored_predictions": scored,
        "skipped_predictions": len(matches) - scored,
        "skip_reasons": dict(skipped_counts),
        "pooled_accuracy": pooled_accuracy,
        "mean_year_accuracy": mean_year_accuracy,
        "stdev_year_accuracy": stdev_year_accuracy,
        "robust_score": mean_year_accuracy - params["stdev_penalty"] * stdev_year_accuracy,
        "accuracy_by_year": {
            str(year): {
                "correct": by_year[year][0],
                "total": by_year[year][1],
                "accuracy": yearly_accuracy[year],
            }
            for year in sorted(by_year)
        },
        "predicted_outcome_counts": dict(outcome_counts),
        "accuracy_by_abs_rating_edge": {
            label: {
                "correct": values[0],
                "total": values[1],
                "accuracy": values[0] / values[1] if values[1] else None,
            }
            for label, values in sorted(edge_bins.items())
        },
    }
    return metrics, rows


def candidate_params(weight_step: float, stdev_penalty: float):
    for weights in simplex_weights(weight_step):
        for draw_baseline in [0.18, 0.22, 0.26, 0.30]:
            for draw_decay in [1.0, 2.5, 4.0]:
                for draw_min in [0.06, 0.10]:
                    for draw_max in [0.24, 0.30, 0.36]:
                        if draw_min >= draw_max or draw_baseline > draw_max:
                            continue
                        for logistic_slope in [4.0, 7.0, 10.0, 13.0]:
                            yield {
                                "weights": weights,
                                "draw_baseline": draw_baseline,
                                "draw_decay": draw_decay,
                                "draw_min": draw_min,
                                "draw_max": draw_max,
                                "logistic_slope": logistic_slope,
                                "stdev_penalty": stdev_penalty,
                            }


def optimize(
    matches: list[dict],
    weight_step: float,
    stdev_penalty: float,
    skip_no_draw_knockout: bool,
    exclude_draw_and_no_draw: bool,
    min_coverage: float,
) -> tuple[dict, dict, list[dict]]:
    compiled_matches = compile_matches(matches)
    best_params = None
    best_metrics = None
    best_key = None
    searched = 0

    for params in candidate_params(weight_step, stdev_penalty):
        searched += 1
        params["skip_no_draw_knockout"] = skip_no_draw_knockout
        params["exclude_draw_and_no_draw"] = exclude_draw_and_no_draw
        metrics = evaluate_compiled(compiled_matches, params)
        if metrics["scored_predictions"] == 0:
            continue
        coverage = metrics["scored_predictions"] / metrics["total_matches"]
        if coverage < min_coverage:
            continue
        entropy = -sum(value * math.log(value) for value in params["weights"].values() if value > 0)
        broad_share = (
            metrics["predicted_outcome_counts"].get("team_a_loss", 0)
            + metrics["predicted_outcome_counts"].get("team_b_loss", 0)
            + metrics["predicted_outcome_counts"].get("no_draw", 0)
        ) / metrics["scored_predictions"]
        key = (
            metrics["robust_score"],
            metrics["mean_year_accuracy"],
            metrics["pooled_accuracy"],
            -metrics["stdev_year_accuracy"],
            coverage,
            -broad_share,
            entropy,
        )
        if best_key is None or key > best_key:
            best_key = key
            best_params = params
            best_metrics = metrics

    assert best_params is not None and best_metrics is not None
    best_metrics["candidates_searched"] = searched
    final_metrics, final_rows = evaluate(matches, best_params)
    final_metrics["candidates_searched"] = searched
    return best_params, final_metrics, final_rows


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Optimize deterministic six-outcome World Cup model.")
    parser.add_argument("--ranking-csv", type=Path, help="Optional historical FIFA ranking CSV.")
    parser.add_argument("--weight-step", type=float, default=0.10)
    parser.add_argument("--stdev-penalty", type=float, default=0.35)
    parser.add_argument("--skip-no-draw-knockout", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--exclude-draw-and-no-draw", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--min-coverage", type=float, default=0.75)
    args = parser.parse_args()

    matches = read_world_cup_matches()
    feature_rows, ranking_source = build_feature_rows(matches, args.ranking_csv, "regulation")
    match_rows = build_matches(feature_rows)
    params, metrics, prediction_rows = optimize(
        match_rows,
        args.weight_step,
        args.stdev_penalty,
        args.skip_no_draw_knockout,
        args.exclude_draw_and_no_draw,
        args.min_coverage,
    )

    predictions_path = PROCESSED / "six_outcome_backtest_predictions.csv"
    summary_path = PROCESSED / "six_outcome_optimized_summary.json"
    write_csv(predictions_path, prediction_rows)
    summary = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "scope": {
            "tournament": "FIFA World Cup",
            "years": [min(row["year"] for row in match_rows), max(row["year"] for row in match_rows)],
            "matches": len(match_rows),
            "result_mode": "regular-time; penalty shootouts count as draws",
            "stage_inference": "1994: first 36 matches group stage; 1998-2022: first 48 matches group stage; later matches knockout",
        },
        "prediction_filters": {
            "skip_no_draw_knockout": args.skip_no_draw_knockout,
            "exclude_draw_and_no_draw": args.exclude_draw_and_no_draw,
        },
        "ranking_source": ranking_source,
        "anti_overfit_method": (
            "Parameters selected by tournament-year mean accuracy penalized by "
            "tournament-year accuracy volatility; tie-breaks penalize broad outcome overuse."
        ),
        "optimized_parameters": {
            "economic_weights": params["weights"],
            "draw_baseline": params["draw_baseline"],
            "draw_decay": params["draw_decay"],
            "draw_min": params["draw_min"],
            "draw_max": params["draw_max"],
            "logistic_slope": params["logistic_slope"],
        },
        "objective": {
            "stdev_penalty": args.stdev_penalty,
            "weight_step": args.weight_step,
            "candidates_searched": metrics["candidates_searched"],
            "min_coverage": args.min_coverage,
        },
        "metrics": metrics,
        "outcome_definitions": {
            "team_a_win": "Team A wins in regular time",
            "draw": "Regular-time draw",
            "team_b_win": "Team B wins in regular time",
            "team_a_loss": "Team A does not win: draw or Team B win",
            "no_draw": "Either team wins in regular time",
            "team_b_loss": "Team B does not win: Team A win or draw",
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("Six-Outcome World Cup Optimizer")
    print(f"Matches: {metrics['total_matches']}")
    print(f"Scored predictions: {metrics['scored_predictions']}")
    print(f"Skipped predictions: {metrics['skipped_predictions']}")
    print(f"Candidates searched: {metrics['candidates_searched']}")
    print(f"Pooled accuracy: {metrics['pooled_accuracy']:.3%} ({metrics['correct']}/{metrics['scored_predictions']})")
    print(f"Mean year accuracy: {metrics['mean_year_accuracy']:.3%}")
    print(f"Year stdev: {metrics['stdev_year_accuracy']:.3%}")
    print(f"Robust score: {metrics['robust_score']:.3%}")
    print("Best parameters:")
    for key, value in summary["optimized_parameters"].items():
        print(f"  {key}: {value}")
    print(f"Wrote {summary_path}")
    print(f"Wrote {predictions_path}")


if __name__ == "__main__":
    main()
