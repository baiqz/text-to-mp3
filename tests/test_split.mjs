/**
 * 切分器交叉验证：与本地 Python 版对同一批文本的输出逐块比对。
 * 只要有一块不一致，就说明两个实现已经分叉。
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { splitText, cleanText, analyze } from "../src/split.js";

const dir = path.dirname(fileURLToPath(import.meta.url));
const vectors = JSON.parse(fs.readFileSync(path.join(dir, "split_vectors.json"), "utf8"));

const TEXTS = {
  markdown: "# 标题\n\n这是**加粗**文字，还有[链接](http://x.com)和`代码`。\n\n---\n\n> 引用内容\n\n- 列表项一\n- 列表项二\n\n![图](a.png)\n\n最后一段。",
  mixed: "第一段内容。“引号测试”，换行\n继续。\n\nSecond paragraph here. It has two sentences! And a third one, with commas.\n\n第三段，短。",
  long_para: "这是一句测试用的中文句子，用来观察长段落切分。它以标点结尾，然后继续下去。".repeat(12),
  no_punct: "啊".repeat(900),
  decor: "———\n\n正常文字在这里。\n\n=======\n\n结束。",
  english: "The quick brown fox jumps over the lazy dog. ".repeat(30) +
    "\n\nAnother paragraph follows here, mostly to exercise packing behaviour across paragraph boundaries and to make sure nothing is lost in translation between implementations.",
};

let pass = 0;
let fail = 0;
const check = (name, ok, detail) => {
  if (ok) { pass += 1; } else { fail += 1; console.log(`  FAIL ${name}  ${detail ?? ""}`); }
};

console.log("=== 1. 与 Python 版逐块比对 ===");
for (const v of vectors) {
  const text = TEXTS[v.name];
  const got = splitText(text, v.max);
  const same = got.length === v.chunks.length && got.every((c, i) => c === v.chunks[i]);
  check(`${v.name} / 上限 ${v.max}`, same,
    same ? "" : `块数 ${got.length} vs ${v.chunks.length}\n     got  ${JSON.stringify(got.slice(0, 3))}\n     want ${JSON.stringify(v.chunks.slice(0, 3))}`);

  if (!same) continue;
  check(`${v.name} / 上限 ${v.max} 内容无损`,
    got.join("").replace(/\n/g, "") === v.joined_no_nl, "");
}

console.log("");
console.log("=== 2. 切分不变式 ===");
const samples = Object.values(TEXTS);
for (const text of samples) {
  for (const max of [200, 500, 1200]) {
    const chunks = splitText(text, max);
    check(`不超过上限 (${max})`, chunks.every((c) => c.length <= max),
      `最长 ${Math.max(...chunks.map((c) => c.length))}`);
    check(`不含空白块 (${max})`, chunks.every((c) => c.trim().length > 0), "");
    check(`块内不残留装饰行 (${max})`, chunks.every((c) => !/^[=\-—_*~]{3,}$/m.test(c)), "");
  }
}
check("空文本返回空数组", splitText("").length === 0);
check("纯符号文本返回空数组", splitText("———\n\n===\n\n***").length === 0);
check("超长无标点文本能被硬切", splitText("啊".repeat(2000), 200).length >= 10,
  splitText("啊".repeat(2000), 200).length);
check("上限有下限保护（过小值被抬到 200）",
  splitText("甲".repeat(1000), 10).every((c) => c.length <= 200), "");

console.log("");
console.log("=== 3. 文本清理 ===");
check("Markdown 标题符号被去掉", !cleanText(TEXTS.markdown).includes("#"), "");
check("链接保留文字去掉地址", cleanText("[点我](http://x.com)") === "点我",
  cleanText("[点我](http://x.com)"));
check("代码块被清除", !cleanText("前\n```\ncode\n```\n后").includes("code"),
  cleanText("前\n```\ncode\n```\n后"));
check("全角空格被归一", !cleanText("甲\u3000乙").includes("\u3000"), "");
check("制表符被归一", !cleanText("甲\t乙").includes("\t"), "");

console.log("");
console.log("=== 4. 规模统计 ===");
const stat = analyze("这是一段中文。\n\nAnd some English words here.");
check("中文字数统计正确", stat.cjk === 6, stat.cjk);
check("英文词数统计正确", stat.words === 5, stat.words);
check("段落数统计正确", stat.paragraphs === 2, stat.paragraphs);
check("预估时长大于 0", stat.estSeconds > 0, stat.estSeconds);

console.log("");
console.log(`通过 ${pass} / 失败 ${fail}`);
process.exit(fail ? 1 : 0);
