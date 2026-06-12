export const defaultWeights = {
  population: 20,
  climate: 10,
  wealth: 20,
  ranking: 50
};

export const factors = [
  ["population", "Population"],
  ["climate", "Climate"],
  ["wealth", "Wealth"],
  ["ranking", "FIFA rank"]
];

const clamp = (value, min = 0, max = 1) => Math.min(max, Math.max(min, value));

export function normalizeWeights(weights) {
  const total = Object.values(weights).reduce((sum, value) => sum + Number(value), 0) || 1;
  return Object.fromEntries(
    Object.entries(weights).map(([key, value]) => [key, Number(value) / total])
  );
}

export function componentScores(team) {
  const population = clamp(Math.log10(team.populationM) / Math.log10(300));
  const wealth = clamp((Math.log10(team.gdpPpp) - Math.log10(4000)) / (Math.log10(90000) - Math.log10(4000)));
  const climateDistance = Math.abs(team.avgTempC - 16);
  const climate = clamp(Math.exp(-Math.pow(climateDistance, 2) / (2 * Math.pow(9, 2))));
  const ranking = clamp(1 - Math.log(team.fifaRank) / Math.log(211));

  return { population, climate, wealth, ranking };
}

export function teamScore(team, weights = defaultWeights) {
  const normalized = normalizeWeights(weights);
  const components = componentScores(team);
  const score = Object.entries(components).reduce(
    (sum, [key, value]) => sum + value * normalized[key],
    0
  );

  return { score, components };
}

export function compareTeams(teamA, teamB, weights = defaultWeights) {
  const a = teamScore(teamA, weights);
  const b = teamScore(teamB, weights);
  const edge = a.score - b.score;
  const probabilityA = 1 / (1 + Math.exp(-edge * 8));
  const winner = edge >= 0 ? teamA.name : teamB.name;
  const factorEdges = Object.fromEntries(
    factors.map(([key]) => [key, a.components[key] - b.components[key]])
  );
  const keyDriver = Object.entries(factorEdges)
    .sort((left, right) => Math.abs(right[1]) - Math.abs(left[1]))[0][0];

  return {
    teamA: { ...a, probability: probabilityA },
    teamB: { ...b, probability: 1 - probabilityA },
    edge,
    winner,
    keyDriver,
    factorEdges
  };
}

export function backtestFinals(finals, weights = defaultWeights) {
  return finals.map((final) => {
    const comparison = compareTeams(final.teamA, final.teamB, weights);
    const correct = comparison.winner === final.winner;
    return { ...final, comparison, correct };
  });
}
