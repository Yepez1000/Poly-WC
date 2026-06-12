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
