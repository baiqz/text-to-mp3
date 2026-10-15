/**
 * MP3 帧级工具（浏览器版）
 *
 * 语音服务返回的是裸 MPEG-2 Layer III 帧流（24kHz / 48kbps / 单声道）。
 * 长文本被切成多段分别合成后再拼成一个文件，但**不能裸拼字节**：
 * 某段若带 ID3 标签或结尾有半帧，拼进去会让解码器错位——表现为杂音、
 * 时长变短甚至整个文件打不开。
 *
 * 所以拼接前逐段做帧级体检：跳过 ID3 标签、按帧长逐帧前进、丢弃尾部残帧，
 * 只输出干净的连续帧流。
 */

export class Mp3Error extends Error {}

const SAMPLE_RATES = {
  3: [44100, 48000, 32000],
  2: [22050, 24000, 16000],
  0: [11025, 12000, 8000],
};
const BITRATES_V1 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, -1];
const BITRATES_V2 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, -1];
const CHANNEL_MODES = ["立体声", "联合立体声", "双声道", "单声道"];

const MPEG_NAME = { 3: "MPEG-1", 2: "MPEG-2", 0: "MPEG-2.5" };
const LAYER_NAME = { 1: "Layer I", 2: "Layer II", 3: "Layer III" };

/** 解析 4 字节帧头；不是合法帧则返回 null */
export function parseFrameHeader(data, pos) {
  if (pos + 4 > data.length) return null;
  const b0 = data[pos];
  const b1 = data[pos + 1];
  const b2 = data[pos + 2];
  const b3 = data[pos + 3];

  if (b0 !== 0xff || (b1 & 0xe0) !== 0xe0) return null;

  const version = (b1 >> 3) & 0x03;
  const layerBits = (b1 >> 1) & 0x03;
  if (version === 1 || layerBits === 0) return null;
  const layer = { 1: 3, 2: 2, 3: 1 }[layerBits];

  const bitrateIndex = (b2 >> 4) & 0x0f;
  const rateIndex = (b2 >> 2) & 0x03;
  const padding = (b2 >> 1) & 0x01;
  if (bitrateIndex === 0 || bitrateIndex === 15 || rateIndex === 3) return null;

  const sampleRate = SAMPLE_RATES[version][rateIndex];
  const table = version === 3 ? BITRATES_V1 : BITRATES_V2;
  const bitrate = table[bitrateIndex] * 1000;
  if (bitrate <= 0) return null;

  const channelsMode = (b3 >> 6) & 0x03;

  let samplesPerFrame;
  if (layer === 1) samplesPerFrame = version === 3 ? 384 : 192;
  else samplesPerFrame = version === 3 ? 1152 : 576;

  let length;
  if (layer === 1) length = Math.floor((12 * bitrate) / sampleRate) + padding * 4;
  else if (layer === 3 && version !== 3) length = Math.floor((72 * bitrate) / sampleRate) + padding;
  else length = Math.floor((144 * bitrate) / sampleRate) + padding;

  if (length < 8) return null;

  return {
    version,
    layer,
    bitrate,
    sampleRate,
    padding,
    channelsMode,
    samplesPerFrame,
    length,
    mpeg: MPEG_NAME[version],
    layerName: LAYER_NAME[layer],
    channelsName: CHANNEL_MODES[channelsMode],
    channels: channelsMode === 3 ? 1 : 2,
    signature: `${version}-${layer}-${sampleRate}-${bitrate}-${channelsMode === 3 ? 1 : 2}`,
  };
}

/** 若 offset 处是 ID3v2 标签，返回标签总长度，否则返回 0 */
function id3v2Size(data, offset) {
  if (offset + 10 > data.length) return 0;
  if (data[offset] !== 0x49 || data[offset + 1] !== 0x44 || data[offset + 2] !== 0x33) return 0;
  const h0 = data[offset + 6], h1 = data[offset + 7], h2 = data[offset + 8], h3 = data[offset + 9];
  const size = ((h0 & 0x7f) << 21) | ((h1 & 0x7f) << 14) | ((h2 & 0x7f) << 7) | (h3 & 0x7f);
  const flags = data[offset + 5];
  let total = 10 + size;
  if (flags & 0x10) total += 10;              // 含 footer
  return total;
}

/** 去掉开头连续的 ID3v2 标签（可能拼接了多个） */
export function stripId3(data) {
  let pos = 0;
  while (pos + 10 <= data.length) {
    const size = id3v2Size(data, pos);
    if (!size) break;
    pos += size;                              // 必须推进，否则这里会死循环
  }
  return data.subarray(pos);
}

/** 顺序产出 { offset, header }，遇到非法字节则向后找下一个同步字 */
export function* iterFrames(data) {
  let pos = 0;
  while (pos + 4 <= data.length) {
    const header = parseFrameHeader(data, pos);
    if (!header) {
      let next = -1;
      for (let i = pos + 1; i < data.length; i += 1) {
        if (data[i] === 0xff) { next = i; break; }
      }
      if (next < 0) return;
      pos = next;
      continue;
    }
    yield { offset: pos, header };
    pos += header.length;
  }
}

/** 定位音频帧区间：{ start, end, count, header } */
export function audioSpan(data) {
  if (!data || !data.length) throw new Mp3Error("空的音频数据");

  const body = stripId3(data);
  const skipped = data.length - body.length;
  if (!body.length) throw new Mp3Error("只有 ID3 标签，没有音频帧");

  let firstOffset = null;
  let firstHeader = null;
  let lastEnd = 0;
  let count = 0;

  for (const { offset, header } of iterFrames(body)) {
    if (offset + header.length > body.length) break;   // 尾部残帧，不要
    if (firstOffset === null) {
      firstOffset = offset;
      firstHeader = header;
    }
    count += 1;
    lastEnd = offset + header.length;
  }

  if (firstOffset === null || count === 0) throw new Mp3Error("未找到合法的 MP3 音频帧");

  return {
    start: skipped + firstOffset,
    end: skipped + lastEnd,
    count,
    header: firstHeader,
  };
}

/** 体检一段 MP3 字节流 */
export function probe(data) {
  const span = audioSpan(data);
  const body = data.subarray(span.start, span.end);
  let totalSamples = 0;
  let n = 0;
  for (const { offset, header } of iterFrames(body)) {
    if (offset + header.length > body.length) break;
    totalSamples += header.samplesPerFrame;
    n += 1;
  }
  return {
    frames: n,
    bytes: span.end - span.start,
    duration: n ? totalSamples / span.header.sampleRate : 0,
    sampleRate: span.header.sampleRate,
    bitrate: span.header.bitrate,
    channels: span.header.channels,
    channelsName: span.header.channelsName,
    mpeg: span.header.mpeg,
    layer: span.header.layerName,
    leadingJunk: span.start,
    trailingJunk: data.length - span.end,
    cleanTail: data.length === span.end,
  };
}

/**
 * 把多段 MP3 裁成干净的帧流片段。
 * 返回 { parts, frames, signature }；各段参数不一致时抛错（避免拼出放不了的文件）。
 */
export function cleanParts(parts) {
  const out = [];
  const signatures = new Set();
  let frames = 0;

  for (const part of parts) {
    if (!part || !part.length) continue;
    const span = audioSpan(part);
    if (!span.count) continue;
    signatures.add(span.header.signature);
    frames += span.count;
    out.push(part.subarray(span.start, span.end));
  }

  if (!out.length) throw new Mp3Error("没有可拼接的音频帧");
  if (signatures.size > 1) {
    throw new Mp3Error(`各片段音频参数不一致，无法直接拼接：${[...signatures].join(" / ")}`);
  }
  return { parts: out, frames, signature: [...signatures][0] };
}

const utf16le = (text) => {
  const out = new Uint8Array(text.length * 2);
  for (let i = 0; i < text.length; i += 1) {
    const c = text.charCodeAt(i);
    out[i * 2] = c & 0xff;
    out[i * 2 + 1] = c >> 8;
  }
  return out;
};

/**
 * 构造最小 ID3v2.3 标签（TIT2 标题 / TSSE 编码信息）。
 * 只写纯文本帧：COMM 之类需要语言码与描述终止符，帧结构稍有偏差
 * 就会让部分解析器在畸形帧上打转。不写入任何作者信息。
 */
export function buildId3v2({ title = "", encoder = "" } = {}) {
  const chunks = [];
  let size = 0;

  const textFrame = (id, text) => {
    if (!text) return;
    const payload = new Uint8Array(3 + text.length * 2);
    payload[0] = 0x01;              // 编码：UTF-16 带 BOM
    payload[1] = 0xff;
    payload[2] = 0xfe;
    payload.set(utf16le(text), 3);
    const head = new Uint8Array(10);
    for (let i = 0; i < 4; i += 1) head[i] = id.charCodeAt(i);
    head[4] = (payload.length >>> 24) & 0xff;
    head[5] = (payload.length >>> 16) & 0xff;
    head[6] = (payload.length >>> 8) & 0xff;
    head[7] = payload.length & 0xff;
    chunks.push(head, payload);
    size += payload.length + 10;
  };

  textFrame("TIT2", title);
  textFrame("TSSE", encoder);
  if (!chunks.length) return new Uint8Array(0);

  const header = new Uint8Array(10);
  header[0] = 0x49; header[1] = 0x44; header[2] = 0x33;   // "ID3"
  header[3] = 3; header[4] = 0; header[5] = 0;
  header[6] = (size >>> 21) & 0x7f;
  header[7] = (size >>> 14) & 0x7f;
  header[8] = (size >>> 7) & 0x7f;
  header[9] = size & 0x7f;

  return concatBytes([header, ...chunks]);
}

/** 按顺序合并若干 Uint8Array */
export function concatBytes(list) {
  let total = 0;
  for (const x of list) total += x.length;
  const out = new Uint8Array(total);
  let at = 0;
  for (const x of list) {
    out.set(x, at);
    at += x.length;
  }
  return out;
}
