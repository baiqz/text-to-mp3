/** 核对两个中文名批处理文件在线上是否与本地逐字节一致 */
import fs from "node:fs";
import crypto from "node:crypto";

const base = "https://baiqz.github.io/text-to-mp3/";
const files = [
  ["local/诊断.bat", "local/%E8%AF%8A%E6%96%AD.bat"],
  ["local/停止.bat", "local/%E5%81%9C%E6%AD%A2.bat"],
];
const sha = (b) => crypto.createHash("sha256").update(b).digest("hex").slice(0, 16);

for (const [local, path] of files) {
  const r = await fetch(base + path, { headers: { "Cache-Control": "no-cache" } });
  const live = Buffer.from(await r.arrayBuffer());
  const mine = fs.readFileSync(local);
  const same = Buffer.compare(mine, live) === 0;
  console.log(`${same ? "一致  " : "不一致"}  ${local}   HTTP ${r.status}  线上 ${live.length} 字节 / 本地 ${mine.length} 字节`);
  console.log(`        本地 ${sha(mine)}   线上 ${sha(live)}`);
}
