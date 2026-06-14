import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = process.cwd();
const processedDir = path.join(root, "data/processed");
const outputDir = path.join(root, "outputs/world_cup_games");
const outputPath = path.join(outputDir, "recommended_trade_montecarlo.xlsx");

const summaryPath = path.join(processedDir, "recommended_trade_montecarlo_summary.json");
const inputsPath = path.join(processedDir, "recommended_trade_montecarlo_inputs.csv");
const histogramPath = path.join(processedDir, "recommended_trade_montecarlo_histogram.csv");
const pathsPath = path.join(processedDir, "recommended_trade_montecarlo_paths.csv");

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

function parseCsv(text, limitRows = null) {
  const lines = text.trim().split(/\r?\n/);
  const headers = parseCsvLine(lines[0]);
  const body = limitRows ? lines.slice(1, limitRows + 1) : lines.slice(1);
  return body.map((line) => {
    const values = parseCsvLine(line);
    return Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ""]));
  });
}

function asNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) && value !== "" ? number : value;
}

function pct(value) {
  return Number(value);
}

function money(value) {
  return Number(value);
}

function writeBlock(sheet, startCell, rows) {
  const column = startCell.match(/[A-Z]+/)[0];
  const row = Number(startCell.match(/\d+/)[0]);
  const startCol = column.split("").reduce((acc, char) => acc * 26 + char.charCodeAt(0) - 64, 0);
  const endColNumber = startCol + rows[0].length - 1;
  let n = endColNumber;
  let endColumn = "";
  while (n > 0) {
    const r = (n - 1) % 26;
    endColumn = String.fromCharCode(65 + r) + endColumn;
    n = Math.floor((n - 1) / 26);
  }
  const range = sheet.getRange(`${startCell}:${endColumn}${row + rows.length - 1}`);
  range.values = rows;
}

const summary = JSON.parse(await fs.readFile(summaryPath, "utf8"));
const inputs = parseCsv(await fs.readFile(inputsPath, "utf8"));
const histogramRows = parseCsv(await fs.readFile(histogramPath, "utf8"));
const pathSample = parseCsv(await fs.readFile(pathsPath, "utf8"), 1000);

const workbook = Workbook.create();
const summarySheet = workbook.worksheets.add("Summary");
const tradesSheet = workbook.worksheets.add("Trade Inputs");
const distributionSheet = workbook.worksheets.add("Distribution");
const pathSheet = workbook.worksheets.add("Path Sample");

const sources = ["polymarket", "model"];
writeBlock(summarySheet, "A1", [
  ["Recommended Trade Monte Carlo", ""],
  ["Created at", summary.created_at],
  ["Recommended trades", summary.recommended_trades],
  ["Total deployed", money(summary.total_deployed)],
  ["Simulations per probability source", summary.simulations_per_probability_source],
  ["Payout rule", summary.payout],
  ["Full path CSV", pathsPath],
]);

writeBlock(summarySheet, "A10", [
  [
    "Probability Source",
    "Mean P&L",
    "Stdev",
    "Min",
    "P01",
    "P05",
    "Median",
    "P95",
    "P99",
    "Max",
    "Probability Loss",
    "Return on Deployed",
  ],
  ...sources.map((source) => {
    const stats = summary.summary[source];
    return [
      source,
      money(stats.mean),
      money(stats.stdev),
      money(stats.min),
      money(stats.p01),
      money(stats.p05),
      money(stats.median),
      money(stats.p95),
      money(stats.p99),
      money(stats.max),
      pct(stats.probability_loss),
      pct(stats.expected_return_on_deployed),
    ];
  }),
]);

writeBlock(tradesSheet, "A1", [
  [
    "Trade #",
    "Match",
    "Start UTC",
    "Predicted Trade",
    "Side",
    "Price",
    "Model Probability",
    "Polymarket Probability",
    "Deploy",
    "Shares",
    "Profit If Win",
    "Loss If Lose",
    "Rating Edge",
    "Edge Bin",
  ],
  ...inputs.map((row) => [
    asNumber(row.trade_index),
    row.match,
    row.start_time_utc,
    row.predicted_trade,
    row.contract_side,
    asNumber(row.price),
    asNumber(row.model_probability),
    asNumber(row.polymarket_probability),
    asNumber(row.deploy_amount),
    asNumber(row.shares),
    asNumber(row.profit_if_win),
    asNumber(row.loss_if_lose),
    asNumber(row.rating_edge),
    row.rating_edge_bin,
  ]),
]);

writeBlock(distributionSheet, "A1", [
  ["Probability Source", "Bin Low", "Bin High", "Count", "Frequency"],
  ...histogramRows.map((row) => [
    row.probability_source,
    asNumber(row.bin_low),
    asNumber(row.bin_high),
    asNumber(row.count),
    asNumber(row.frequency),
  ]),
]);

const pathHeaders = Object.keys(pathSample[0] ?? {});
writeBlock(pathSheet, "A1", [
  pathHeaders,
  ...pathSample.map((row) => pathHeaders.map((header) => asNumber(row[header]))),
]);

summarySheet.getRange("A1:L12").format.autofitColumns();
summarySheet.getRange("A1:L12").format.autofitRows();
tradesSheet.getRange(`A1:N${inputs.length + 1}`).format.autofitColumns();
tradesSheet.getRange(`A1:N${inputs.length + 1}`).format.autofitRows();
distributionSheet.getRange(`A1:E${histogramRows.length + 1}`).format.autofitColumns();
distributionSheet.getRange(`A1:E${histogramRows.length + 1}`).format.autofitRows();
if (pathSample.length > 0) {
  pathSheet.getRange(`A1:BG${pathSample.length + 1}`).format.autofitColumns();
  pathSheet.getRange(`A1:BG${pathSample.length + 1}`).format.autofitRows();
}

await fs.mkdir(outputDir, { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(outputPath);
