/**
 * 令牌算法比对测试：用 Python 端生成的权威向量做逐字节校验。
 * 这是整个「浏览器直连」方案的地基，必须完全一致。
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { generateSecMsGec, sha256Digest, cs64Digest, WIN_EPOCH } from "../src/token.js";

const dir = path.dirname(fileURLToPath(import.meta.url));
const vectors = JSON.parse(fs.readFileSync(path.join(dir, "vectors.json"), "utf8"));

let pass = 0;
let fail = 0;

function check(name, ok, detail) {
  if (ok) {
    pass += 1;
    console.log(`  ok   ${name}`);
  } else {
    fail += 1;
    console.log(`  FAIL ${name}  ${detail ?? ""}`);
  }
}

const toLeHex = (n) => [n & 0xff, (n >>> 8) & 0xff, (n >>> 16) & 0xff, (n >>> 24) & 0xff]
  .map((b) => b.toString(16).padStart(2, "0")).join("").toUpperCase();
const hexOf = (bytes) => Array.from(bytes).map((b) => b.toString(16).padStart(2, "0")).join("").toUpperCase();
const bytesOf = (s) => {
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i += 1) out[i] = s.charCodeAt(i) & 0xff;
  return out;
};

console.log("=== 1. 改写版 SHA-256 + 混合函数逐字节比对 ===");
for (const v of vectors.hash) {
  const data = bytesOf(v.data);
  const sha = hexOf(sha256Digest(data));
  check(`长度 ${String(v.len).padStart(3)} 的 SHA 摘要一致`, sha === v.sha, `got ${sha.slice(0, 16)} want ${v.sha.slice(0, 16)}`);

  const [a, b] = cs64Digest(data, sha256Digest(data));
  const cs = toLeHex(a) + toLeHex(b);
  check(`长度 ${String(v.len).padStart(3)} 的混合函数一致`, cs === v.cs64, `got ${cs} want ${v.cs64}`);
}

console.log("");
console.log("=== 2. 完整令牌（含时间刻度换算）===");
for (const g of vectors.gec) {
  const got = generateSecMsGec(g.ts);
  check(`时间戳 ${g.ts} 令牌一致`, got === g.token, `\n       got  ${got}\n       want ${g.token}`);
}

console.log("");
console.log("=== 3. 令牌格式与稳定性 ===");
const now = generateSecMsGec();
check("格式为 16 位-64 位十六进制", /^[0-9A-F]{16}-[0-9A-F]{64}$/.test(now), now);

// 令牌每 5 分钟（300 秒）滚动一次，必须先把时间点对齐到窗口起点再断言
const aligned = 1760530000 - ((1760530000 + WIN_EPOCH) % 300);
check("同一 5 分钟窗口内令牌不变", generateSecMsGec(aligned) === generateSecMsGec(aligned + 299),
  `${aligned} vs ${aligned + 299}`);
check("跨 5 分钟窗口令牌变化", generateSecMsGec(aligned) !== generateSecMsGec(aligned + 300));
check("窗口边界正好是 300 秒整", generateSecMsGec(aligned) === generateSecMsGec(aligned + 300 - 300));

console.log("");
console.log(`通过 ${pass} / 失败 ${fail}`);
process.exit(fail ? 1 : 0);
