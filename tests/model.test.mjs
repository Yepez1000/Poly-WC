import assert from "node:assert/strict";
import { currentTeams, worldCupFinals } from "../src/data.js";
import { backtestFinals, compareTeams, componentScores, defaultWeights } from "../src/model.js";

for (const team of currentTeams) {
  const scores = componentScores(team);
  for (const [factor, score] of Object.entries(scores)) {
    assert.ok(score >= 0 && score <= 1, `${team.name} ${factor} score stays inside 0..1`);
  }
}

const spain = currentTeams.find((team) => team.name === "Spain");
const argentina = currentTeams.find((team) => team.name === "Argentina");
const matchup = compareTeams(spain, argentina, defaultWeights);
assert.equal(matchup.winner, "Spain");
assert.ok(matchup.teamA.probability > 0.5);
assert.ok(matchup.teamB.probability < 0.5);

const results = backtestFinals(worldCupFinals, defaultWeights);
assert.equal(results.length, 8);
assert.ok(results.filter((row) => row.correct).length >= 5);

console.log("Model tests passed");
