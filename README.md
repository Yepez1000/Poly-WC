# Poly-WC

World Cup Winner Backtest is a self-contained browser app for testing a simple
economic model of international soccer strength.

The model uses four variables:

- Population: a larger talent pool, with diminishing returns.
- Climate: a year-round playability score centered on mild temperatures.
- Wealth: GDP per person at purchasing-power parity as an infrastructure proxy.
- FIFA ranking: a current-generation quality signal.

Open `index.html` in a browser to use the app. The backtest covers World Cup
finals from 1994 through 2022 because FIFA men's rankings began in December
1992.

## Data Notes

This is a transparent model prototype, not an official statistical feed.
Historical population, GDP PPP, climate, and pre-tournament ranking inputs are
rounded to keep the assumptions readable. FIFA's public ranking page showed the
latest men's ranking update as June 11, 2026 at build time, and the current-team
selector is labeled accordingly.

## Verification

Run the model checks with:

```sh
node tests/model.test.mjs
```

## Quantitative Match-Level Backtest

The Python pipeline in `scripts/world_cup_quant_model.py` builds a match-level
World Cup backtest from 1994 through 2022. It fetches:

- FIFA World Cup match results from the public `martj42/international_results`
  CSV.
- Year-matched population from World Bank `SP.POP.TOTL`.
- Year-matched GDP per capita PPP from World Bank `NY.GDP.PCAP.PP.CD`.
- A country annual-temperature table embedded in the script for the climate
  factor.

Run it with:

```sh
python3 scripts/world_cup_quant_model.py
```

By default, the quantitative pipeline uses `--result-mode advancement`, which
joins the public `shootouts.csv` file and counts penalty shootout winners as the
actual winner. To reproduce the older regulation-score-only behavior:

```sh
python3 scripts/world_cup_quant_model.py --result-mode regulation
```

For the finer optimization used in the current generated outputs:

```sh
python3 scripts/world_cup_quant_model.py --step 0.01 --result-mode advancement
```

If you have a historical FIFA ranking CSV with `rank_date`, `country_full`, and
`rank` columns, use:

```sh
python3 scripts/world_cup_quant_model.py --ranking-csv path/to/fifa_ranking.csv
```

Without that file, the script uses a documented pre-match rolling strength rank
fallback so the optimizer remains runnable. Outputs are written to:

- `data/processed/optimized_model_summary.json`
- `data/processed/world_cup_match_predictions.csv`
- `data/processed/world_cup_team_match_features.csv`

The current `--step 0.01 --result-mode advancement` run exported 500 World Cup
matches from 1994-2022 and includes penalty shootout winners as advancement
winners. It scored 409 decisive/advancement matches, including 27 penalty
shootouts. The optimized hit rate was 71.883% with these weights:

- Population: 1%
- Climate: 5%
- Wealth: 25%
- Ranking: 69%

Penalty shootout matches alone were predicted correctly 16 times out of 27
matches, or 59.259%.

Important caveat: population and wealth are year-matched to each match year.
Ranking is year-relevant when a historical FIFA ranking CSV is supplied; without
one, the script uses a pre-match rolling rank fallback. Climate is an embedded
country mean-temperature input because a stable open annual country-temperature
API was not available during this build.

## Polymarket World Cup Trade Research

The script `scripts/polymarket_world_cup_trader.py` fetches active public
Polymarket 2026 FIFA World Cup markets and compares market prices with the
backtested economic model.

Run it with:

```sh
python3 scripts/polymarket_world_cup_trader.py --bankroll 1000 --simulations 20000
```

It currently analyzes team outright-winner and group-winner markets. The public
market scan used for the current output did not expose individual match-winner
markets, so the Monte Carlo layer simulates group play and an approximate
32-team knockout tournament from the detected groups.

Outputs:

- `data/processed/polymarket_world_cup_trade_recommendations.csv`
- `data/processed/polymarket_world_cup_portfolio_summary.json`

The trading layer:

- Uses the optimized advancement-mode weights from
  `optimized_model_summary.json`.
- Runs a Monte Carlo tournament simulation.
- Shrinks model probabilities toward Polymarket prices using historical
  backtest accuracy.
- Computes edge, expected value per dollar, fractional Kelly size, max loss, and
  profit if the trade wins.
- Applies conservative defaults: 25% Kelly scale, 2% max per trade, 25% max
  portfolio deployment, 2% minimum edge, 3% minimum EV, and $5 minimum
  deployment.

This is a research tool only. It does not place trades.

## Individual Match Moneyline Sheet

The script `scripts/polymarket_world_cup_games.py` targets the individual
moneyline games page:

```sh
python3 scripts/polymarket_world_cup_games.py --bankroll 1000
```

It fetches the current game slugs from
`https://polymarket.com/sports/world-cup/games`, including the full
client-side `parentToChildEventIds` list, pulls each match event from
Polymarket Gamma, and applies the deterministic six-outcome model:

- Team A wins
- Draw
- Team B wins
- Team A loss / Team A does not win
- No draw
- Team B loss / Team B does not win

Only the model-predicted outcome is evaluated for each match. Expected value is
computed as:

```text
E = p * (1 - x) + (1 - p) * (-x)
```

where `p` is the six-outcome backtest accuracy and `x` is the Polymarket price
for the predicted contract. This simplifies to `E = p - x`.

The generated CSV is:

- `data/processed/polymarket_world_cup_games_moneyline.csv`

The workbook builder exports:

```sh
node scripts/build_world_cup_games_workbook.mjs
```

Final workbook:

- `outputs/world_cup_games/world_cup_individual_moneyline_model.xlsx`

The latest run found 63 currently active individual matches and exported 63
predicted-trade rows.
