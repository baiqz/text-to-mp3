/**
 * MP3 帧处理交叉验证：与本地 Python 版逐字节比对，
 * 并覆盖真实会遇到的脏数据（残帧、垃圾前缀、自带 ID3 的片段）。
 */
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";
import {
  probe, cleanParts, buildId3v2, concatBytes, audioSpan, stripId3, Mp3Error,
} from "../src/mp3.js";

const dir = path.dirname(fileURLToPath(import.meta.url));
const fx = (n) => new Uint8Array(fs.readFileSync(path.join(dir, "fixtures", n)));
const vec = JSON.parse(fs.readFileSync(path.join(dir, "mp3_vectors.json"), "utf8"));

const sha = (bytes) => crypto.createHash("sha256").update(Buffer.from(bytes)).digest("hex");
const near = (a, b) => Math.abs(a - b) < 1e-9;

let pass = 0;
let fail = 0;
const check = (name, ok, detail) => {
  if (ok) { pass += 1; } else { fail += 1; console.log(`  FAIL ${name}  ${detail ?? ""}`); }
};

const A = fx("part_a.mp3");
const B = fx("part_b.mp3");
const C = fx("part_c.mp3");
const JOINED = fx("joined.mp3");

console.log("=== 1. 单段解析与 Python 一致 ===");
for (const [key, data] of [["a", A], ["b", B], ["c", C]]) {
  const info = probe(data);
  const want = vec[key].probe;
  check(`片段 ${key} 字节数一致`, data.length === vec[key].bytes, `${data.length} vs ${vec[key].bytes}`);
  check(`片段 ${key} 帧数一致`, info.frames === want.frames, `${info.frames} vs ${want.frames}`);
  check(`片段 ${key} 时长一致`, near(info.duration, want.duration), `${info.duration} vs ${want.duration}`);
  check(`片段 ${key} 采样率/码率/声道一致`,
    info.sampleRate === want.sample_rate && info.bitrate === want.bitrate && info.channels === want.channels,
    `${info.sampleRate}/${info.bitrate}/${info.channels}`);
  check(`片段 ${key} 无前导垃圾`, info.leadingJunk === want.leading_junk, info.leadingJunk);
  check(`片段 ${key} 尾部干净`, info.cleanTail === want.clean_tail, info.cleanTail);
}

console.log("");
console.log("=== 2. 拼接结果与 Python 逐字节相同 ===");
const { parts, frames, signature } = cleanParts([A, B, C]);
const joined = concatBytes(parts);
check("拼接字节数与 Python 相同", joined.length === vec.joined.bytes, `${joined.length} vs ${vec.joined.bytes}`);
check("拼接结果 SHA-256 与 Python 相同", sha(joined) === vec.joined.sha256,
  `\n     got  ${sha(joined)}\n     want ${vec.joined.sha256}`);
check("拼接帧数与 Python 相同", frames === vec.joined.probe.frames, `${frames} vs ${vec.joined.probe.frames}`);
check("音频参数一致", signature === "2-3-24000-48000-1", signature);

console.log("");
console.log("=== 3. ID3 标签与 Python 逐字节相同 ===");
const tag = buildId3v2({ title: "切片测试", encoder: "tts-web" });
check("ID3 标签字节数相同", tag.length === vec.id3.bytes,
  `${tag.length} vs ${vec.id3.bytes}`);
check("ID3 标签 SHA-256 相同", sha(tag) === vec.id3.sha256,
  `\n     got  ${sha(tag)}\n     want ${vec.id3.sha256}`);
check("带标签文件整体 SHA-256 相同", sha(concatBytes([tag, joined])) === vec.tagged_sha256, "");
check("标签能被正确跳过", probe(concatBytes([tag, joined])).frames === frames, "");
check("stripId3 处理标签后偏移正确", stripId3(concatBytes([tag, joined])).length === joined.length, "");

console.log("");
console.log("=== 4. 脏数据容错（真实场景）===");
const truncated = joined.subarray(0, joined.length - 37);
// 末帧 144 字节，切掉 37 字节后这一帧已不完整，应当被丢弃
check("尾部残帧被丢弃", probe(truncated).frames === frames - 1,
  `${probe(truncated).frames} vs ${frames - 1}`);
check("尾部残帧被标记", probe(truncated).cleanTail === false, "");
check("残帧不计入时长", probe(truncated).duration < probe(joined).duration, "");
const frameLen = joined.length / frames;      // 恒定码率，每帧等长
check("恒定码率下每帧等长", Number.isInteger(frameLen), frameLen);
check("残帧字节被排除在净帧流外",
  probe(truncated).bytes === probe(joined).bytes - frameLen,
  `${probe(truncated).bytes} vs ${probe(joined).bytes - frameLen}`);
check("残帧字节被标记为尾部垃圾",
  probe(truncated).trailingJunk === frameLen - 37, probe(truncated).trailingJunk);

const withJunk = concatBytes([new Uint8Array([0x00, 0x01, 0x02, 0xff]), joined]);
check("前导垃圾被跳过", probe(withJunk).frames === frames, probe(withJunk).frames);
check("前导垃圾长度被记录", probe(withJunk).leadingJunk === 4, probe(withJunk).leadingJunk);

const twoTags = concatBytes([tag, tag, joined]);
check("连续多个 ID3 标签都能跳过", audioSpan(twoTags).start === tag.length * 2, audioSpan(twoTags).start);

check("空数据抛错", (() => { try { probe(new Uint8Array(0)); return false; } catch (e) { return e instanceof Mp3Error; } })());
check("纯垃圾数据抛错", (() => {
  try { probe(new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9])); return false; } catch (e) { return e instanceof Mp3Error; }
})());
check("参数不一致的片段拒绝拼接", (() => {
  try {
    cleanParts([A, new Uint8Array([0xff, 0xfb, 0x90, 0x00, ...new Array(400).fill(0)])]);
    return false;
  } catch (e) { return e instanceof Mp3Error; }
})());

console.log("");
console.log("=== 5. 拼接顺序与完整性 ===");
const reversed = concatBytes(cleanParts([C, B, A]).parts);
check("顺序不同结果不同", sha(reversed) !== sha(joined), "");
check("重排后帧数不变", cleanParts([C, B, A]).frames === frames, "");
check("单段拼接等于其自身净帧流", sha(concatBytes(cleanParts([A]).parts)) === sha(A.subarray(
  audioSpan(A).start, audioSpan(A).end)), "");

console.log("");
console.log(`通过 ${pass} / 失败 ${fail}`);
process.exit(fail ? 1 : 0);
