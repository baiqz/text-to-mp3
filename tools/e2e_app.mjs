/**
 * 成品页面的真实用户流程验证
 *
 * 在无头 Edge 里打开构建出来的 index.html，像用户一样操作：
 * 粘贴文字 → 点开始 → 等成品 → 播放 → 下载 → 取消 → 清空。
 * 最后截一张整页图供目检。
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { startServer, launchBrowser, CDP, sleep } from "./browser_probe.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const SAMPLE = [
  "傍晚的风从巷口吹过来，带着一点潮湿的凉意。这是城市里最容易被忽略的时刻，路灯还没亮，天色介于白天和夜晚之间。",
  "我们总以为生活是由那些重要的日子构成的。可真正占满生命的，恰恰是这些没有任何标记的黄昏。",
  "这段文字用来验证长文本会被自动切成多段，分别合成之后再按顺序拼成一个完整的音频文件。",
  "A short English line is included to make sure mixed content works too.",
].join("\n\n").repeat(4);

const EVAL_A = `(async () => {
  const checks = [];
  const check = (n, ok, d = "") => checks.push({ n, ok: !!ok, d: String(d) });
  const $ = (id) => document.getElementById(id);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const waitFor = async (fn, ms) => {
    const t = Date.now();
    while (Date.now() - t < ms) { if (fn()) return true; await sleep(200); }
    return false;
  };

  check("页面初始化完成", window.__uiReady === true);
  check("声库数量已显示", /\\d+ 个音色 · 中文 \\d+ 个/.test($("voice-count").textContent),
    $("voice-count").textContent);
  const rows = document.querySelectorAll("#voices .voice").length;
  check("音色列表已渲染", rows === 26, rows + " 项");
  check("默认选中一个音色", !!document.querySelector("#voices .voice.on"),
    document.querySelector("#voices .voice.on")?.textContent || "");
  check("列表里没有已下架音色", !$("voices").textContent.includes("云泽"), "");
  check("官方清单外的扩展音色带标记", document.querySelectorAll("#voices .vtag").length >= 1,
    document.querySelectorAll("#voices .vtag").length + " 个");

  // ---- 显示更多：323 个音色必须能一路翻到底，不能只能靠搜索 ----
  // 按钮必须在滚动区外面（否则藏在 26 条之后根本看不见）
  check("「显示更多」在列表滚动区外面",
    $("voice-more") && !$("voices").contains($("voice-more")) &&
    $("voice-more").getBoundingClientRect().height > 0,
    $("voice-more").textContent);
  const moreBtn = $("voice-more");
  if (!moreBtn.hidden) {
    moreBtn.click();
    const after = document.querySelectorAll("#voices .voice").length;
    check("点「显示更多」后列表变长", after > rows, rows + " → " + after);
  } else {
    check("点「显示更多」后列表变长", false, "按钮是隐藏的");
  }

  // ---- 类别筛选 ----
  const chip = (f) => [...document.querySelectorAll("#voice-filters .chip")]
    .find((b) => b.dataset.filter === f);
  chip("zh").click();
  const zhRows = document.querySelectorAll("#voices .voice").length;
  check("筛选「中文」后只剩中文音色", zhRows > 0 && zhRows <= 16,
    zhRows + " 项 · " + $("voice-shown").textContent);
  chip("multi").click();
  const mlRows = document.querySelectorAll("#voices .voice").length;
  check("筛选「多语言」只剩多语言音色", mlRows > 0 && mlRows <= 20, mlRows + " 项");
  chip("all").click();
  check("切回「全部」恢复完整列表",
    document.querySelectorAll("#voices .voice").length === 26, "");

  // ---- 一键风格：音色 + 语速/音调要一起变，且必须与按钮上标注的数值完全一致 ----
  const preset = document.querySelector("#presets .chip");
  check("一键风格按钮已渲染", document.querySelectorAll("#presets .chip").length >= 5,
    document.querySelectorAll("#presets .chip").length + " 个");
  if (preset) {
    const title = preset.title || "";
    const wantRate = (title.match(/语速 ([+-]?\\d+)%/) || [])[1];
    const wantPitch = (title.match(/音调 ([+-]?\\d+)Hz/) || [])[1];
    const wantVolume = (title.match(/音量 ([+-]?\\d+)%/) || [])[1];
    const wantVoice = (title.match(/音色 (\\S+?)，/) || [])[1];
    preset.click();
    check("套用风格的语速与按钮标注一致", $("rate").value === wantRate,
      $("rate").value + " vs 标注 " + wantRate);
    check("套用风格的音调与按钮标注一致", $("pitch").value === wantPitch,
      $("pitch").value + " vs 标注 " + wantPitch);
    check("套用风格的音量与按钮标注一致", $("volume").value === wantVolume,
      $("volume").value + " vs 标注 " + wantVolume);
    check("套用风格后语速标签同步", /[+-]\\d+%/.test($("rate-val").textContent),
      $("rate-val").textContent);
    const on = document.querySelector("#voices .voice.on");
    check("套用风格后选中音色跟着变", !!on && on.title.startsWith(wantVoice),
      (on?.title || "") + " vs 标注 " + wantVoice);
  }

  $("text").value = ${JSON.stringify(SAMPLE)};
  $("text").dispatchEvent(new Event("input"));
  check("字数统计已更新", /字/.test($("stats").textContent), $("stats").textContent);

  $("chunk").value = "200";
  $("chunk").dispatchEvent(new Event("input"));
  const segText = $("stats").textContent;
  const seg = Number((segText.match(/(\\d+)\\s*段/) || [])[1] || 0);
  check("长文本被切成多段", seg > 1, segText);

  $("voice-search").value = "晓萱";
  $("voice-search").dispatchEvent(new Event("input"));
  check("能搜到实测恢复的中文音色晓萱", $("voices").textContent.includes("晓萱"),
    document.querySelectorAll("#voices .voice").length + " 项");
  $("voice-search").value = "粤语";
  $("voice-search").dispatchEvent(new Event("input"));
  const filtered = document.querySelectorAll("#voices .voice").length;
  check("搜索能过滤音色", filtered > 0 && filtered < 25, filtered + " 项");
  $("voice-search").value = "";
  $("voice-search").dispatchEvent(new Event("input"));

  $("btn-go").click();
  check("开始后按钮被禁用", $("btn-go").disabled === true, "");
  check("取消按钮出现", $("btn-cancel").hidden === false, "");

  const started = Date.now();
  const done = await waitFor(() => !$("result").hidden && $("player").src, 150000);
  const elapsed = ((Date.now() - started) / 1000).toFixed(1);
  check("在超时前拿到成品", done, $("progress-text").textContent);
  check("进度条走满", $("bar").style.width === "100%", $("bar").style.width);
  check("结果区可见且有实际高度", $("result").getBoundingClientRect().height > 100,
    $("result").getBoundingClientRect().height + "px");
  check("成品信息含时长与帧数",
    /时长/.test($("result-stats").textContent) && /帧/.test($("result-stats").textContent),
    $("result-stats").textContent);
  check("结束后按钮恢复可用", $("btn-go").disabled === false, "");

  const dur = await new Promise((res) => {
    const a = $("player");
    if (a.readyState >= 1) return res(a.duration);
    a.addEventListener("loadedmetadata", () => res(a.duration), { once: true });
    a.addEventListener("error", () => res(null), { once: true });
    setTimeout(() => res(null), 20000);
  });
  check("播放器能解码成品并读出时长", typeof dur === "number" && dur > 3, dur);

  let downloaded = null;
  const orig = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function () { downloaded = this.download; };
  $("btn-download").click();
  HTMLAnchorElement.prototype.click = orig;
  check("下载触发的是 .mp3 文件名", /\\.mp3$/.test(downloaded || ""), downloaded);

  check("有可打开的播放地址", !!$("btn-open"), "");

  return {
    checks,
    stats: {
      elapsed: Number(elapsed),
      audioSeconds: typeof dur === "number" ? Number(dur.toFixed(2)) : null,
      resultText: $("result-stats").textContent,
      voice: $("voice-count").textContent,
      logLines: document.querySelectorAll("#log div").length,
    },
  };
})()`;

const EVAL_B = `(async () => {
  const checks = [];
  const check = (n, ok, d = "") => checks.push({ n, ok: !!ok, d: String(d) });
  const $ = (id) => document.getElementById(id);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const waitFor = async (fn, ms) => {
    const t = Date.now();
    while (Date.now() - t < ms) { if (fn()) return true; await sleep(200); }
    return false;
  };

  // 取消路径
  $("btn-go").click();
  await sleep(700);
  const hadCancel = $("btn-cancel").hidden === false;
  $("btn-cancel").click();
  const restored = await waitFor(() => $("btn-go").disabled === false, 25000);
  check("取消按钮可用且取消后按钮恢复", hadCancel && restored, $("progress-text").textContent);
  check("取消后提示为已取消", /取消/.test($("progress-text").textContent), $("progress-text").textContent);

  // 清空
  $("btn-clear").click();
  check("清空后结果区收起", $("result").hidden === true, "");
  check("清空后文本为空", $("text").value === "", "");
  check("清空后进度归零", $("bar").style.width === "0%" || $("bar").style.width === "", $("bar").style.width);

  // 空文本点生成应有提示而不是崩掉
  $("btn-go").click();
  await sleep(400);
  const noteShown = !$("result-note").hidden;
  check("空文本时给出提示", noteShown, $("result-note").textContent);
  check("空文本时没有卡在运行态", $("btn-go").disabled === false, "");

  // 示例按钮
  $("btn-sample").click();
  check("示例按钮填入文字", $("text").value.length > 50, $("text").value.length + " 字");

  // 说明弹窗
  $("btn-about").click();
  await sleep(250);
  const opened = document.getElementById("about").open === true;
  check("说明弹窗能打开", opened, "");
  if (opened) document.getElementById("about").close();

  return { checks };
})()`;

const server = await startServer(5312);
const browser = await launchBrowser(9334);
const errors = [];
const logs = [];

// 设了 E2E_LIVE_URL 就直接打线上网址，跳过本地服务 —— 这能证明"部署出去的那份文件真的能跑"
const LIVE = process.env.E2E_LIVE_URL || "";
const PAGE_URL = LIVE || "http://127.0.0.1:5312/index.html";

try {
  const cdp = new CDP(browser.ws);
  cdp.on((msg) => {
    if (msg.method === "Runtime.consoleAPICalled") {
      logs.push((msg.params.args || []).map((a) => a.value ?? a.description ?? "").join(" "));
    } else if (msg.method === "Runtime.exceptionThrown") {
      errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
    }
  });

  const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
  await cdp.send("Runtime.enable", {}, sessionId);
  await cdp.send("Page.enable", {}, sessionId);
  await cdp.send("Emulation.setDeviceMetricsOverride",
    { width: 1280, height: 1400, deviceScaleFactor: 1, mobile: false }, sessionId);

  console.log(`[1/4] 打开页面 ${PAGE_URL}`);
  await cdp.send("Page.navigate", { url: PAGE_URL }, sessionId);

  const ready = Date.now() + 30000;
  for (;;) {
    const r = await cdp.send("Runtime.evaluate",
      { expression: "!!window.__uiReady", returnByValue: true }, sessionId);
    if (r.result.value) break;
    if (Date.now() > ready) throw new Error("页面初始化超时");
    await sleep(300);
  }
  console.log("[2/4] 页面已就绪，开始走完整流程");

  const a = await cdp.send("Runtime.evaluate",
    { expression: EVAL_A, awaitPromise: true, returnByValue: true }, sessionId);
  if (a.exceptionDetails) throw new Error("第一段脚本异常：" + JSON.stringify(a.exceptionDetails));
  const first = a.result.value;

  fs.mkdirSync(path.join(ROOT, "_verify"), { recursive: true });
  const png = await cdp.send("Page.captureScreenshot",
    { format: "png", captureBeyondViewport: true }, sessionId);
  fs.writeFileSync(path.join(ROOT, "_verify", "app-result.png"), Buffer.from(png.data, "base64"));
  console.log("[3/4] 已截图 _verify/app-result.png");

  const b = await cdp.send("Runtime.evaluate",
    { expression: EVAL_B, awaitPromise: true, returnByValue: true }, sessionId);
  if (b.exceptionDetails) throw new Error("第二段脚本异常：" + JSON.stringify(b.exceptionDetails));
  const second = b.result.value;

  const checks = [...first.checks, ...second.checks];
  console.log("[4/4] 流程跑完\n");
  for (const c of checks) {
    console.log(`  ${c.ok ? "ok  " : "FAIL"} ${c.n}${c.d ? "  " + c.d : ""}`);
  }
  console.log("\n--- 数据 ---");
  console.log(JSON.stringify(first.stats, null, 1));
  const failed = checks.filter((c) => !c.ok);
  console.log(`\n共 ${checks.length} 项，失败 ${failed.length} 项`);
  if (errors.length) {
    console.log("\n--- 页面异常 ---");
    for (const e of errors) console.log("  " + e);
  }
  process.exit(failed.length || errors.length ? 1 : 0);
} finally {
  browser.close();
  server.close();
}
