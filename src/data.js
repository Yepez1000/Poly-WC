export const currentTeams = [
  { name: "Spain", populationM: 48.6, avgTempC: 13.3, gdpPpp: 54000, fifaRank: 1 },
  { name: "Argentina", populationM: 46.2, avgTempC: 14.8, gdpPpp: 29000, fifaRank: 2 },
  { name: "France", populationM: 68.4, avgTempC: 11.7, gdpPpp: 62000, fifaRank: 3 },
  { name: "England", populationM: 57.1, avgTempC: 9.3, gdpPpp: 59000, fifaRank: 4 },
  { name: "Brazil", populationM: 203.1, avgTempC: 25.0, gdpPpp: 21000, fifaRank: 5 },
  { name: "Portugal", populationM: 10.5, avgTempC: 15.2, gdpPpp: 50000, fifaRank: 6 },
  { name: "Netherlands", populationM: 18.0, avgTempC: 10.4, gdpPpp: 73000, fifaRank: 7 },
  { name: "Belgium", populationM: 11.8, avgTempC: 10.7, gdpPpp: 70000, fifaRank: 8 },
  { name: "Germany", populationM: 84.7, avgTempC: 9.6, gdpPpp: 69000, fifaRank: 9 },
  { name: "Italy", populationM: 58.9, avgTempC: 13.5, gdpPpp: 60000, fifaRank: 10 },
  { name: "Croatia", populationM: 3.9, avgTempC: 11.9, gdpPpp: 47000, fifaRank: 11 },
  { name: "Uruguay", populationM: 3.4, avgTempC: 17.6, gdpPpp: 32000, fifaRank: 12 },
  { name: "Morocco", populationM: 37.8, avgTempC: 18.3, gdpPpp: 10000, fifaRank: 13 },
  { name: "United States", populationM: 341.0, avgTempC: 8.6, gdpPpp: 85000, fifaRank: 14 },
  { name: "Mexico", populationM: 129.0, avgTempC: 21.0, gdpPpp: 25000, fifaRank: 15 },
  { name: "Japan", populationM: 124.0, avgTempC: 14.6, gdpPpp: 52000, fifaRank: 18 }
];

const team = (name, populationM, avgTempC, gdpPpp, fifaRank) => ({
  name,
  populationM,
  avgTempC,
  gdpPpp,
  fifaRank
});

export const worldCupFinals = [
  {
    year: 1994,
    teamA: team("Brazil", 159.4, 25.0, 8000, 1),
    teamB: team("Italy", 57.0, 13.5, 22000, 4),
    winner: "Brazil",
    result: "Brazil beat Italy on penalties"
  },
  {
    year: 1998,
    teamA: team("Brazil", 171.0, 25.0, 9200, 1),
    teamB: team("France", 58.7, 11.7, 25000, 18),
    winner: "France",
    result: "France 3-0 Brazil"
  },
  {
    year: 2002,
    teamA: team("Brazil", 179.5, 25.0, 10300, 2),
    teamB: team("Germany", 82.5, 9.6, 30000, 11),
    winner: "Brazil",
    result: "Brazil 2-0 Germany"
  },
  {
    year: 2006,
    teamA: team("Italy", 58.1, 13.5, 35000, 13),
    teamB: team("France", 63.6, 11.7, 35000, 8),
    winner: "Italy",
    result: "Italy beat France on penalties"
  },
  {
    year: 2010,
    teamA: team("Spain", 46.6, 13.3, 33000, 2),
    teamB: team("Netherlands", 16.6, 10.4, 44000, 4),
    winner: "Spain",
    result: "Spain 1-0 Netherlands"
  },
  {
    year: 2014,
    teamA: team("Germany", 80.9, 9.6, 47000, 2),
    teamB: team("Argentina", 42.7, 14.8, 21000, 5),
    winner: "Germany",
    result: "Germany 1-0 Argentina"
  },
  {
    year: 2018,
    teamA: team("France", 66.9, 11.7, 46000, 7),
    teamB: team("Croatia", 4.1, 11.9, 28000, 20),
    winner: "France",
    result: "France 4-2 Croatia"
  },
  {
    year: 2022,
    teamA: team("Argentina", 45.8, 14.8, 26000, 3),
    teamB: team("France", 67.9, 11.7, 56000, 4),
    winner: "Argentina",
    result: "Argentina beat France on penalties"
  }
];

export const sourceNotes = [
  "World Cup final pairings and results: FIFA/RSSSF summaries via the public List of FIFA World Cup finals.",
  "FIFA ranking input: historical pre-tournament rank estimates for finals; current selector labeled from FIFA's June 11, 2026 ranking update.",
  "Population, GDP per capita PPP, and annual average temperature are rounded model inputs, intended for backtesting and comparison rather than official statistical reporting."
];
