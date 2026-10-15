/**
 * Microsoft Edge 朗读服务的访问令牌生成（Sec-MS-GEC v2）
 *
 * 该算法是纯 32 位整数运算（标准 SHA-256 + 一个自定义混合函数），
 * 因此可以逐位移植到浏览器里，用 Math.imul / 无符号移位复现。
 *
 * 关键点：官方用的是**经过改写的 SHA-256**（常量表与初始状态被替换过），
 * 与标准 SHA-256 结果不同，必须照原样实现，不能替换成 crypto.subtle。
 *
 * 已用 Python 端生成的测试向量逐字节比对通过（见 tests/test_token.mjs）。
 */

const MASK = 0xFFFFFFFF;

const TRUSTED_CLIENT_TOKEN = "6A5AA1D4EAFF4E9FB37E23D68491D6F4";
const WIN_EPOCH = 11644473600;

// Chromium 大版本号，需与令牌版本号一致
export const CHROMIUM_FULL_VERSION = "143.0.3650.75";
export const SEC_MS_GEC_VERSION = `2-${CHROMIUM_FULL_VERSION}`;

const SHA256_INITIAL = [
  0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
  0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
];
const SHA256_STATE = [
  0xfd9fbea2, 0x91243ce7, 0xbe499094, 0xcd2e1e0a,
  0x6c006f49, 0x04bd35ab, 0xfeb0a7e4, 0x03effbd2,
];
const SHA256_SCHEDULE = [
  0xeb24bf9e, 0x1558691f, 0xa0c09ee2, 0x0cfa32ba,
  0xa622ee8c, 0xd1dd9491, 0xc0dd25a0, 0xf9e70ab6,
  0xf0c31573, 0x7058ce4e, 0x7188e606, 0x00e5ab33,
  0xd258b060, 0x72766564,
];
const SHA256_ROUND_CONSTANTS = [
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
  0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
  0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
  0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
  0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
  0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];
const SHA256_ADJUSTMENT = 0xc9dc06a7;
const SHA256_SIGMA0_ADJUSTMENT = 0x059c0b80;
const SHA256_SIGMA1_ADJUSTMENT = 0x000b92c0;

const BLOCK_CONSTANTS = SHA256_ROUND_CONSTANTS.slice();
BLOCK_CONSTANTS[14] = SHA256_ADJUSTMENT;

const CS64_COEFFICIENTS = [
  [0xe82e91b1, 0xb32700d0], [0x44de24c6, 0x88dff51d],
  [0x3beb65bc, 0x29514038], [0xc89c5933, 0xb328cbdf],
  [0x8d1ec211, 0xd405b4d5], [0x314ef5d5, 0x26e55f3c],
  [0xe92491b1, 0x607801c6], [0xf0a5b9d5, 0x82b2c1fb],
  [0x06064cf3, 0xfa51a3da], [0xda79fa60, 0x207cc464],
  [0xbed20560, 0x3c3c74f3], [0x89f9c9f8, 0xd2d14b61],
  [0x35fa38c6, 0xbb04a2c1], [0xcb49e3c6, 0xc5626167],
];
const CS64_FACTOR = 0x82893933;
const CS64_ADJUSTMENT0 = 0xf2f90b1a;
const CS64_ADJUSTMENT1 = 0x0b1a0000;

// --- 32 位运算原语 -----------------------------------------------------------

const u32 = (x) => x >>> 0;

/** 32 位取模乘法（低 32 位） */
const mul = (a, b) => Math.imul(a, b) >>> 0;

/** 循环左移 16 位 */
const rol16 = (v) => (((v << 16) | (v >>> 16)) >>> 0);

/** 循环右移 */
const rotr = (x, k) => (((x >>> k) | ((x << (32 - k)) >>> 0)) >>> 0);

/** SHA-256 小写 sigma0（与标准一致，只是把组合移位保持到最后一并截断） */
function sigma0(value) {
  return (
    (value >>> 7) ^ ((value << 25) >>> 0) ^ (value >>> 18) ^
    ((value << 14) >>> 0) ^ (value >>> 3)
  ) >>> 0;
}

/** SHA-256 小写 sigma1 */
function sigma1(value) {
  return (
    (value >>> 17) ^ ((value << 15) >>> 0) ^ (value >>> 19) ^
    ((value << 13) >>> 0) ^ (value >>> 10)
  ) >>> 0;
}

/** 展开消息字 */
function expandSchedule(words, firstWord) {
  for (let i = firstWord; i < 64; i += 1) {
    const x = words[i - 2];
    const y = words[i - 15];
    const s1 = (
      (x >>> 17) ^ ((x << 15) >>> 0) ^ (x >>> 19) ^
      ((x << 13) >>> 0) ^ (x >>> 10)
    ) >>> 0;
    const s0 = (
      (y >>> 7) ^ ((y << 25) >>> 0) ^ (y >>> 18) ^
      ((y << 14) >>> 0) ^ (y >>> 3)
    ) >>> 0;
    words.push((s1 + words[i - 7] + s0 + words[i - 16]) >>> 0);
  }
}

/** 压缩函数（可从中途开始，复现官方的分段计算） */
function sha256Rounds(state, words, firstRound) {
  const constants = firstRound === 14 ? BLOCK_CONSTANTS : SHA256_ROUND_CONSTANTS;
  let a = state[0], b = state[1], c = state[2], d = state[3];
  let e = state[4], f = state[5], g = state[6], h = state[7];

  for (let i = firstRound; i < 64; i += 1) {
    const re = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
    const ra = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
    const t1 = (h + re + (g ^ (e & (f ^ g))) + constants[i] + words[i]) >>> 0;
    const t2 = (ra + ((a & b) | (c & (a | b)))) >>> 0;
    const na = (t1 + t2) >>> 0;
    const ne = (d + t1) >>> 0;
    h = g; g = f; f = e; e = ne;
    d = c; c = b; b = a; a = na;
  }
  return [a, b, c, d, e, f, g, h];
}

/** 前 7 字节的初始状态（离线预先算好，避免每个数据块重复计算） */
function sha256Init(head) {
  const words = new Array(14).fill(0);
  words[14] = ((head[0] << 16) | (head[1] << 8) | head[2]) >>> 0;
  words[15] = (
    (head[3] << 24) | (head[4] << 16) | (head[5] << 8) | head[6]
  ) >>> 0;

  for (let i = 16; i < 30; i += 1) {
    let value = SHA256_SCHEDULE[i - 16];
    if (i - 2 >= 14) {
      const s = sigma1(words[i - 2]);
      value += i === 16 ? (s ^ SHA256_SIGMA1_ADJUSTMENT) : s;
    }
    if (i - 7 >= 14) value += words[i - 7];
    if (i - 15 >= 14) {
      const s = sigma0(words[i - 15]);
      value += i === 29 ? (s ^ SHA256_SIGMA0_ADJUSTMENT) : s;
    }
    words.push(value >>> 0);
  }
  words.push((
    sigma1(words[28]) + words[23] + sigma0(words[15]) + words[14] +
    SHA256_ADJUSTMENT - SHA256_ROUND_CONSTANTS[14]
  ) >>> 0);

  expandSchedule(words, 31);
  const working = sha256Rounds(SHA256_STATE, words, 14);
  return SHA256_INITIAL.map((x, i) => (x + working[i]) >>> 0);
}

/** 改写的 SHA-256，返回 32 字节 */
export function sha256Digest(data) {
  const len = data.length;
  if (len < 7) throw new Error("输入至少需要 7 字节");

  const length = 57 + len;
  const padLen = ((55 - length) % 64 + 64) % 64;
  const padded = new Uint8Array(len - 7 + 1 + padLen + 8);
  padded.set(data.subarray(7), 0);
  padded[len - 7] = 0x80;

  // 大端写入 64 位比特长度
  const bits = BigInt(length) * 8n;
  const at = padded.length - 8;
  for (let i = 0; i < 8; i += 1) {
    padded[at + i] = Number((bits >> BigInt((7 - i) * 8)) & 0xffn);
  }

  let state = sha256Init(data.subarray(0, 7));
  for (let off = 0; off < padded.length; off += 64) {
    // 每个数据块都要用全新的消息字数组，否则会串到上一块的残留值
    const words = new Array(16);
    for (let i = 0; i < 16; i += 1) {
      const p = off + i * 4;
      words[i] = (
        (padded[p] << 24) | (padded[p + 1] << 16) | (padded[p + 2] << 8) | padded[p + 3]
      ) >>> 0;
    }
    expandSchedule(words, 16);
    const working = sha256Rounds(state, words, 0);
    state = state.map((x, i) => (x + working[i]) >>> 0);
  }

  const out = new Uint8Array(32);
  for (let i = 0; i < 8; i += 1) {
    out[i * 4] = (state[i] >>> 24) & 0xff;
    out[i * 4 + 1] = (state[i] >>> 16) & 0xff;
    out[i * 4 + 2] = (state[i] >>> 8) & 0xff;
    out[i * 4 + 3] = state[i] & 0xff;
  }
  return out;
}

/** 小端 32 位字序列（必要时补零到偶数个） */
function wordsLe4Even(data) {
  const padded = new Uint8Array(data.length + ((4 - (data.length % 4)) % 4));
  padded.set(data);
  const words = [];
  for (let i = 0; i < padded.length; i += 4) {
    words.push(
      (padded[i] | (padded[i + 1] << 8) | (padded[i + 2] << 16) |
        (padded[i + 3] << 24)) >>> 0
    );
  }
  if (words.length % 2) words.push(0);
  return words;
}

/** 自定义混合函数，返回两个字 */
export function cs64Digest(data, digest) {
  let keyA = (digest[0] | (digest[1] << 8) | (digest[2] << 16) | (digest[3] << 24)) >>> 0;
  let keyB = (digest[4] | (digest[5] << 8) | (digest[6] << 16) | (digest[7] << 24)) >>> 0;
  keyA = (keyA | 1) >>> 0;
  keyB = (keyB | 1) >>> 0;

  const factors = [mul(keyA, CS64_FACTOR), mul(keyB, CS64_FACTOR)];
  const terms = CS64_COEFFICIENTS.map((c, i) => mul(c[0], factors[i % 2]));
  const offsets = CS64_COEFFICIENTS.map((c) => c[1] >>> 0);

  const body = new Uint8Array(1 + data.length);
  body.set(data, 1);                     // 前置一个 0 字节
  const words = wordsLe4Even(body);

  for (let i = 0; i < words.length; i += 1) {
    const word = words[i];
    let term = mul(word, i % 2 === 0 ? keyA : keyB);
    let offset = mul(rol16(word), i % 2 === 0 ? 0xef0569fb : 0xc31713db);
    if (i === 0) {
      term = (term + mul(CS64_ADJUSTMENT0, factors[0])) >>> 0;
      offset = (offset + CS64_ADJUSTMENT1) >>> 0;
    }
    terms.push(term >>> 0);
    offsets.push(offset >>> 0);
  }

  let swapAcc = 0, swapLast = 0, state = 0, acc = 0;

  for (let i = 0; i < terms.length; i += 2) {
    const x = terms[i];
    const y = terms[i + 1];

    let t = rol16((offsets[i] + x) >>> 0);
    let q = mul(t, 0x76f50f34);
    swapAcc = (swapAcc + ((mul(rol16(q), 0xc3efea97) + q) >>> 0)) >>> 0;

    t = rol16((offsets[i + 1] + y) >>> 0);
    q = mul(t, 0x1881ce3c);
    swapLast = (mul(rol16(q), 0x35bd1ec9) + q) >>> 0;
    swapAcc = (swapAcc + swapLast) >>> 0;

    state = (mul(state, keyA) + x) >>> 0;
    state = mul(rol16(state), 0xcf98b111);
    state = mul(rol16(state), 0x87085b9f);
    state = mul(rol16(state), 0x12ceb96d);
    state = mul(rol16(state), 0x257e1d83);
    acc = (acc + state) >>> 0;

    t = (mul(state, keyB) + y) >>> 0;
    t = mul(rol16(t), 0xa27416f5);
    t = mul(rol16(t), 0xd38396ff);
    state = mul(rol16(mul(rol16(t), 0x7c932b89)), 0xbFA49F69);
    acc = (acc + state) >>> 0;
  }

  return [(swapLast ^ state) >>> 0, (swapAcc ^ acc) >>> 0];
}

/** 按小端字节序输出 32 位字（官方用的是 struct.pack("<I")） */
const toLeHex = (n) => [
  n & 0xff, (n >>> 8) & 0xff, (n >>> 16) & 0xff, (n >>> 24) & 0xff,
].map((b) => b.toString(16).padStart(2, "0")).join("").toUpperCase();

/**
 * 生成 Sec-MS-GEC 令牌。
 * @param {number} [unixTimestamp] 秒（可带小数），默认取当前时间
 * @returns {string} "十六位-六十四位"
 */
export function generateSecMsGec(unixTimestamp) {
  let ticks = (unixTimestamp === undefined ? Date.now() / 1000 : unixTimestamp) + WIN_EPOCH;
  ticks -= ticks % 300;                       // 向下取整到 5 分钟
  ticks *= 1e9 / 100;                         // 100 纳秒刻度
  const raw = `${ticks.toFixed(0)}${TRUSTED_CLIENT_TOKEN}`;

  const data = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) data[i] = raw.charCodeAt(i) & 0xff;

  const digest = sha256Digest(data);
  const [a, b] = cs64Digest(data, digest);
  const cs64 = toLeHex(a) + toLeHex(b);
  let sha = "";
  for (let i = 0; i < digest.length; i += 1) sha += digest[i].toString(16).padStart(2, "0");
  return `${cs64}-${sha.toUpperCase()}`;
}

export { TRUSTED_CLIENT_TOKEN, WIN_EPOCH };
