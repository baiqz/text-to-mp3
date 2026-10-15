/**
 * 长文本切分器（浏览器版，逻辑与本地 Python 版一致）
 *
 * 原则只有一条：**宁可块小，不可断句**。在句子中间断开，合成语音的语调
 * 会直接"摔在地上"——上一句没了尾音，下一句从半口气起头。
 * 因此严格按层级递降切分：
 *
 *   段落 → 句末标点（。！？!?…；） → 停顿标点（，,、：:）） → 空格 → 硬切
 *
 * 切完再顺序装箱，合并到接近 maxChars，减少请求次数。
 */

const LEVEL_PATTERNS = [
  /(?<=[。！？!?…；;])/,                        // 句末
  /(?<=[，,、：:）)】」』”])/,                   // 停顿
  /(?<=[ \t\u3000])/,                          // 空格（英文为主）
];

// 纯符号行：整行都是这些字符时直接丢弃（读出来是"破折号破折号破折号"）
const DECOR_LINE = /^[\s\-—_=*~#・·。.,，、:：;；!！?？>|/\\\[\]()（）"'“”‘’]+$/;

const MD_RULES = [
  [/```[\s\S]*?```/g, " "],                    // 代码块
  [/`([^`]*)`/g, "$1"],                        // 行内代码
  [/!\[[^\]]*\]\([^)]*\)/g, " "],              // 图片
  [/\[([^\]]*)\]\([^)]*\)/g, "$1"],            // 链接 → 保留文字
  [/^\s{0,3}#{1,6}\s+/gm, ""],                 // 标题
  [/^\s*>\s?/gm, ""],                          // 引用
  [/^\s*[-*+]\s+/gm, ""],                      // 无序列表
  [/^\s*\d+[.)]\s+/gm, ""],                    // 有序列表
  [/\*\*([^*]*)\*\*/g, "$1"],                  // 粗体
  [/(?<!\w)\*([^*\n]+)\*(?!\w)/g, "$1"],       // 斜体
  [/_{2,}/g, ""],                              // 下划线
];

/** 归一化空白、清理 Markdown 标记与纯装饰行 */
export function cleanText(text, { stripMarkdown = true } = {}) {
  if (!text) return "";

  let s = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  s = s.replace(/\u3000/g, " ").replace(/\t/g, " ").replace(/\ufeff/g, "");

  if (stripMarkdown) {
    for (const [re, repl] of MD_RULES) s = s.replace(re, repl);
  }

  const kept = [];
  for (const line of s.split("\n")) {
    if (!line.trim()) { kept.push(""); continue; }
    if (DECOR_LINE.test(line.trim())) continue;          // 丢弃纯符号行
    kept.push(line.replace(/ {2,}/g, " ").trim());
  }

  return kept.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

/** 硬切：按码点切，避免把代理对（emoji）劈成半个字符 */
function hardCut(seg, maxChars) {
  const points = Array.from(seg);
  const out = [];
  for (let i = 0; i < points.length; i += maxChars) {
    out.push(points.slice(i, i + maxChars).join(""));
  }
  return out;
}

function breakSeg(seg, maxChars, level = 0) {
  if (seg.length <= maxChars) return [seg];
  if (level >= LEVEL_PATTERNS.length) return hardCut(seg, maxChars);

  const pieces = seg.split(LEVEL_PATTERNS[level]).filter(Boolean);
  if (pieces.length <= 1) return breakSeg(seg, maxChars, level + 1);

  const out = [];
  for (const p of pieces) out.push(...breakSeg(p, maxChars, level + 1));
  return out;
}

/** 顺序装箱：累加到接近 maxChars 为止（块间的换行会被保留，避免两段连读） */
function pack(atoms, maxChars) {
  const chunks = [];
  let buf = "";
  for (const a of atoms) {
    if (!a) continue;
    if (!a.trim()) {
      if (buf && !buf.endsWith("\n")) buf += "\n";
      continue;
    }
    if (buf && buf.length + a.length > maxChars) {
      chunks.push(buf);
      buf = a;
    } else {
      buf += a;
    }
  }
  if (buf) chunks.push(buf);

  // 收尾碎片并回前一块（仍不超限）
  if (chunks.length >= 2) {
    const tail = chunks[chunks.length - 1];
    if (tail.trim().length < maxChars * 0.25) {
      const merged = chunks[chunks.length - 2] + tail;
      if (merged.trim().length <= maxChars) {
        chunks[chunks.length - 2] = merged;
        chunks.pop();
      }
    }
  }
  return chunks;
}

/** 把任意长度的文本切成适合逐块合成的片段列表 */
export function splitText(text, maxChars = 1200, { stripMarkdown = true } = {}) {
  if (!text) return [];
  const limit = Math.max(200, Math.floor(maxChars));

  const cleaned = cleanText(text, { stripMarkdown });
  if (!cleaned) return [];

  const atoms = [];
  for (const raw of cleaned.split(/\n\s*\n/)) {
    const para = raw.trim();
    if (!para) continue;
    if (para.length <= limit) {
      atoms.push(`${para}\n`);
    } else {
      const sub = breakSeg(para, limit);
      sub.forEach((s, i) => atoms.push(i === sub.length - 1 ? `${s}\n` : s));
    }
  }

  return pack(atoms, limit).map((c) => c.trim()).filter(Boolean);
}

/** 粗略估算朗读时长：中文约 4.2 字/秒，英文约 2.9 词/秒 */
export function estimateSeconds(text) {
  if (!text) return 0;
  const count = (re) => (text.match(re) || []).length;
  const cjk = count(/[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]/g);
  const words = count(/[A-Za-z]+(?:'[A-Za-z]+)?/g);
  const digits = count(/\d+/g);
  const pauses = count(/[，,、：:；;]/g) * 0.18;
  const stops = count(/[。！？!?…\n]/g) * 0.35;
  return Math.max(0, cjk / 4.2 + words / 2.9 + digits * 0.35 + pauses + stops);
}

export function formatDuration(seconds) {
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${h} 小时 ${m} 分`;
  return m ? `${m} 分 ${s} 秒` : `${s} 秒`;
}

/** 统计文本规模与预估时长 */
export function analyze(text) {
  const cleaned = text ? cleanText(text) : "";
  const count = (re) => (cleaned.match(re) || []).length;
  return {
    chars: (text || "").length,
    cjk: count(/[\u3400-\u4dbf\u4e00-\u9fff]/g),
    words: count(/[A-Za-z]+(?:'[A-Za-z]+)?/g),
    paragraphs: cleaned.split(/\n\s*\n/).filter((p) => p.trim()).length,
    estSeconds: estimateSeconds(cleaned),
  };
}
