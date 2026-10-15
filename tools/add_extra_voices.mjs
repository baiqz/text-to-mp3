/**
 * 把「官方清单之外、实测可用」的音色合并进 voices.json。
 *
 * 数据来源：tests/browser/voice_report.html 与 voice_report2.html 两轮真机巡检
 * （用真实 Edge 逐个合成，能拿到有效 MP3 帧才算可用）。
 *
 * 只收录两类：
 *   1. 中文音色（服务端仍接受，但 voices/list 不返回）；
 *   2. Azure 多语言音色（MultilingualNeural，能读中文，带口音）。
 * 已实测被服务端明确拒绝的（云泽、晓墨、云枫…）一律不写入 —— 写进去只会让用户选了就报错。
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const FILE = path.join(ROOT, "voices.json");

/** 实测可用、且官方 voices/list 里没有的音色 */
const EXTRAS = [
  {
    id: "zh-CN-XiaoxuanNeural", name: "晓萱", locale: "zh-CN", localeName: "普通话",
    gender: "Female", extra: true, note: "不在官方清单里，实测可用 · 女声，干练利落",
  },
  {
    id: "en-US-AvaMultilingualNeural", name: "Ava", locale: "multi", localeName: "多语言",
    gender: "Female", extra: true, note: "Azure 多语言音色，能读中文，带英语口音",
  },
  {
    id: "en-US-AndrewMultilingualNeural", name: "Andrew", locale: "multi", localeName: "多语言",
    gender: "Male", extra: true, note: "Azure 多语言音色，能读中文，带英语口音",
  },
  {
    id: "en-US-EmmaMultilingualNeural", name: "Emma", locale: "multi", localeName: "多语言",
    gender: "Female", extra: true, note: "Azure 多语言音色，能读中文，带英语口音",
  },
  {
    id: "en-US-BrianMultilingualNeural", name: "Brian", locale: "multi", localeName: "多语言",
    gender: "Male", extra: true, note: "Azure 多语言音色，能读中文，带英语口音",
  },
  {
    id: "fr-FR-VivienneMultilingualNeural", name: "Vivienne", locale: "multi", localeName: "多语言",
    gender: "Female", extra: true, note: "Azure 多语言音色，能读中文，带法语口音",
  },
  {
    id: "fr-FR-RemyMultilingualNeural", name: "Rémy", locale: "multi", localeName: "多语言",
    gender: "Male", extra: true, note: "Azure 多语言音色，能读中文，带法语口音",
  },
  {
    id: "de-DE-SeraphinaMultilingualNeural", name: "Seraphina", locale: "multi", localeName: "多语言",
    gender: "Female", extra: true, note: "Azure 多语言音色，能读中文，带德语口音",
  },
  {
    id: "de-DE-FlorianMultilingualNeural", name: "Florian", locale: "multi", localeName: "多语言",
    gender: "Male", extra: true, note: "Azure 多语言音色，能读中文，带德语口音",
  },
  {
    id: "pt-BR-ThalitaMultilingualNeural", name: "Thalita", locale: "multi", localeName: "多语言",
    gender: "Female", extra: true, note: "Azure 多语言音色，能读中文，带葡萄牙语口音",
  },
  {
    id: "ko-KR-HyunsuMultilingualNeural", name: "Hyunsu", locale: "multi", localeName: "多语言",
    gender: "Male", extra: true, note: "Azure 多语言音色，能读中文，带韩语口音",
  },
];

const data = JSON.parse(fs.readFileSync(FILE, "utf8"));
const ids = new Set(data.voices.map((v) => v.id));

let added = 0;
for (const e of EXTRAS) {
  if (ids.has(e.id)) continue;
  data.voices.push(e);
  ids.add(e.id);
  added += 1;
}

// 记录这批扩展音色的巡检日期，与官方清单的 testedAt 区分开
data.extraTestedAt = "2026-10-15";

// 清掉历史遗留的「extras 残留」（若重复运行不会产生重复项）
fs.writeFileSync(FILE, JSON.stringify(data, null, 1) + "\n", "utf8");

const zh = data.voices.filter((v) => v.locale.startsWith("zh")).length;
const extra = data.voices.filter((v) => v.extra).length;
console.log(`新增 ${added} 个 → 现在共 ${data.voices.length} 个音色（中文 ${zh} 个，其中扩展 ${extra} 个）`);
