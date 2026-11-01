// Drives START_HERE.html in headless Chrome or Edge through the DevTools protocol and checks the virtual system's
// operator actions end to end:
//
//     node tools/check_page.js [path/to/START_HERE.html]
//
// Checks: a pushbutton press is 1 for exactly one scan, and a second press while paused gives a second pulse; the
// two channel selectors stay in step; clearing a number field leaves the input unchanged; a scenario replays to its
// documented result; hostile URL parameters are ignored.  Talks only to the local browser (127.0.0.1); needs Node.js
// 22 or later (built-in WebSocket).  Exit status 0 when every check passes, 1 when one fails, 2 when no browser is
// found.
"use strict";
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

if (typeof WebSocket !== "function") { console.log("needs Node.js 22 or later (built-in WebSocket)"); process.exit(2); }
const page = path.resolve(process.argv[2] || path.join(__dirname, "..", "START_HERE.html"));
const browsers = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
];
const exe = browsers.find((b) => fs.existsSync(b));
if (!exe) { console.log("no Chrome or Edge found"); process.exit(2); }

const port = 9200 + Math.floor(Math.random() * 700);
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "rps_page_"));
const browser = spawn(exe, ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let ws, nextId = 1;
const waiting = new Map();
function send(method, params) {
  const id = nextId++;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve) => waiting.set(id, resolve));
}
async function evaluate(expr) {
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error("page error: " + JSON.stringify(r.exceptionDetails).slice(0, 400));
  return r.result.value;
}
async function open(hash) {
  await send("Page.navigate", { url: "about:blank" });          // a fragment change alone would not reload the page
  for (let k = 0; k < 50 && (await evaluate("location.href")) !== "about:blank"; k++) await sleep(50);
  await send("Page.navigate", { url: toUrl(page) + (hash || "") });
  for (let k = 0; k < 100; k++) {
    await sleep(100);
    if (await evaluate("typeof window.RPS_PAGE === 'object'").catch(() => false)) return;
  }
  throw new Error("the page did not start");
}
function toUrl(p) {
  const parts = p.replace(/\\/g, "/").split("/");
  return "file:///" + parts.map((s, i) => (i === 0 && /:$/.test(s) ? s : encodeURIComponent(s))).join("/");
}

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok });
  console.log(`  ${ok ? "pass" : "FAIL"}  ${name}${ok ? "" : ": " + detail}`);
}

(async () => {
  let target;
  for (let k = 0; k < 100 && !target; k++) {
    await sleep(100);
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
      target = list.find((t) => t.type === "page");
    } catch (e) { /* the browser is still starting */ }
  }
  if (!target) throw new Error("no DevTools target");
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r) => (ws.onopen = r));
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (waiting.has(d.id)) { waiting.get(d.id)(d.result || d); waiting.delete(d.id); } };
  await send("Page.enable", {});
  await send("Runtime.enable", {});

  // 1. Three presses of a pushbutton while paused give three one-scan pulses, each logged at the scan of its pulse.
  await open("");
  const pulses = await evaluate(`(() => {
    const P = window.RPS_PAGE; P.setRunning(false);
    const seen = [];
    const step = P.sim.step.bind(P.sim);
    P.sim.step = () => { const s = step(); seen.push([P.sim.cycle, P.sim.get("BP_A.f_PZR_P_LO_SP_Reset"), P.sim.get("BP_A.h_PZR_P_LO_Reset_Pulse")]); return s; };
    for (let k = 0; k < 3; k++) P.press("f_PZR_P_LO_SP_Reset");
    P.sim.step = step;
    const logged = P.log().filter((e) => e.text.startsWith("Operator: f_PZR_P_LO_SP_Reset pressed")).map((e) => e.cycle).sort((a, b) => a - b);
    return { seen, logged, after: P.sim.get("BP_A.f_PZR_P_LO_SP_Reset"), pending: P.pending().length };
  })()`);
  const pulseCycles = pulses.seen.filter(([, , q]) => q === true).map(([c]) => c);
  check("three presses while paused give three one-scan pulses", pulseCycles.length === 3 && pulses.after === false && pulses.pending === 0,
        JSON.stringify(pulses));
  check("a press holds the button for exactly one scan", pulses.seen.every(([, b, q]) => !b || q === true) && pulses.seen.filter(([, b]) => b).length === 3,
        JSON.stringify(pulses.seen));
  check("each press is logged at the scan of its pulse", JSON.stringify(pulses.logged) === JSON.stringify(pulseCycles),
        JSON.stringify({ logged: pulses.logged, pulses: pulseCycles }));

  // 1b. Presses on different channel sets, and a double click before one scan, are each logged at the scan of their
  //     pulses and with exactly the channels that pulsed.
  await open("");
  const mixed = await evaluate(`(() => {
    const P = window.RPS_PAGE; P.setRunning(false);
    const pulses = {};
    const step = P.sim.step.bind(P.sim);
    P.sim.step = () => {
      const s = step();
      const chs = ["A", "B", "C", "D"].filter((c) => P.sim.get("BP_" + c + ".h_PZR_P_LO_Reset_Pulse"));
      if (chs.length) pulses[P.sim.cycle] = chs.join(",");
      return s;
    };
    document.querySelector(".target-seg").querySelector('[data-target="BP_A"]').click();
    P.press("f_PZR_P_LO_SP_Reset");
    document.querySelector(".target-seg").querySelector('[data-target="BP"]').click();
    P.press("f_PZR_P_LO_SP_Reset");
    P.setRunning(true);
    P.press("f_PZR_P_LO_SP_Reset");
    P.press("f_PZR_P_LO_SP_Reset");
    P.setRunning(false);
    do P.scan(); while (P.pending().length);
    P.sim.step = step;
    const notes = {};
    for (const e of P.log()) {
      const m = /^Operator: f_PZR_P_LO_SP_Reset pressed in (every channel|channels? (.*))\\.$/.exec(e.text);
      if (m) notes[e.cycle] = m[1] === "every channel" ? "A,B,C,D" : m[2].split(", ").join(",");
    }
    return { pulses, notes };
  })()`);
  // A pressed three times (alone, with All, and the merged double click); B to D twice; a button pressed in the scan
  // before waits one scan at 0, so the pulses alternate A, BCD, A, BCD, A.
  const count = (c) => Object.values(mixed.pulses).filter((v) => v.split(",").includes(c)).length;
  check("presses on different channel sets and a double click are logged at their pulses",
        count("A") === 3 && ["B", "C", "D"].every((c) => count(c) === 2) &&
        JSON.stringify(mixed.pulses) === JSON.stringify(mixed.notes), JSON.stringify(mixed));

  // 2. The channel selectors of the two tabs stay in step, and the process controls follow a change made in the
  //    other tab.
  const seg = await evaluate(`(() => {
    const P = window.RPS_PAGE;
    P.sim.set("BP_C.f_PZR_P", 2100);
    const segs = [...document.querySelectorAll(".target-seg")];
    segs[0].querySelector('[data-target="BP_C"]').click();
    const num = [...document.querySelectorAll('#tab-proc input[type=number]')].find((e) => /^Pressurizer pressure/.test(e.getAttribute("aria-label")));
    return { pressed: [...document.querySelectorAll(".target-seg")].map((s) => s.querySelector('[aria-pressed="true"]').dataset.target),
             shown: num.value };
  })()`);
  check("both channel selectors show the chosen channel", seg.pressed.length === 2 && seg.pressed.every((t) => t === "BP_C"), JSON.stringify(seg));
  check("the process controls show the chosen channel's values", seg.shown === "2100", JSON.stringify(seg));

  // 3. Clearing a number field leaves the input unchanged.
  const cleared = await evaluate(`(() => {
    const P = window.RPS_PAGE;
    const before = P.sim.get("BP_C.f_PZR_P");
    const num = document.querySelector('#tab-proc input[type=number]');
    num.value = ""; num.dispatchEvent(new Event("change"));
    return [before, P.sim.get("BP_C.f_PZR_P"), P.sim.get("BP_C.f_NPWR_LIN")];
  })()`);
  check("clearing a number field changes nothing", cleared[2] === 100, JSON.stringify(cleared));

  // 4. The loss-of-flow scenario initiates a reactor trip at cycle 15, logged at the right cycles.
  await open("#scenario=loss_of_flow_4ch&scans=31");
  const lof = await evaluate(`(() => {
    const P = window.RPS_PAGE;
    const log = P.log();
    const rt = log.find((e) => e.text === "REACTOR TRIP INITIATED");
    const sc = log.find((e) => e.text.startsWith("Scenario:"));
    return { cycle: P.sim.cycle, rt: P.sim.get("CP_A.f_RT_Init"), rtCycle: rt && rt.cycle, scenarioCycle: sc && sc.cycle,
             banner: document.getElementById("rt").textContent };
  })()`);
  check("loss of flow: reactor trip initiated at cycle 15", lof.rt === false && lof.rtCycle === 15 && /INITIATED/.test(lof.banner),
        JSON.stringify(lof));
  check("the scenario note is stamped cycle 0", lof.scenarioCycle === 0, JSON.stringify(lof));

  // 5. Hostile URL parameters are ignored.
  await open("#scenario=constructor&fn=__proto__&speed=abc");
  const hostile = await evaluate(`({ cycle: window.RPS_PAGE.sim.cycle, rt: window.RPS_PAGE.sim.get("CP_A.f_RT_Init") })`);
  check("hostile URL parameters are ignored", hostile.rt === true && hostile.cycle >= 2, JSON.stringify(hostile));

  ws.close();
  const failed = results.filter((r) => !r.ok).length;
  console.log(failed ? `${failed} check(s) failed` : `all ${results.length} page checks passed`);
  await finish(failed ? 1 : 0);
})().catch(async (e) => { console.log("error: " + e.message); await finish(1); });

// Stops the browser and removes its temporary profile.
async function finish(code) {
  const exited = new Promise((r) => browser.once("exit", r));
  browser.kill();
  await Promise.race([exited, sleep(5000)]);
  for (let k = 0; k < 20; k++) {
    try { fs.rmSync(profile, { recursive: true, force: true }); break; } catch (e) { await sleep(250); }
  }
  process.exit(code);
}
