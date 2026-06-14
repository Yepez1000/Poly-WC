import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = process.cwd();
const csvPath = path.join(root, "data/processed/polymarket_world_cup_games_moneyline.csv");
const summaryPath = path.join(root, "data/processed/polymarket_world_cup_games_summary.json");
const outputDir = path.join(root, "outputs/world_cup_games");
const outputPath = path.join(outputDir, "world_cup_individual_moneyline_model.xlsx");

function parseCsvLine(line) {
  const cells = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < line.length; i += 1) {
    const char = line[i];
    const next = line[i + 1];
    if (char === '"' && quoted && next === '"') {
      cell += '"';
      i += 1;
    } else if (char === '"') {
      quoted = !quoted;
    } else if (char === "," && !quoted) {
      cells.push(cell);
      cell = "";
    } else {
      cell += char;
    }
  }
  cells.push(cell);
  return cells;
}

function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/);
  const headers = parseCsvLine(lines[0]);
  return lines.slice(1).map((line) => {
    const values = parseCsvLine(line);
    return Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ""]));
  });
}

function asNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : value;
}

function pct(value) {
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function money(value) {
  return `$${Number(value).toFixed(2)}`;
}

function writeBlock(sheet, startCell, rows) {
  const range = sheet.getRange(`${startCell}:${String.fromCharCode(startCell.charCodeAt(0) + rows[0].length - 1)}${Number(startCell.slice(1)) + rows.length - 1}`);
  range.values = rows;
}

const rows = parseCsv(await fs.readFile(csvPath, "utf8"));
const summary = JSON.parse(await fs.readFile(summaryPath, "utf8"));
const recommended = rows
  .filter((row) => row.recommended === "YES")
  .sort((a, b) => {
    const dateCompare = new Date(a.start_time_utc) - new Date(b.start_time_utc);
    if (dateCompare !== 0) return dateCompare;
    return Number(b.ev_per_dollar) - Number(a.ev_per_dollar);
  });

const matchSummary = Object.values(
  rows.reduce((acc, row) => {
    if (!acc[row.match]) {
      acc[row.match] = {
        match: row.match,
        start_time_utc: row.start_time_utc,
        model_pick: row.predicted_trade,
        market_pick: row.market_pick_3way,
        best_outcome: row.recommended === "YES" ? `${row.contract_side} ${row.contract_outcome}` : "",
        best_edge: row.recommended === "YES" ? Number(row.expected_value) : -999,
        best_ev: row.recommended === "YES" ? Number(row.expected_value) : -999,
        deploy_amount: row.recommended === "YES" ? Number(row.deploy_amount) : 0,
      };
    } else if (row.recommended === "YES" && Number(row.expected_value) > acc[row.match].best_ev) {
      acc[row.match].best_outcome = `${row.contract_side} ${row.contract_outcome}`;
      acc[row.match].best_edge = Number(row.expected_value);
      acc[row.match].best_ev = Number(row.expected_value);
      acc[row.match].deploy_amount = Number(row.deploy_amount);
    }
    return acc;
  }, {})
).sort((a, b) => new Date(a.start_time_utc) - new Date(b.start_time_utc));

rows.sort((a, b) => {
  const dateCompare = new Date(a.start_time_utc) - new Date(b.start_time_utc);
  if (dateCompare !== 0) return dateCompare;
  const matchCompare = a.match.localeCompare(b.match);
  if (matchCompare !== 0) return matchCompare;
  return a.predicted_trade.localeCompare(b.predicted_trade);
});

const workbook = Workbook.create();
const summarySheet = workbook.worksheets.add("Summary");
const recSheet = workbook.worksheets.add("Recommendations");
const detailSheet = workbook.worksheets.add("Outcome Detail");

writeBlock(summarySheet, "A1", [
  ["World Cup Individual Moneyline Model", ""],
  ["Source page", summary.source_page],
  ["Created at", summary.created_at],
  ["Events found", summary.events_found],
  ["Events scored", summary.events_scored],
  ["Outcome rows", summary.outcome_rows],
  ["Backtest accuracy used", pct(summary.backtest_accuracy_used)],
  ["Weights", `Population ${pct(summary.weights.population)}, Climate ${pct(summary.weights.climate)}, Wealth ${pct(summary.weights.wealth)}, Ranking ${pct(summary.weights.ranking)}`],
  ["EV formula", summary.ev_formula],
  ["Bankroll", money(summary.bankroll)],
  ["Kelly scale", pct(summary.kelly_scale)],
  ["Max fraction per outcome", pct(summary.max_fraction_per_outcome)],
]);

writeBlock(summarySheet, "A14", [
  ["Match", "Start UTC", "Predicted Trade", "Market Pick", "Contract", "EV", "EV / Share", "Deploy"],
  ...matchSummary.map((row) => [
    row.match,
    row.start_time_utc,
    row.model_pick,
    row.market_pick,
    row.best_outcome || "PASS",
    row.best_edge === -999 ? "" : row.best_edge,
    row.best_ev === -999 ? "" : row.best_ev,
    row.deploy_amount,
  ]),
]);

writeBlock(recSheet, "A1", [
  ["Match", "Start UTC", "Predicted Outcome", "Trade", "Side", "Price x", "Backtest p", "EV", "Deploy", "Max Loss", "Profit If Win", "Market Question"],
  ...recommended.map((row) => [
    row.match,
    row.start_time_utc,
    row.predicted_outcome,
    row.predicted_trade,
    row.contract_side,
    asNumber(row.polymarket_price_x),
    asNumber(row.backtest_accuracy_p),
    asNumber(row.expected_value),
    asNumber(row.deploy_amount),
    asNumber(row.max_loss),
    asNumber(row.profit_if_win),
    row.market_question,
  ]),
]);

writeBlock(detailSheet, "A1", [
  [
    "Match",
    "Start UTC",
    "Predicted Outcome",
    "Predicted Trade",
    "Contract Outcome",
    "Side",
    "Price x",
    "Backtest p",
    "EV",
    "EV Simplified",
    "Kelly Fraction",
    "Deploy",
    "Recommended",
    "Rating Edge",
    "Edge Bin",
    "Model Pick 3-Way",
    "Market Pick 3-Way",
    "Slug",
  ],
  ...rows.map((row) => [
    row.match,
    row.start_time_utc,
    row.predicted_outcome,
    row.predicted_trade,
    row.contract_outcome,
    row.contract_side,
    asNumber(row.polymarket_price_x),
    asNumber(row.backtest_accuracy_p),
    asNumber(row.expected_value),
    asNumber(row.expected_value_simplified),
    asNumber(row.full_kelly_fraction),
    asNumber(row.deploy_amount),
    row.recommended,
    asNumber(row.rating_edge),
    row.rating_edge_bin,
    row.model_pick_3way,
    row.market_pick_3way,
    row.slug,
  ]),
]);

summarySheet.getRange(`A1:H${14 + matchSummary.length}`).format.autofitColumns();
summarySheet.getRange(`A1:H${14 + matchSummary.length}`).format.autofitRows();
recSheet.getRange(`A1:L${1 + recommended.length}`).format.autofitColumns();
recSheet.getRange(`A1:L${1 + recommended.length}`).format.autofitRows();
detailSheet.getRange(`A1:R${1 + rows.length}`).format.autofitColumns();
detailSheet.getRange(`A1:R${1 + rows.length}`).format.autofitRows();

await fs.mkdir(outputDir, { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(outputPath);
