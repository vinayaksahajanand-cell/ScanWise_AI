// Run: npm test
import assert from "node:assert/strict";
import { test } from "node:test";

import { formatDate, isoToDmy, parseDmy } from "../src/lib/dates.ts";

test("formatDate shows day/month/year, whatever the locale", () => {
  assert.equal(formatDate("2023-02-01"), "01/02/2023");
  assert.equal(formatDate("2026-12-25"), "25/12/2026");
  assert.equal(formatDate(null), "—");
  assert.equal(formatDate(""), "—");
  assert.equal(formatDate("not a date"), "not a date");
});

test("formatDate with time", () => {
  assert.match(formatDate("2023-02-01T14:05:00", true), /^01\/02\/2023 14:05$/);
  assert.equal(formatDate("2023-02-01", true), "01/02/2023 00:00");
});

test("isoToDmy only rewrites plain ISO dates", () => {
  assert.equal(isoToDmy("2023-02-01"), "01/02/2023");
  assert.equal(isoToDmy(""), "");
  assert.equal(isoToDmy(null), "");
  assert.equal(isoToDmy("01/02/2023"), "01/02/2023");
});

test("parseDmy reads day first", () => {
  assert.equal(parseDmy("01/02/2023"), "2023-02-01");
  assert.equal(parseDmy("1/2/2023"), "2023-02-01");
  assert.equal(parseDmy("25-12-2026"), "2026-12-25");
  assert.equal(parseDmy("25.12.2026"), "2026-12-25");
  assert.equal(parseDmy("5 3 24"), "2024-03-05");
  assert.equal(parseDmy("2023-02-01"), "2023-02-01");
});

test("parseDmy: empty clears, impossible dates are rejected", () => {
  assert.equal(parseDmy(""), "");
  assert.equal(parseDmy("   "), "");
  assert.equal(parseDmy("31/02/2023"), null);
  assert.equal(parseDmy("13/13/2023"), null);
  assert.equal(parseDmy("00/01/2023"), null);
  assert.equal(parseDmy("29/02/2023"), null);
  assert.equal(parseDmy("29/02/2024"), "2024-02-29");
  assert.equal(parseDmy("tomorrow"), null);
});

test("round trip", () => {
  for (const iso of ["2023-01-01", "2024-02-29", "1999-12-31"]) assert.equal(parseDmy(isoToDmy(iso)), iso);
});
