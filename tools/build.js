/**
 * 构建：把 src/ 下的模块与声库数据内联成仓库根目录的单文件 index.html
 *
 * 为什么是单文件：
 *  - GitHub Pages 直接托管根目录 index.html 即可，没有构建步骤；
 *  - 用户把 index.html 存到本地双击也能用（ES 模块在 file:// 下会被浏览器拦住，
 *    内联成普通脚本就没这个问题）。
 *
 * 构建过程本身就是一次静态自检：残留的外链脚本、没剥干净的 import/export、
 * 重名的顶层声明、JS 里引用了不存在的 DOM id —— 任何一条都会让构建失败。
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SRC = path.join(ROOT, "src");

const read = (p) => fs.readFileSync(path.join(ROOT, p), "utf8");

const MODULES = ["token.js", "mp3.js", "split.js", "tts.js", "ui.js"];

/** 去掉模块语法，让代码能在同一个作用域里顺序执行 */
function stripModule(code, name) {
  const before = code;
  let out = code
    .replace(/^\s*import[\s\S]*?from\s+["'][^"']+["'];?[ \t]*$/gm, "")
    .replace(/^\s*export\s*\{[^}]*\};?[ \t]*$/gm, "")
    .replace(/^\s*export\s+/gm, "");
  if (/^\s*(import|export)\s/m.test(out)) {
    throw new Error(`${name} 里仍有没剥掉的 import/export`);
  }
  if (before.length === out.length && /export/.test(before)) {
    throw new Error(`${name} 的 export 未被处理`);
  }
  return out;
}

const TOP_LEVEL = /^(?:const|let|var)\s+([A-Za-z_$][\w$]*)|^(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)|^class\s+([A-Za-z_$][\w$]*)/gm;

function topLevelNames(code) {
  const names = [];
  for (const m of code.matchAll(TOP_LEVEL)) names.push(m[1] || m[2] || m[3]);
  return names;
}

function main() {
  const voices = JSON.parse(read("voices.json"));
  const alive = voices.voices.filter((v) => !v.dead);
  const chinese = alive.filter((v) => v.locale.startsWith("zh"));
  if (alive.length < 50) throw new Error(`声库数据异常：只剩 ${alive.length} 个可用音色`);
  if (!chinese.some((v) => v.id === "zh-CN-XiaoxiaoNeural")) {
    throw new Error("声库里没有默认中文音色，数据可能不对");
  }

  const payload = {
    defaultVoice: "zh-CN-XiaoxiaoNeural",
    fallback: "zh-CN-XiaoxiaoNeural",
    voices: voices.voices,
    dead: voices.dead ?? [],
    testedAt: voices.testedAt ?? "",
  };

  // 合并模块代码，顺带检查重名
  const seen = new Map();
  const parts = MODULES.map((name) => {
    const code = stripModule(read(path.join("src", name)), name);
    for (const n of topLevelNames(code)) {
      if (seen.has(n)) {
        throw new Error(`顶层名称冲突：${n}（${seen.get(n)} 与 ${name}）—— 内联后会互相覆盖`);
      }
      seen.set(n, name);
    }
    return `/* ===== ${name} ===== */\n${code.trim()}`;
  });

  const app = `(function(){\n"use strict";\n${parts.join("\n\n")}\n})();`;

  const html = read("src/index.html")
    .replace("/*__CSS__*/", read("src/style.css").trim())
    .replace("/*__VOICES__*/", `var VOICES = ${JSON.stringify(payload)};`)
    .replace("/*__APP__*/", app);

  // ---- 产出校验 ----
  const problems = [];
  if (/<script[^>]+src=/i.test(html)) problems.push("产物里仍有外链脚本");
  if (/^\s*(import|export)\s/m.test(html)) problems.push("产物里仍有 import/export");
  if (/\/\*__[A-Z]+__\*\//.test(html)) problems.push("有未替换的占位符");

  // 绝对路径在 GitHub Pages 的项目子路径下会 404，必须用相对路径
  const absRefs = [
    ...html.matchAll(/(?:src|href)\s*=\s*["']\/[^"']*["']/gi),
    ...html.matchAll(/fetch\(\s*["']\/[^"']*["']/g),
  ].map((m) => m[0]);
  if (absRefs.length) {
    problems.push(`存在绝对路径引用（子路径下会 404）：${absRefs.slice(0, 3).join(", ")}`);
  }

  // JS 里引用的 DOM id 必须都存在于 HTML 中
  const htmlIds = new Set([...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]));
  const usedIds = new Set([...app.matchAll(/\$\("([^"]+)"\)/g)].map((m) => m[1]));
  for (const id of usedIds) {
    if (!htmlIds.has(id)) problems.push(`JS 引用了不存在的元素 id：${id}`);
  }

  if (problems.length) {
    console.error("构建失败：");
    for (const p of problems) console.error("  - " + p);
    process.exit(1);
  }

  const out = path.join(ROOT, "index.html");
  fs.writeFileSync(out, html, "utf8");
  console.log(`已生成 index.html  ${(Buffer.byteLength(html) / 1024).toFixed(1)} KB`);
  console.log(`  内联模块 ${MODULES.length} 个，顶层声明 ${seen.size} 个`);
  console.log(`  声库 ${alive.length} 个可用音色（中文 ${chinese.length} 个），已排除 ${payload.dead.length} 个已下架音色`);
  console.log(`  引用 DOM id ${usedIds.size} 个，全部存在`);
}

main();
