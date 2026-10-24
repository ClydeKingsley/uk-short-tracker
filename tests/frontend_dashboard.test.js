"use strict";

const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

// Exercise the application calculations without starting a server or reading
// personal databases. The test hook is injected only into this in-memory copy.
const webRoot = join(__dirname, "..", "web");
const source = readFileSync(join(webRoot, "app.js"), "utf8");
function harness() {
  const context = vm.createContext({
    document: { readyState: "loading", addEventListener() {}, getElementById() { return {}; } },
    window: { localStorage: { getItem() { return null; } } },
  });
  vm.runInContext(readFileSync(join(webRoot, "i18n.js"), "utf8"), context);
  const instrumented = source.replace(/\}\)\(\);\s*$/, `
    configureFormatters();
    window.__test = { state, formatCurrency, formatAxisPrice, normalizePrices, getRecentPositionRankings,
      matchesRankingChip, getFilteredRankings, calculateSnapshotStatistics,
      calculateRankingDistribution, calculateDetailSummary, findSnapshotRanking };
  })();`);
  assert.notEqual(source, instrumented);
  vm.runInContext(instrumented, context);
  return context.window.__test;
}
const day = (value) => Date.parse(`${value}T00:00:00Z`);
function ranking(rank, shortPercent, date = "2026-10-23", extras = {}) {
  return {
    rank, shortPercent, positionTime: date ? day(date) : null, positionDate: date,
    ageDays: 0, positionDateInFuture: false,
    security: { id: String(rank), name: `Company ${rank}`, isin: `GB${rank}`, ticker: `T${rank}`, priceSymbol: "" },
    ...extras,
  };
}
const json = (value) => JSON.parse(JSON.stringify(value));

test("latest position list sorts raw dates, breaks ties by rank and leaves input unchanged", () => {
  const h = harness();
  const items = [ranking(4, 10, null), ranking(3, 2, "2026-10-22"), ranking(2, 1), ranking(1, 0.5)];
  assert.deepEqual(json(h.getRecentPositionRankings(items, 2).map((item) => item.rank)), [1, 2]);
  assert.deepEqual(items.map((item) => item.rank), [4, 3, 2, 1]);
});

test("thresholds are inclusive; seven calendar days include ages 0 through 6 only", () => {
  const h = harness();
  assert.equal(h.matchesRankingChip(ranking(1, 1.99), "above2"), false);
  assert.equal(h.matchesRankingChip(ranking(1, 2), "above2"), true);
  assert.equal(h.matchesRankingChip(ranking(1, 5), "above5"), true);
  for (const ageDays of [0, 6]) assert.equal(h.matchesRankingChip(ranking(1, 1, undefined, { ageDays }), "recent7"), true);
  for (const ageDays of [7, -1, null]) assert.equal(h.matchesRankingChip(ranking(1, 1, undefined, { ageDays }), "recent7"), false);
  assert.equal(h.matchesRankingChip(ranking(1, 1, undefined, { positionDateInFuture: true }), "recent7"), false);
  assert.equal(h.matchesRankingChip(ranking(1, 1, null), "recent7"), false);
});

test("chips combine with text and sorting while whole-snapshot statistics remain unchanged", () => {
  const h = harness();
  h.state.rankings = [ranking(1, 10), ranking(2, 5), ranking(3, 1)];
  const before = json(h.calculateSnapshotStatistics(h.state.rankings));
  const distribution = json(h.calculateRankingDistribution(h.state.rankings));
  h.state.rankingFilter = "company";
  h.state.rankingChip = "above2";
  h.state.rankingSort = { key: "shortPercent", direction: "asc" };
  assert.deepEqual(json(h.getFilteredRankings().map((item) => item.rank)), [2, 1]);
  h.state.rankingFilter = "GB1";
  assert.deepEqual(json(h.getFilteredRankings().map((item) => item.rank)), [1]);
  assert.deepEqual(json(h.calculateSnapshotStatistics(h.state.rankings)), before);
  assert.deepEqual(json(h.calculateRankingDistribution(h.state.rankings)), distribution);
  assert.deepEqual(h.state.rankings.map((item) => item.rank), [1, 2, 3]);
});

test("ANSP high and low include carried intervals, respect interval ends and exclude legacy", () => {
  const h = harness();
  const points = [
    { time: day("2026-07-01"), legacy: 99 },
    { time: day("2026-07-09"), ansp: 2, anspIsCurrent: false, anspIntervalEnd: day("2026-07-20") },
    { time: day("2026-07-25"), ansp: 3, anspIsCurrent: true },
  ];
  let result = h.calculateDetailSummary(points, [], day("2026-07-15"), day("2026-07-19"));
  assert.equal(result.anspHigh, 2);
  assert.equal(result.anspLow, 2);
  assert.equal(result.nodes.length, 0, "carried position is not a new point inside the range");
  result = h.calculateDetailSummary(points, [], day("2026-07-20"), day("2026-07-24"));
  assert.equal(result.anspHigh, null, "a historic interval cannot extend across a gap");
  assert.equal(result.anspLow, null);
  result = h.calculateDetailSummary(points, [], day("2026-07-01"), day("2026-07-26"));
  assert.equal(result.anspHigh, 3);
  assert.equal(result.anspLow, 2);
});

test("closing summary excludes marked live quote but chart normalization preserves it", () => {
  const h = harness();
  const prices = h.normalizePrices({ items: [
    { date: "2026-10-21", close: 100 },
    { date: "2026-10-22", close: 110 },
    { date: "2026-10-23", close: 200, is_latest_quote: true },
  ] });
  assert.equal(prices.length, 3);
  assert.equal(prices[2].isLatestQuote, true);
  const result = h.calculateDetailSummary([], prices, day("2026-10-21"), day("2026-10-23"));
  assert.equal(result.priceHigh, 110);
  assert.equal(result.priceLow, 100);
  assert.ok(Math.abs(result.priceChange - 10) < 1e-10);
  assert.equal(result.priceLast, day("2026-10-22"));
});

test("closing change needs two observations, positive starting close and visible date bounds", () => {
  const h = harness();
  const prices = [{ time: 1, close: 50 }, { time: 2, close: 100 }, { time: 3, close: 80 }];
  let result = h.calculateDetailSummary([], prices, 2, 3);
  assert.ok(Math.abs(result.priceChange + 20) < 1e-10);
  assert.equal(result.priceLow, 80);
  assert.equal(h.calculateDetailSummary([], prices, 3, 3).priceChange, null);
  assert.equal(h.calculateDetailSummary([], [{ time: 1, close: 0 }, { time: 2, close: 2 }], 1, 2).priceChange, null);
  result = h.calculateDetailSummary([], prices, 4, 5);
  for (const key of ["priceHigh", "priceLow", "priceChange", "priceFirst", "priceLast"]) assert.equal(result[key], null);
});

test("last five visible nodes retain separate regimes and effective dates", () => {
  const h = harness();
  const points = [
    { time: day("2026-07-08"), legacy: 0.5 },
    { time: day("2026-07-09"), legacy: 1, ansp: 2, anspPositionTime: day("2026-07-01") },
    ...[10, 11, 12, 13].map((date) => ({ time: day(`2026-07-${date}`), ansp: date / 10 })),
  ];
  const result = h.calculateDetailSummary(points, [], day("2026-07-09"), day("2026-07-12"));
  assert.equal(result.nodes.length, 5);
  assert.deepEqual(json(result.nodes.map((node) => node.regime)), ["ansp", "ansp", "ansp", "ansp", "legacy"]);
  assert.equal(result.nodes[3].time, day("2026-07-09"), "effective date, not old constituent date");
  assert.equal(result.nodes[3].point.anspPositionTime, day("2026-07-01"));
});

test("snapshot rank matches exact ISIN and never guesses another share class by issuer ID", () => {
  const h = harness();
  const items = [ranking(1, 5), ranking(2, 4)];
  items[1].security.id = items[0].security.id;
  assert.equal(h.findSnapshotRanking(items, { id: "1", isin: "gb2" }).rank, 2);
  assert.equal(h.findSnapshotRanking(items, { id: "1", isin: "GB3" }), null);
  assert.equal(h.findSnapshotRanking(items, { id: "1", isin: "—" }), null);
});

test("provider currency distinguishes sterling pounds from pence", () => {
  const h = harness();
  assert.equal(h.formatCurrency(123.45, "GBp"), "123.45p");
  assert.equal(h.formatCurrency(123.45, "GBX"), "123.45p");
  assert.match(h.formatCurrency(123.45, "GBP"), /£/);
  assert.ok(!h.formatCurrency(123.45, "GBP").endsWith("p"));
  assert.equal(h.formatAxisPrice(123.45, "GBP"), "£123");
  assert.equal(h.formatAxisPrice(123.45, "GBp"), "123p");
  assert.equal(h.formatAxisPrice(123.45, "GBX"), "123p");
  assert.equal(h.formatAxisPrice(123.45, " GBP "), "£123");
});
