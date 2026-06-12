import { currentTeams, sourceNotes, worldCupFinals } from "./data.js";
import { backtestFinals, compareTeams, defaultWeights, factors } from "./model.js";

const weights = { ...defaultWeights };
const weightRoot = document.querySelector("#weights");
const backtestRoot = document.querySelector("#backtest");
const accuracyRoot = document.querySelector("#accuracy");
const recordRoot = document.querySelector("#record");
const teamASelect = document.querySelector("#team-a");
const teamBSelect = document.querySelector("#team-b");
const matchupRoot = document.querySelector("#matchup");

function percent(value, digits = 0) {
  return `${(value * 100).toFixed(digits)}%`;
}

function formatEdge(edge) {
  return `${edge >= 0 ? "+" : ""}${(edge * 100).toFixed(1)} pts`;
}

function factorLabel(key) {
  return factors.find(([factor]) => factor === key)?.[1] ?? key;
}

function renderWeights() {
  weightRoot.innerHTML = factors.map(([key, label]) => `
    <div class="slider-row">
      <label for="weight-${key}">${label}</label>
      <input id="weight-${key}" data-weight="${key}" type="range" min="0" max="100" step="1" value="${weights[key]}">
      <strong>${weights[key]}%</strong>
    </div>
  `).join("");
}

function renderSelects() {
  const options = currentTeams
    .map((team) => `<option value="${team.name}">${team.name} (rank ${team.fifaRank})</option>`)
    .join("");
  teamASelect.innerHTML = options;
  teamBSelect.innerHTML = options;
  teamASelect.value = "Spain";
  teamBSelect.value = "Argentina";
}

function teamByName(name) {
  return currentTeams.find((team) => team.name === name);
}

function renderTeamPanel(team, result, isWinner) {
  return `
    <article class="team-panel ${isWinner ? "is-winner" : ""}">
      <div class="team-panel__top">
        <div>
          <strong>${team.name}</strong>
          <span>Rank ${team.fifaRank} · ${team.populationM.toFixed(1)}M people · ${team.avgTempC.toFixed(1)}°C</span>
        </div>
        <span class="probability">${percent(result.probability)}</span>
      </div>
      <div class="factor-list">
        ${factors.map(([key, label]) => `
          <div class="factor-row">
            <span>${label}</span>
            <span class="bar"><span style="width: ${percent(result.components[key])}"></span></span>
            <span>${percent(result.components[key])}</span>
          </div>
        `).join("")}
      </div>
    </article>
  `;
}

function renderMatchup() {
  const teamA = teamByName(teamASelect.value);
  const teamB = teamByName(teamBSelect.value);
  const comparison = compareTeams(teamA, teamB, weights);
  matchupRoot.innerHTML = [
    renderTeamPanel(teamA, comparison.teamA, comparison.winner === teamA.name),
    renderTeamPanel(teamB, comparison.teamB, comparison.winner === teamB.name)
  ].join("");
}

function renderBacktest() {
  const rows = backtestFinals(worldCupFinals, weights);
  const wins = rows.filter((row) => row.correct).length;
  accuracyRoot.textContent = percent(wins / rows.length);
  recordRoot.textContent = `${wins} correct out of ${rows.length} finals`;

  backtestRoot.innerHTML = rows.map((row) => {
    const { comparison } = row;
    const predictedTeam = comparison.winner;
    const pickedA = predictedTeam === row.teamA.name;
    const probability = pickedA ? comparison.teamA.probability : comparison.teamB.probability;

    return `
      <tr>
        <td>${row.year}</td>
        <td><strong>${row.teamA.name}</strong> vs <strong>${row.teamB.name}</strong><br><span>${row.result}</span></td>
        <td>${predictedTeam} <span class="pill ${row.correct ? "good" : "bad"}">${row.correct ? "hit" : "miss"}</span></td>
        <td>${row.winner}</td>
        <td>${percent(probability)} confidence · ${formatEdge(comparison.edge)}</td>
        <td>${factorLabel(comparison.keyDriver)}</td>
      </tr>
    `;
  }).join("");
}

function renderSourceNotes() {
  const method = document.querySelector(".method");
  const notes = document.createElement("p");
  notes.className = "source-notes";
  notes.textContent = sourceNotes.join(" ");
  method.append(notes);
}

function render() {
  renderWeights();
  renderBacktest();
  renderMatchup();
}

weightRoot.addEventListener("input", (event) => {
  const key = event.target.dataset.weight;
  if (!key) return;
  weights[key] = Number(event.target.value);
  render();
});

teamASelect.addEventListener("change", renderMatchup);
teamBSelect.addEventListener("change", renderMatchup);

document.querySelector("#swap").addEventListener("click", () => {
  const previous = teamASelect.value;
  teamASelect.value = teamBSelect.value;
  teamBSelect.value = previous;
  renderMatchup();
});

renderSelects();
renderSourceNotes();
render();
