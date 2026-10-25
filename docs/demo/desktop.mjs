// vmforai view in the browser (noVNC), recorded as a video (CDP screencast):
// the VM's desktop; a terminal where the agents are on the PATH; Firefox
// opened with the mouse. The clicks and keys are real, the VM answers them;
// the arrow is drawn on the page, as headless Chromium shows no pointer.
// Writes OUT/frames/NNNNN.jpg, OUT/frames.json and OUT/marks.json, like
// phone.mjs; phone_video.py puts them together (window layout).
// The dock's icons are where XFCE puts them on a 1280x720 screen.
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync, mkdirSync } from "node:fs";

const out = process.argv[2], url = process.argv[3];
const W = 1280, H = 720, TERMINAL = [568, 695], BROWSER = [663, 695];
mkdirSync(`${out}/frames`, { recursive: true });
const chrome = spawn("chromium", ["--headless=new", "--remote-debugging-port=9337", "--no-first-run",
  "--user-data-dir=" + mkdtempSync(out + "/prof-"), "about:blank"], { stdio: "ignore" });
const sleep = ms => new Promise(r => setTimeout(r, ms));
let ws, id = 0;
const pending = new Map(), frames = [], marks = [];
const t0 = Date.now(), now = () => (Date.now() - t0) / 1000;
const mark = (caption, speed = 1) => { marks.push({ t: now(), caption, speed }); console.log("mark", caption); };
const cmd = (method, params = {}) => new Promise(res => {
  const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params }));
});
const ev = async expr => (await cmd("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })).result.result.value;
async function until(expr, ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) { if (await ev(expr)) return true; await sleep(300); }
  return false;
}
// The arrow, and a ring where it clicks.
const POINTER = `(() => {
  const p = document.createElement("div");
  p.id = "ptr";
  p.innerHTML = '<svg width="22" height="30" viewBox="0 0 22 30"><path d="M1 1 L1 24 L7 18 L11 28 L15 26 L11 17 L20 17 Z" fill="#fff" stroke="#000" stroke-width="1.5"/></svg>';
  p.style.cssText = "position:fixed;z-index:99999;pointer-events:none;left:640px;top:360px;transition:left .7s ease,top .7s ease";
  document.body.appendChild(p);
  window.ring = (x, y) => { const d = document.createElement("div");
    d.style.cssText = "position:fixed;z-index:99998;pointer-events:none;width:40px;height:40px;border-radius:50%;" +
      "border:3px solid #9cc28f;background:rgba(156,194,143,.35);transition:transform .5s,opacity .5s;" +
      "left:" + (x - 20) + "px;top:" + (y - 20) + "px";
    document.body.appendChild(d); requestAnimationFrame(() => { d.style.transform = "scale(1.8)"; d.style.opacity = "0"; });
    setTimeout(() => d.remove(), 700); };
  return true; })()`;
const mouse = (type, x, y) => cmd("Input.dispatchMouseEvent", { type, x, y, button: "left", clickCount: 1 });
async function click([x, y]) {
  await ev(`(p => { p.style.left = "${x}px"; p.style.top = "${y}px"; })(document.getElementById("ptr"))`);
  await mouse("mouseMoved", x, y);
  await sleep(800);
  await ev(`ring(${x}, ${y})`);
  await mouse("mousePressed", x, y);
  await sleep(80);
  await mouse("mouseReleased", x, y);
}
const CODES = { " ": ["Space", 32], "-": ["Minus", 189], ".": ["Period", 190], "/": ["Slash", 191] };
async function key(k, code, vk, modifiers = 0) {
  const text = k.length === 1 && !modifiers ? k : undefined;
  await cmd("Input.dispatchKeyEvent", { type: "keyDown", key: k, code, windowsVirtualKeyCode: vk, text, modifiers });
  await cmd("Input.dispatchKeyEvent", { type: "keyUp", key: k, code, windowsVirtualKeyCode: vk, modifiers });
}
async function type(s) {
  for (const ch of s) {
    const [code, vk] = CODES[ch] || (/[a-z]/.test(ch) ? ["Key" + ch.toUpperCase(), ch.toUpperCase().charCodeAt(0)]
      : ["Digit" + ch, ch.charCodeAt(0)]);
    await key(ch, code, vk);
    await sleep(70);
  }
}
const enter = () => key("Enter", "Enter", 13);
try {
  let list;
  for (let i = 0; i < 50; i++) { try { list = await (await fetch("http://127.0.0.1:9337/json")).json(); break; } catch (e) { await sleep(200); } }
  ws = new WebSocket(list.find(t => t.type === "page").webSocketDebuggerUrl);
  ws.onmessage = m => {
    const d = JSON.parse(m.data);
    if (d.method === "Page.screencastFrame") {
      writeFileSync(`${out}/frames/${String(frames.length).padStart(5, "0")}.jpg`, Buffer.from(d.params.data, "base64"));
      frames.push(now());
      ws.send(JSON.stringify({ id: ++id, method: "Page.screencastFrameAck", params: { sessionId: d.params.sessionId } }));
    } else if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); }
  };
  await new Promise(r => ws.onopen = r);
  await cmd("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  await cmd("Page.enable");
  await cmd("Page.navigate", { url });
  await until(`document.getElementById("msg") && document.getElementById("msg").textContent === ""`, 30000);
  await sleep(4000);  // the desktop takes the page's size
  await ev(POINTER);
  await cmd("Page.startScreencast", { format: "jpeg", quality: 85, everyNthFrame: 1 });
  mark("vmforai view dev: the VM's desktop, here in the browser");
  await sleep(2500);
  await click(TERMINAL);
  await sleep(2500);
  mark("Its terminals have the agents on the PATH");
  await type("claude --version");
  await enter();
  await sleep(2500);
  await type("codex --version");
  await enter();
  await sleep(3000);
  mark("A browser, GUI apps: what an agent cannot do without a screen");
  await click(BROWSER);
  await sleep(1500);
  mark("A browser, GUI apps: what an agent cannot do without a screen", 3);
  await sleep(9000);  // Firefox starts
  mark("A browser, GUI apps: what an agent cannot do without a screen");
  // Ctrl+L, the address bar: noVNC follows Ctrl by its own key events.
  await cmd("Input.dispatchKeyEvent", { type: "rawKeyDown", key: "Control", code: "ControlLeft", windowsVirtualKeyCode: 17, modifiers: 2 });
  await key("l", "KeyL", 76, 2);
  await cmd("Input.dispatchKeyEvent", { type: "keyUp", key: "Control", code: "ControlLeft", windowsVirtualKeyCode: 17 });
  await sleep(500);
  await type("github.com/emincan2000/vmforai");
  await enter();
  await sleep(1500);
  mark("A browser, GUI apps: what an agent cannot do without a screen", 3);
  await sleep(7000);
  mark("A browser, GUI apps: what an agent cannot do without a screen");
  await sleep(3000);
  mark("end");
  await cmd("Page.stopScreencast");
} finally {
  writeFileSync(`${out}/frames.json`, JSON.stringify(frames));
  writeFileSync(`${out}/marks.json`, JSON.stringify(marks));
  chrome.kill();
  process.exit(0);
}
