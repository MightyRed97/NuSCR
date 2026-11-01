// Replays a stimulus file on the browser engine (4_code/js/rps_engine.js) under Node.js and writes the trace, as
// 4_code/build/rps_sim.exe does:
//
//     node tools/js_runner.js ENGINE.js TARGET STIMULUS.csv TRACE.csv
//
// The stimulus is the scenario format (tools/nuscr_sim.py): 'cycle' and input columns, empty cell = unchanged,
// '#' comment lines.  The trace has cycle, time_ms and every computed signal; flags are written 1/0 and numbers in
// the shortest form that reads back as the same double.  Used by tools/verify_js.py.
"use strict";
const fs = require("fs");
const path = require("path");

function fail(message) {
  process.stderr.write("js_runner: " + message + "\n");
  process.exit(2);
}

const [enginePath, target, stimulusPath, tracePath] = process.argv.slice(2);
if (!tracePath) fail("usage: node tools/js_runner.js ENGINE.js TARGET STIMULUS.csv TRACE.csv");
const RPS = require(path.resolve(enginePath));
const sim = new RPS.Simulator(target);

const lines = fs.readFileSync(stimulusPath, "utf8").split(/\r?\n/);
let header = null;
const rows = [];
lines.forEach((line, k) => {
  if (/^\s*(#|$)/.test(line)) return;
  const cells = line.split(",").map((c) => c.trim());
  if (!header) {
    if (cells[0] !== "cycle") fail(stimulusPath + ": the first column must be 'cycle'");
    header = cells.slice(1);
    for (const name of header) if (!sim.resolveInput(name).length) fail(name + " is not an input of " + target);
    return;
  }
  const cycle = Number(cells[0]);
  if (!Number.isInteger(cycle) || cycle < 0) fail(stimulusPath + ":" + (k + 1) + ": bad cycle " + cells[0]);
  if (cells.length - 1 > header.length) fail(stimulusPath + ":" + (k + 1) + ": more cells than columns");
  rows.push({ cycle, order: rows.length, cells: cells.slice(1) });
});
if (!header) fail(stimulusPath + ": no header");
rows.sort((a, b) => a.cycle - b.cycle || a.order - b.order);
const last = rows.length ? rows[rows.length - 1].cycle : -1;

const names = sim.signalNames();
const out = ["cycle,time_ms," + names.join(",")];
const fmt = (v) => (v === true ? "1" : v === false ? "0" : String(v));
let r = 0;
for (let c = 0; c <= last; c++) {
  for (; r < rows.length && rows[r].cycle === c; r++) {
    rows[r].cells.forEach((cell, j) => {
      if (cell !== "") sim.set(header[j], cell);
    });
  }
  sim.step();
  out.push(sim.cycle + "," + fmt(sim.timeMs) + "," + names.map((n) => fmt(sim.get(n))).join(","));
}
fs.writeFileSync(tracePath, out.join("\n") + "\n");
