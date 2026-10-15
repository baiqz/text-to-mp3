# -*- coding: utf-8 -*-
"""
语音合成引擎
============

负责把"任意长度的文本"变成"一个 MP3 文件"。关键设计:

* **不限长度** —— 文本切成 N 段并发合成,每段独立重试;成功一段就落盘一段,
  内存里从不堆整个音频,因此几万字和几十万字的内存占用是一样的。
* **可中断** —— 每个检查点都会看取消标志;取消后已完成的片段仍在,可选择保留。
* **可观测** —— 进度 = 已完成段数 / 总段数,同时累计真实音频秒数(由 MP3 帧算出),
  不是估算值。
* **可降级** —— 某一段反复失败不会毁掉整个任务,默认跳过并记录,最后如实报告。
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import edge_tts

from . import catalog, mp3join, netcheck, splitter

__all__ = [
    "Canceled",
    "SynthError",
    "Options",
    "Job",
    "run_job",
    "start_job",
    "engine_info",
    "selftest",
]

CHUNK_TIMEOUT = 180          # 单段合成超时(秒)
MAX_TEXT_CHARS = 5_000_000   # 软上限,防误操作


class Canceled(Exception):
    """用户主动取消。"""


class SynthError(Exception):
    """合成失败。"""


# ---------------------------------------------------------------------------
# 参数规范化
# ---------------------------------------------------------------------------
def _num(value, lo: float, hi: float, default: float) -> float:
    try:
        v = float(str(value).strip())
    except Exception:
        return default
    return max(lo, min(hi, v))


def norm_percent(value, default: float = 0.0) -> str:
    v = _num(value, -100.0, 200.0, default)
    return f"{v:+.0f}%"


def norm_hz(value, default: float = 0.0) -> str:
    v = _num(value, -100.0, 100.0, default)
    return f"{v:+.0f}Hz"


@dataclass
class Options:
    voice: str = catalog.DEFAULT_VOICE
    rate: float = 0.0        # 百分比
    volume: float = 0.0      # 百分比
    pitch: float = 0.0       # Hz
    chunk_chars: int = 1200
    retries: int = 3
    concurrency: int = 3
    title: str = ""
    strip_markdown: bool = True
    keep_partial: bool = True     # 有片段失败时,仍输出已成功的部分

    def edge_kwargs(self) -> dict:
        return {
            "voice": self.voice,
            "rate": norm_percent(self.rate, 0),
            "volume": norm_percent(self.volume, 0),
            "pitch": norm_hz(self.pitch, 0),
        }

    def summary(self) -> dict:
        return {
            "voice": self.voice,
            "rate": self.rate,
            "volume": self.volume,
            "pitch": self.pitch,
            "chunk_chars": self.chunk_chars,
            "concurrency": self.concurrency,
        }


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------
class Job:
    """一次转换任务的全部状态。线程安全(读快照时加锁)。"""

    def __init__(self, text: str, options: Options):
        self.id = uuid.uuid4().hex[:12]
        self.text = text
        self.options = options

        self.status = "pending"      # pending|running|done|error|canceled
        self.progress = 0.0
        self.chunks_total = 0
        self.chunks_done = 0
        self.audio_seconds = 0.0
        self.est_seconds = splitter.estimate_seconds(splitter.clean_text(text) if text else "")
        self.message = "等待开始"
        self.error: Optional[str] = None
        self.failures: List[dict] = []
        # 真实异常样本(不随重试轮次清空),失败时用来告诉用户到底怎么了
        self.error_samples: List[str] = []
        self.route = ""              # 实际走通的那条通路

        self.output_path: Optional[str] = None
        self.output_name: Optional[str] = None
        self.size_bytes = 0
        self.duration = 0.0

        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None

        self.cancel_event = threading.Event()
        self.lock = threading.Lock()
        self.tmpdir: Optional[str] = None
        self.chunks: Optional[List[str]] = None

    # -- 状态更新 ---------------------------------------------------------
    def update(self, **kw) -> None:
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def bump(self, duration: float) -> None:
        """完成一段后的进度更新。"""
        with self.lock:
            self.chunks_done += 1
            self.audio_seconds += duration
            self.progress = (self.chunks_done / self.chunks_total) if self.chunks_total else 0.0
            self.message = f"已合成 {self.chunks_done}/{self.chunks_total} 段"

    def add_failure(self, index: int, reason: str) -> None:
        with self.lock:
            self.failures.append({"index": index + 1, "reason": str(reason)[:200]})

    def note_error(self, reason: str) -> None:
        """留一条真实异常的样本,供失败时展示给用户。"""
        text = str(reason)[:300]
        with self.lock:
            if text not in self.error_samples and len(self.error_samples) < 5:
                self.error_samples.append(text)

    def cancel(self) -> None:
        self.cancel_event.set()

    @property
    def canceled(self) -> bool:
        return self.cancel_event.is_set()

    @property
    def finished(self) -> bool:
        return self.status in ("done", "error", "canceled")

    def snapshot(self) -> dict:
        with self.lock:
            elapsed = (self.finished_at or time.time()) - (self.started_at or self.created_at)
            return {
                "id": self.id,
                "status": self.status,
                "progress": round(self.progress, 4),
                "chunks_total": self.chunks_total,
                "chunks_done": self.chunks_done,
                "audio_seconds": round(self.audio_seconds, 2),
                "est_seconds": round(self.est_seconds, 1),
                "message": self.message,
                "error": self.error,
                "errors": list(self.error_samples),
                "route": self.route,
                "failures": list(self.failures),
                "size_bytes": self.size_bytes,
                "duration": round(self.duration, 2),
                "filename": self.output_name,
                "has_audio": bool(self.output_path and os.path.exists(self.output_path)),
                "elapsed": round(elapsed, 1),
                "options": self.options.summary(),
                "chars": len(self.text or ""),
                "preview": (splitter.clean_text(self.text or "") or "")[:60],
            }


# ---------------------------------------------------------------------------
# 合成本体
# ---------------------------------------------------------------------------
async def _collect(text: str, opt: Options, proxy: Optional[str] = None) -> bytes:
    """合成一段文本,返回 MP3 字节。

    ``proxy`` 为 None 时交给 aiohttp 按环境变量自行决定;显式给出地址则强制走它。
    """
    kwargs = {k: v for k, v in opt.edge_kwargs().items() if k != "voice"}
    if proxy:
        kwargs["proxy"] = proxy
    communicate = edge_tts.Communicate(text, opt.voice, **kwargs)
    buf = bytearray()
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            buf += chunk["data"]
    return bytes(buf)


async def _synth_chunk(job: Job, index: int, text: str, tmpdir: str,
                       proxy: Optional[str] = None) -> Tuple[int, str, float]:
    """合成单段:带重试、带 MP3 帧校验。返回 (序号, 文件路径, 时长秒)。"""
    opt = job.options
    last_err: Optional[Exception] = None

    for attempt in range(1, max(1, opt.retries) + 1):
        if job.canceled:
            raise Canceled()
        try:
            data = await asyncio.wait_for(_collect(text, opt, proxy), timeout=CHUNK_TIMEOUT)
            if not data:
                raise SynthError("引擎返回了空音频")
            info = mp3join.probe(data)          # 帧级校验,坏数据当场发现
            if info["frames"] <= 0:
                raise SynthError("音频帧校验未通过")

            path = os.path.join(tmpdir, f"part_{index:06d}.mp3")
            with open(path, "wb") as f:
                f.write(data)
            return index, path, float(info["duration"])
        except Canceled:
            raise
        except Exception as exc:                # noqa: BLE001
            last_err = exc
            job.note_error(f"{type(exc).__name__}: {exc}")
            if attempt < max(1, opt.retries):
                await asyncio.sleep(min(0.6 * (2 ** (attempt - 1)), 3.0))

    raise SynthError(f"第 {index + 1} 段合成失败:{last_err}")


async def _synth_pending(job: Job, chunks: List[str], tmpdir: str,
                         results: List[Optional[Tuple[int, str, float]]],
                         proxy: Optional[str]) -> None:
    """把 results 里还是 None 的片段合成出来，成功即就地写入。"""
    pending = [i for i in range(len(chunks)) if results[i] is None]
    if not pending:
        return

    concurrency = max(1, min(8, job.options.concurrency))
    sem = asyncio.Semaphore(concurrency)

    async def worker(i: int) -> None:
        async with sem:
            if job.canceled:
                return
            try:
                results[i] = await _synth_chunk(job, i, chunks[i], tmpdir, proxy)
                job.bump(results[i][2])
            except Canceled:
                raise
            except SynthError as exc:
                if not job.options.keep_partial:
                    raise
                job.add_failure(i, exc)

    tasks = [asyncio.create_task(worker(i)) for i in pending]

    async def watchdog() -> None:
        while not job.canceled:
            await asyncio.sleep(0.2)
        for t in tasks:
            if not t.done():
                t.cancel()

    watcher = asyncio.create_task(watchdog())
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        watcher.cancel()
        try:
            await watcher
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


def _pick_next_routes(job: Job, tried: List[str]) -> List[dict]:
    """整批失败后,挑出还没试过、且当前真的可用的通路。

    这里才会花代价去探测代理 —— 属于低频的"兜底"路径,不影响正常转换。
    """
    # 先把失效的环境代理摘掉:否则"改用直连"名义上直连、实际还在撞死代理
    sanitize = netcheck.sanitize_env()
    if sanitize["removed"]:
        job.note_error("；".join(sanitize["notes"]))

    routes = []
    for route in netcheck.candidate_routes():
        proxy = route.get("proxy")
        if netcheck.effective_key(proxy) in tried:
            continue
        if proxy and not netcheck.proxy_alive(proxy, timeout=4.0):
            job.note_error(f"通路「{route['label']}」不可用,已跳过")
            continue
        routes.append(route)
    return routes


async def _synth_all(job: Job, chunks: List[str], tmpdir: str) -> List[Optional[Tuple[int, str, float]]]:
    """并发合成全部片段，保序返回结果；失败的段落记为 None。

    关键设计：**整批失败会换通路再试**。素材是别人机器上的网络，
    直连走不通而代理走得通（或反过来）是常态，不该让用户去猜。
    """
    results: List[Optional[Tuple[int, str, float]]] = [None] * len(chunks)
    first = netcheck.candidate_routes()[0]
    tried = [netcheck.effective_key(first["proxy"])]
    job.update(route=first["label"])

    await _synth_pending(job, chunks, tmpdir, results, first["proxy"])
    if job.canceled:
        raise Canceled()
    if all(results):
        return results

    # 一轮跑完仍有缺口 → 换通路补齐
    for route in _pick_next_routes(job, tried):
        if job.canceled:
            raise Canceled()
        tried.append(netcheck.effective_key(route["proxy"]))
        left = sum(1 for r in results if r is None)
        if left == 0:
            break
        job.update(failures=[], route=route["label"],
                   message=f"改用「{route['label']}」重试剩余 {left} 段…")
        await _synth_pending(job, chunks, tmpdir, results, route["proxy"])

    if job.canceled:
        raise Canceled()
    return results


def _safe_filename(title: str, fallback: str = "语音") -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "", title or "")
    name = re.sub(r"\s+", " ", name).strip(" .")
    name = name[:40].strip(" .")
    return name or fallback


def _join_parts(parts: List[Tuple[int, str, float]], out_path: str, opt: Options) -> float:
    """把分片按序拼成最终 MP3,返回总时长。"""
    total = 0.0
    with open(out_path, "wb") as out:
        tag = mp3join.build_id3v2(
            title=opt.title,
            comment=f"由「文字转 MP3」生成 · 音色 {opt.voice}",
            encoder="text-to-mp3",
        )
        out.write(tag)
        for _idx, path, _dur in sorted(parts, key=lambda x: x[0]):
            with open(path, "rb") as f:
                data = f.read()
            start, end, count, header = mp3join.audio_span(data)
            if count <= 0:
                continue
            out.write(data[start:end])
            total += count * header.duration
    return total


def _switch_to_default_voice(job: Job, reason: str) -> bool:
    """把任务换成默认音色,返回是否真的换了。

    音色下架是最容易踩的坑:服务端声库里没有的音色,合成时不会报"找不到音色",
    而是一段段地返回空音频,用户看到的只是"全部合成失败"。所以这里主动兜底。
    """
    if job.options.voice == catalog.DEFAULT_VOICE:
        return False
    old = job.options.voice
    job.options.voice = catalog.DEFAULT_VOICE
    job.note_error(f"音色「{old}」不可用（{reason}），已自动改用「{catalog.DEFAULT_VOICE}」")
    job.update(message=f"音色「{old}」不可用，已改用「{catalog.DEFAULT_VOICE}」…")
    return True


def _reset_progress(job: Job) -> None:
    job.update(progress=0.0, chunks_done=0, audio_seconds=0.0, failures=[])


def run_job(job: Job, output_dir: str) -> None:
    """执行任务(阻塞,设计为在线程中调用)。"""
    job.update(status="running", started_at=time.time(), message="正在切分文本…")
    tmpdir = tempfile.mkdtemp(prefix="ttsmp3-")
    job.tmpdir = tmpdir

    try:
        if not (job.text or "").strip():
            raise SynthError("没有可转换的文字内容")
        if len(job.text) > MAX_TEXT_CHARS:
            raise SynthError(
                f"文本超过 {MAX_TEXT_CHARS:,} 字上限,请分批转换"
            )

        chunks = job.chunks
        if not chunks:
            chunks = splitter.split_text(
                job.text, job.options.chunk_chars, strip_markdown=job.options.strip_markdown
            )
        if not chunks:
            raise SynthError("清理后没有可朗读的文字(可能全是符号或 Markdown 标记)")

        # 音色已经不在服务端声库里 → 不必白跑一轮，直接换掉
        if not catalog.is_valid_voice(job.options.voice):
            _switch_to_default_voice(job, "已从服务端声库下架")

        job.update(chunks_total=len(chunks), message=f"共 {len(chunks)} 段,开始合成…")

        results = asyncio.run(_synth_all(job, chunks, tmpdir))
        ok = [r for r in results if r]

        # 一段都没成功 → 换默认音色再跑一遍（下架音色是头号原因）
        if not ok and _switch_to_default_voice(job, "所有片段都返回空音频"):
            _reset_progress(job)
            job.update(message=f"改用「{job.options.voice}」重试…")
            results = asyncio.run(_synth_all(job, chunks, tmpdir))
            ok = [r for r in results if r]

        if not ok:
            samples = [s for s in job.error_samples if "已跳过" not in s][:2]
            detail = "；".join(samples) if samples else "原因未知（引擎没有返回可用的错误信息）"
            raise SynthError(
                f"{len(chunks)} 段全部合成失败。真实错误：{detail}。{netcheck.quick_hint()}"
            )
        if len(ok) < len(chunks):
            job.update(message=f"{len(chunks) - len(ok)} 段失败,已生成其余部分")

        title = job.options.title.strip() or (chunks[0][:24] if chunks else "语音")
        job.options.title = title
        filename = f"{_safe_filename(title)}_{time.strftime('%Y%m%d_%H%M%S')}.mp3"
        out_path = os.path.join(output_dir, filename)

        duration = _join_parts(ok, out_path, job.options)
        size = os.path.getsize(out_path)
        if size <= 0:
            raise SynthError("生成的文件为空")

        job.update(
            status="done",
            progress=1.0,
            output_path=out_path,
            output_name=filename,
            size_bytes=size,
            duration=duration,
            message=f"完成 · {len(ok)}/{len(chunks)} 段 · {_fmt_time(duration)}",
            finished_at=time.time(),
        )
    except Canceled:
        job.update(status="canceled", message="已取消", finished_at=time.time())
    except Exception as exc:                    # noqa: BLE001
        job.update(status="error", error=str(exc), message=f"失败:{exc}", finished_at=time.time())
    finally:
        try:
            if tmpdir and os.path.isdir(tmpdir):
                shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:                        # noqa: BLE001
            pass
        job.tmpdir = None


def _fmt_time(seconds: float) -> str:
    seconds = int(round(seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def start_job(text: str, options: Options, output_dir: str) -> Job:
    """创建任务并在线程中启动。

    切分放在这里同步完成(百万字也只需零点几秒),好处是:
      * 调用方立刻就能知道一共几段、能否转换;
      * 文本为空或全是标记时,当场报错而不是等到后台线程里才失败。
    """
    if not (text or "").strip():
        raise SynthError("没有可转换的文字内容")
    if len(text) > MAX_TEXT_CHARS:
        raise SynthError(f"文本超过 {MAX_TEXT_CHARS:,} 字上限,请分批转换")

    chunks = splitter.split_text(text, options.chunk_chars,
                                 strip_markdown=options.strip_markdown)
    if not chunks:
        raise SynthError("清理后没有可朗读的文字(可能全是符号或 Markdown 标记)")

    job = Job(text, options)
    job.chunks = chunks
    job.chunks_total = len(chunks)

    thread = threading.Thread(target=run_job, args=(job, output_dir), daemon=True)
    thread.start()
    job.thread = thread               # type: ignore[attr-defined]
    return job


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
def engine_info() -> dict:
    return {
        "edge_tts": getattr(edge_tts, "__version__", "unknown"),
        "python": __import__("sys").version.split()[0],
    }


async def _selftest_probe(opt: Options, proxy: Optional[str] = None) -> bytes:
    return await asyncio.wait_for(_collect("引擎自检，一二三四五。", opt, proxy), timeout=30)


def selftest(voice: Optional[str] = None) -> dict:
    """合成一小段音频,验证引擎当前真的可用。

    自检本身也会换通路:环境代理死了就摘掉走直连,直连不通再试代理 ——
    目标是给出"现在到底能不能用、走哪条路能用"的确定答案。
    """
    voice = voice or catalog.DEFAULT_VOICE
    opt = Options(voice=voice, concurrency=1, retries=2)
    t0 = time.time()
    errors: List[str] = []
    attempts: List[dict] = []

    def _try(route: dict, proxy: Optional[str]) -> Optional[bytes]:
        try:
            return asyncio.run(_selftest_probe(opt, proxy))
        except Exception as exc:                   # noqa: BLE001
            msg = f"{type(exc).__name__}: {exc}"
            errors.append(msg)
            attempts.append({"route": route["label"], "error": msg[:200]})
            return None

    routes = netcheck.candidate_routes()
    data: Optional[bytes] = None
    used = ""

    for idx, route in enumerate(routes[:4]):
        proxy = route.get("proxy")
        if proxy and not netcheck.proxy_alive(proxy, timeout=4.0):
            attempts.append({"route": route["label"], "error": "代理不可用,未尝试"})
            continue
        data = _try(route, proxy)
        if data:
            used = route["label"] if idx else "默认通路（直连）"
            break
        if idx == 0:
            # 默认通路失败：很可能是继承来的环境代理已经死了，摘掉再试
            cleaned = netcheck.sanitize_env()
            if cleaned["removed"]:
                attempts.append({"route": "清理失效代理后重试", "error": ""})
                data = _try({"label": "直连"}, None)
                if data:
                    used = "直连（已自动摘除失效代理）"
                    break

    if data is None:
        return {
            "ok": False,
            "latency": round(time.time() - t0, 2),
            "error": errors[0] if errors else "未知错误",
            "errors": errors[:4],
            "attempts": attempts,
            "hint": netcheck.quick_hint(),
            "voice": voice,
            "engine": engine_info(),
        }

    info = mp3join.probe(data)
    return {
        "ok": True,
        "latency": round(time.time() - t0, 2),
        "bytes": len(data),
        "duration": round(info["duration"], 2),
        "sample_rate": info["sample_rate"],
        "voice": voice,
        "route": used,
        "engine": engine_info(),
    }
