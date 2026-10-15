/**
 * 真实浏览器验证台
 *
 * 用无头 Edge + CDP 加载页面、等结果、抓控制台报错、截图。
 * Node 22 自带 WebSocket 全局对象，不需要任何第三方依赖。
 *
 * 用法：node tools/browser_probe.mjs <相对页面路径> [等待超时秒]
 */

import { spawn } from "node:child_process";
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const REAL_EDGE_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0";

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".css": "text/css; charset=utf-8",
};

export function startServer(port) {
  const server = http.createServer((req, res) => {
    const rel = decodeURIComponent(req.url.split("?")[0]).replace(/^\/+/, "") || "index.html";
    const file = path.join(ROOT, rel);
    if (!file.startsWith(ROOT) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404); res.end("not found"); return;
    }
    res.writeHead(200, { "Content-Type": MIME[path.extname(file)] || "application/octet-stream" });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((resolve) => server.listen(port, "127.0.0.1", () => resolve(server)));
}

export class CDP {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.listeners = [];
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(msg.error.message)); else resolve(msg.result);
      } else if (msg.method) {
        for (const fn of this.listeners) fn(msg);
      }
    });
  }

  on(fn) { this.listeners.push(fn); }

  send(method, params = {}, sessionId) {
    const id = ++this.id;
    const payload = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;
    this.ws.send(JSON.stringify(payload));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (this.pending.has(id)) { this.pending.delete(id); reject(new Error(`${method} 超时`)); }
      }, 120000);
    });
  }
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 启动无头 Edge 并接上调试端口 */
export async function launchBrowser(dbgPort = 9333) {
  const profile = path.join(os.tmpdir(), `edge-probe-${Date.now()}`);
  const edge = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--no-proxy-server",                        // 避免继承会话代理
    `--user-agent=${REAL_EDGE_UA}`,             // 模拟真实 Edge（否则是无头 UA）
    `--remote-debugging-port=${dbgPort}`,
    `--user-data-dir=${profile}`,
    "--window-size=1440,1200",
    "about:blank",
  ], { stdio: "ignore" });

  const info = await waitForDebugger(dbgPort);
  const ws = new WebSocket(info.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.addEventListener("open", res);
    ws.addEventListener("error", () => rej(new Error("无法连接浏览器调试端口")));
  });
  return {
    edge,
    ws,
    profile,
    close() {
      try { ws.close(); } catch { /* 忽略 */ }
      edge.kill();
      try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* 忽略 */ }
    },
  };
}

async function waitForDebugger(port, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (r.ok) return await r.json();
    } catch { /* 还没起来 */ }
    await sleep(250);
  }
  throw new Error("调试端口未就绪");
}

export async function runProbe({ page, timeoutSec = 90, shot = null }) {
  const port = 5311;
  const dbgPort = 9333;
  const profile = path.join(os.tmpdir(), `edge-probe-${Date.now()}`);
  const server = await startServer(port);
  console.log(`[1/5] 静态服务已起 http://127.0.0.1:${port}`);

  const edge = spawn(EDGE, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--no-proxy-server",                        // 避免继承会话代理
    `--user-agent=${REAL_EDGE_UA}`,             // 模拟真实 Edge（否则是无头 UA）
    `--remote-debugging-port=${dbgPort}`,
    `--user-data-dir=${profile}`,
    "--window-size=1440,1200",
    "about:blank",
  ], { stdio: "ignore" });

  const logs = [];
  const errors = [];
  let browserWs;
  try {
    const info = await waitForDebugger(dbgPort);
    browserWs = new WebSocket(info.webSocketDebuggerUrl);
    await new Promise((res, rej) => {
      browserWs.addEventListener("open", res);
      browserWs.addEventListener("error", () => rej(new Error("无法连接浏览器调试端口")));
    });
    const cdp = new CDP(browserWs);
    console.log("[2/5] 已连接浏览器调试端口");

    const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });

    cdp.on((msg) => {
      if (msg.method === "Runtime.consoleAPICalled") {
        const text = (msg.params.args || []).map((a) => a.value ?? a.description ?? "").join(" ");
        logs.push(`[${msg.params.type}] ${text}`);
      } else if (msg.method === "Runtime.exceptionThrown") {
        const d = msg.params.exceptionDetails;
        errors.push(d.exception?.description || d.text);
      } else if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") {
        errors.push(msg.params.entry.text);
      }
    });

    await cdp.send("Runtime.enable", {}, sessionId);
    await cdp.send("Log.enable", {}, sessionId);
    await cdp.send("Page.enable", {}, sessionId);
    await cdp.send("Emulation.setDeviceMetricsOverride",
      { width: 1440, height: 1200, deviceScaleFactor: 1, mobile: false }, sessionId);

    const url = `http://127.0.0.1:${port}/${page}`;
    await cdp.send("Page.navigate", { url }, sessionId);
    console.log(`[3/5] 已打开 ${url}`);

    // 等页面的 __done 标记（页面自行把结果挂到 window.__result）
    const deadline = Date.now() + timeoutSec * 1000;
    let result = null;
    while (Date.now() < deadline) {
      const r = await cdp.send("Runtime.evaluate", {
        expression: "window.__done ? JSON.stringify(window.__result ?? null) : ''",
        returnByValue: true,
      }, sessionId);
      if (r.result.value) { result = JSON.parse(r.result.value); break; }
      await sleep(500);
    }
    if (result === null) {
      const snap = await cdp.send("Runtime.evaluate", {
        expression: "window.__status || '(无状态)'", returnByValue: true,
      }, sessionId);
      throw new Error(`等待超时，页面状态：${snap.result.value}`);
    }
    console.log("[4/5] 页面已给出结果");

    if (shot) {
      const rel = path.join("_verify", shot);
      fs.mkdirSync(path.dirname(path.join(ROOT, rel)), { recursive: true });
      const png = await cdp.send("Page.captureScreenshot", { format: "png" }, sessionId);
      fs.writeFileSync(path.join(ROOT, rel), Buffer.from(png.data, "base64"));
      console.log(`[5/5] 截图已保存 ${rel}`);
    }

    return { result, logs, errors };
  } finally {
    try { browserWs?.close(); } catch { /* 忽略 */ }
    edge.kill();
    server.close();
    await sleep(300);
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* 忽略 */ }
  }
}

if (process.argv[1] && process.argv[1].endsWith("browser_probe.mjs")) {
  const page = process.argv[2] || "tests/browser/engine.html";
  const timeoutSec = Number(process.argv[3] || 90);
  const out = await runProbe({ page, timeoutSec, shot: "_verify/probe.png" });
  console.log("\n--- 控制台 ---");
  for (const l of out.logs.slice(-25)) console.log("  " + l);
  if (out.errors.length) {
    console.log("--- 页面错误 ---");
    for (const e of out.errors) console.log("  " + e);
  }
  console.log("\n--- 结果 ---");
  console.log(JSON.stringify(out.result, null, 2));
  process.exit(out.errors.length ? 2 : 0);
}
