/**
 * 浏览器端语音合成引擎
 *
 * 原理：Edge 浏览器的「朗读」功能走的是一个公开的 WebSocket 端点，
 * 页面可以直接连上去把文字换成音频，因此**不需要任何后端**。
 *
 * 一次连接只发一小段文本，所以长文本按切分器分段、并发合成，
 * 最后按顺序做帧级拼接。
 *
 * 实测要点：
 *  - 服务端会校验请求头里的 User-Agent（浏览器天然带）与 Sec-MS-GEC 令牌，
 *    但对 Origin 不设限，所以网页可以直连；
 *  - 音色是否可用必须实测：声库里有已下架的音色，用它会返回"无音频"，
 *    所以整批失败时会自动换默认音色重试一遍。
 */

import { generateSecMsGec, SEC_MS_GEC_VERSION, TRUSTED_CLIENT_TOKEN } from "./token.js";
import { cleanParts, concatBytes, probe, buildId3v2 } from "./mp3.js";
import { splitText } from "./split.js";

const HOST = "speech.platform.bing.com";
const OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3";

export const FALLBACK_VOICE = "zh-CN-XiaoxiaoNeural";

export class SynthError extends Error {}
export class Canceled extends Error {}
/** 服务端明确拒绝该音色（一般是已下架），属于确定性错误，不必重试 */
export class VoiceUnsupported extends SynthError {}

/** 在二进制报文里找 Path:audio 标记 */
function headerHasPathAudio(buf, headerLength) {
  const needle = "Path:audio";
  const limit = Math.min(headerLength, buf.length - needle.length);
  for (let i = 0; i <= limit; i += 1) {
    let hit = true;
    for (let j = 0; j < needle.length; j += 1) {
      if (buf[i + j] !== needle.charCodeAt(j)) { hit = false; break; }
    }
    if (hit) return true;
  }
  return false;
}

const hex32 = () => {
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  return Array.from(b).map((x) => x.toString(16).padStart(2, "0")).join("");
};

/** 与浏览器控制台同款的日期串，服务端只把它当作字段回显 */
function timestamp() {
  const d = new Date();
  const WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const MO = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const p = (n) => String(n).padStart(2, "0");
  return `${WD[d.getUTCDay()]} ${MO[d.getUTCMonth()]} ${p(d.getUTCDate())} ${d.getUTCFullYear()} ` +
    `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())} GMT+0000 (Coordinated Universal Time)`;
}

/** 服务端不支持个别控制字符，遇到会直接报错 */
export function sanitizeText(text) {
  let out = "";
  for (const ch of text) {
    const code = ch.codePointAt(0);
    if ((code >= 0 && code <= 8) || (code >= 11 && code <= 12) || (code >= 14 && code <= 31)) {
      out += " ";
    } else {
      out += ch;
    }
  }
  return out;
}

const escapeXml = (s) => s
  .replace(/&/g, "&amp;")
  .replace(/</g, "&lt;")
  .replace(/>/g, "&gt;");

// 注意：官方 SSML 的 mstts:express-as（情感风格）在这个朗读端点上会被拒绝
// （close code 1007 · SSML is invalid，https/http/局部声明三种写法都试过），
// 所以这里只支持 prosody 三种参数，音色差异只能靠换音色。
function buildSsml({ text, voice, rate, volume, pitch }) {
  return `<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'>` +
    `<voice name='${voice}'><prosody pitch='${pitch}' rate='${rate}' volume='${volume}'>` +
    `${escapeXml(sanitizeText(text))}</prosody></voice></speak>`;
}

const pct = (v) => `${v >= 0 ? "+" : ""}${Math.round(v)}%`;
const hz = (v) => `${v >= 0 ? "+" : ""}${Math.round(v)}Hz`;

/**
 * 合成一小段文本，返回 mp3 字节。
 * @param {{text:string, voice:string, rate?:number, volume?:number, pitch?:number,
 *          timeoutMs?:number, signal?:AbortSignal}} opts
 */
export function synthesize(opts) {
  const {
    text, voice, rate = 0, volume = 0, pitch = 0,
    timeoutMs = 30000, signal,
  } = opts;

  const url = `wss://${HOST}/consumer/speech/synthesize/readaloud/edge/v1` +
    `?TrustedClientToken=${TRUSTED_CLIENT_TOKEN}` +
    `&ConnectionId=${hex32()}` +
    `&Sec-MS-GEC=${generateSecMsGec()}` +
    `&Sec-MS-GEC-Version=${SEC_MS_GEC_VERSION}`;

  return new Promise((resolve, reject) => {
    let ws;
    let settled = false;
    let audioBytes = 0;
    const pieces = [];
    let timer = null;

    const cleanup = () => {
      if (timer) clearTimeout(timer);
      if (signal) signal.removeEventListener("abort", onAbort);
      try { if (ws && ws.readyState <= 1) ws.close(); } catch { /* 忽略 */ }
    };
    const done = (err, value) => {
      if (settled) return;
      settled = true;
      cleanup();
      if (err) reject(err); else resolve(value);
    };
    const onAbort = () => done(new Canceled("已取消"));

    if (signal) {
      if (signal.aborted) return done(new Canceled("已取消"));
      signal.addEventListener("abort", onAbort);
    }

    try {
      ws = new WebSocket(url);
    } catch (e) {
      return done(new SynthError(`无法建立连接：${e.message}`));
    }
    ws.binaryType = "arraybuffer";

    timer = setTimeout(() => {
      done(new SynthError(`超时（${Math.round(timeoutMs / 1000)} 秒未收到完整音频）`));
    }, timeoutMs);

    ws.onopen = () => {
      const ts = timestamp();
      const meta = JSON.stringify({
        context: {
          synthesis: {
            audio: {
              metadataoptions: { sentenceBoundaryEnabled: "false", wordBoundaryEnabled: "false" },
              outputFormat: OUTPUT_FORMAT,
            },
          },
        },
      });
      ws.send(`X-Timestamp:${ts}\r\nContent-Type:application/json; charset=utf-8\r\n` +
        `Path:speech.config\r\n\r\n${meta}\r\n`);
      ws.send(`X-RequestId:${hex32()}\r\nContent-Type:application/ssml+xml\r\n` +
        `X-Timestamp:${ts}Z\r\nPath:ssml\r\n\r\n` +
        buildSsml({ text, voice, rate: pct(rate), volume: pct(volume), pitch: hz(pitch) }));
    };

    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        if (ev.data.includes("Path:turn.end")) {
          if (!audioBytes) {
            done(new SynthError("服务端没有返回音频（该音色可能已下架）"));
          } else {
            done(null, concatBytes(pieces));
          }
        }
        return;
      }
      const buf = new Uint8Array(ev.data);
      if (buf.length < 4) return;
      const headerLength = (buf[0] << 8) | buf[1];
      if (headerLength > buf.length) return;
      if (!headerHasPathAudio(buf, headerLength)) return;   // 非音频报文（如元数据）
      // 音频从「头部长度 + 2」处开始（与官方实现一致），少切或多切 2 字节都会毁掉整帧
      const payload = buf.subarray(headerLength + 2);
      if (!payload.length) return;
      audioBytes += payload.length;
      pieces.push(payload.slice());
    };

    ws.onerror = () => {
      done(new SynthError("连接被拒绝。若长期如此，可能是服务端更新了校验方式。"));
    };
    ws.onclose = (e) => {
      if (settled) return;
      const reason = e.reason || "";
      // 服务端会明确告知音色不受支持，这类错误重试没有意义
      if (/unsupported voice/i.test(reason) || e.code === 1007) {
        done(new VoiceUnsupported(`音色不被服务端支持${reason ? "：" + reason : `（代码 ${e.code}）`}`));
        return;
      }
      done(new SynthError(
        audioBytes
          ? `连接提前关闭（已收到 ${audioBytes} 字节，但不完整）`
          : `连接被关闭（代码 ${e.code}${reason ? "：" + reason : ""}）`
      ));
    };
  });
}

/** 带重试的单段合成 */
async function synthesizeWithRetry(opts, { retries = 3, onLog } = {}) {
  let lastErr = null;
  for (let attempt = 1; attempt <= retries; attempt += 1) {
    try {
      const data = await synthesize(opts);
      const info = probe(data);            // 帧级体检，坏数据当场发现
      if (!info.frames) throw new SynthError("返回的音频不含有效帧");
      return { data, info };
    } catch (e) {
      if (e instanceof Canceled) throw e;
      lastErr = e;
      if (e instanceof VoiceUnsupported) throw e;      // 确定性错误，重试无用
      if (attempt < retries) {
        if (onLog) onLog(`第 ${attempt} 次失败（${e.message}），正在重试…`);
        await new Promise((r) => setTimeout(r, 400 * attempt));
      }
    }
  }
  throw lastErr ?? new SynthError("合成失败");
}

/**
 * 完整任务：切分 → 并发合成 → 帧级拼接 → 加标签。
 * @param {object} job
 * @param {string} job.text
 * @param {object} job.options
 * @param {(ev:object)=>void} [job.onEvent]
 * @param {AbortSignal} [job.signal]
 */
export async function runJob({ text, options = {}, onEvent = () => {}, signal }) {
  const opt = {
    voice: options.voice || FALLBACK_VOICE,
    rate: options.rate ?? 0,
    volume: options.volume ?? 0,
    pitch: options.pitch ?? 0,
    chunkChars: options.chunkChars ?? 1200,
    concurrency: Math.max(1, Math.min(8, options.concurrency ?? 3)),
    retries: Math.max(1, Math.min(5, options.retries ?? 3)),
    title: options.title || "",
  };

  const chunks = splitText(text, opt.chunkChars, { stripMarkdown: options.stripMarkdown !== false });
  if (!chunks.length) throw new SynthError("清理后没有可朗读的文字（可能全是符号或 Markdown 标记）");

  const total = chunks.length;
  onEvent({ type: "start", total, chars: chunks.reduce((a, c) => a + c.length, 0) });

  /** 跑一轮；返回按序的结果数组 */
  async function pass(voice) {
    const results = new Array(total).fill(null);
    let done = 0;
    let cursor = 0;
    let firstError = null;
    let unsupported = null;          // 服务端明确拒绝该音色时记下来

    const worker = async () => {
      for (;;) {
        if (signal?.aborted) throw new Canceled("已取消");
        if (unsupported) return;             // 音色已被否决，不必再跑剩下的
        const index = cursor;
        cursor += 1;
        if (index >= total) return;
        try {
          const { data, info } = await synthesizeWithRetry(
            { text: chunks[index], voice, rate: opt.rate, volume: opt.volume, pitch: opt.pitch, signal },
            { retries: opt.retries, onLog: (m) => onEvent({ type: "log", text: `第 ${index + 1} 段：${m}` }) },
          );
          results[index] = { data, duration: info.duration };
        } catch (e) {
          if (e instanceof Canceled) throw e;
          if (e instanceof VoiceUnsupported) { unsupported = e; return; }
          results[index] = null;
          firstError = firstError || e;
          onEvent({ type: "log", text: `第 ${index + 1} 段失败：${e.message}`, bad: true });
        }
        done += 1;
        onEvent({
          type: "progress", done, total,
          percent: Math.round((done / total) * 100),
          voice,
        });
      }
    };

    await Promise.all(Array.from({ length: Math.min(opt.concurrency, total) }, worker));
    return { results, firstError, unsupported };
  }

  let voice = opt.voice;
  let { results, firstError, unsupported } = await pass(voice);
  let switched = false;

  // 一段都没成功 → 换默认音色再来一遍（音色下架是最常见的原因）
  if (!results.some(Boolean) && voice !== FALLBACK_VOICE) {
    const why = unsupported ? unsupported.message : "该音色没有任何音频返回";
    onEvent({ type: "log", text: `音色「${voice}」不可用（${why}），自动改用默认音色重试…`, bad: true });
    voice = FALLBACK_VOICE;
    switched = true;
    onEvent({ type: "voiceSwitched", voice, reason: why });
    ({ results, firstError } = await pass(voice));
  }

  const okParts = results.filter(Boolean);
  if (!okParts.length) {
    throw new SynthError(
      `全部 ${total} 段都没有合成成功。${firstError ? "最后一次错误：" + firstError.message : ""}`
    );
  }

  const failed = results.filter((r) => !r).length;
  onEvent({ type: "joining", failed });

  const { parts, frames } = cleanParts(okParts.map((r) => r.data));
  const audio = concatBytes(parts);
  const info = probe(audio);
  const tag = buildId3v2({
    title: opt.title || chunks[0].replace(/\s+/g, " ").slice(0, 40),
    encoder: "文字转 MP3 · 浏览器版",
  });
  const bytes = tag.length ? concatBytes([tag, audio]) : audio;

  const result = {
    bytes,
    frames,
    duration: info.duration,
    sampleRate: info.sampleRate,
    bitrate: info.bitrate,
    chunks: total,
    failed,
    voice,
    switched,
  };
  onEvent({ type: "done", ...result });
  return result;
}
